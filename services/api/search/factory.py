"""Backend selection. The only place a search backend is chosen (ADR-0002)."""

from __future__ import annotations

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


# There was a `get_search_backend = once(make_search_backend)` here, and it had
# no callers. `datahub.api.deps._backend` is the process-wide instance: a second
# `Once` over the same factory means the first code to reach for this one gets a
# *different* backend, which `deps.reset()` neither flushes nor clears. That is
# the two-instances bug `datahub.singleton` exists to prevent, reintroduced by
# having two singletons instead of one broken one. One factory, one holder.
