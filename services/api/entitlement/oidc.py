"""Federated sign-in (WP-6.1).

PRD §F10: *federated SSO — GitHub, Google, Microsoft — plus an OpenGrid-native
credential path.* Authorization Code with PKCE, which is the flow for a public
client and increasingly the only one providers will issue.

**No provider-specific branches in the flow.** Each provider is a
:class:`Provider` record — three URLs, a scope string and a function that turns
that provider's user payload into a subject and an email. Everything else is
the same code, because it is the same protocol; a per-provider code path is how
one provider's quirk becomes three providers' bugs.

**State and PKCE are checked, not merely sent.** A flow that generates a state
parameter and does not verify it on the way back has implemented the *shape* of
CSRF protection and none of the substance, which is worse than omitting it —
it looks protected in review.

**A user is matched on ``(provider, subject)``, never on email.** An email is
reassignable and a subject is not, so matching on email would let a reused
address inherit the previous holder's allow-list grants. That rule lives in
:meth:`UserRepository.upsert_federated`; this module simply hands it the
subject.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
from datahub.api.models.base import utcnow
from datahub.api.models.operational import Session as SessionRow
from datahub.api.models.repositories import Repositories
from datahub.config import Settings, get_settings
from datahub.errors import DataHubError, NotAuthenticated
from datahub.logging import get_logger

log = get_logger(__name__)


class OidcError(DataHubError):
    status_code = 400
    code = "oidc_error"


@dataclass(frozen=True, slots=True)
class Provider:
    """One identity provider. Three URLs and a way to read its user payload."""

    name: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scope: str
    #: Payload → (subject, email, display name). Providers disagree about all
    #: three field names, and that disagreement is the only thing that differs
    #: between them.
    read_user: Callable[[dict[str, Any]], tuple[str, str | None, str | None]]
    #: GitHub needs an explicit Accept header to return JSON from its token
    #: endpoint; without it the response is form-encoded and every client
    #: written against the spec breaks on it.
    token_headers: dict[str, str] = field(default_factory=lambda: {"Accept": "application/json"})


def _github(payload: dict[str, Any]) -> tuple[str, str | None, str | None]:
    return str(payload["id"]), payload.get("email"), payload.get("name") or payload.get("login")


def _google(payload: dict[str, Any]) -> tuple[str, str | None, str | None]:
    return str(payload["sub"]), payload.get("email"), payload.get("name")


def _microsoft(payload: dict[str, Any]) -> tuple[str, str | None, str | None]:
    return (
        str(payload["id"]),
        payload.get("mail") or payload.get("userPrincipalName"),
        payload.get("displayName"),
    )


PROVIDERS: dict[str, Provider] = {
    "github": Provider(
        name="github",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        userinfo_url="https://api.github.com/user",
        scope="read:user user:email",
        read_user=_github,
    ),
    "google": Provider(
        name="google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scope="openid email profile",
        read_user=_google,
    ),
    "microsoft": Provider(
        name="microsoft",
        authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        userinfo_url="https://graph.microsoft.com/v1.0/me",
        scope="openid email profile User.Read",
        read_user=_microsoft,
    ),
}


@dataclass(slots=True)
class Handshake:
    """The half of a sign-in that has to survive the round trip.

    Held server-side and keyed by ``state``. Not in a cookie: a verifier the
    browser holds is a verifier an attacker who can read cookies holds, and
    PKCE then protects nothing.
    """

    state: str
    verifier: str
    provider: str
    redirect_uri: str
    next_url: str | None = None
    created_at: Any = field(default_factory=utcnow)


class Handshakes:
    """In-memory handshake store.

    Sufficient for a single process; a multi-process deployment needs this in
    Redis or the operational store, and the shape is deliberately small so that
    swap is a class rather than a refactor. Entries expire, because a state
    parameter that stays valid for ever is a replay window.
    """

    def __init__(self, ttl: timedelta = timedelta(minutes=10)) -> None:
        self._ttl = ttl
        self._entries: dict[str, Handshake] = {}

    def put(self, handshake: Handshake) -> None:
        self._sweep()
        self._entries[handshake.state] = handshake

    def take(self, state: str) -> Handshake | None:
        """One use only. A state parameter that can be replayed is not a state
        parameter."""
        self._sweep()
        return self._entries.pop(state, None)

    def _sweep(self) -> None:
        cutoff = utcnow() - self._ttl
        for key in [k for k, v in self._entries.items() if v.created_at < cutoff]:
            del self._entries[key]


HANDSHAKES = Handshakes()


def begin(
    provider_name: str,
    *,
    redirect_uri: str,
    next_url: str | None = None,
    settings: Settings | None = None,
    handshakes: Handshakes | None = None,
) -> str:
    """Start a sign-in. Returns the URL to send the browser to."""
    settings = settings or get_settings()
    provider = _provider(provider_name, settings)
    client_id = _client_id(provider_name, settings)

    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    state = secrets.token_urlsafe(32)
    (handshakes or HANDSHAKES).put(
        Handshake(
            state=state,
            verifier=verifier,
            provider=provider_name,
            redirect_uri=redirect_uri,
            next_url=next_url,
        )
    )

    from urllib.parse import urlencode

    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": provider.scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{provider.authorize_url}?{query}"


def complete(
    state: str,
    code: str,
    *,
    session: Any,
    settings: Settings | None = None,
    handshakes: Handshakes | None = None,
    client: httpx.Client | None = None,
) -> tuple[SessionRow, str | None]:
    """Finish a sign-in. Returns the session row and where to send the browser.

    The state is looked up and consumed before anything else happens. A flow
    that generates a state parameter and does not verify it has implemented the
    shape of CSRF protection and none of the substance — and looks protected in
    review, which is the worst of both.
    """
    settings = settings or get_settings()
    handshake = (handshakes or HANDSHAKES).take(state)
    if handshake is None:
        raise NotAuthenticated(
            "this sign-in could not be matched to a request we started. "
            "It may have expired, or already been used."
        )

    provider = _provider(handshake.provider, settings)
    owned = client is None
    http = client or httpx.Client(timeout=settings.harvest_timeout_s)
    try:
        token = _exchange(http, provider, code, handshake, settings)
        payload = _userinfo(http, provider, token)
    finally:
        if owned:
            http.close()

    subject, email, display_name = provider.read_user(payload)
    repos = Repositories(session)
    user = repos.users.upsert_federated(
        provider.name, subject, email=email, display_name=display_name
    )

    row = repos.sessions.open(user.id, ttl=timedelta(seconds=settings.session_ttl_s))
    log.info("sign-in complete", provider=provider.name, user=user.id)
    return row, handshake.next_url


def _exchange(
    client: httpx.Client,
    provider: Provider,
    code: str,
    handshake: Handshake,
    settings: Settings,
) -> str:
    response = client.post(
        provider.token_url,
        data={
            "client_id": _client_id(provider.name, settings),
            "client_secret": _client_secret(provider.name, settings),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": handshake.redirect_uri,
            "code_verifier": handshake.verifier,
        },
        headers=provider.token_headers,
    )
    if response.status_code != 200:
        # The provider's body is not echoed: it can contain the client secret
        # in an error message, and it is no use to the person signing in.
        log.warning(
            "token exchange failed",
            provider=provider.name,
            status=response.status_code,
        )
        raise OidcError(f"{provider.name} refused the sign-in. Please try again.")

    body = response.json()
    token = body.get("access_token")
    if not token:
        raise OidcError(f"{provider.name} returned no access token.")
    return str(token)


def _userinfo(client: httpx.Client, provider: Provider, token: str) -> dict[str, Any]:
    response = client.get(
        provider.userinfo_url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    if response.status_code != 200:
        raise OidcError(f"{provider.name} would not say who signed in.")
    payload = response.json()
    if not isinstance(payload, dict):
        raise OidcError(f"{provider.name} returned an unexpected user payload.")
    return payload


def _provider(name: str, settings: Settings) -> Provider:
    if name not in PROVIDERS:
        raise OidcError(f"unknown provider {name!r}", available=sorted(PROVIDERS))
    if name not in settings.oidc_provider_list:
        # Configured off is not the same as unknown, and the message says which.
        raise OidcError(
            f"{name} sign-in is not enabled on this deployment",
            enabled=settings.oidc_provider_list,
        )
    return PROVIDERS[name]


def _client_id(name: str, settings: Settings) -> str:
    value = getattr(settings, f"oidc_{name}_client_id", None)
    if not value:
        raise OidcError(f"{name} sign-in is enabled but has no client id configured")
    return str(value)


def _client_secret(name: str, settings: Settings) -> str:
    value = getattr(settings, f"oidc_{name}_client_secret", None)
    if not value:
        raise OidcError(f"{name} sign-in is enabled but has no client secret configured")
    return str(value)


__all__ = [
    "HANDSHAKES",
    "PROVIDERS",
    "Handshake",
    "Handshakes",
    "OidcError",
    "Provider",
    "begin",
    "complete",
]
