"""Sign-in, tokens and the custodian allow-list API (WP-6.1, WP-6.2).

The OIDC flow is exercised against a stub provider, because the build
environment cannot reach GitHub, Google or Microsoft — and because what is
worth testing is our half of the protocol, not theirs. The state and PKCE
checks in particular: a flow that generates a state parameter and does not
verify it has implemented the shape of CSRF protection and none of the
substance, and looks protected in review.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.entitlement import oidc, tokens
from datahub.api.models.base import session_scope
from datahub.api.models.repositories import Repositories


@pytest.fixture
def user(client):
    with session_scope() as session:
        repos = Repositories(session)
        row = repos.users.upsert_federated(
            "local", "alice", email="alice@example.org", display_name="Alice"
        )
        issued = tokens.mint(repos, row, name="cli", scopes=("catalog:read",))
        return {"id": row.id, "token": issued.token, "token_id": issued.row.id}


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- who am I ------------------------------------------------------------


def test_me_answers_for_an_anonymous_caller(client) -> None:
    """A client asking "who am I" wants to know, and "nobody" is an answer. A
    401 here would make every UI special-case the state it spends most of its
    time in."""
    body = client.get("/v1/auth/me").json()

    assert body["authenticated"] is False
    assert body["principal_id"] is None
    assert body["role"] == "anonymous"


def test_me_identifies_a_token_holder(client, user) -> None:
    body = client.get("/v1/auth/me", headers=auth(user["token"])).json()

    assert body["authenticated"] is True
    assert body["principal_id"] == user["id"]
    assert body["email"] == "alice@example.org"


def test_providers_lists_only_the_configured_ones(client) -> None:
    """Rendering a Google button on a deployment with no Google client id
    produces an error page that blames the user."""
    body = client.get("/v1/auth/providers").json()
    assert body["providers"] == [], "no client ids are configured in tests"


def test_providers_lists_one_that_is_configured(client, monkeypatch) -> None:
    monkeypatch.setenv("DATAHUB_OIDC_GITHUB_CLIENT_ID", "abc123")
    from datahub.config import reset_settings

    reset_settings()
    assert client.get("/v1/auth/providers").json()["providers"] == ["github"]


# ---- the OIDC flow -------------------------------------------------------


@pytest.fixture
def configured(monkeypatch):
    for provider in ("github",):
        monkeypatch.setenv(f"DATAHUB_OIDC_{provider.upper()}_CLIENT_ID", "client-id")
        monkeypatch.setenv(f"DATAHUB_OIDC_{provider.upper()}_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("DATAHUB_OIDC_PROVIDERS", "github")
    from datahub.config import get_settings, reset_settings

    reset_settings()
    return get_settings()


def test_beginning_a_sign_in_sends_pkce(configured) -> None:
    """Authorization Code with PKCE: the flow for a public client, and
    increasingly the only one providers will issue."""
    handshakes = oidc.Handshakes()
    url = oidc.begin(
        "github",
        redirect_uri="https://hub.example/callback",
        settings=configured,
        handshakes=handshakes,
    )
    params = parse_qs(urlparse(url).query)

    assert params["response_type"] == ["code"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"][0]
    assert params["state"][0]
    assert handshakes.take(params["state"][0]) is not None


def test_the_verifier_never_leaves_the_server(configured) -> None:
    """A verifier the browser holds is a verifier an attacker who can read
    cookies holds, and PKCE then protects nothing."""
    handshakes = oidc.Handshakes()
    url = oidc.begin(
        "github",
        redirect_uri="https://hub.example/callback",
        settings=configured,
        handshakes=handshakes,
    )
    assert "code_verifier" not in url


def test_an_unmatched_state_is_refused(configured, client) -> None:
    """A flow that generates a state parameter and does not verify it has
    implemented the shape of CSRF protection and none of the substance."""
    from datahub.errors import NotAuthenticated

    with session_scope() as session, pytest.raises(NotAuthenticated):
        oidc.complete(
            "a-state-nobody-issued",
            "some-code",
            session=session,
            settings=configured,
            handshakes=oidc.Handshakes(),
        )


def test_a_state_is_single_use(configured) -> None:
    """A state parameter that can be replayed is not a state parameter."""
    handshakes = oidc.Handshakes()
    url = oidc.begin(
        "github",
        redirect_uri="https://hub.example/callback",
        settings=configured,
        handshakes=handshakes,
    )
    state = parse_qs(urlparse(url).query)["state"][0]

    assert handshakes.take(state) is not None
    assert handshakes.take(state) is None


def test_a_completed_sign_in_creates_a_user_and_a_session(configured, client) -> None:
    handshakes = oidc.Handshakes()
    url = oidc.begin(
        "github",
        redirect_uri="https://hub.example/callback",
        settings=configured,
        handshakes=handshakes,
    )
    state = parse_qs(urlparse(url).query)["state"][0]

    def handler(request: httpx.Request) -> httpx.Response:
        if "access_token" in str(request.url) or request.method == "POST":
            return httpx.Response(200, json={"access_token": "provider-token"})
        return httpx.Response(200, json={"id": 4242, "email": "bob@example.org", "name": "Bob"})

    with session_scope() as session:
        row, _ = oidc.complete(
            state,
            "the-code",
            session=session,
            settings=configured,
            handshakes=handshakes,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        user = Repositories(session).users.get(row.user_id)

    assert user.email == "bob@example.org"
    assert row.expires_at is not None


def test_a_second_sign_in_reuses_the_same_user(configured, client) -> None:
    """Matched on `(provider, subject)`, never on email: an email is
    reassignable and a subject is not, so matching on email would let a reused
    address inherit the previous holder's allow-list grants."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"access_token": "t"})
        return httpx.Response(200, json={"id": 4242, "email": "changed@example.org"})

    ids = []
    for _ in range(2):
        handshakes = oidc.Handshakes()
        url = oidc.begin(
            "github",
            redirect_uri="https://hub.example/callback",
            settings=configured,
            handshakes=handshakes,
        )
        state = parse_qs(urlparse(url).query)["state"][0]
        with session_scope() as session:
            row, _ = oidc.complete(
                state,
                "code",
                session=session,
                settings=configured,
                handshakes=handshakes,
                client=httpx.Client(transport=httpx.MockTransport(handler)),
            )
            ids.append(row.user_id)

    assert ids[0] == ids[1]


def test_an_unconfigured_provider_says_which(configured) -> None:
    """Configured off is not the same as unknown."""
    with pytest.raises(oidc.OidcError, match="not enabled"):
        oidc.begin("google", redirect_uri="https://x/cb", settings=configured)
    with pytest.raises(oidc.OidcError, match="unknown provider"):
        oidc.begin("myspace", redirect_uri="https://x/cb", settings=configured)


def test_an_off_site_next_url_is_refused(client) -> None:
    """An unchecked `next` is an open redirect: a link that goes to our sign-in
    and lands on somebody else's page, having worn our domain in the address
    bar for the part the user was paying attention to."""
    from datahub.api.routers.auth import _safe_next
    from datahub.config import get_settings

    settings = get_settings()
    assert _safe_next("/datasets/era5", settings) == "/datasets/era5"
    assert _safe_next("https://evil.example/phish", settings) is None
    assert _safe_next("//evil.example/phish", settings) is None
    assert _safe_next("http://localhost:3000/x", settings) == "http://localhost:3000/x"


# ---- tokens --------------------------------------------------------------


def test_a_token_is_shown_once(client, user) -> None:
    body = client.post(
        "/v1/auth/tokens", json={"name": "laptop"}, headers=auth(user["token"])
    ).json()

    assert body["token"].startswith("og_pat_")
    assert "only time" in body["warning"]

    listed = client.get("/v1/auth/tokens", headers=auth(user["token"])).json()
    assert all("token" not in row for row in listed)


def test_the_prefix_identifies_without_authenticating(client, user) -> None:
    """Enough for a person to tell which of their four tokens to revoke, and
    not enough to authenticate with."""
    created = client.post(
        "/v1/auth/tokens", json={"name": "laptop"}, headers=auth(user["token"])
    ).json()

    assert created["token"].startswith(created["prefix"])
    assert len(created["prefix"]) < len(created["token"])
    assert (
        client.get("/v1/auth/me", headers=auth(created["prefix"])).json()["authenticated"] is False
    )


def test_only_a_hash_is_stored(client, user) -> None:
    created = client.post(
        "/v1/auth/tokens", json={"name": "laptop"}, headers=auth(user["token"])
    ).json()

    with session_scope() as session:
        row = Repositories(session).tokens.get(created["id"])
    assert created["token"] not in row.token_hash
    assert len(row.token_hash) == 64


def test_a_scope_beyond_the_users_role_is_refused_not_dropped(client, user) -> None:
    """A silently narrowed token is the worst outcome available: the holder
    believes they have a working credential, every call fails in a way that
    looks like a bug, and nothing says why."""
    response = client.post(
        "/v1/auth/tokens",
        json={"name": "escalate", "scopes": ["admin"]},
        headers=auth(user["token"]),
    )

    assert response.status_code == 403
    assert "requires the admin role" in response.json()["title"]


def test_an_unknown_scope_is_refused(client, user) -> None:
    response = client.post(
        "/v1/auth/tokens",
        json={"name": "typo", "scopes": ["catalog:reed"]},
        headers=auth(user["token"]),
    )
    assert response.status_code == 403
    assert response.json()["available"]


def test_revocation_is_immediate(client, user) -> None:
    created = client.post(
        "/v1/auth/tokens", json={"name": "temp"}, headers=auth(user["token"])
    ).json()
    assert client.get("/v1/auth/me", headers=auth(created["token"])).json()["authenticated"]

    client.delete(f"/v1/auth/tokens/{created['id']}", headers=auth(user["token"]))

    assert not client.get("/v1/auth/me", headers=auth(created["token"])).json()["authenticated"]


def test_you_cannot_revoke_somebody_elses_token(client, user) -> None:
    """The same answer whether it is somebody else's token or none at all:
    confirming that a token id exists tells an attacker their guess was
    right."""
    with session_scope() as session:
        repos = Repositories(session)
        other = repos.users.upsert_federated("local", "mallory")
        theirs = tokens.mint(repos, other, name="theirs")
        theirs_id = theirs.row.id

    response = client.delete(f"/v1/auth/tokens/{theirs_id}", headers=auth(user["token"]))
    missing = client.delete("/v1/auth/tokens/does-not-exist", headers=auth(user["token"]))

    assert response.status_code == missing.status_code == 403
    assert response.json()["title"] == missing.json()["title"]


def test_issuing_a_token_needs_a_caller(client) -> None:
    assert client.post("/v1/auth/tokens", json={"name": "x"}).status_code == 401


# ---- sessions ------------------------------------------------------------


def test_logout_revokes_server_side_not_only_the_cookie(client, user) -> None:
    """Clearing only the cookie leaves a session id that still works for anyone
    who copied it, which is exactly the case someone logs out to prevent."""
    with session_scope() as session:
        from datetime import timedelta

        row = Repositories(session).sessions.open(user["id"], ttl=timedelta(days=1))
        session_id = row.id

    client.cookies.set("og_session", session_id)
    client.post("/v1/auth/logout")

    with session_scope() as session:
        assert Repositories(session).sessions.live(session_id) is None


def test_logout_everywhere_ends_every_session(client, user) -> None:
    from datetime import timedelta

    with session_scope() as session:
        repos = Repositories(session)
        ids = [repos.sessions.open(user["id"], ttl=timedelta(days=1)).id for _ in range(3)]

    client.post("/v1/auth/logout-everywhere", headers=auth(user["token"]))

    with session_scope() as session:
        repos = Repositories(session)
        assert all(repos.sessions.live(i) is None for i in ids)


def test_an_expired_session_is_not_live(client, user) -> None:
    from datetime import timedelta

    with session_scope() as session:
        repos = Repositories(session)
        row = repos.sessions.open(user["id"], ttl=timedelta(seconds=-1))
        assert repos.sessions.live(row.id) is None


def test_a_session_cookie_authenticates_the_browser(client, user) -> None:
    """The cookie is the browser's credential, so it has to resolve to a caller.

    Worth its own test because the failure mode is quiet: sign-in appears to
    work — the provider round trip completes, the cookie is set, the redirect
    lands — and then every subsequent request is anonymous, so the UI shows a
    signed-in chrome over signed-out data.
    """
    from datetime import timedelta

    with session_scope() as session:
        session_id = Repositories(session).sessions.open(user["id"], ttl=timedelta(days=1)).id

    client.cookies.set("og_session", session_id)
    body = client.get("/v1/auth/me").json()

    assert body["authenticated"] is True
    assert body["principal_id"] == user["id"]


def test_a_revoked_session_cookie_resolves_to_nobody(client, user) -> None:
    """Expiry and revocation are conditions of the lookup, not checks after it —
    so a cookie that survives a logout is a cookie that stops working."""
    from datetime import timedelta

    with session_scope() as session:
        session_id = Repositories(session).sessions.open(user["id"], ttl=timedelta(days=1)).id

    client.cookies.set("og_session", session_id)
    client.post("/v1/auth/logout")
    client.cookies.set("og_session", session_id)

    assert client.get("/v1/auth/me").json()["authenticated"] is False


def test_a_bearer_token_wins_over_a_session_cookie(client, user) -> None:
    """A browser signed in as one person driving a script that presents another
    person's token: the explicit credential is the one the caller chose."""
    from datetime import timedelta

    with session_scope() as session:
        repos = Repositories(session)
        other = repos.users.upsert_federated("local", "bob", email="bob@example.org")
        other_token = tokens.mint(repos, other, name="cli").token
        other_id = other.id
        session_id = repos.sessions.open(user["id"], ttl=timedelta(days=1)).id

    client.cookies.set("og_session", session_id)
    body = client.get("/v1/auth/me", headers=auth(other_token)).json()

    assert body["principal_id"] == other_id
