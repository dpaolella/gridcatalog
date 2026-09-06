"""Push one record's change into the search index, inside the caller's session.

Shared by the two routers that change a record's visibility — the custodian API
and the steward review queue — because they need identical behaviour and the
subtleties below are easy to get wrong once, let alone twice.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from datahub.logging import get_logger

log = get_logger(__name__)


def reproject(iri: str, records: Any, session: Any) -> None:
    """Reindex a single record on this request's own session.

    The session matters twice over. It must exist, because
    ``entitled_principals`` is read from the operational store during
    projection — a projector built without one writes a document with an empty
    allow-list, and the grant just recorded silently does not work. And it must
    be *this* session: opening a second one blocks on the write lock this
    request is holding, which on SQLite is "database is locked" and on
    PostgreSQL is a stall until the statement timeout.

    Never fatal. A change that is recorded but not yet indexed becomes visible
    at the next reindex, whereas refusing the write because the index is down
    would lose it entirely.
    """
    try:
        from datahub.api.deps import search_backend
        from datahub.projector import Projector

        Projector(records, search_backend(), session_factory=lambda: nullcontext(session)).project(
            iri
        )
    except Exception as exc:
        log.warning("change not yet indexed", dataset=iri, error=str(exc))
