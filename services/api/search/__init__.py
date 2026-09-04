"""Search: the read path for the list view, facets and search-while-typing.

Nothing outside this package constructs a search client (ADR-0002), and nothing
constructs a :class:`SearchRequest` without an :class:`Entitlement` (ADR-0006).
"""

from __future__ import annotations

from datahub.api.search.backend import (
    BBoxFilter,
    Entitlement,
    FacetValue,
    Hit,
    InMemorySearchBackend,
    RangeFilter,
    SearchBackend,
    SearchRequest,
    SearchResponse,
    SortSpec,
)
from datahub.api.search.document import SearchDocument
from datahub.api.search.factory import make_search_backend

__all__ = [
    "BBoxFilter",
    "Entitlement",
    "FacetValue",
    "Hit",
    "InMemorySearchBackend",
    "RangeFilter",
    "SearchBackend",
    "SearchDocument",
    "SearchRequest",
    "SearchResponse",
    "SortSpec",
    "make_search_backend",
]
