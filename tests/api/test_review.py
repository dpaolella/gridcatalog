"""The steward queue (WP-9.5).

The interesting property here is the one that differs from everywhere else in
this API: the refusal is a **403, not a 404**. Every other endpoint answers a
caller who may not see something by saying it does not exist, because knowing
it exists is itself a disclosure. The review queue's existence is not a secret;
its contents are.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.entitlement import tokens
from datahub.api.models.base import session_scope
from datahub.api.models.repositories import Repositories


def _user(role: str, name: str) -> dict[str, str]:
    with session_scope() as session:
        repos = Repositories(session)
        row = repos.users.upsert_federated("local", name, email=f"{name}@example.org")
        row.role = role
        session.flush()
        issued = tokens.mint(repos, row, name="cli")
        return {"id": row.id, "token": issued.token}


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def queued(client):
    with session_scope() as session:
        repos = Repositories(session)
        repos.review.enqueue(
            "eia-930",
            source_id="curated",
            data_domain="DD4",
            completeness_level=2,
            validation_conforms=True,
        )
        repos.review.enqueue(
            "ecmwf-era5",
            source_id="curated",
            data_domain="DD5",
            completeness_level=3,
            validation_conforms=False,
            violations=["og:license must be an absolute IRI"],
        )
    return client


def test_the_queue_needs_a_session(queued) -> None:
    assert queued.get("/v1/review").status_code == 401


def test_a_signed_in_non_steward_gets_403_not_404(queued) -> None:
    """Deliberately unlike the rest of the API. A steward with the wrong
    session should be told to change sessions, not sent looking for a typo."""
    user = _user("user", "alice")

    response = queued.get("/v1/review", headers=auth(user["token"]))

    assert response.status_code == 403
    assert "steward" in response.json()["title"].lower()


def test_a_steward_sees_the_queue(queued) -> None:
    steward = _user("steward", "sam")

    body = queued.get("/v1/review", headers=auth(steward["token"])).json()

    assert {item["dataset_id"] for item in body["items"]} == {"eia-930", "ecmwf-era5"}


def test_violations_travel_with_the_item(queued) -> None:
    """ "Does not conform" is not something a steward can act on;
    "og:license must be an absolute IRI" is."""
    steward = _user("steward", "sam")

    body = queued.get("/v1/review", headers=auth(steward["token"])).json()
    era5 = next(i for i in body["items"] if i["dataset_id"] == "ecmwf-era5")

    assert era5["violations"]


def test_confirming_unions_rather_than_replaces(queued) -> None:
    """A second review confirming three more fields must not un-confirm the
    first review's work, or a steward who reviewed one tab loses the others."""
    steward = _user("steward", "sam")

    queued.post(
        "/v1/review/eia-930/confirm",
        json={"confirmed_fields": ["title", "license"]},
        headers=auth(steward["token"]),
    )
    second = queued.post(
        "/v1/review/eia-930/confirm",
        json={"confirmed_fields": ["temporal"]},
        headers=auth(steward["token"]),
    ).json()

    assert set(second["confirmed_fields"]) == {"title", "license", "temporal"}
    assert second["state"] == "confirmed"
    assert second["reviewed_by"] == steward["id"]


def test_confirming_something_not_in_the_queue_is_a_404(queued) -> None:
    steward = _user("steward", "sam")

    response = queued.post(
        "/v1/review/not-queued-at-all/confirm",
        json={"confirmed_fields": []},
        headers=auth(steward["token"]),
    )

    assert response.status_code == 404


def test_the_queue_is_ordered_by_leverage(queued) -> None:
    """PRD §7.6: a record twelve others cite is worth reviewing before one
    nothing points at."""
    steward = _user("steward", "sam")
    with session_scope() as session:
        item = Repositories(session).review.by_dataset("eia-930")
        item.inbound_link_count = 12

    body = queued.get("/v1/review", headers=auth(steward["token"])).json()

    assert body["items"][0]["dataset_id"] == "eia-930"


def test_a_confirmation_is_audited(queued) -> None:
    steward = _user("steward", "sam")

    queued.post(
        "/v1/review/eia-930/confirm",
        json={"confirmed_fields": ["title"]},
        headers=auth(steward["token"]),
    )

    with session_scope() as session:
        events = Repositories(session).audit.for_principal(steward["id"], limit=20)
        assert any(e.action == "review.confirm" for e in events)
