"""The custodian allow-list API and rate limiting (WP-6.2, WP-6.3).

PRD §F8, stated twice in the PRD because it is the thing custodians most often
assume otherwise:

> **The dataset creator manages the allow-list. OpenGrid stores and enforces it
> and never arbitrates its contents.**
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.entitlement import tokens
from datahub.api.models.base import session_scope
from datahub.api.models.operational import Custodianship
from datahub.api.models.repositories import Repositories

HIDDEN = "utility-load-shapes-allowlisted"
IRI = f"https://catalog.opengrid.org/ds/{HIDDEN}"


@pytest.fixture
def people(client):
    """A custodian, a colleague they will grant, a stranger, and an admin."""
    out: dict[str, str] = {}
    with session_scope() as session:
        repos = Repositories(session)
        for handle, role in (
            ("custodian", "user"),
            ("colleague", "user"),
            ("stranger", "user"),
            ("admin", "admin"),
        ):
            user = repos.users.upsert_federated("local", handle, email=f"{handle}@example.org")
            user.role = role
            session.flush()
            out[handle] = tokens.mint(repos, user, name=handle).token
            out[f"{handle}_id"] = user.id
        session.add(
            Custodianship(dataset_id=IRI, user_id=out["custodian_id"], contact_email="c@x.org")
        )
    return out


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- who may see and change the list -------------------------------------


def test_the_custodian_can_read_the_list(client, people) -> None:
    response = client.get(f"/v1/allowlists/{HIDDEN}", headers=auth(people["custodian"]))

    assert response.status_code == 200
    assert response.json()["dataset_id"] == HIDDEN


def test_a_stranger_cannot(client, people) -> None:
    """404 on an allow-listed-existence record, because 403 would confirm it.

    The refusal a caller gets has to depend on whether they were allowed to know
    the record is there — see `test_an_admin_cannot_either` just below, where a
    403 is right precisely because an admin may already see it.
    """
    response = client.get(f"/v1/allowlists/{HIDDEN}", headers=auth(people["stranger"]))
    assert response.status_code == 404


def test_a_stranger_gets_a_403_on_a_public_record(client, people) -> None:
    """The other half of the rule, so the 404 above is not mistaken for a blanket.

    Flattening every refusal to 404 would be the easy fix and the wrong one: on
    a record the catalog publishes to the world, "this is not yours" discloses
    nothing and is what a custodian who mistyped a slug needs to hear.
    """
    response = client.get("/v1/allowlists/ecmwf-era5", headers=auth(people["stranger"]))
    assert response.status_code == 403


def test_an_admin_cannot_either(client, people) -> None:
    """Not a steward, not an admin. The list belongs to the dataset's
    custodian, and an admin who could edit it would be arbitrating its
    contents — exactly what PRD §F8 forbids. An admin can change *who the
    custodian is*; that is a different power with a different audit trail."""
    response = client.get(f"/v1/allowlists/{HIDDEN}", headers=auth(people["admin"]))
    assert response.status_code == 403


def test_anonymous_cannot(client) -> None:
    assert client.get(f"/v1/allowlists/{HIDDEN}").status_code == 401


def test_the_response_says_who_owns_the_list(client, people) -> None:
    """On every response, because it is the thing custodians most often assume
    otherwise."""
    body = client.get(f"/v1/allowlists/{HIDDEN}", headers=auth(people["custodian"])).json()
    assert "never arbitrates" in body["managed_by"]


# ---- replacing the list --------------------------------------------------


def test_a_grant_takes_effect_immediately(client, people) -> None:
    """A custodian who adds a colleague expects them to be able to search for
    the dataset now, not after the next reindex."""
    before = client.get(f"/v1/datasets/{HIDDEN}", headers=auth(people["colleague"]))

    client.put(
        f"/v1/allowlists/{HIDDEN}",
        json={"entries": [{"principal_id": people["colleague_id"]}]},
        headers=auth(people["custodian"]),
    )
    after = client.get(f"/v1/datasets/{HIDDEN}", headers=auth(people["colleague"]))

    assert before.status_code == 404
    assert after.status_code == 200


def test_a_revocation_takes_effect_immediately(client, people) -> None:
    client.put(
        f"/v1/allowlists/{HIDDEN}",
        json={"entries": [{"principal_id": people["colleague_id"]}]},
        headers=auth(people["custodian"]),
    )
    assert (
        client.get(f"/v1/datasets/{HIDDEN}", headers=auth(people["colleague"])).status_code == 200
    )

    client.put(f"/v1/allowlists/{HIDDEN}", json={"entries": []}, headers=auth(people["custodian"]))

    assert (
        client.get(f"/v1/datasets/{HIDDEN}", headers=auth(people["colleague"])).status_code == 404
    )


def test_a_put_replaces_rather_than_merges(client, people) -> None:
    """A diff-based API makes "who can see this" a question you answer by
    replaying a history, and the one question a custodian actually asks is "who
    is on it right now"."""
    client.put(
        f"/v1/allowlists/{HIDDEN}",
        json={"entries": [{"principal_id": people["colleague_id"]}]},
        headers=auth(people["custodian"]),
    )
    body = client.put(
        f"/v1/allowlists/{HIDDEN}",
        json={"entries": [{"principal_id": people["stranger_id"]}]},
        headers=auth(people["custodian"]),
    ).json()

    ids = {e["principal_id"] for e in body["entries"]}
    assert ids == {people["stranger_id"]}


def test_a_grant_can_name_someone_who_has_not_signed_in(client, people) -> None:
    """A custodian grants access to a colleague by address, before that
    colleague has an account. Requiring a user id first would make the
    allow-list unusable for the case it exists for."""
    body = client.put(
        f"/v1/allowlists/{HIDDEN}",
        json={"entries": [{"principal_email": "future@example.org", "note": "joining Monday"}]},
        headers=auth(people["custodian"]),
    ).json()

    assert body["entries"][0]["principal_email"] == "future@example.org"
    assert body["entries"][0]["note"] == "joining Monday"


def test_a_stranger_cannot_add_themselves(client, people) -> None:
    """404, not 403 — `HIDDEN` is an allow-listed-existence record.

    This asserted 403 until the refusal was made indistinguishable. A 403 says
    "this exists and is not yours", which on a record whose *existence* is the
    restricted part is the whole disclosure: a stranger could enumerate slugs
    against this endpoint and learn which ones were real. The record read on the
    next line has always answered 404; the allow-list endpoint now agrees with
    it.
    """
    response = client.put(
        f"/v1/allowlists/{HIDDEN}",
        json={"entries": [{"principal_id": people["stranger_id"]}]},
        headers=auth(people["stranger"]),
    )

    assert response.status_code == 404
    assert client.get(f"/v1/datasets/{HIDDEN}", headers=auth(people["stranger"])).status_code == 404


def test_the_change_is_audited(client, people) -> None:
    client.put(
        f"/v1/allowlists/{HIDDEN}",
        json={"entries": [{"principal_id": people["colleague_id"]}]},
        headers=auth(people["custodian"]),
    )

    with session_scope() as session:
        events = Repositories(session).audit.for_principal(people["custodian_id"])
    assert any(e.action == "allowlist.replace" for e in events)


def test_a_refusal_is_audited_too(client, people) -> None:
    """PRD §F10: authorization grants *and refusals* logged."""
    client.get(f"/v1/allowlists/{HIDDEN}", headers=auth(people["stranger"]))

    with session_scope() as session:
        refusals = Repositories(session).audit.refusals()
    assert any(e.reason == "not the custodian" for e in refusals)


def test_an_unknown_dataset_is_a_404(client, people) -> None:
    response = client.get("/v1/allowlists/no-such-dataset", headers=auth(people["custodian"]))
    assert response.status_code == 404


# ---- rate limiting -------------------------------------------------------


def test_every_response_carries_the_budget(client) -> None:
    """A client that can see it has four requests left paces itself; one that
    finds out by being refused has already failed a user's request."""
    response = client.get("/v1/datasets")

    assert int(response.headers["RateLimit-Limit"]) > 0
    assert int(response.headers["RateLimit-Remaining"]) >= 0
    assert int(response.headers["RateLimit-Reset"]) > 0


def test_an_agent_gets_a_larger_budget_than_a_person(client, settings) -> None:
    """PRD §F9: agent traffic is several times chattier, and a limit that made
    agentic use impossible would just push it to scraping the UI."""
    from datahub.api.ratelimit import RateLimiter

    limiter = RateLimiter(settings)
    assert limiter.budget(principal_id="u", is_agent=True) > limiter.budget(
        principal_id="u", is_agent=False
    )
    assert limiter.budget(principal_id="u", is_agent=False) > limiter.budget(
        principal_id=None, is_agent=False
    )


def test_the_limit_is_per_principal_not_per_address(settings) -> None:
    """A shared office NAT is one address and forty people; an agent behind a
    rotating pool is forty addresses and one caller."""
    from datahub.api.ratelimit import RateLimiter

    limiter = RateLimiter(settings)
    first = limiter.check(principal_id="alice", is_agent=False, client_host="10.0.0.1")
    second = limiter.check(principal_id="bob", is_agent=False, client_host="10.0.0.1")

    assert first.bucket != second.bucket
    assert second.remaining == second.limit - 1


def test_exceeding_the_budget_is_a_429_that_says_when_to_retry(settings) -> None:
    """A 429 with no Retry-After teaches a client to hammer."""
    from datahub.api.ratelimit import RateLimiter

    limiter = RateLimiter(settings)
    limit = limiter.budget(principal_id=None, is_agent=False)
    for _ in range(limit):
        decision = limiter.check(principal_id=None, is_agent=False, client_host="1.1.1.1")
    assert decision.allowed

    refused = limiter.check(principal_id=None, is_agent=False, client_host="1.1.1.1")

    assert refused.allowed is False
    assert refused.headers["Retry-After"]
    assert int(refused.headers["RateLimit-Remaining"]) == 0


def test_health_is_never_rate_limited(client) -> None:
    """A health check that can be rate-limited takes the deployment out of
    rotation under exactly the load it exists to report on."""
    from datahub.api.ratelimit import exempt

    assert exempt("/v1/health")
    assert exempt("/openapi.json")
    assert not exempt("/v1/datasets")

    for _ in range(50):
        assert client.get("/v1/health").status_code == 200
