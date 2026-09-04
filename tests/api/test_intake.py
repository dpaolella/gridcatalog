"""``/v1/submissions`` and ``/v1/reports`` (WP-4.4)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SUBMISSION = {
    "title": "ERCOT nodal LMP archive",
    "description": "Ten years of settlement-point prices at five-minute resolution.",
    "license_text": "Public, no formal statement",
    "access_urls": ["https://www.ercot.com/mp/data-products"],
    "data_domain": "DD9",
}
REPORT = {
    "dataset_id": "https://catalog.opengrid.org/ds/ecmwf-era5",
    "issue_type": "broken-link",
    "target_kind": "distribution",
    "target_id": "https://catalog.opengrid.org/dist/ecmwf-era5--zarr-s3",
    "comment": "The S3 prefix 404s as of this morning.",
}


# ---- submissions ---------------------------------------------------------


def test_a_submission_is_accepted(client) -> None:
    response = client.post("/v1/submissions", json=SUBMISSION)

    assert response.status_code == 202
    body = response.json()
    assert body["id"]
    assert body["received_at"]


def test_the_receipt_says_no_status_will_be_tracked(client) -> None:
    """PRD §F3 makes submissions fire-and-forget. A status field would imply an
    SLA on triage, and a stale "received" badge three months later is worse
    than saying up front that we will look at it."""
    body = client.post("/v1/submissions", json=SUBMISSION).json()

    assert "do not track" in body["message"].lower()
    assert "status" not in body


def test_a_submission_is_202_not_201(client) -> None:
    """Nothing has been created that the submitter can go and look at, and
    "created" would imply otherwise."""
    assert client.post("/v1/submissions", json=SUBMISSION).status_code == 202


def test_a_submission_without_a_licence_statement_is_refused(client) -> None:
    """The one field a triaging steward cannot work without. "I do not know"
    is an acceptable answer and has to be typed."""
    payload = {k: v for k, v in SUBMISSION.items() if k != "license_text"}
    response = client.post("/v1/submissions", json=payload)

    assert response.status_code == 422
    assert any("license" in e["field"] for e in response.json()["errors"])


def test_a_submission_reaches_the_store(client) -> None:
    body = client.post("/v1/submissions", json=SUBMISSION).json()

    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories

    with session_scope() as session:
        row = Repositories(session).submissions.get(body["id"])
    assert row is not None
    assert row.title == SUBMISSION["title"]
    assert row.state == "received"


# ---- reports -------------------------------------------------------------


def test_a_report_is_accepted_anonymously(client) -> None:
    """The person who notices a download 404s is whoever tried to download it.
    Requiring an account here would mean the reports that matter most never
    arrive."""
    response = client.post("/v1/reports", json=REPORT)

    assert response.status_code == 202
    assert response.json()["id"]


def test_a_report_records_the_exact_thing_flagged(client) -> None:
    """A report against a record is much harder to act on than one against a
    field or a distribution."""
    body = client.post("/v1/reports", json=REPORT).json()

    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories

    with session_scope() as session:
        row = Repositories(session).reports.get(body["id"])
    assert row.target_kind == "distribution"
    assert row.target_id == REPORT["target_id"]


def test_reports_on_one_target_are_counted_not_deduped(client) -> None:
    """A target flagged eleven times reads as eleven (PRD §12.11 carries the
    surface-versus-dedupe choice forward)."""
    counts = [
        client.post("/v1/reports", json=REPORT).json()["open_reports_on_target"] for _ in range(3)
    ]
    assert counts == [1, 2, 3]


def test_an_unknown_issue_type_is_refused(client) -> None:
    """The type drives triage routing, so a free-text one would land nowhere."""
    response = client.post("/v1/reports", json={**REPORT, "issue_type": "vibes"})
    assert response.status_code == 422


# ---- rate limiting -------------------------------------------------------


def test_an_unattended_script_is_capped(client) -> None:
    """Generous enough that a person filing several genuine reports in a
    sitting is unaffected; low enough that a loop is not free."""
    from datahub.api.routers.intake import SUBMISSION_LIMIT

    codes = [
        client.post("/v1/submissions", json=SUBMISSION).status_code
        for _ in range(SUBMISSION_LIMIT + 2)
    ]

    assert codes[:SUBMISSION_LIMIT] == [202] * SUBMISSION_LIMIT
    assert codes[-1] == 429


def test_the_limit_response_says_what_the_limit_is(client) -> None:
    from datahub.api.routers.intake import SUBMISSION_LIMIT

    for _ in range(SUBMISSION_LIMIT + 1):
        response = client.post("/v1/submissions", json=SUBMISSION)

    body = response.json()
    assert body["status"] == 429
    assert body["limit"] == SUBMISSION_LIMIT
    assert body["window_seconds"] == 3600


def test_a_client_address_is_hashed_not_stored(client) -> None:
    """The requirement is to distinguish clients — to spot a flood, to group a
    person's reports — not to retain addresses."""
    body = client.post("/v1/reports", json=REPORT).json()

    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories

    with session_scope() as session:
        row = Repositories(session).reports.get(body["id"])
    assert row.ip_hash
    assert "testclient" not in row.ip_hash
    assert len(row.ip_hash) == 64


# ---- degraded mode -------------------------------------------------------


def test_intake_refuses_rather_than_pretending_when_the_store_is_down(client, monkeypatch) -> None:
    """Telling someone their submission was received when it was dropped is
    worse than telling them to try later."""
    from datahub.api import deps

    monkeypatch.setitem(client.app.dependency_overrides, deps.db_session, lambda: None)
    response = client.post("/v1/submissions", json=SUBMISSION)

    assert response.status_code == 503
    assert "nothing was recorded" in response.json()["title"]
