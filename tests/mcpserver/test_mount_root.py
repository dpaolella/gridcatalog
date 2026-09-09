"""The connector URL people are actually given must work.

`docs/mcp-deployment.md:93`, `fly.toml`'s header comment and the deployment
setup issue all hand out `https://<host>/mcp`. That URL returned **404**:

    POST /mcp   -> 404      POST /mcp/  -> 200
    GET  /mcp   -> 404      GET  /mcp/  -> 405 (POST-only, correct)

Not a redirect a client could follow -- a flat 404. Starlette's `Mount` builds
the regex `^/mcp(?P<path>/.*)$`, so a request to exactly `/mcp` never matches
the mount at all; it falls through to the `/` mount behind it and the API
answers 404. Nothing inside the MCP app is reached, which is why the fix is
middleware above the router rather than anything in the mounted app.

The endpoint's whole promise is one URL and no account. A trailing slash is not
something a person pastes reliably, and keeping four documents in agreement
about it forever is a worse bet than normalising the path once.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "regression-test", "version": "1"},
    },
}
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def client():
    from datahub.mcp.asgi import build_app

    with TestClient(build_app(), raise_server_exceptions=False) as c:
        yield c


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_the_connector_url_answers_with_and_without_a_trailing_slash(client, path: str) -> None:
    response = client.post(path, json=INITIALIZE, headers=HEADERS, follow_redirects=False)
    assert response.status_code == 200, (
        f"POST {path} returned {response.status_code}; this is the URL a reader pastes "
        f"into a connector, and both spellings of it are handed out in the docs"
    )
    assert "serverInfo" in response.text, f"{path} answered but did not initialize a session"


def test_the_api_still_answers_underneath(client) -> None:
    """The rewrite must not shadow the API mounted at `/`.

    The middleware matches one exact path. If it ever widened to a prefix it
    would swallow `/v1/...`, and the failure would look like the whole API
    disappearing.
    """
    assert client.get("/v1/health").status_code == 200
    assert client.get("/v1/datasets?limit=1").status_code == 200


def test_an_unrelated_path_is_untouched(client) -> None:
    assert client.get("/mcpx").status_code == 404
    assert client.get("/not-mcp").status_code == 404
