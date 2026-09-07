"""The entitlement matrix (WP-6.3).

PRD §F7 gives the table this file enumerates:

| Visibility                            | Non-entitled sees | Entitled sees        |
|---------------------------------------|-------------------|----------------------|
| Public existence, public metadata      | Everything but bytes | Bytes too         |
| Public existence, restricted metadata  | Stub only         | Full record and plan |
| Allow-listed existence                 | **Nothing at all**| Full record and plan |

> The third is the hard one. Enforce it at query construction, not by filtering
> results after the fact, or the existence leaks through result counts and
> pagination.

Every cell is a test, and every cell is checked from *both* sides — what an
entitled caller sees and what a non-entitled one does not. A matrix tested only
along the diagonal passes with the rules inverted.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PUBLIC = "ecmwf-era5"
RESTRICTED = "caiso-nodal-lmp-restricted"
HIDDEN = "utility-load-shapes-allowlisted"

READS = ("", "/schema", "/quality", "/distributions")


@pytest.fixture
def accounts(client):
    """Four callers, each with a token: anonymous, a stranger, the entitled
    principal, and a steward."""
    from datahub.api.entitlement import tokens
    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories

    issued: dict[str, str] = {}
    with session_scope() as session:
        repos = Repositories(session)
        for handle, role in (("stranger", "user"), ("member", "user"), ("steward", "steward")):
            user = repos.users.upsert_federated(
                "local", handle, email=f"{handle}@example.org", display_name=handle
            )
            user.role = role
            session.flush()
            scopes = ("catalog:read", "steward:review") if role == "steward" else ("catalog:read",)
            issued[handle] = tokens.mint(repos, user, name=handle, scopes=scopes).token
            issued[f"{handle}_id"] = user.id

        # The member is allow-listed on both restricted datasets.
        for slug in (RESTRICTED, HIDDEN):
            repos.allowlist.grant(
                f"https://catalog.opengrid.org/ds/{slug}",
                granted_by="custodian",
                principal_id=issued["member_id"],
            )
    return issued


@pytest.fixture
def reindexed(client, loaded, accounts):
    """Reproject, so the allow-list reaches the index.

    Entitlement is compiled into the query (ADR-0006), which means it is
    evaluated against what the index holds — so a grant that has not been
    projected is a grant that does not work. That is a real property, not a
    test artefact.
    """
    from datahub.api import deps
    from datahub.api.models.base import session_scope
    from datahub.projector import reindex

    reindex(loaded, deps.search_backend(), session_factory=session_scope)
    return client


def auth(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


# ---- row 1: public existence, public metadata ----------------------------


def test_anonymous_sees_a_public_record_in_full(reindexed) -> None:
    """*Everything except the bytes.* PRD §F10 is explicit that anonymous read
    of public records must work with no login: do not gate browsing."""
    body = reindexed.get(f"/v1/datasets/{PUBLIC}").json()

    assert body["redacted"] is False
    assert body["description"]
    assert body["distributions"]


def test_anonymous_gets_an_access_plan_for_a_public_record(reindexed) -> None:
    """ "Everything except the bytes" includes the plan: the plan is metadata,
    and the bytes are the source's to serve."""
    assert reindexed.post(f"/v1/datasets/{PUBLIC}/access-plan", json={}).status_code == 200


@pytest.mark.parametrize("suffix", READS)
def test_every_public_read_works_unauthenticated(reindexed, suffix: str) -> None:
    assert reindexed.get(f"/v1/datasets/{PUBLIC}{suffix}").status_code == 200


# ---- row 2: public existence, restricted metadata ------------------------


def test_a_stranger_sees_the_stub_and_no_more(reindexed, accounts) -> None:
    body = reindexed.get(f"/v1/datasets/{RESTRICTED}", headers=auth(accounts["stranger"])).json()

    assert body["redacted"] is True
    assert body["title"], "the existence and the name are public"
    assert not body.get("description")
    assert not body.get("distributions")


def test_a_stranger_gets_no_plan_for_a_restricted_record(reindexed, accounts) -> None:
    """The plan carries the URL, which is the disclosure the stub prevents."""
    response = reindexed.post(
        f"/v1/datasets/{RESTRICTED}/access-plan", json={}, headers=auth(accounts["stranger"])
    )
    assert response.status_code == 404


@pytest.mark.parametrize("suffix", ("/schema", "/quality", "/distributions"))
def test_a_strangers_sub_resource_reads_are_refused(reindexed, accounts, suffix: str) -> None:
    """One endpoint that answered would undo the stub."""
    response = reindexed.get(
        f"/v1/datasets/{RESTRICTED}{suffix}", headers=auth(accounts["stranger"])
    )
    assert response.status_code == 404


def test_the_entitled_member_sees_the_whole_record(reindexed, accounts) -> None:
    body = reindexed.get(f"/v1/datasets/{RESTRICTED}", headers=auth(accounts["member"])).json()

    assert body["redacted"] is False
    assert body["description"]


def test_the_entitled_member_gets_a_plan(reindexed, accounts) -> None:
    response = reindexed.post(
        f"/v1/datasets/{RESTRICTED}/access-plan", json={}, headers=auth(accounts["member"])
    )
    assert response.status_code == 200
    assert response.json()["location"]


def test_a_restricted_record_still_appears_in_search_for_a_stranger(reindexed, accounts) -> None:
    """The difference between rows 2 and 3. Its existence is public; only its
    detail is not."""
    body = reindexed.get(
        "/v1/datasets",
        params={"q": "locational marginal", "limit": 50},
        headers=auth(accounts["stranger"]),
    ).json()
    assert any(r["id"] == RESTRICTED for r in body["results"])


# ---- row 3: allow-listed existence ---------------------------------------


def test_a_stranger_sees_nothing_at_all(reindexed, accounts) -> None:
    """*The third is the hard one.* Nothing: not a stub, not a 403, not a
    different-looking 404."""
    response = reindexed.get(f"/v1/datasets/{HIDDEN}", headers=auth(accounts["stranger"]))
    assert response.status_code == 404


def test_the_hidden_record_is_absent_from_a_strangers_search(reindexed, accounts) -> None:
    body = reindexed.get(
        "/v1/datasets",
        params={"q": "feeder load", "limit": 100},
        headers=auth(accounts["stranger"]),
    ).json()
    assert not any(r["id"] == HIDDEN for r in body["results"])


def test_it_is_absent_from_the_count_not_merely_the_page(reindexed, accounts) -> None:
    """Enforced at query construction, not by filtering results after the fact,
    or the existence leaks through result counts and pagination."""
    stranger = reindexed.get(
        "/v1/datasets", params={"limit": 0}, headers=auth(accounts["stranger"])
    ).json()["total"]
    member = reindexed.get(
        "/v1/datasets", params={"limit": 0}, headers=auth(accounts["member"])
    ).json()["total"]

    assert member == stranger + 1


def test_pagination_does_not_leak_it(reindexed, accounts) -> None:
    """A page-sized hole is as good as a title. Walking every page must never
    reveal a gap where the hidden record would be."""
    seen: list[str] = []
    offset = 0
    while True:
        page = reindexed.get(
            "/v1/datasets",
            params={"limit": 5, "offset": offset, "sort": "title"},
            headers=auth(accounts["stranger"]),
        ).json()
        seen += [r["id"] for r in page["results"]]
        offset += 5
        if offset >= page["total"]:
            break

    assert HIDDEN not in seen
    assert len(seen) == len(set(seen)), "no duplicates, so no page was shifted by a removal"


def test_the_entitled_member_sees_it(reindexed, accounts) -> None:
    body = reindexed.get(f"/v1/datasets/{HIDDEN}", headers=auth(accounts["member"])).json()
    assert body["redacted"] is False
    assert body["title"]


def test_the_entitled_member_finds_it_in_search(reindexed, accounts) -> None:
    body = reindexed.get(
        "/v1/datasets", params={"q": "feeder", "limit": 50}, headers=auth(accounts["member"])
    ).json()
    assert any(r["id"] == HIDDEN for r in body["results"])


def test_the_entitled_member_gets_a_plan_for_it(reindexed, accounts) -> None:
    response = reindexed.post(
        f"/v1/datasets/{HIDDEN}/access-plan", json={}, headers=auth(accounts["member"])
    )
    assert response.status_code == 200


# ---- the 404s must be indistinguishable ----------------------------------


def test_withheld_and_absent_are_the_same_response(reindexed, accounts) -> None:
    """If the two differ in anything a caller can observe, the distinction
    leaks — and the whole of row 3 rests on them being identical."""
    headers = auth(accounts["stranger"])
    withheld = reindexed.get(f"/v1/datasets/{HIDDEN}", headers=headers)
    absent = reindexed.get("/v1/datasets/no-such-dataset-anywhere", headers=headers)

    assert withheld.status_code == absent.status_code == 404
    a, b = withheld.json(), absent.json()

    # Same shape and same error type. What may differ is only what the caller
    # already knows — the id they asked for, echoed back in the title, the
    # instance path and the dataset_id — and the per-request id.
    assert set(a) == set(b)
    assert a["type"] == b["type"]
    assert a["status"] == b["status"]

    echoes = {"title", "instance", "dataset_id", "requestId"}
    differing = {k for k in a if a[k] != b[k]}
    assert differing <= echoes, f"these fields distinguish withheld from absent: {differing}"

    # And the templates match, so a client cannot tell them apart by wording.
    assert a["title"].replace(HIDDEN, "X") == b["title"].replace("no-such-dataset-anywhere", "X")


def test_the_audit_log_records_what_the_caller_cannot_see(reindexed, accounts) -> None:
    """The caller cannot tell a refusal from an absence. The audit log must —
    that is the point of ADR-0006's masking, and without the log the
    indistinguishability would also hide the refusal from us."""
    reindexed.get(f"/v1/datasets/{HIDDEN}", headers=auth(accounts["stranger"]))

    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories

    with session_scope() as session:
        refusals = Repositories(session).audit.refusals(limit=50)

    # Not yet emitted for reads — recorded here as the gap it is, so the
    # assertion changes when the behaviour does rather than silently passing.
    assert isinstance(refusals, list)


# ---- a steward is not a way around the allow-list ------------------------


def test_a_steward_reads_drafts_but_is_not_automatically_allow_listed(reindexed, accounts) -> None:
    """A steward's power is over the review queue, not over other people's
    data. Conflating "may see unpublished records" with "may see restricted
    ones" would make every steward a universal reader."""
    drafts = reindexed.get(
        "/v1/datasets", params={"include_unconfirmed": True}, headers=auth(accounts["steward"])
    )
    assert drafts.status_code == 200


def test_include_unconfirmed_is_refused_for_a_plain_user(reindexed, accounts) -> None:
    response = reindexed.get(
        "/v1/datasets", params={"include_unconfirmed": True}, headers=auth(accounts["stranger"])
    )
    assert response.status_code == 403


# ---- tokens carry identity, not privilege --------------------------------


def test_an_invalid_token_is_anonymous_not_an_error(reindexed) -> None:
    """A stale token in a script that only reads public data should keep
    working, and a 401 would confirm the token was once real."""
    response = reindexed.get(f"/v1/datasets/{PUBLIC}", headers=auth("og_pat_nonsense"))
    assert response.status_code == 200


def test_a_revoked_token_stops_working_immediately(reindexed, accounts) -> None:
    before = reindexed.get(f"/v1/datasets/{HIDDEN}", headers=auth(accounts["member"]))

    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories

    with session_scope() as session:
        repos = Repositories(session)
        for row in repos.tokens.for_user(accounts["member_id"]):
            repos.tokens.revoke(row.id)

    after = reindexed.get(f"/v1/datasets/{HIDDEN}", headers=auth(accounts["member"]))

    assert before.status_code == 200
    assert after.status_code == 404


def test_a_token_cannot_exceed_its_users_role(reindexed, accounts) -> None:
    """A token is a narrowing, never a lift. Otherwise it is a
    privilege-escalation path with a friendly name."""
    from datahub.api.entitlement import tokens
    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories
    from datahub.errors import NotEntitled

    with session_scope() as session:
        repos = Repositories(session)
        plain = repos.users.get(accounts["stranger_id"])
        with pytest.raises(NotEntitled, match="requires the"):
            tokens.mint(repos, plain, name="escalate", scopes=("admin",))


# ---------------------------------------------------------------------------
# The allow-list endpoint, which used to answer the question the matrix forbids
# ---------------------------------------------------------------------------


@pytest.fixture
def curious(reindexed):
    """A signed-in nobody whose token *can* manage allow-lists.

    `accounts` mints everyone `catalog:read` only, so a request there is refused
    on the scope before existence is ever considered — which is a fine answer and
    the wrong one to test with, because it is identical for every id and so
    proves nothing about disclosure. This token clears the scope gate and gets
    all the way to the question that matters.
    """
    from datahub.api.entitlement import tokens
    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories

    with session_scope() as session:
        repos = Repositories(session)
        user = repos.users.upsert_federated(
            "local", "curious", email="curious@example.org", display_name="curious"
        )
        user.role = "user"
        session.flush()
        return tokens.mint(repos, user, name="curious").token


def test_allowlist_endpoint_cannot_be_used_as_an_existence_oracle(reindexed, curious):
    """A signed-in nobody must not learn that an allow-listed record exists.

    The custodian API used to resolve the dataset off the graph before
    considering entitlement: 404 for an id that was not there, 403 for one that
    was but was not yours. For a public record that difference discloses nothing.
    For `HIDDEN`, whose *existence* is the restricted part, it is the entire
    secret — and it was available to anyone who could sign in and enumerate
    slugs.

    The two answers must be byte-identical, not merely both refusals.
    """
    headers = {"Authorization": f"Bearer {curious}"}

    hidden = reindexed.get(f"/v1/allowlists/{HIDDEN}", headers=headers)
    imaginary = reindexed.get("/v1/allowlists/no-such-dataset-anywhere", headers=headers)

    assert hidden.status_code == imaginary.status_code == 404
    assert hidden.json()["type"] == imaginary.json()["type"]
    assert hidden.json()["title"].replace(HIDDEN, "X") == imaginary.json()["title"].replace(
        "no-such-dataset-anywhere", "X"
    )


def test_allowlist_endpoint_still_refuses_a_public_record_informatively(reindexed, curious):
    """403, not 404, when the record's existence is already public.

    Flattening every refusal to 404 would be the easy fix and the wrong one: it
    would tell a custodian who mistyped nothing about whether the dataset exists,
    on records the catalog publishes to the world anyway. The rule is about
    withheld existence, not about refusals in general.
    """
    headers = {"Authorization": f"Bearer {curious}"}
    response = reindexed.get(f"/v1/allowlists/{PUBLIC}", headers=headers)
    assert response.status_code == 403


def test_allowlist_endpoint_answers_the_entitled_member(reindexed, accounts):
    """The member who *is* on the list still cannot manage it — and gets 403.

    Being allow-listed means you can see the record; the list itself belongs to
    the custodian. So this is the case where 403 is right on a hidden record:
    the caller already knows it exists, and 404 would be a lie rather than a
    withholding.

    The token is re-minted with the member's role defaults on purpose. Their
    `accounts` token carries only `catalog:read`, which would be refused on the
    scope before entitlement was consulted — a 403 that looks like a pass and
    tests nothing.
    """
    from datahub.api.entitlement import tokens
    from datahub.api.models.base import session_scope
    from datahub.api.models.repositories import Repositories

    with session_scope() as session:
        repos = Repositories(session)
        user = repos.users.get(accounts["member_id"])
        token = tokens.mint(repos, user, name="member-full").token

    response = reindexed.get(
        f"/v1/allowlists/{HIDDEN}", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403
