"""One process serving both the REST API and a remote MCP endpoint.

    uvicorn datahub.mcp.asgi:app

`/mcp`  streamable-HTTP MCP, the URL a Claude connector is pointed at
`/`     the REST API, unchanged

**Why one process.** PRD §F9 calls the MCP server *a thin client over the REST
API*, and it stays one: the tools reach the API through an in-process
transport, so nothing here bypasses a router, an entitlement check or a rate
limit. What it avoids is a second deployment whose only job is to forward
requests to the first — and, for a catalog that is read-only and anonymous, a
second thing to pay for and keep alive.

**Why no authentication.** The catalog's anonymous surface is the product, not
a degraded mode: PRD §2 names the external evaluator — a regulator, an
intervenor — as the persona *most likely to be dropped during implementation
and the one that most differentiates this product*, and requires that
read-only quality and provenance inspection work with no account. So the MCP
endpoint is public, and a reader adds it by pasting one URL. The tier-gated
tool refuses per call with a message naming the tier, which is the behaviour
PRD §F9 already specifies for a caller who lacks one.

That is a decision about *this* deployment, not a property of the code. A
deployment that carries restricted metadata must not mount this app
unauthenticated: entitlement is resolved from the caller's token, and every
caller here is anonymous.
"""

from __future__ import annotations

from typing import Any

from datahub.logging import configure_logging, get_logger
from fastapi import FastAPI

log = get_logger(__name__)

#: Where the MCP endpoint is mounted.
#:
#: **Both `/mcp` and `/mcp/` work**, and that is deliberate. This comment used
#: to say a connector URL was "this plus a slash", which was true and useless:
#: `docs/mcp-deployment.md`, `fly.toml` and the setup issue all handed out the
#: slashless form anyway, and it did not merely redirect — it 404'd:
#:
#:     GET  /mcp   -> 404      GET  /mcp/  -> 405 (POST-only, correct)
#:     POST /mcp   -> 404      POST /mcp/  -> 200
#:
#: A trailing slash is not something a person pastes reliably, and the whole
#: promise of this endpoint is one URL and no account. Normalising it here is
#: one line; keeping four documents in agreement forever is not.
MCP_PATH = "/mcp"


class MountRootMiddleware:
    """Serve ``/mcp`` as well as ``/mcp/``.

    Middleware rather than anything inside the mounted app, because the fix has
    to happen *before* routing. Starlette's ``Mount`` builds the regex
    ``^/mcp(?P<path>/.*)$``, so a request to exactly ``/mcp`` never matches the
    mount — it falls through to the ``/`` mount behind it and the API answers
    404. Nothing inside the MCP app is reached, so nothing inside it can help.

    A redirect is the other option and is worse: a streamable-HTTP client that
    does not follow a 307 sees a status it cannot use, which is harder to
    diagnose than the 404 it would replace.
    """

    def __init__(self, app: Any, path: str) -> None:
        self.app = app
        self.path = path
        self.raw = (path + "/").encode()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and scope.get("path") == self.path:
            scope = {**scope, "path": self.path + "/", "raw_path": self.raw}
        await self.app(scope, receive, send)


def build_app(**kwargs: Any) -> FastAPI:
    """The API with an MCP endpoint mounted in front of it."""
    from datahub.api.app import create_app
    from datahub.mcp.client import ApiClient
    from datahub.mcp.server import create_server
    from datahub.mcp.tools import Tools, resolve_tier
    from starlette.testclient import TestClient

    api = create_app(**kwargs)

    # The bridge `datahub.mcp.client` documents: *an ASGI app cannot be reached
    # from a synchronous httpx client through a transport alone — the app is
    # async and the transport would hand back an async stream — so the object
    # that knows how to bridge the two has to be passed whole.*
    #
    # `TestClient` is that object. Its name is unhelpful here and the mechanism
    # is not test-only: it is a blocking portal over an ASGI app, from
    # Starlette's public API. FastMCP runs a synchronous tool in a worker
    # thread, so the portal never blocks the event loop the API is served on.
    #
    # The alternative is a loopback HTTP call to the process's own port, which
    # works and costs a round trip through the kernel for every tool call, and
    # makes the server's own readiness a precondition for answering.
    inprocess = TestClient(api, base_url="http://api.internal")
    client = ApiClient(client=inprocess)
    tools = Tools(client=client, tier=resolve_tier(client))

    mcp = create_server(tools=tools)
    # Stateless: every request carries its own context and no session is kept
    # between them. That is what makes the endpoint safe to scale to zero and
    # back, which is how it is deployed.
    mcp_app = mcp.http_app(path="/", stateless_http=True)

    # The mounted app has a lifespan of its own — the streamable-HTTP session
    # manager starts there. Mounting without adopting it yields a server that
    # accepts a connection and then fails every call.
    app = FastAPI(
        title="OpenGrid Data Hub",
        lifespan=mcp_app.lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.mount(MCP_PATH, mcp_app)
    app.mount("/", api)
    log.info("mcp endpoint mounted", path=MCP_PATH, tier=tools.tier)
    # Added last so it wraps the router: `add_middleware` installs above the
    # routing layer, which is the only place the rewrite can work.
    app.add_middleware(MountRootMiddleware, path=MCP_PATH)
    return app


def _create() -> FastAPI:
    configure_logging()
    return build_app()


app = _create()

__all__ = ["MCP_PATH", "app", "build_app"]
