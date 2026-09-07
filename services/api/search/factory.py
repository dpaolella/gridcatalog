"""Backend selection. The only place a search backend is chosen (ADR-0002)."""

from __future__ import annotations

from datahub.api.search.backend import InMemorySearchBackend, SearchBackend
from datahub.config import SearchBackend as SearchBackendKind
from datahub.config import Settings, get_settings
from datahub.singleton import once


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


#: Process-wide backend, built once. `Once`, not `lru_cache`: see
#: `datahub.singleton`. Cleared with ``get_search_backend.clear()``.
get_search_backend = once(make_search_backend)
