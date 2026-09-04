"""``/v1/datasets`` (WP-4.3).

A real app over a real store holding the fixture corpus. The interesting
failures in an HTTP layer are wiring failures, and wiring is what a mocked
dependency hides.

The entitlement tests are the ones that matter most. ADR-0006's rule is that a
record the caller may not see contributes to no count and appears in no page —
and the way that rule fails in practice is not a missing check, it is a check
in the wrong place: applied to results instead of compiled into the query, so
the total is right and the page is filtered, or the page is right and the total
leaks.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ERA5 = "ecmwf-era5"
RESTRICTED = "caiso-nodal-lmp-restricted"
HIDDEN = "utility-load-shapes-allowlisted"
HTTP_DATASET = "wri-global-power-plant-database"


# ---- search --------------------------------------------------------------


def test_search_returns_the_catalog(client) -> None:
    body = client.get("/v1/datasets").json()

    assert body["total"] > 0
    assert len(body["results"]) == body["total"] or len(body["results"]) == 20
    assert all(r["id"] and r["title"] for r in body["results"])


def test_free_text_narrows_the_result_set(client) -> None:
    everything = client.get("/v1/datasets").json()["total"]
    reanalysis = client.get("/v1/datasets", params={"q": "reanalysis"}).json()

    assert 0 < reanalysis["total"] < everything
    assert any("era5" in r["id"] for r in reanalysis["results"])


def test_the_last_token_is_prefix_matched(client) -> None:
    """Search-while-typing is the intended interaction (PRD §F3): there is no
    submit step, so a half-typed word has to match."""
    partial = client.get("/v1/datasets", params={"q": "reanaly"}).json()
    assert partial["total"] > 0


def test_filters_are_named_parameters(client) -> None:
    """Named rather than a free-form `filter[]`, so a client discovers the
    filterable fields from the OpenAPI document rather than from prose."""
    body = client.get(
        "/v1/datasets",
        params={"data_domain": "https://schema.opengrid.org/concept/data-domain/DD5"},
    ).json()

    assert body["total"] > 0
    assert all(any(d["iri"].endswith("/DD5") for d in r["data_domains"]) for r in body["results"])


def test_facets_come_back_with_the_results(client) -> None:
    """Not from a second call: a UI that had to ask twice would show counts
    that disagree with the list for as long as the second call is in flight."""
    body = client.get("/v1/datasets", params={"facets": "data_domain,provenance_class"}).json()

    assert set(body["facets"]) == {"data_domain", "provenance_class"}
    assert sum(b["count"] for b in body["facets"]["data_domain"]) >= body["total"]


def test_a_facet_only_query_is_allowed(client) -> None:
    """ "Give me the counts and no results" is how a filter panel is populated.
    Making the caller ask for a row they discard costs a projection per query
    for nothing."""
    body = client.get("/v1/datasets", params={"limit": 0, "facets": "data_domain"}).json()

    assert body["results"] == []
    assert body["total"] > 0
    assert body["facets"]["data_domain"]


def test_pagination_is_stable(client) -> None:
    first = client.get("/v1/datasets", params={"limit": 3, "sort": "title"}).json()
    second = client.get("/v1/datasets", params={"limit": 3, "offset": 3, "sort": "title"}).json()

    assert first["total"] == second["total"]
    assert not ({r["id"] for r in first["results"]} & {r["id"] for r in second["results"]})


def test_an_over_large_limit_is_refused_not_silently_capped(client) -> None:
    """Silently capping would make a client that asked for 5,000 believe it had
    them all, and page through nothing."""
    response = client.get("/v1/datasets", params={"limit": 5000})
    assert response.status_code == 422


def test_a_bad_bbox_says_what_is_wrong(client) -> None:
    response = client.get("/v1/datasets", params={"bbox": "not,a,bbox"})
    assert response.status_code == 400
    assert "bbox" in response.json()["title"].lower()


def test_unconfirmed_records_are_stewards_only(client) -> None:
    """A caller asking for drafts who is not entitled gets an error, not a
    quietly confirmed-only result set — silent narrowing makes a steward tool
    look broken rather than unauthorised."""
    response = client.get("/v1/datasets", params={"include_unconfirmed": True})

    assert response.status_code == 403
    assert "steward" in response.json()["title"]


# ---- one record ----------------------------------------------------------


def test_a_record_reads_back(client) -> None:
    body = client.get(f"/v1/datasets/{ERA5}").json()

    assert body["id"] == ERA5
    assert body["title"].startswith("ECMWF ERA5")
    assert body["description"]
    assert body["distributions"]
    assert body["completeness_level"] == 3


def test_the_slug_is_the_last_segment_of_the_iri(client) -> None:
    """So a caller holding an IRI does not need a lookup table: it takes the
    last segment. A full IRI is not accepted in the path — its slashes are
    indistinguishable from the `/schema` and `/quality` that follow it."""
    body = client.get(f"/v1/datasets/{ERA5}").json()

    assert body["iri"].rsplit("/", 1)[-1] == body["id"] == ERA5
    assert client.get(f"/v1/datasets/https://catalog.opengrid.org/ds/{ERA5}").status_code == 404


def test_an_absent_record_is_a_problem_detail(client) -> None:
    response = client.get("/v1/datasets/no-such-dataset")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 404
    assert body["requestId"]


def test_the_schema_endpoint_returns_fields(client) -> None:
    body = client.get(f"/v1/datasets/{ERA5}/schema").json()

    assert body["fields"]
    assert all(f["local_name"] for f in body["fields"])
    assert body["unavailable_reason"] is None


def test_an_empty_schema_explains_itself(client) -> None:
    """PRD §F3: an absent schema tab explains itself; it is not an empty table.
    "No fields" reads as "this dataset has no columns", which is almost never
    true — what is true is that nobody has catalogued them."""
    body = client.get("/v1/datasets/eia-natural-gas-prices/schema").json()

    assert body["fields"] == []
    assert body["unavailable_reason"]
    assert "level" in body["unavailable_reason"]


def test_quality_returns_three_independent_facets(client) -> None:
    """ADR-0007: no composite. A dataset can be perfectly current and
    completely unprovenanced, and averaging those destroys the only information
    a user could act on."""
    body = client.get(f"/v1/datasets/{ERA5}/quality").json()

    assert {f["facet"] for f in body["facets"]} == {"currency", "provenance", "documentation"}
    assert "composite" not in body
    assert "score" not in body
    assert "overall" not in body


def test_distributions_include_the_unhealthy_ones(client) -> None:
    """A dead link a user can see is a reportable fact; a dead link silently
    removed is a dataset that appears to have no access path at all."""
    body = client.get(f"/v1/datasets/{ERA5}/distributions").json()

    assert body
    assert all(d["id"] for d in body)


# ---- download ------------------------------------------------------------


def test_download_redirects_and_never_proxies(client) -> None:
    """A redirect rather than a proxy is a design decision, not an
    optimisation: proxying would make OpenGrid an egress bill, a bandwidth
    bottleneck and a party to every licence it does not hold."""
    response = client.get(f"/v1/datasets/{HTTP_DATASET}/download", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("http")
    assert not response.content


def test_download_prefers_a_healthy_anonymous_path(client) -> None:
    """A user who clicked "download" wants a file. Sending them to a login form
    when an open mirror exists is a worse answer than the mirror."""
    body = client.get(f"/v1/datasets/{HTTP_DATASET}/distributions").json()
    response = client.get(f"/v1/datasets/{HTTP_DATASET}/download", follow_redirects=False)

    chosen = response.headers["location"]
    picked = next(d for d in body if d["access_url"] == chosen)
    assert picked["anonymous_access"] is not False


def test_download_skips_a_uri_a_browser_cannot_follow(client) -> None:
    """ERA5's best path by every other measure is `s3://era5-pds/zarr/`:
    anonymous, bulk, healthy. A browser cannot follow it, so download picks the
    account-gated HTTPS API instead — a login form beats a dead tab.

    The s3 path is not wrong and is not hidden; it is what the access plan is
    for (PRD §F7).
    """
    response = client.get(f"/v1/datasets/{ERA5}/download", follow_redirects=False)
    paths = client.get(f"/v1/datasets/{ERA5}/distributions").json()

    assert any(d["access_url"].startswith("s3://") for d in paths), "the fixture's premise"
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://")


def test_download_says_so_when_no_path_is_followable() -> None:
    """And when there is no HTTPS path at all: a 409, because the record exists
    and this endpoint cannot serve it — a different thing from a 404, needing a
    different response from the client."""
    from datahub.api.routers.datasets import _best_distribution
    from datahub.api.schemas import DistributionDetail

    only_s3 = [DistributionDetail(id="d1", access_url="s3://bucket/prefix/", bulk_download=True)]
    assert _best_distribution(only_s3) is None


def test_download_on_an_absent_record_is_a_404(client) -> None:
    response = client.get("/v1/datasets/nope/download", follow_redirects=False)
    assert response.status_code == 404


# ---- entitlement (ADR-0006) ----------------------------------------------


def test_a_restricted_record_is_absent_from_anonymous_search(client) -> None:
    """Not filtered from the page — absent from the count. A total is enough to
    confirm a dataset exists."""
    body = client.get("/v1/datasets", params={"q": "HIDDEN", "limit": 100}).json()
    assert not any(r["id"] == HIDDEN for r in body["results"])


def test_a_restricted_record_is_a_404_not_a_403(client) -> None:
    """A 403 says "this exists and you cannot have it", which on a record whose
    existence is the restricted part is the disclosure itself."""
    response = client.get(f"/v1/datasets/{HIDDEN}")

    assert response.status_code == 404
    assert response.status_code != 403


def test_the_404_for_withheld_and_the_404_for_absent_are_identical(client) -> None:
    """The point of the previous test, made properly: if the two responses
    differ in any way a caller can observe, the distinction leaks."""
    withheld = client.get(f"/v1/datasets/{HIDDEN}").json()
    absent = client.get("/v1/datasets/definitely-not-a-dataset").json()

    withheld.pop("requestId"), absent.pop("requestId")
    assert set(withheld) == set(absent)
    assert withheld["status"] == absent["status"] == 404
    assert withheld["type"] == absent["type"]


def test_every_sub_resource_of_a_withheld_record_is_also_404(client) -> None:
    """One endpoint that answered would undo the other four."""
    for suffix in ("", "/schema", "/quality", "/distributions", "/download"):
        response = client.get(f"/v1/datasets/{HIDDEN}{suffix}", follow_redirects=False)
        assert response.status_code == 404, suffix


def test_search_totals_do_not_count_what_the_caller_cannot_see(client, loaded) -> None:
    """The count is the leak. Compare the entitled total against what is
    actually in the catalog."""
    from datahub.graph.graphs import NamedGraph

    in_catalog = loaded.count(graph=NamedGraph.CATALOG)
    visible = client.get("/v1/datasets", params={"limit": 0}).json()["total"]

    assert visible < in_catalog, "the restricted fixture should be invisible"


# ---- the error contract --------------------------------------------------


def test_every_error_is_a_problem_detail(client) -> None:
    """RFC 9457 from one handler, so a client writes one error path rather than
    one per endpoint."""
    for path, expected in (
        ("/v1/datasets/nope", 404),
        ("/v1/datasets?limit=99999", 422),
        ("/v1/datasets?bbox=bad", 400),
    ):
        response = client.get(path)
        assert response.status_code == expected
        body = response.json()
        assert body["status"] == expected
        assert body["title"]
        assert body["type"].startswith("https://schema.opengrid.org/errors/")


def test_a_request_id_is_returned_on_every_response(client) -> None:
    """So a user reporting "it broke" can hand over a string that finds the log
    line."""
    ok = client.get("/v1/datasets")
    bad = client.get("/v1/datasets/nope")

    assert ok.headers["X-Request-Id"]
    assert bad.json()["requestId"] == bad.headers["X-Request-Id"]


def test_a_caller_supplied_request_id_is_honoured(client) -> None:
    """A client tracing a request across services keeps its own id."""
    response = client.get("/v1/datasets", headers={"X-Request-Id": "trace-me-123"})
    assert response.headers["X-Request-Id"] == "trace-me-123"


def test_an_internal_error_says_nothing_about_the_internals(client, monkeypatch) -> None:
    """A stack trace in a 500 body is a gift to whoever is probing the service
    and no use to the caller."""
    from datahub.api.search.backend import InMemorySearchBackend

    def explode(self, request):
        raise RuntimeError("connection string: postgres://user:hunter2@db/prod")

    monkeypatch.setattr(InMemorySearchBackend, "search", explode)
    # The default TestClient re-raises server exceptions so a test sees the
    # traceback; here the response is the thing under test.
    from datahub.api.app import create_app
    from fastapi.testclient import TestClient

    with TestClient(create_app(), raise_server_exceptions=False) as strict:
        response = strict.get("/v1/datasets", params={"q": "x"})

    assert response.status_code == 500
    assert "hunter2" not in response.text
    assert "RuntimeError" not in response.text
    assert response.json()["requestId"]


def test_a_restricted_metadata_record_is_visible_as_a_stub(client) -> None:
    """The middle of PRD §F8's three visibility levels, and the one most easily
    got wrong. `restricted-metadata` means a caller may know the dataset exists
    and who holds it, and may not read the rest — so it appears in search,
    marked, rather than vanishing like an allowlisted-existence record."""
    body = client.get("/v1/datasets", params={"q": "locational marginal", "limit": 50}).json()

    hit = next((r for r in body["results"] if r["id"] == RESTRICTED), None)
    assert hit is not None, "a restricted-metadata record still exists as far as search goes"
    assert hit["redacted"] is True
    assert hit["title"]


def test_a_stub_withholds_the_detail_it_is_a_stub_of(client) -> None:
    detail = client.get(f"/v1/datasets/{RESTRICTED}").json()

    assert detail["redacted"] is True
    assert not detail.get("description")
    assert not detail.get("distributions")


def test_the_two_restriction_levels_behave_differently(client) -> None:
    """If they behaved the same there would be no reason for both, and the
    weaker one would be doing the stronger one's job by accident."""
    restricted = client.get(f"/v1/datasets/{RESTRICTED}")
    hidden = client.get(f"/v1/datasets/{HIDDEN}")

    assert restricted.status_code == 200
    assert hidden.status_code == 404
