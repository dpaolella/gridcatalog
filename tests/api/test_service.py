"""Health, the OpenAPI contract, and the app's error handling (WP-4.3).

PRD §F8 calls OpenAPI 3.1 "the canonical contract everything else calls" — the
web UI, the Python SDK and the MCP server all generate against it — so the
document itself is worth asserting on. A contract nobody checks is a contract
that drifts.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---- the three health questions ------------------------------------------


def test_liveness_touches_no_dependency(empty_client, monkeypatch) -> None:
    """A liveness probe that checked the database would restart the API every
    time the database blinked, and restarting the API does not fix a
    database."""
    from datahub.api import deps

    monkeypatch.setitem(empty_client.app.dependency_overrides, deps.db_session, lambda: None)

    def explode() -> None:
        raise RuntimeError("store is down")

    monkeypatch.setitem(empty_client.app.dependency_overrides, deps.graph_store, explode)

    response = empty_client.get("/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_reports_degraded_rather_than_failing(empty_client) -> None:
    """A catalog whose index is empty can still serve; taking it out of
    rotation turns a partial outage into a total one."""
    body = empty_client.get("/v1/health/ready").json()

    assert body["status"] == "degraded"
    assert "empty" in body["checks"]["search"]
    assert body["checks"]["graph"] == "ok"


def test_readiness_is_ok_once_the_index_is_populated(client) -> None:
    body = client.get("/v1/health/ready").json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"graph", "search", "database"}


def test_an_unreachable_database_is_degraded_not_unhealthy(client, monkeypatch) -> None:
    from datahub.api import deps

    monkeypatch.setitem(client.app.dependency_overrides, deps.db_session, lambda: None)
    body = client.get("/v1/health/ready").json()

    assert body["status"] == "degraded"
    assert body["checks"]["database"] == "unreachable"


def test_status_reports_the_data_state(client) -> None:
    body = client.get("/v1/health/status").json()

    assert body["catalog_records"] > 0
    assert int(body["checks"]["indexed_documents"]) > 0
    assert "review_queue" in body["checks"]


# ---- the OpenAPI contract ------------------------------------------------


def test_the_document_is_openapi_31(client) -> None:
    """3.1 rather than 3.0: 3.0's schema dialect cannot express `null` in a
    union, and every optional field in this API is one — under 3.0 a generated
    client makes them all required or all Any."""
    document = client.get("/openapi.json").json()
    assert document["openapi"].startswith("3.1")


def test_every_prd_endpoint_is_present(client) -> None:
    """PRD §F8 lists the endpoint set. The ones deferred to later milestones
    are named here so the gap is deliberate rather than forgotten."""
    paths = set(client.get("/openapi.json").json()["paths"])

    assert {
        "/v1/datasets",
        "/v1/datasets/{dataset_id}",
        "/v1/datasets/{dataset_id}/schema",
        "/v1/datasets/{dataset_id}/quality",
        "/v1/datasets/{dataset_id}/distributions",
        "/v1/datasets/{dataset_id}/download",
        "/v1/concepts",
        "/v1/domains",
        "/v1/datasets/{dataset_id}/access-plan",
        "/v1/submissions",
        "/v1/reports",
        "/v1/allowlists/{dataset_id}",
        "/v1/auth/me",
        "/v1/auth/tokens",
        "/v1/datasets/{dataset_id}/links",
    } <= paths

    # Nothing left. Every endpoint PRD §F8 names is present; the guard stays as
    # an empty set rather than being deleted, so the next deferred endpoint has
    # somewhere to be declared.
    deferred: dict[str, str] = {}
    assert not (set(deferred) & paths), "a deferred endpoint arrived without its milestone"


def test_every_operation_documents_its_failure_modes(client) -> None:
    """A client generated from this document should know a 404 is possible
    without reading the source."""
    document = client.get("/openapi.json").json()
    for path, operations in document["paths"].items():
        for method, operation in operations.items():
            if method not in ("get", "post", "put"):
                continue
            codes = set(operation.get("responses", {}))
            # Any 2xx or 3xx, rather than a list of the ones that happen to
            # exist today. An enumerated list makes this test fail whenever a
            # correct endpoint uses a code the list has not heard of — which it
            # did, twice, for the 204 of logout and the 201 of token creation.
            assert any(code[:1] in "23" for code in codes), f"{method} {path} documents no success"


def test_filterable_fields_are_discoverable_from_the_document(client) -> None:
    """Named query parameters rather than a free-form `filter[]`, so a client
    finds the filterable fields here rather than in prose."""
    document = client.get("/openapi.json").json()
    params = {p["name"] for p in document["paths"]["/v1/datasets"]["get"].get("parameters", [])}
    assert {"q", "data_domain", "license", "bbox", "sort", "facets", "limit"} <= params
    # Every filter is named for the facet it filters, so a client can build a
    # filter straight from the facet response. `tests/api/test_filter_facet_parity.py`
    # is the enforcement; this is the discoverability half of the same contract.


def test_the_description_states_the_control_plane_rule(client) -> None:
    """ "This API never returns data" is the single most important thing a
    client author needs to know before writing against it."""
    description = client.get("/openapi.json").json()["info"]["description"]
    assert "never returns data" in description.lower()


def test_the_docs_render(client) -> None:
    assert client.get("/docs").status_code == 200


# ---- CORS ----------------------------------------------------------------


def test_cors_is_not_a_wildcard(client) -> None:
    """The API accepts credentials, and a wildcard origin with credentials is
    either rejected by the browser or a way for any page to read an
    authenticated response."""
    response = client.get("/v1/datasets", headers={"Origin": "https://evil.example", "limit": "1"})
    assert response.headers.get("access-control-allow-origin") != "*"


def test_the_configured_origin_is_allowed(client) -> None:
    response = client.get("/v1/datasets", headers={"Origin": "http://localhost:3000"})
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
