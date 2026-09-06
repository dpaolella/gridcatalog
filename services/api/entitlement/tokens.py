"""Personal access tokens (WP-6.1).

The credential the SDK and the MCP server carry. PRD §F10: *identity propagates
through MCP and SDK calls; agent requests are strictly bounded by the
represented user's own permissions.* A token is that propagation — it is the
user, in a header.

Three properties, each of which is a decision rather than a default:

**The token is shown once.** Only a keyed hash is stored, so a leaked database
does not yield working credentials, and there is no "show me my token again"
path because there is nothing to show. What is stored beside the hash is the
first twelve characters, which is enough for a person to tell which of their
four tokens to revoke and not enough to authenticate with.

**Scopes are a ceiling, never a floor.** A token cannot grant its holder
anything the user does not already have; it can only narrow. So a read-only
token issued to an agent is genuinely read-only even if the user is an admin,
and a token cannot become a privilege-escalation path.

**Revocation is immediate and irreversible.** No un-revoke: a token someone
revoked because they think it leaked must stay dead, and "undo" on that button
is a way to reinstate a compromised credential by accident.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from datahub.api.entitlement.resolve import TOKEN_PREFIX, hash_token
from datahub.api.models.base import utcnow
from datahub.api.models.operational import ApiToken, User
from datahub.api.models.repositories import Repositories
from datahub.config import Settings, get_settings
from datahub.errors import NotEntitled
from datahub.logging import get_logger

log = get_logger(__name__)

#: What a token may be scoped to. A closed set: an unknown scope in a token is
#: either a typo that silently grants nothing or a future permission being
#: honoured early, and both are worse than a rejected request.
SCOPES: dict[str, str] = {
    "catalog:read": "Search and read catalog records, and issue access plans.",
    "catalog:write": "Submit datasets and file issue reports.",
    "steward:review": "Read drafts and confirm records. Steward role required.",
    "custodian:manage": "Manage the allow-lists of datasets you are custodian of.",
    "admin": "Everything, including changing roles. Admin role required.",
}

#: Scopes that require a role beyond `user`. A token cannot lift its holder —
#: asking for a scope the user's role does not carry is refused at issue time,
#: loudly, rather than granted and then quietly ignored at every use.
ROLE_REQUIRED: dict[str, tuple[str, ...]] = {
    "steward:review": ("steward", "admin"),
    "admin": ("admin",),
}

DEFAULT_SCOPES = ("catalog:read",)

#: What "give me a token" means for each role, when the caller does not narrow it.
#:
#: A token defaults to the authority its holder already has; narrowing is the
#: deliberate act. The alternative — defaulting to `catalog:read` — reads as
#: least-privilege but is not, because it was never enforced: every token in
#: existence has been acting with its holder's full role authority regardless of
#: what its `scopes` column said. Enforcing that column *and* keeping the narrow
#: default would silently revoke every steward's queue access, which is a
#: migration disguised as a bug fix.
#:
#: `custodian:manage` is here for plain users because custodianship is a property
#: of a dataset, not a role: whether it grants anything is decided per record by
#: `_custodian_check`.
ROLE_SCOPES: dict[str, tuple[str, ...]] = {
    "user": ("catalog:read", "catalog:write", "custodian:manage"),
    "steward": ("catalog:read", "catalog:write", "custodian:manage", "steward:review"),
    "admin": ("admin",),
}


def default_scopes_for(role: str) -> tuple[str, ...]:
    """The scopes a token gets when its issuer does not choose."""
    return ROLE_SCOPES.get(role, DEFAULT_SCOPES)


@dataclass(slots=True)
class IssuedToken:
    """A token, exactly once.

    The plaintext exists in this object and nowhere else: it is not stored, not
    logged, and not recoverable. A caller that loses it issues another.
    """

    token: str
    row: ApiToken

    @property
    def id(self) -> str:
        return self.row.id

    @property
    def prefix(self) -> str:
        return self.row.prefix


def mint(
    repos: Repositories,
    user: User,
    *,
    name: str,
    scopes: tuple[str, ...] | None = None,
    ttl: timedelta | None = None,
    settings: Settings | None = None,
) -> IssuedToken:
    """Issue a token for a user, within that user's own permissions.

    A scope the user's role does not carry is refused rather than dropped. A
    silently narrowed token is the worst outcome available: the holder believes
    they have a working credential, every call fails in a way that looks like a
    bug, and nothing says why.

    ``scopes=None`` means "as capable as its holder" — see `ROLE_SCOPES`.
    Narrowing is what a caller asks for explicitly, and now that scopes are
    actually enforced, asking gets it.
    """
    settings = settings or get_settings()
    scopes = default_scopes_for(user.role) if scopes is None else scopes
    unknown = [scope for scope in scopes if scope not in SCOPES]
    if unknown:
        raise NotEntitled(
            f"unknown scope(s): {', '.join(sorted(unknown))}",
            available=sorted(SCOPES),
        )

    for scope in scopes:
        allowed_roles = ROLE_REQUIRED.get(scope)
        if allowed_roles and user.role not in allowed_roles:
            raise NotEntitled(
                f"the {scope!r} scope requires the {' or '.join(allowed_roles)} role; "
                f"this account is {user.role!r}",
                scope=scope,
            )

    # 32 bytes of urlsafe randomness. The prefix is not a secret and exists so
    # a person can identify which token to revoke.
    secret = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    row = repos.tokens.add(
        ApiToken(
            user_id=user.id,
            name=name,
            token_hash=hash_token(secret, settings),
            prefix=secret[:12],
            scopes=list(scopes),
            expires_at=utcnow() + ttl if ttl else None,
        )
    )
    log.info("token issued", user=user.id, token=row.id, scopes=list(scopes))
    return IssuedToken(token=secret, row=row)


def permits(row: ApiToken, scope: str) -> bool:
    """Whether a token carries a scope.

    ``admin`` implies everything, because a token that had to enumerate every
    scope would silently lose access each time one is added — and an admin
    token that stops working after a deploy is worse than one that is broad.
    """
    scopes = set(row.scopes or ())
    return scope in scopes or "admin" in scopes


def require_scope(caller: Any, scope: str, *, allow_anonymous: bool = False) -> None:
    """Raise unless this request may exercise ``scope``.

    The guard that was written and never called. `require()` below takes a token
    row, and no route had one to hand — so scopes were validated at issue time,
    stored, echoed back by `describe()`, and enforced nowhere. A token minted
    `catalog:read` for a CI job carried its holder's full role authority, which
    is the opposite of what asking for one scope means.

    Three cases, and the middle one is the one worth stating: an anonymous
    caller fails every scope check; a caller who authenticated with a **session
    cookie** carries no scopes and passes, because a browser session is the user
    themselves and there is no narrowing to honour; a caller who presented a
    **token** is held to exactly what that token asked for.

    Role checks are unaffected and still apply on top. A scope cannot lift its
    holder — `mint` refuses a scope the user's role does not carry — so this
    only ever narrows.

    ``allow_anonymous`` is for the endpoints the PRD deliberately leaves open,
    like the intake form and an access plan for a public record. Anonymous still
    works there; what it adds is that a caller who *did* present a token is held
    to it, so a `catalog:read` token cannot file a submission just because
    anybody could.
    """
    from datahub.errors import NotAuthenticated, NotEntitled

    if caller.is_anonymous:
        if allow_anonymous:
            return
        raise NotAuthenticated(f"this endpoint needs the {scope!r} scope; sign in or send a token")
    if caller.scopes is None:
        return
    if scope in caller.scopes or "admin" in caller.scopes:
        return
    raise NotEntitled(
        f"this token does not carry the {scope!r} scope",
        scope=scope,
        token_scopes=sorted(caller.scopes),
    )


def require(row: ApiToken | None, scope: str) -> None:
    """Raise unless the token carries the scope.

    ``None`` — no token at all — fails every scope check. Anonymous read of
    public records does not come through here (PRD §F10: *do not gate
    browsing*); this is the guard on the things that are gated.
    """
    if row is None:
        raise NotEntitled(
            f"this endpoint needs the {scope!r} scope and the request carried no token",
            scope=scope,
        )
    if not permits(row, scope):
        raise NotEntitled(
            f"this token does not carry the {scope!r} scope",
            scope=scope,
            token_scopes=sorted(row.scopes or ()),
        )


def describe(row: ApiToken) -> dict[str, object]:
    """A token as it can safely be shown: everything but the token."""
    return {
        "id": row.id,
        "name": row.name,
        "prefix": row.prefix,
        "scopes": sorted(row.scopes or ()),
        "created_at": row.created_at,
        "expires_at": row.expires_at,
        "last_used_at": row.last_used_at,
    }


def is_live(row: ApiToken, *, now: datetime | None = None) -> bool:
    moment = now or utcnow()
    if row.revoked_at is not None:
        return False
    return row.expires_at is None or row.expires_at > moment


__all__ = [
    "DEFAULT_SCOPES",
    "ROLE_REQUIRED",
    "SCOPES",
    "IssuedToken",
    "describe",
    "is_live",
    "mint",
    "permits",
    "require",
]
