"""The REST client the MCP server uses (WP-10.2).

`services/mcp` **may not contain SPARQL and talks to the REST API only**
(architecture boundary table, PRD principle 9). That is not a style rule. It is
the mechanism behind PRD §F9's most important requirement:

> Every response grounded strictly in real catalog metadata. **The server never
> fabricates a dataset or a field.** Treat this as the single most important
> correctness property; a plausible fabricated dataset is worse than no answer.

A server that could read the store could also compose, summarise and infer, and
each of those is a place where something plausible and untrue can be produced.
A server that can only forward what the API returned cannot fabricate a
dataset, because it has no way to make one.

**In-process transport, same code path.** Tests and single-node deployments
mount the FastAPI app directly through httpx's ASGI transport rather than
crossing a socket. That is not a mock: it is the same application, the same
routers, the same entitlement, reached without a listening port.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from datahub.logging import get_logger

log = get_logger(__name__)

#: PRD §F9: *payload cap on every read tool so bulk data can never enter the
#: agent's context. Default 100 KB per response.*
DEFAULT_PAYLOAD_CAP = 100 * 1024

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class ApiError(RuntimeError):
    """A non-2xx from the API, carrying the status so a tool can honour it.

    The status matters: PRD §F9 requires an out-of-tier tool to return 403
    rather than to be absent, and a client that flattened every failure into
    one exception could not tell 403 from 404 — which is exactly the
    distinction the entitlement design spent M6 making indistinguishable to
    *callers* and keeps visible to *the server*.
    """

    def __init__(self, status: int, detail: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail
        self.payload = payload or {}


@dataclass
class ApiClient:
    """A thin, identity-propagating HTTP client for the OpenGrid API."""

    base_url: str = "http://localhost:8000"
    token: str | None = None
    #: An httpx transport, for a deployment that mounts the API in-process.
    transport: httpx.BaseTransport | None = None
    #: A ready-made httpx client, used as-is.
    #:
    #: The escape hatch that matters: an ASGI app cannot be reached from a
    #: *synchronous* httpx client through a transport alone — the app is async
    #: and the transport would hand back an async stream — so the object that
    #: knows how to bridge the two (Starlette's ``TestClient``, or anything
    #: else built on a blocking portal) has to be passed whole. It is also
    #: where a caller puts retries, a proxy or a custom auth flow.
    client: httpx.Client | None = None
    payload_cap: int = DEFAULT_PAYLOAD_CAP
    _client: httpx.Client | None = None
    _owned: bool = True

    def __post_init__(self) -> None:
        if self.client is not None:
            self._client = self.client
            self._owned = False
            self._client.headers.update(self._headers())
            return
        self._client = httpx.Client(
            base_url=self.base_url.rstrip("/"),
            transport=self.transport,
            timeout=TIMEOUT,
            headers=self._headers(),
            follow_redirects=False,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "opengrid-datahub-mcp/1.0"}
        if self.token:
            # Identity propagates through every call (PRD §F9). The token is
            # the caller's, never the server's: there is no service account
            # here to escalate to.
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    # -- verbs -------------------------------------------------------------

    def get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=_clean(params))

    def post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, json=json or {})

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        assert self._client is not None
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise ApiError(response.status_code, _detail(response), _json(response))
        return _json(response)

    def raw(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """The response object, for the one caller that needs the headers.

        `/download` is a 302 and the SDK follows it itself; a client that
        auto-followed would put the dataset's bytes in this process, which is
        the one thing the control plane must never do.
        """
        assert self._client is not None
        return self._client.request(method, path, **kwargs)

    def close(self) -> None:
        # Only what this object opened. Closing a client the caller passed in
        # would break the next thing that used it, from a place with no
        # obvious connection to the close.
        if self._client is not None and self._owned:
            self._client.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"detail": response.text[:500]}


def _detail(response: httpx.Response) -> str:
    body = _json(response)
    if isinstance(body, dict):
        return str(body.get("detail") or body.get("title") or response.reason_phrase)
    return response.reason_phrase


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    """Drop None-valued params.

    httpx serialises ``None`` as an empty string, which the API reads as "the
    caller asked for the empty value" rather than "the caller did not ask" —
    the same absent-versus-empty confusion PRD principle 2 is about, arriving
    through a query string.
    """
    out: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        out[key] = value
    return out


__all__ = ["DEFAULT_PAYLOAD_CAP", "ApiClient", "ApiError"]
