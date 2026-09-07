"""Scopes must narrow what a token can do.

`tokens.require()` was written, complete and correct, and no route ever called
it. Scopes were checked against the holder's role at issue time, stored, echoed
back by `describe()`, and enforced nowhere — so a token minted `catalog:read`
for a CI job carried its holder's entire role authority. Anyone reading the
`scopes` field of a token, or the documentation, would reasonably believe
otherwise.

The tests below are about the narrowing, not about the roles. A scope can never
*lift* its holder — `mint` refuses a scope the user's role does not carry — so
the only thing left to get wrong is failing to honour a deliberate restriction.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PUBLIC = "ecmwf-era5"


@pytest.fixture
def steward_tokens(client):
    """One steward, two tokens: one that may review and one that may not."""
    from datahub.api.entitlement import tokens
    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories

    issued: dict[str, str] = {}
    with session_scope() as session:
        repos = Repositories(session)
        user = repos.users.upsert_federated(
            "local", "steward-scopes", email="steward-scopes@example.org", display_name="s"
        )
        user.role = "steward"
        session.flush()
        issued["full"] = tokens.mint(
            repos, user, name="full", scopes=("catalog:read", "steward:review")
        ).token
        issued["read_only"] = tokens.mint(
            repos, user, name="read-only", scopes=("catalog:read",)
        ).token
    return issued


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_a_scoped_token_reaches_the_endpoint_its_scope_names(client, steward_tokens):
    response = client.get("/v1/review", headers=auth(steward_tokens["full"]))
    assert response.status_code == 200


def test_a_narrowed_token_is_refused_though_its_holder_is_a_steward(client, steward_tokens):
    """The regression. Same person, same role, weaker credential.

    Before scopes were enforced this returned 200: the route checked
    `caller.entitlement.is_steward`, which comes from the user's role, and the
    token's own answer was never consulted.
    """
    response = client.get("/v1/review", headers=auth(steward_tokens["read_only"]))
    assert response.status_code == 403
    body = response.json()
    assert "steward:review" in body["title"]


def test_a_narrowed_token_cannot_confirm_a_record(client, steward_tokens):
    """The write path, not just the read — they are separately guarded."""
    response = client.post(
        f"/v1/review/{PUBLIC}/confirm",
        json={"confirmed_fields": [], "notes": None},
        headers=auth(steward_tokens["read_only"]),
    )
    assert response.status_code == 403


def test_an_unscoped_session_is_not_narrowed(client):
    """A browser session carries no scopes and must not be treated as carrying none.

    `None` means "unscoped" — the user themselves, with whatever their role
    allows. Conflating that with an empty tuple would lock every signed-in
    steward out of the queue, which is a worse bug than the one being fixed.
    """
    from datahub.api.entitlement.resolve import Caller
    from datahub.api.entitlement.tokens import require_scope
    from datahub.api.search.backend import Entitlement

    session_caller = Caller(
        entitlement=Entitlement(principal_id="u1", custodian_of=frozenset(), is_steward=True),
        principal_id="u1",
        role="steward",
        scopes=None,
    )
    require_scope(session_caller, "steward:review")  # must not raise


def test_anonymous_still_reaches_the_endpoints_the_prd_leaves_open(client):
    """`allow_anonymous` endpoints stay anonymous.

    PRD §F3 is explicit that the intake form needs no login. Enforcing scopes
    must not close a door the product deliberately leaves open.
    """
    response = client.post(
        "/v1/reports",
        json={"dataset_id": PUBLIC, "issue_type": "broken-link", "comment": "test"},
    )
    assert response.status_code in (202, 503)
