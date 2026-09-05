"""The FastAPI application (WP-4.3).

PRD §F8: *REST, OpenAPI 3.1, the canonical contract everything else calls.*
Everything else means the web UI, the Python SDK and the MCP server — none of
them reaches past this into the store, so a rule enforced here is enforced for
all three.

Two properties this module is responsible for:

**Control plane, not data plane.** The API returns small cacheable JSON and
never streams bytes. ``/download`` is a 302 and ``/access-plan`` returns a plan;
neither ever proxies a file. A catalog that starts serving data becomes an
egress bill and a bandwidth bottleneck, and stops being a catalog.

**One error shape.** RFC 9457 problem details, from one handler, so a client
writes one error path rather than one per endpoint. Every deliberate failure in
the system derives from :class:`~datahub.errors.DataHubError` and carries its
own status; an undeliberate one becomes a 500 whose detail says nothing about
the internals.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from datahub.api import deps
from datahub.api.deps import CallerDep
from datahub.api.ratelimit import WINDOW_S, RateLimiter, exempt
from datahub.api.routers import allowlists, auth, concepts, datasets, health, intake, review
from datahub.config import Settings, get_settings
from datahub.errors import DataHubError, RateLimited
from datahub.logging import configure_logging, get_logger
from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = get_logger(__name__)

DESCRIPTION = """\
The OpenGrid Data Hub API. A control plane for finding grid-modelling datasets
and working out how to get at them.

**This API never returns data.** It returns metadata, and access plans that say
where the data is and how to read it. `/download` is a redirect to the source;
`/access-plan` is a document. Nothing here proxies bytes.

**Absent means "not captured", never "no source".** A field missing from a
record is a gap in what has been catalogued, not a statement that the dataset
lacks it. Completeness levels say how much of a record has been filled in, and
the three quality facets are graded independently and never combined.
"""

TAGS: list[dict[str, Any]] = [
    {"name": "datasets", "description": "Search and read catalog records."},
    {"name": "concepts", "description": "The SKOS vocabulary and the ten data domains."},
    {"name": "intake", "description": "Submit a dataset, or report a problem with one."},
    {"name": "auth", "description": "Signing in, and the tokens that stand in for it."},
    {
        "name": "allowlists",
        "description": (
            "Who may see a restricted dataset. Managed by its custodian; OpenGrid stores "
            "and enforces the list and never arbitrates its contents."
        ),
    },
    {
        "name": "review",
        "description": (
            "The steward queue. Highest-leverage records first: most inbound links, then most "
            "complete."
        ),
    },
    {"name": "service", "description": "Health and readiness."},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
    configure_logging()
    settings = get_settings()
    log.info(
        "api starting",
        environment=settings.environment,
        graph=str(settings.graph_backend),
        search=str(settings.search_backend),
    )
    yield
    # Flush the store on the way out: the rdflib backend writes N-Quads on
    # flush, and a dev server killed without one loses the session's writes.
    deps.reset()
    log.info("api stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="OpenGrid Data Hub",
        version="1.0.0",
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        # OpenAPI 3.1 rather than 3.0, because 3.0's schema dialect cannot
        # express `null` in a union and every optional field in this API is one
        # — under 3.0 a generated client makes them all required or all Any.
        separate_input_output_schemas=False,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        # So a browser client can page without a second round trip to count.
        expose_headers=["X-Request-Id", "X-Total-Count"],
    )

    _register_middleware(app)
    _register_errors(app)

    # The rate limit is a router dependency rather than middleware, because
    # middleware runs before FastAPI resolves dependencies — so it would see no
    # caller and charge every authenticated request to its IP address. That
    # errs safe (the anonymous budget is the tightest) and is still wrong: PRD
    # §F9 requires agent traffic to get a *larger* budget, and an agent
    # throttled to the anonymous rate cannot work.
    limited = [Depends(rate_limit)]

    for router in (
        datasets.router,
        concepts.router,
        intake.router,
        auth.router,
        allowlists.router,
        review.router,
        health.router,
    ):
        app.include_router(router, prefix="/v1", dependencies=limited)

    return app


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

LIMITER = RateLimiter()


def rate_limit(request: Request, response: Response, caller: CallerDep) -> None:
    """Count this request against the caller's budget.

    A dependency rather than middleware, so it runs *after* the caller is
    resolved and an agent is charged to its own, larger budget (PRD §F9: agent
    traffic is several times chattier than human traffic, and a limit that made
    agentic use impossible would just push it to scraping the UI).

    The headers go on every response and not only on a 429: a client that can
    see it has four requests left paces itself, and one that finds out by being
    refused has already failed a user's request.
    """
    if exempt(request.url.path):
        return

    decision = LIMITER.check(
        principal_id=caller.principal_id,
        is_agent=caller.is_agent,
        client_host=request.client.host if request.client else None,
    )
    response.headers.update(decision.headers)
    if not decision.allowed:
        log.info("rate limited", bucket=decision.bucket, limit=decision.limit)
        raise RateLimited(
            f"more than {decision.limit} requests in the last minute from this client",
            limit=decision.limit,
            window_seconds=WINDOW_S,
            retry_after_seconds=decision.reset_in,
        )


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


def _register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Any:
        """A request id on every response, and one log line per request.

        The id is echoed in the ``X-Request-Id`` header and in the problem
        detail of any error, so a user reporting "it broke" can hand over a
        string that finds the log line.
        """
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        started = time.perf_counter()

        response = await call_next(request)

        duration_ms = (time.perf_counter() - started) * 1000
        response.headers["X-Request-Id"] = request_id
        caller = getattr(request.state, "caller", None)
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=round(duration_ms, 1),
            request_id=request_id,
            principal=getattr(caller, "principal_id", None),
        )
        return response


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def _problem(
    request: Request,
    *,
    status: int,
    title: str,
    detail: str | None = None,
    **extra: Any,
) -> JSONResponse:
    """RFC 9457, with the request id attached."""
    body: dict[str, Any] = {
        "type": f"https://schema.opengrid.org/errors/{extra.pop('code', 'error')}",
        "title": title,
        "status": status,
        "instance": str(request.url.path),
        "requestId": getattr(request.state, "request_id", None),
    }
    if detail:
        body["detail"] = detail
    body.update({k: v for k, v in extra.items() if v is not None})
    return JSONResponse(status_code=status, content=body, media_type="application/problem+json")


def _register_errors(app: FastAPI) -> None:
    @app.exception_handler(DataHubError)
    async def datahub_error(request: Request, exc: DataHubError) -> JSONResponse:
        """Every deliberate failure. The class carries its own status, so a new
        error type gets the right code without touching this function."""
        payload = exc.to_payload()
        response = _problem(
            request,
            status=exc.status_code,
            title=exc.message,
            code=payload.pop("code", exc.code),
            **{k: v for k, v in payload.items() if k not in ("message", "error")},
        )
        if retry_after := payload.get("retry_after_seconds"):
            # A 429 with no Retry-After teaches a client to hammer.
            response.headers["Retry-After"] = str(retry_after)
        return response

    @app.exception_handler(RequestValidationError)
    async def bad_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(
            request,
            status=422,
            title="the request did not validate",
            code="bad_request",
            errors=[
                {"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]}
                for e in exc.errors()
            ][:20],
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(
            request,
            status=exc.status_code,
            title=str(exc.detail),
            code="http_error",
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
        """The detail says nothing about the internals.

        A stack trace in a 500 body is a gift to whoever is probing the service,
        and it is no use to the caller either. The request id is what connects
        their report to the log line that does have the trace.
        """
        log.exception(
            "unhandled error",
            path=request.url.path,
            request_id=getattr(request.state, "request_id", None),
        )
        return _problem(
            request,
            status=500,
            title="internal error",
            detail="Something failed on our side. Quote the request id when reporting it.",
            code="internal_error",
        )


app = create_app()

__all__ = ["app", "create_app"]
