"""``DataHub`` — the SDK's entry point (WP-10.1).

PRD §F9's target is *from zero to first dataset pull in one line*, so the
constructor takes no required arguments and every method returns something you
can act on immediately.

The SDK talks to the REST API and nothing else. It holds no store client, no
SPARQL, and — importantly — no second copy of the entitlement rules. A second
copy would eventually disagree with the first, and the one that disagreed would
be the one a user was standing behind when they published a figure.
"""

from __future__ import annotations

import os
from typing import Any, Self

import httpx

from opengrid.errors import DataHubError, NotEntitled, NotFound
from opengrid.models import AccessPlan, Dataset, Distribution, Field, Link, ResultSet

DEFAULT_BASE_URL = "https://api.opengrid.org"
TIMEOUT = httpx.Timeout(60.0, connect=10.0)

#: Search parameters the API accepts, mapped from the names a user would reach
#: for. ``domain`` and ``region`` are what PRD §F9's example writes; the API
#: calls them ``data_domain`` and resolves places through ``place``.
_ALIASES: dict[str, str] = {
    "domain": "data_domain",
    "domains": "data_domain",
    "region": "place",
    "concepts": "concept",
    "license": "license",
    "format": "format",
    "free": "anonymous_access",
}


class DataHub:
    """A connection to an OpenGrid Data Hub.

    >>> hub = DataHub()
    >>> ds = hub.search(domain="DD5", region="DE")[0]      # doctest: +SKIP
    >>> da = ds.open(time=slice("2019-01", "2019-12"))     # doctest: +SKIP
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("OPENGRID_API_URL") or DEFAULT_BASE_URL).rstrip(
            "/"
        )
        # The token comes from the environment by default, so a script that is
        # committed to a repository does not carry one. There is no config file
        # search and no keyring: one obvious place, documented.
        self.token = token or os.environ.get("OPENGRID_TOKEN")

        # ``client`` is used as-is when given: it is where a caller puts
        # retries, a proxy or a custom auth flow, and it is the only way to
        # reach an ASGI app from a synchronous client — the app is async, so
        # the object that bridges the two has to be passed whole rather than
        # as a transport.
        self._owned = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            transport=transport,
            timeout=timeout or TIMEOUT,
            follow_redirects=False,
        )
        self._client.headers.update(self._headers())
        self._params_cache: set[str] | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "opengrid-python/1.0"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    # -- search and read ---------------------------------------------------

    def search(
        self, q: str | None = None, *, limit: int = 20, offset: int = 0, **filters: Any
    ) -> ResultSet:
        """Search the catalog, returning datasets rather than dicts.

        Filter names are passed through with a few aliases for the words a
        modeller actually uses (``domain``, ``region``). An unknown filter is a
        ``DataHubError`` naming it rather than a silently ignored argument —
        a typo that quietly widens a search returns results the caller then
        trusts.
        """
        params: dict[str, Any] = {"q": q, "limit": limit, "offset": offset}
        for key, value in filters.items():
            params[_ALIASES.get(key, key)] = value
        self._check_filters(params)

        body = self._get("/v1/datasets", params)
        return ResultSet(
            datasets=[Dataset.from_payload(item, self) for item in body.get("results", [])],
            total=int(body.get("total", 0)),
            offset=offset,
            limit=limit,
        )

    def _check_filters(self, params: dict[str, Any]) -> None:
        """Refuse a filter the API does not have.

        The API ignores query parameters it does not recognise, as HTTP APIs
        do. That is right for HTTP and wrong for a client library: a mistyped
        filter silently widens the search, and the caller trusts the results.

        The accepted names come from the API's own OpenAPI document, so this
        cannot drift from what the API actually takes. If the document is
        unreachable the check is skipped — failing a search because a schema
        fetch hiccuped would be a worse bug than the one being prevented.
        """
        known = self._search_params()
        if known is None:
            return
        unknown = sorted(set(params) - known)
        if unknown:
            raise DataHubError(
                f"unknown search filter(s): {', '.join(unknown)}. "
                f"Accepted: {', '.join(sorted(known))}."
            )

    def _search_params(self) -> set[str] | None:
        if self._params_cache is None:
            try:
                document = self._get("/openapi.json")
                operation = document["paths"]["/v1/datasets"]["get"]
                self._params_cache = {p["name"] for p in operation.get("parameters", [])}
            except (DataHubError, httpx.HTTPError, KeyError, TypeError):
                # Named rather than blind, but still every way this can fail:
                # the endpoint refusing, the network dropping, or a document
                # shaped differently from the one this code expects. All three
                # mean "cannot check", and none of them should stop a search.
                return None
        return self._params_cache

    def get(self, dataset_id: str) -> Dataset:
        """One record. Raises :class:`NotFound` if it is absent — or hidden."""
        return Dataset.from_payload(self._get(f"/v1/datasets/{dataset_id}"), self)

    def fields(self, dataset_id: str) -> list[Field]:
        body = self._get(f"/v1/datasets/{dataset_id}/schema")
        return [Field.from_payload(f) for f in body.get("fields", [])]

    def distributions(self, dataset_id: str) -> list[Distribution]:
        body = self._get(f"/v1/datasets/{dataset_id}/distributions")
        # A bare list today, an envelope if it ever grows one. Both, because
        # the difference is one refactor and the failure would be an
        # AttributeError in a user's script.
        items = body if isinstance(body, list) else body.get("distributions", [])
        return [Distribution.from_payload(d) for d in items]

    def links(self, dataset_id: str) -> list[Link]:
        body = self._get(f"/v1/datasets/{dataset_id}/links")
        return [Link.from_payload(item) for item in body.get("links", [])]

    def domains(self) -> list[dict[str, Any]]:
        return self._get("/v1/domains").get("domains", [])

    def whoami(self) -> dict[str, Any]:
        return self._get("/v1/auth/me")

    # -- access ------------------------------------------------------------

    def access_plan(
        self,
        dataset_id: str,
        *,
        distribution_id: str | None = None,
        time: Any = None,
        bbox: list[float] | None = None,
        variables: list[str] | None = None,
    ) -> AccessPlan:
        """Ask how to read a dataset.

        ``time`` accepts a ``slice`` because that is what a user writing
        ``ds.open(time=slice("2019-01", "2019-12"))`` already has in their
        hand, and converting it here rather than at the call site is the
        difference between one line and three.
        """
        start, end = _time_bounds(time)
        body = self._post(
            f"/v1/datasets/{dataset_id}/access-plan",
            {
                "distribution_id": distribution_id,
                "time_start": start,
                "time_end": end,
                "bbox": bbox,
                "variables": variables or [],
            },
        )
        return AccessPlan.from_payload(body)

    def open(self, dataset_id: str, **slice_spec: Any) -> Any:
        """Fetch a plan and execute it in this process."""
        from opengrid.readers import execute

        return execute(self.access_plan(dataset_id, **slice_spec))

    def download_url(self, dataset_id: str) -> str:
        """The redirect target, without following it.

        Without following it deliberately: this is a control-plane client, and
        a method that returned bytes would make every script that called it
        hold a dataset in memory it did not ask for.
        """
        # ``follow_redirects=False`` per request, not just on the client: a
        # caller may pass a client of their own that follows by default, and
        # following this one would pull the dataset into this process — the one
        # thing a control-plane client must never do.
        response = self._client.get(f"/v1/datasets/{dataset_id}/download", follow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308):
            return response.headers["location"]
        _raise_for(response)
        return response.json().get("location", "")

    # -- plumbing ----------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self._client.get(path, params=_clean(params or {}))
        _raise_for(response)
        return response.json()

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        response = self._client.post(path, json=_clean(body))
        _raise_for(response)
        return response.json()

    def close(self) -> None:
        # Only what this object opened. Closing a caller's client would break
        # the next thing that used it, from a place with no obvious connection.
        if self._owned:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"DataHub({self.base_url!r}, authenticated={bool(self.token)})"


def _raise_for(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    try:
        body = response.json()
    except ValueError:
        body = {}
    detail = body.get("detail") or body.get("title") or response.reason_phrase

    if response.status_code == 404:
        raise NotFound(str(detail), status=404)
    if response.status_code in (401, 403):
        raise NotEntitled(str(detail), status=response.status_code)
    raise DataHubError(str(detail), status=response.status_code, body=body)


def _time_bounds(time: Any) -> tuple[str | None, str | None]:
    if time is None:
        return None, None
    if isinstance(time, slice):
        return _iso(time.start), _iso(time.stop)
    if isinstance(time, (tuple, list)) and len(time) == 2:
        return _iso(time[0]), _iso(time[1])
    raise DataHubError(
        f"time must be a slice or a (start, end) pair, not {type(time).__name__}. "
        'For example: time=slice("2019-01", "2019-12").'
    )


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    """Drop None values.

    httpx renders ``None`` as an empty string, which the API reads as "the
    caller asked for the empty value" rather than "the caller did not ask" —
    absent versus empty, arriving through a query string.
    """
    return {k: v for k, v in params.items() if v is not None}


__all__ = ["DEFAULT_BASE_URL", "DataHub"]
