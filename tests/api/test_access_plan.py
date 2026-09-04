"""``POST /v1/datasets/{id}/access-plan`` (WP-5.1).

The broker's own rules are tested in `tests/broker/`. What is tested here is
the HTTP surface: that the plan is entitlement-gated the same way every other
read is, that issuing one is audited, and that the endpoint never returns data.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ERA5 = "ecmwf-era5"
HIDDEN = "utility-load-shapes-allowlisted"
RESTRICTED = "caiso-nodal-lmp-restricted"


def test_a_plan_is_issued(client) -> None:
    body = client.post(f"/v1/datasets/{ERA5}/access-plan", json={}).json()

    assert body["dataset_id"] == ERA5
    assert body["distribution_id"]
    assert body["mode"] in ("redirect", "partial-read", "subsetting-protocol")
    assert body["location"]
    assert body["path_rationale"]


def test_the_licence_is_in_the_payload(client) -> None:
    """The whole point of the object (PRD §F7). An agent handed a URL cannot
    know it may not redistribute what it downloads; an agent handed a plan is
    told in a field it cannot miss."""
    body = client.post(f"/v1/datasets/{ERA5}/access-plan", json={}).json()

    assert body["license"]
    assert "redistribution_allowed" in body
    assert set(body["quality_grades"]) == {"provenance", "documentation", "currency"}


def test_the_plan_never_carries_data(client) -> None:
    """The control-plane rule: the API returns small cacheable JSON and never
    streams bytes."""
    response = client.post(f"/v1/datasets/{ERA5}/access-plan", json={})

    assert response.headers["content-type"].startswith("application/json")
    assert len(response.content) < 8192
    assert not {"content", "body", "data", "bytes"} & set(response.json())


def test_a_slice_is_echoed_and_pushed_down_where_possible(client) -> None:
    body = client.post(
        f"/v1/datasets/{ERA5}/access-plan",
        json={
            "time_start": "2019-01-01",
            "time_end": "2019-12-31",
            "bbox": [5.9, 45.8, 10.5, 47.8],
        },
    ).json()

    assert body["requested_slice"]["time"] == ["2019-01-01", "2019-12-31"]
    assert body["requested_slice"]["bbox"] == [5.9, 45.8, 10.5, 47.8]


def test_a_plan_expires(client) -> None:
    body = client.post(f"/v1/datasets/{ERA5}/access-plan", json={}).json()
    assert body["expires_at"]


def test_a_redirect_plan_explains_the_absence_of_partial_read(client) -> None:
    """An absent partial-read section is ambiguous — it looks the same whether
    the dataset cannot do it or nobody recorded that it can."""
    body = client.post("/v1/datasets/eia-930/access-plan", json={}).json()

    if body["mode"] == "redirect":
        assert body["partial_read_unavailable_reason"]


# ---- entitlement ---------------------------------------------------------


def test_a_plan_for_a_hidden_record_is_the_same_404(client) -> None:
    response = client.post(f"/v1/datasets/{HIDDEN}/access-plan", json={})
    assert response.status_code == 404


def test_a_stub_gets_no_plan(client) -> None:
    """A caller who may see that a record exists but not read it must not get
    an access plan: the plan carries the URL, which is the disclosure the stub
    exists to prevent."""
    detail = client.get(f"/v1/datasets/{RESTRICTED}")
    plan = client.post(f"/v1/datasets/{RESTRICTED}/access-plan", json={})

    assert detail.status_code == 200
    assert detail.json()["redacted"] is True
    assert plan.status_code == 404


def test_a_plan_for_an_absent_record_is_a_404(client) -> None:
    assert client.post("/v1/datasets/nope/access-plan", json={}).status_code == 404


# ---- audit ---------------------------------------------------------------


def test_issuing_a_plan_is_recorded(client) -> None:
    """PRD §F10 requires grants to be logged, and §12.9 leaves open whether a
    plan is revoked when an allow-list changes — which needs a row per issue to
    be implementable at all."""
    client.post(f"/v1/datasets/{ERA5}/access-plan", json={})

    from datahub.api.models.base import session_scope
    from datahub.api.models.operational import AccessPlanIssue
    from datahub.api.models.repositories import Repositories

    with session_scope() as session:
        repos = Repositories(session)
        issued = repos.plans.count(AccessPlanIssue.dataset_id == ERA5)
        granted = [
            e for e in repos.audit.for_principal("", limit=50) if e.action == "dataset.access_plan"
        ]

    assert issued == 1
    assert granted == [] or granted[0].outcome == "granted"


def test_a_failed_audit_write_does_not_refuse_the_plan(client, monkeypatch) -> None:
    """A plan that failed to log is still a plan; refusing to issue it because
    the audit table is unreachable would take the catalog down with the
    database."""
    from datahub.api.models.repositories import AccessPlanRepository

    def explode(self, **kwargs):
        raise RuntimeError("table is gone")

    monkeypatch.setattr(AccessPlanRepository, "issue", explode)
    response = client.post(f"/v1/datasets/{ERA5}/access-plan", json={})

    assert response.status_code == 200


# ---- the endpoint is in the contract -------------------------------------


def test_the_endpoint_is_documented(client) -> None:
    document = client.get("/openapi.json").json()
    operation = document["paths"]["/v1/datasets/{dataset_id}/access-plan"]["post"]

    assert "never" in operation["summary"].lower()
    schema = document["components"]["schemas"]["AccessPlanResponse"]
    assert "redistribution_allowed" in schema["properties"]
    assert "quality_grades" in schema["properties"]
