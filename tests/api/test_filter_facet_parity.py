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
from datahub.api.search.document import FACET_FIELDS


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
        "bbox",
        "temporal_start",
        "temporal_end",
    }
    return {
        name for name in inspect.signature(search_datasets).parameters if name not in not_filters
    }


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
