"""Turning an HTTP request into a search query.

**This module is one of the two places entitlement is enforced** (ADR-0006).
Every search request the API issues is built here, and
:class:`~datahub.api.search.backend.SearchRequest` cannot be constructed
without an :class:`~datahub.api.search.backend.Entitlement`. There is no code
path that reaches the backend without one, and
``tests/api/test_entitlement_matrix.py`` enumerates the routes from the OpenAPI
schema rather than from a hand-written list, so a new endpoint that bypasses
this fails rather than quietly leaking.

The other half of the discipline lives in the backends: the visibility
predicate is compiled into the query rather than applied to the result set, so
a record the caller may not see contributes to no hit count, no facet count and
no page total.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from datahub.api.search.backend import (
    BBoxFilter,
    Entitlement,
    RangeFilter,
    SearchRequest,
    SortSpec,
)
from datahub.api.search.document import FACET_FIELDS, SORT_FIELDS
from datahub.errors import DataHubError

#: Facets returned when the caller does not ask for specific ones. Chosen to be
#: the filters the list view renders by default; asking for all twenty on every
#: request costs more than it returns.
DEFAULT_FACETS: tuple[str, ...] = (
    "data_domain",
    "provenance_class",
    "license",
    "access_restriction",
    "format",
    "completeness_level",
    "anonymous_access",
    "bulk_download",
)

MAX_LIMIT = 200
DEFAULT_LIMIT = 20
MAX_QUERY_LENGTH = 512

#: Filter values are matched exactly, so they need no escaping — but a value of
#: unbounded length is a denial-of-service vector on the in-process backend and
#: a mapping explosion on OpenSearch.
MAX_FILTER_VALUE_LENGTH = 256
MAX_FILTER_VALUES = 50

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T[\d:.+\-Z]+)?$")


class BadSearchRequest(DataHubError):
    """The caller asked for something the query language does not express.

    A 400, not a silently narrowed result set. PRD §F10: "a silently truncated
    result set is a correctness bug that looks like a UX choice."
    """

    status_code = 400
    code = "bad_search_request"


@dataclass(frozen=True, slots=True)
class SearchParams:
    """The wire form of a search, before entitlement is attached.

    Deliberately a separate type from :class:`SearchRequest`: this is what a
    caller sends, that is what the backend runs, and the transition between
    them is where the entitlement clause is added. Collapsing the two would put
    an ``entitlement`` field on the HTTP surface, which is precisely the thing
    that must never be caller-supplied.
    """

    q: str | None = None
    filters: dict[str, list[str]] | None = None
    bbox: tuple[float, float, float, float] | None = None
    temporal_start: datetime | None = None
    temporal_end: datetime | None = None
    completeness_min: int | None = None
    sort: str | None = None
    offset: int = 0
    limit: int = DEFAULT_LIMIT
    facets: tuple[str, ...] | None = None
    ids: tuple[str, ...] | None = None
    include_unconfirmed: bool = False


def build(params: SearchParams, entitlement: Entitlement) -> SearchRequest:
    """Compile a caller's parameters and their entitlement into one query.

    The entitlement argument is required and has no default. That is the whole
    design: a caller who forgets it gets a ``TypeError`` at import-test time
    rather than an unfiltered result set in production.
    """
    _check_text(params.q)
    filters = _check_filters(params.filters or {})
    ranges = _ranges(params)

    if params.include_unconfirmed and not (
        entitlement.is_steward or entitlement.include_unconfirmed
    ):
        # A caller asking for drafts who is not entitled to them gets an error,
        # not a quietly confirmed-only result set. Silent narrowing here would
        # make a steward tool look broken rather than unauthorised.
        raise BadSearchRequest(
            "unconfirmed records are visible to stewards only",
            hint="drop include_unconfirmed, or authenticate as a steward",
        )

    return SearchRequest(
        entitlement=entitlement,
        q=params.q.strip() if params.q else None,
        filters=filters,
        ranges=ranges,
        bbox=_bbox(params.bbox),
        temporal=_temporal(params),
        sort=_sort(params.sort),
        offset=max(0, params.offset),
        limit=_limit(params.limit),
        facets=params.facets if params.facets is not None else DEFAULT_FACETS,
        ids=params.ids,
    )


def build_for_ids(ids: tuple[str, ...], entitlement: Entitlement) -> SearchRequest:
    """A lookup by id that still goes through the entitlement clause.

    Used by the links and the MCP surfaces, which know which datasets they want
    and must still not reveal one the caller may not see.
    """
    return SearchRequest(
        entitlement=entitlement,
        ids=ids,
        limit=min(len(ids) or 1, MAX_LIMIT),
        facets=(),
    )


# ---------------------------------------------------------------------------
# Parameter parsing and validation
# ---------------------------------------------------------------------------


def parse_filters(raw: dict[str, list[str]] | None) -> dict[str, list[str]]:
    """Read ``?data_domain=DD5&data_domain=DD1&license=CC-BY-4.0`` into filters.

    Repeated keys are OR within a field; distinct keys are AND across fields.
    That is the convention every faceted search uses, and departing from it
    would surprise a user in the direction of returning too little.
    """
    if not raw:
        return {}
    out: dict[str, list[str]] = {}
    unknown: list[str] = []
    for key, values in raw.items():
        name = key.strip()
        if name not in FACET_FIELDS:
            unknown.append(name)
            continue
        out[name] = [v for v in values if v != ""]
    if unknown:
        raise BadSearchRequest(
            f"unknown filter field(s): {sorted(unknown)}",
            available=sorted(FACET_FIELDS),
        )
    return {k: v for k, v in out.items() if v}


def parse_bbox(raw: str | None) -> tuple[float, float, float, float] | None:
    """``?bbox=minLon,minLat,maxLon,maxLat``, in EPSG:4326."""
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise BadSearchRequest(
            "bbox takes four comma-separated numbers: minLon,minLat,maxLon,maxLat",
            got=raw,
        )
    try:
        min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    except ValueError as exc:
        raise BadSearchRequest(f"bbox values must be numbers: {raw}") from exc
    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
        raise BadSearchRequest("bbox longitudes must lie between -180 and 180", got=raw)
    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise BadSearchRequest("bbox latitudes must lie between -90 and 90", got=raw)
    if min_lon > max_lon or min_lat > max_lat:
        raise BadSearchRequest(
            "bbox is inverted: minimum must not exceed maximum. An antimeridian-"
            "crossing box is not supported; send two boxes.",
            got=raw,
        )
    return (min_lon, min_lat, max_lon, max_lat)


def parse_datetime(raw: str | None, *, field: str) -> datetime | None:
    if not raw:
        return None
    if not _ISO_DATE.match(raw):
        raise BadSearchRequest(f"{field} must be an ISO 8601 date or date-time", got=raw)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BadSearchRequest(f"{field} is not a valid date: {raw}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_facets(raw: str | None) -> tuple[str, ...] | None:
    """``?facets=data_domain,license`` — or ``none`` to ask for none at all."""
    if raw is None:
        return None
    if raw.strip().lower() in ("none", ""):
        return ()
    names = [n.strip() for n in raw.split(",") if n.strip()]
    unknown = [n for n in names if n not in FACET_FIELDS]
    if unknown:
        raise BadSearchRequest(
            f"unknown facet(s): {sorted(unknown)}", available=sorted(FACET_FIELDS)
        )
    return tuple(names)


def _check_text(q: str | None) -> None:
    if q and len(q) > MAX_QUERY_LENGTH:
        raise BadSearchRequest(
            f"query text is limited to {MAX_QUERY_LENGTH} characters", length=len(q)
        )


def _check_filters(filters: dict[str, list[str]]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for name, values in filters.items():
        if name not in FACET_FIELDS:
            raise BadSearchRequest(f"unknown filter field: {name}")
        if len(values) > MAX_FILTER_VALUES:
            raise BadSearchRequest(
                f"{name} takes at most {MAX_FILTER_VALUES} values", got=len(values)
            )
        cleaned: list[Any] = []
        for value in values:
            text = str(value)
            if len(text) > MAX_FILTER_VALUE_LENGTH:
                raise BadSearchRequest(f"{name} filter value is too long", length=len(text))
            cleaned.append(_coerce(name, text))
        out[name] = cleaned
    return out


def _coerce(name: str, value: str) -> Any:
    """Turn a query-string value into the type the document field holds.

    ``?completeness_level=3`` arrives as a string and has to match an integer,
    and ``?anonymous_access=true`` has to match a boolean. Comparing them as
    strings would return nothing, which looks like "no results" rather than
    like a bug.
    """
    lowered = value.strip().lower()
    if name in ("anonymous_access", "bulk_download", "reference_only"):
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
        raise BadSearchRequest(f"{name} takes true or false", got=value)
    if name == "completeness_level":
        if lowered not in ("1", "2", "3"):
            raise BadSearchRequest("completeness_level is 1, 2 or 3", got=value)
        return int(lowered)
    return value


def _ranges(params: SearchParams) -> dict[str, RangeFilter]:
    ranges: dict[str, RangeFilter] = {}
    if params.completeness_min is not None:
        if params.completeness_min not in (1, 2, 3):
            raise BadSearchRequest("completeness_min is 1, 2 or 3", got=params.completeness_min)
        ranges["completeness_level"] = RangeFilter(gte=params.completeness_min)
    return ranges


def _bbox(raw: tuple[float, float, float, float] | None) -> BBoxFilter | None:
    return BBoxFilter(*raw) if raw else None


def _temporal(params: SearchParams) -> RangeFilter | None:
    if params.temporal_start is None and params.temporal_end is None:
        return None
    if (
        params.temporal_start
        and params.temporal_end
        and params.temporal_start > params.temporal_end
    ):
        raise BadSearchRequest("temporal_start is after temporal_end")
    return RangeFilter(gte=params.temporal_start, lte=params.temporal_end)


def _sort(raw: str | None) -> tuple[SortSpec, ...]:
    """``?sort=-modified,title`` — a leading minus is descending.

    Sorting by a quality facet is deliberately not offered. The three facets are
    independent and there is no composite (ADR-0007); a "sort by quality" would
    have to invent one, and an ordering is an implicit composite whether or not
    a field for it exists.
    """
    if not raw:
        return ()
    specs: list[SortSpec] = []
    for token in (t.strip() for t in raw.split(",")):
        if not token:
            continue
        descending = token.startswith("-")
        name = token.lstrip("-+")
        if name not in SORT_FIELDS:
            raise BadSearchRequest(f"unknown sort field: {name}", available=sorted(SORT_FIELDS))
        specs.append(SortSpec(field=name, descending=descending))
    return tuple(specs)


def _limit(raw: int) -> int:
    if raw < 1:
        raise BadSearchRequest("limit must be at least 1", got=raw)
    if raw > MAX_LIMIT:
        raise BadSearchRequest(
            f"limit is capped at {MAX_LIMIT}; page through with offset instead",
            got=raw,
        )
    return raw
