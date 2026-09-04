"""``/v1/auth`` — signing in, and the tokens that stand in for it (WP-6.1).

PRD §F10. Two credential kinds for two kinds of caller:

* a **session cookie** for the browser, issued by the federated sign-in flow;
* a **personal access token** for the SDK and the MCP server, which is the
  user in a header (*identity propagates through MCP and SDK calls*).

**Anonymous read of public records works with no login.** Nothing here is
required to browse the catalog — PRD §F10 says *do not gate browsing*, and
these endpoints exist for the things that are gated.

**A token is shown once.** Only a keyed hash is stored, so there is no
"show me my token again" path because there is nothing to show.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from datahub.api.deps import CallerDep, SessionDep, SettingsDep
from datahub.api.entitlement import oidc, tokens
from datahub.api.entitlement.resolve import SESSION_COOKIE
from datahub.api.models.repositories import Repositories
from datahub.api.schemas import (
    IssuedTokenResponse,
    MeResponse,
    ProviderList,
    TokenRequest,
    TokenSummary,
)
from datahub.errors import NotAuthenticated, NotEntitled
from datahub.logging import get_logger
from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse

log = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _require_store(session: Any) -> None:
    if session is None:
        raise NotAuthenticated(
            "the identity store is unreachable, so nobody can be signed in right now. "
            "Public browsing is unaffected."
        )


# ---- who am I ------------------------------------------------------------


@router.get("/me", response_model=MeResponse, summary="The caller, as the API sees them")
def me(caller: CallerDep) -> MeResponse:
    """Answers honestly for an anonymous caller rather than returning 401.

    A client asking "who am I" wants to know, and "nobody" is an answer. A 401
    here would make every UI implement a special case for the state it spends
    most of its time in.
    """
    return MeResponse(
        authenticated=not caller.is_anonymous,
        principal_id=caller.principal_id,
        email=caller.email,
        role=caller.role,
        is_agent=caller.is_agent,
        is_steward=caller.entitlement.is_steward,
        custodian_of=sorted(caller.entitlement.custodian_of),
    )


@router.get("/providers", response_model=ProviderList, summary="Enabled sign-in providers")
def providers(settings: SettingsDep) -> ProviderList:
    """What a sign-in page should offer.

    Only the configured ones. Rendering a Google button on a deployment with no
    Google client id produces an error page that blames the user.
    """
    enabled = [
        name
        for name in settings.oidc_provider_list
        if name in oidc.PROVIDERS and getattr(settings, f"oidc_{name}_client_id", None)
    ]
    return ProviderList(providers=enabled, native_credentials=False)


# ---- federated sign-in ---------------------------------------------------


@router.get(
    "/login/{provider}",
    status_code=status.HTTP_302_FOUND,
    summary="Begin a federated sign-in",
)
def login(
    provider: str,
    request: Request,
    settings: SettingsDep,
    next_url: Annotated[str | None, Query(alias="next")] = None,
) -> RedirectResponse:
    """Authorization Code with PKCE.

    ``next`` is validated against the configured origins before it is stored.
    An unchecked ``next`` is an open redirect: a link that goes to our sign-in
    and lands on somebody else's page, wearing our domain in the address bar
    for the part that matters.
    """
    target = oidc.begin(
        provider,
        redirect_uri=str(request.url_for("callback")),
        next_url=_safe_next(next_url, settings),
        settings=settings,
    )
    return RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)


@router.get(
    "/callback",
    name="callback",
    status_code=status.HTTP_302_FOUND,
    summary="Finish a federated sign-in",
)
def callback(
    session: SessionDep,
    settings: SettingsDep,
    state: Annotated[str, Query()],
    code: Annotated[str, Query()],
) -> RedirectResponse:
    _require_store(session)
    row, next_url = oidc.complete(state, code, session=session, settings=settings)

    redirect = RedirectResponse(
        url=next_url or settings.cors_origins_list[0], status_code=status.HTTP_302_FOUND
    )
    redirect.set_cookie(
        SESSION_COOKIE,
        row.id,
        max_age=settings.session_ttl_s,
        # HttpOnly: a session id readable from JavaScript is a session id an
        # XSS bug exfiltrates. SameSite=lax so the cookie survives the
        # provider's redirect back to us but is not sent on a cross-site POST.
        httponly=True,
        samesite="lax",
        secure=settings.environment != "development",
        path="/",
    )
    return redirect


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="End this session")
def logout(request: Request, session: SessionDep) -> Response:
    """Revoke server-side and clear the cookie.

    Both: clearing only the cookie leaves a session id that still works for
    anyone who copied it, which is exactly the case someone logs out to
    prevent.
    """
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id and session is not None:
        Repositories(session).sessions.revoke(session_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.post(
    "/logout-everywhere",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End every session for this user",
)
def logout_everywhere(caller: CallerDep, session: SessionDep) -> Response:
    """What a person clicks after losing a laptop."""
    _require_store(session)
    if caller.is_anonymous:
        raise NotAuthenticated("nobody is signed in")
    count = Repositories(session).sessions.revoke_all(caller.principal_id)
    log.info("all sessions revoked", user=caller.principal_id, sessions=count)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


# ---- personal access tokens ---------------------------------------------


@router.get("/tokens", response_model=list[TokenSummary], summary="Your tokens")
def list_tokens(caller: CallerDep, session: SessionDep) -> list[TokenSummary]:
    _require_store(session)
    if caller.is_anonymous:
        raise NotAuthenticated("sign in to manage tokens")
    return [
        TokenSummary(**tokens.describe(row))
        for row in Repositories(session).tokens.for_user(caller.principal_id)
    ]


@router.post(
    "/tokens",
    response_model=IssuedTokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a token. The only time you will see it.",
)
def create_token(
    body: TokenRequest,
    caller: CallerDep,
    session: SessionDep,
) -> IssuedTokenResponse:
    """Issue a personal access token, within the caller's own permissions.

    A scope the user's role does not carry is refused rather than dropped. A
    silently narrowed token is the worst outcome available: the holder believes
    they have a working credential, every call fails in a way that looks like a
    bug, and nothing says why.
    """
    _require_store(session)
    if caller.is_anonymous:
        raise NotAuthenticated("sign in to issue a token")

    repos = Repositories(session)
    user = repos.users.get(caller.principal_id)
    if user is None:  # pragma: no cover - the caller resolved from this row
        raise NotAuthenticated("this account no longer exists")

    issued = tokens.mint(
        repos,
        user,
        name=body.name,
        scopes=tuple(body.scopes or tokens.DEFAULT_SCOPES),
        ttl=timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    return IssuedTokenResponse(
        token=issued.token,
        **tokens.describe(issued.row),
        warning=(
            "This is the only time the token is shown. Only a hash is stored, so it cannot "
            "be recovered — issue another if you lose it."
        ),
    )


@router.delete(
    "/tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a token, permanently",
)
def revoke_token(token_id: str, caller: CallerDep, session: SessionDep) -> Response:
    """No un-revoke. A token someone revoked because they think it leaked must
    stay dead, and an undo button is a way to reinstate a compromised
    credential by accident."""
    _require_store(session)
    if caller.is_anonymous:
        raise NotAuthenticated("sign in to manage tokens")

    repos = Repositories(session)
    row = repos.tokens.get(token_id)
    if row is None or row.user_id != caller.principal_id:
        # The same answer whether it is somebody else's token or none at all:
        # confirming that a token id exists tells an attacker their guess was
        # right.
        raise NotEntitled("no such token on this account", token_id=token_id)

    repos.tokens.revoke(token_id)
    repos.audit.record(
        action="token.revoke",
        outcome="granted",
        resource_kind="token",
        resource_id=token_id,
        principal_id=caller.principal_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _safe_next(next_url: str | None, settings: Any) -> str | None:
    """Only a path, or an origin we configured.

    An unchecked ``next`` is an open redirect: a link that goes to our sign-in
    and lands on somebody else's page, having worn our domain in the address
    bar for the part the user was paying attention to.
    """
    if not next_url:
        return None
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    if any(next_url.startswith(origin) for origin in settings.cors_origins_list):
        return next_url
    log.warning("refused an off-site next URL", next_url=next_url)
    return None
