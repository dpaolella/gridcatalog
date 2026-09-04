"""Backend selection. The only place a search backend is chosen (ADR-0002)."""

from __future__ import annotations

import functools

from datahub.api.search.backend import InMemorySearchBackend, SearchBackend
from datahub.config import SearchBackend as SearchBackendKind
from datahub.config import Settings, get_settings


def make_search_backend(settings: Settings | None = None) -> SearchBackend:
    settings = settings or get_settings()
    if settings.search_backend is SearchBackendKind.OPENSEARCH:
        from datahub.api.search.opensearch_backend import OpenSearchBackend

        auth = (
            (settings.opensearch_user, settings.opensearch_password)
            if settings.opensearch_user and settings.opensearch_password
            else None
        )
        backend = OpenSearchBackend(settings.opensearch_url, settings.opensearch_index, auth=auth)
        backend.ensure_index()
        return backend
    return InMemorySearchBackend(settings.search_store_path)


@functools.lru_cache(maxsize=1)
def get_search_backend() -> SearchBackend:
    """Process-wide backend. Cleared by tests via ``get_search_backend.cache_clear()``."""
    return make_search_backend()
