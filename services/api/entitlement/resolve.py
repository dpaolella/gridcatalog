"""Turning a request into an :class:`Entitlement` (WP-4.3, extended in M6).

ADR-0006: **entitlement is compiled into the query, never applied to its
results.** This module is the one place a caller's identity becomes the
predicate that gets compiled. Everything downstream takes an ``Entitlement`` and
has no way to ask who the caller is — which is deliberate, because a handler
that could ask would eventually decide.

Two identity sources, because there are two kinds of caller (PRD §F10): a
**bearer token** for the SDK and the MCP server, and a **session cookie** for
the browser. Both land in the same :class:`Caller`, so nothing downstream knows
or cares which was presented.

**A bad token is anonymous, not an error.** A caller presenting an expired token
to a public search gets the public results, the same as a caller presenting
none. Returning 401 instead would leak that the token was once valid, and would
break the common case of a stale token in a script that only reads public data.
The audit log records the presentation; the response does not.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from datahub.api.models.repositories import Repositories
from datahub.api.search.backend import Entitlement
from datahub.config import Settings, get_settings
from datahub.logging import get_logger

log = get_logger(__name__)

#: Presented tokens look like ``og_pat_<32 bytes base64url>``. The prefix is
#: stored alongside the hash so a user can identify which token to revoke
#: without the token itself being recoverable.
TOKEN_PREFIX = "og_pat_"

#: The browser session cookie. Defined here rather than in the auth router
#: because both the router that sets it and the resolver that reads it need the
#: name, and a cookie whose name is spelled in two places is a sign-in that
#: works until someone renames one of them.
SESSION_COOKIE = "og_session"


@dataclass(frozen=True, slots=True)
class Caller:
    """Who is asking, and what they may see.

    Both, because the audit log needs the identity and the query needs the
    predicate, and deriving one from the other at two call sites is how they
    come apart.
    """

    entitlement: Entitlement
    principal_id: str | None = None
    email: str | None = None
    role: str = "anonymous"
    token_id: str | None = None
    is_agent: bool = False
    #: The scopes of the token this request carried, or ``None`` when the caller
    #: did not present one.
    #:
    #: The distinction matters. A browser session *is* the user, so it carries
    #: whatever their role carries — narrowing does not apply. An API token is a
    #: deliberately weaker credential its holder chose the reach of, and honouring
    #: that choice is the whole point of scopes. ``None`` means "unscoped", not
    #: "no permissions".
    scopes: tuple[str, ...] | None = None

    @property
    def is_anonymous(self) -> bool:
        return self.principal_id is None

    @property
    def client_kind(self) -> str:
        return "agent" if self.is_agent else ("user" if self.principal_id else "anonymous")


def hash_token(token: str, settings: Settings | None = None) -> str:
    """Hash a presented token for lookup.

    Keyed HMAC rather than a bare digest: the tokens are high-entropy so a
    rainbow table is not the threat, but a keyed hash means a leaked database
    alone does not let an attacker confirm a guessed token offline.
    """
    settings = settings or get_settings()
    return hmac.new(
        settings.secret_key.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def anonymous() -> Caller:
    return Caller(entitlement=Entitlement.anonymous())


def resolve(
    authorization: str | None,
    session: Any = None,
    settings: Settings | None = None,
    cookie: str | None = None,
) -> Caller:
    """The caller behind a request.

    ``session`` is a SQLAlchemy session or None. None means no operational
    store is reachable, which resolves to anonymous — a catalog whose database
    is down should still serve public search rather than 500 on every request.

    A bearer token wins over a session cookie when both are present. The
    ambiguous case is a browser that is signed in as one person driving a
    script that presents another person's token, and the explicit credential is
    the one the caller chose to send.
    """
    settings = settings or get_settings()
    if session is None:
        return anonymous()

    token = _bearer(authorization)
    if token:
        return _from_token(token, session, settings)
    if cookie:
        return _from_cookie(cookie, session)
    return anonymous()


def _from_token(token: str, session: Any, settings: Settings) -> Caller:
    repos = Repositories(session)
    row = repos.tokens.by_hash(hash_token(token, settings))
    if row is None:
        # Not an error. A stale token in a script that only reads public data
        # should keep working, and a 401 would confirm the token was once real.
        log.info("unrecognised bearer token presented", prefix=token[:12])
        return anonymous()

    user = repos.users.get(row.user_id)
    if user is None or not user.is_active:
        return anonymous()

    repos.tokens.mark_used(row.id)
    return _caller_for(user, repos, token_id=row.id, scopes=tuple(row.scopes or ()))


def _from_cookie(session_id: str, session: Any) -> Caller:
    """Resolve a browser session.

    ``live`` is what makes this safe: expiry and revocation are conditions of
    the lookup, so a logged-out cookie resolves to anonymous rather than to the
    person who logged out.

    Unlike the token path this writes nothing. A session that recorded its own
    last use would put every browser GET in a write transaction, and the cost
    of that shows up somewhere unrelated — as a refusal whose audit row cannot
    be written because the request being refused holds the write lock.
    """
    repos = Repositories(session)
    row = repos.sessions.live(session_id)
    if row is None:
        return anonymous()
    user = repos.users.get(row.user_id)
    if user is None or not user.is_active:
        return anonymous()
    return _caller_for(user, repos)


def _caller_for(
    user: Any,
    repos: Repositories,
    *,
    token_id: str | None = None,
    scopes: tuple[str, ...] | None = None,
) -> Caller:
    # Allow-list membership is NOT resolved here. It is projected onto each
    # search document as `entitled_principals`, so the predicate is evaluated
    # inside the query rather than against its results (ADR-0006). Resolving it
    # here would mean either a second pass over the hits — which leaks
    # existence through counts — or an IN clause the length of the user's
    # grants.
    custodian_of = repos.custodians.custodian_iris_for(user.id)

    return Caller(
        entitlement=Entitlement(
            principal_id=user.id,
            email=user.email,
            custodian_of=frozenset(custodian_of),
            is_steward=user.role in ("steward", "admin"),
        ),
        principal_id=user.id,
        email=user.email,
        role=user.role,
        token_id=token_id,
        is_agent=user.is_agent,
        scopes=scopes,
    )


def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


__all__ = ["SESSION_COOKIE", "TOKEN_PREFIX", "Caller", "anonymous", "hash_token", "resolve"]
