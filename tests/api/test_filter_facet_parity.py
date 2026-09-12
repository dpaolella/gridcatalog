"""Every facet the API offers as a filter must be spelled the same way twice.

The licence filter was broken in both directions at once and nothing noticed:
the response advertised a facet called ``license``, the route accepted a
parameter called ``license_id``, and the two never met. A UI built from the
facet response sent ``?license=`` and got it silently ignored, because FastAPI
drops unknown query parameters. Anything sending the documented ``?license_id=``
got a 500, because ``SearchRequest.__post_init__`` rejects a filter name that is
not a key of ``FACET_FIELDS``.

Both halves are the same mistake — a name that exists in two places and has to
be kept identical by hand. These tests keep them identical by machine.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.routers.datasets import search_datasets
from datahub.api.search.document import FACET_FIELDS, RANGE_FIELDS, range_path


def _query_parameters() -> set[str]:
    """The names ``/v1/datasets`` accepts, minus the ones that are not filters."""
    not_filters = {
        "caller",
        "backend",
        "q",
        "sort",
        "facets",
        "offset",
        "limit",
        "include_unconfirmed",
        # Bounds, not exact matches. These resolve through RANGE_FIELDS and
        # the bbox clause rather than FACET_FIELDS, so the parity rule below
        # does not apply to them — `test_every_range_parameter_resolves`
        # covers them instead.
        "bbox",
        "temporal_start",
        "temporal_end",
    }
    return {
        name
        for name in inspect.signature(search_datasets).parameters
        if name not in not_filters and not _is_bound(name)
    }


#: Suffixes that mark a route parameter as a bound rather than an exact match.
#: Derived rather than listed, because the list was hand-kept and this test
#: exists to stop hand-keeping: adding `field_count_min` broke it, exactly as
#: adding `resolution_max_m` had, and the fix both times was to name the new
#: parameter in one more place. A convention the route already follows costs
#: nothing to honour and cannot fall behind.
BOUND_SUFFIXES = ("_min", "_max", "_max_m")


def _is_bound(name: str) -> bool:
    return name.endswith(BOUND_SUFFIXES)


def _probe_value(name: str) -> str:
    """A value of the right *type* for this filter.

    The point of the probe is to exercise the name, not the value — so it has to
    type-check, or a 422 for the wrong reason masks the 500 we are looking for.
    """
    annotation = str(inspect.signature(search_datasets).parameters[name].annotation)
    if "bool" in annotation:
        return "true"
    if "int" in annotation:
        return "1"
    return "x"


def test_every_filter_parameter_is_a_known_facet():
    """A filter the route accepts but the backend cannot resolve is a 500.

    ``SearchRequest`` raises ``ValueError`` on an unknown filter field, and a
    ValueError inside a route is an unhandled exception, so this failure mode is
    a server error on a well-formed request.
    """
    unknown = sorted(_query_parameters() - set(FACET_FIELDS))
    assert not unknown, (
        f"these query parameters are not keys of FACET_FIELDS, so passing any of "
        f"them raises 'unknown filter field' and returns 500: {unknown}"
    )


def test_every_facet_is_a_filter_parameter_the_route_declares():
    """The half this file was missing, and the half that keeps breaking.

    `test_every_filter_parameter_is_a_known_facet` catches a route parameter
    with no facet behind it — a 500. This catches the opposite and quieter one:
    a facet offered in the response with no route parameter to send it to.
    FastAPI silently drops a query parameter it has not declared, so the caller
    gets the whole catalog back and presents it as filtered. No error, no log
    line, and a result set that is wrong in the direction of "too much".

    That has now happened three times. The licence mismatch was found in
    production. `concept=` was being sent by the MCP search tool since M10 and
    dropped the whole time, so an agent filtering by concept got the unfiltered
    catalog. `record_type=` was added as a facet, offered on every response,
    and dropped by the route — `?record_type=reference_model` returned all 60
    records. Each time the fix was to name the field in one more place, and
    each time only one direction was asserted.
    """
    missing = sorted(set(FACET_FIELDS) - _query_parameters() - set(RANGE_FIELDS))
    assert not missing, (
        "these facets are advertised in every response and the route declares no "
        "parameter for them, so a client that filters on one gets the unfiltered "
        f"catalog back and no error: {missing}"
    )


@pytest.mark.parametrize("name", sorted(_query_parameters()))
def test_each_filter_round_trips_through_the_api(client, name):
    """Passing a filter must not error, and must be the name the facet uses.

    Asserted against a live app rather than by reading the signature, because
    the signature is only half the contract — the other half is that the
    backend accepts the same string.
    """
    probe = _probe_value(name)
    response = client.get("/v1/datasets", params={name: probe, "facets": name, "limit": 0})
    assert response.status_code == 200, (
        f"filtering on {name!r} returned {response.status_code}: {response.text[:200]}"
    )
    facets = response.json()["facets"]
    assert name in facets, (
        f"{name!r} is an accepted filter but is not returned as a facet under that "
        f"name, so a UI built from the response cannot construct the filter"
    )


def test_the_licence_filter_actually_filters(client):
    """The regression this file exists for, end to end."""
    everything = client.get("/v1/datasets", params={"limit": 0, "facets": "license"})
    assert everything.status_code == 200
    buckets = everything.json()["facets"]["license"]
    assert buckets, "the fixture corpus should carry licences to filter on"

    value = buckets[0]["value"]
    filtered = client.get("/v1/datasets", params={"license": value, "limit": 50})
    assert filtered.status_code == 200
    results = filtered.json()["results"]
    assert results, f"filtering on the licence the API itself reported ({value!r}) found nothing"
    assert all(r["license_id"] == value for r in results)


@pytest.mark.parametrize(
    "params",
    [
        {"facets": "not_a_facet"},
        {"facets": "data_domain,not_a_facet"},
        {"sort": "not_a_field"},
    ],
)
def test_a_bad_field_name_is_a_client_error(client, params):
    """400 with the valid names, not 500.

    The router used to split ``?facets=`` inline and hand the result straight to
    ``SearchRequest``, whose ``__post_init__`` raises ``ValueError`` — which is
    an unhandled exception, so a typo in a query string was a server error. The
    validating helper already existed; the router simply did not call it.
    """
    response = client.get("/v1/datasets", params=params)
    assert response.status_code == 400, response.text[:200]
    assert "unknown" in response.json()["title"]


def test_every_range_parameter_resolves_to_a_field_the_backends_can_bound():
    """The same rule as above, for the parameters that are bounds not matches.

    A range name the backends cannot resolve fails in two different ways —
    silently matching nothing in memory, `KeyError` against OpenSearch — so it
    needs the same machine check that `FACET_FIELDS` gets, not a hand-kept list.
    """
    from datahub.api.search.query import SearchParams, _ranges

    named = set(_ranges(SearchParams(completeness_min=1, resolution_max_m=1.0, field_count_min=1)))
    assert named, "no range parameter is wired, so this test covers nothing"
    for name in named:
        assert range_path(name), name
    assert named <= set(RANGE_FIELDS), (
        f"a range parameter resolves through a fallback rather than RANGE_FIELDS: "
        f"{sorted(named - set(RANGE_FIELDS))}"
    )
