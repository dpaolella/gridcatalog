"""Request-scoped dependencies (WP-4.3).

FastAPI's dependency system is the seam where a handler gets a store, a search
backend and a caller. Keeping that in one module means a handler cannot reach
around it — and the one it must not reach around is
:func:`current_caller`, because a handler that resolved its own entitlement
could resolve it differently (ADR-0006).

**The graph store and search backend are process-wide, not per request.** The
rdflib store holds the whole dataset in memory and the in-memory index holds
every document; building either per request would be absurd. The Fuseki and
OpenSearch backends are HTTP clients with their own pools, and building those
per request is merely wasteful. The database session *is* per request, because
a transaction that outlived a request is a transaction nobody closes.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator
from typing import Annotated

from datahub.api.entitlement import SESSION_COOKIE, Caller, anonymous, resolve
from datahub.api.models.base import get_sessionmaker
from datahub.api.search.backend import SearchBackend
from datahub.api.search.factory import make_search_backend
from datahub.config import Settings, get_settings
from datahub.graph.records import RecordStore
from datahub.graph.store import GraphStore, make_store
from datahub.logging import get_logger
from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

log = get_logger(__name__)


@functools.lru_cache(maxsize=1)
def _store() -> GraphStore:
    return make_store()


@functools.lru_cache(maxsize=1)
def _backend() -> SearchBackend:
    return make_search_backend()


def reset() -> None:
    """Drop the cached process-wide resources. Test helper, and the hook the
    app's shutdown uses so a store with a file behind it is flushed."""
    if _store.cache_info().currsize:
        _store().close()
    _store.cache_clear()
    _backend.cache_clear()


def settings_dep() -> Settings:
    return get_settings()


def graph_store() -> GraphStore:
    return _store()


def search_backend() -> SearchBackend:
    return _backend()


def records(store: Annotated[GraphStore, Depends(graph_store)]) -> RecordStore:
    return RecordStore(store)


def db_session() -> Iterator[Session | None]:
    """A session per request, or None if the database is unreachable.

    None rather than an exception: a catalog whose operational store is down
    should still serve public search from the graph and the index. The caller
    then resolves to anonymous, which is the safe direction — a reader sees
    less than they are entitled to, never more.
    """
    try:
        session = get_sessionmaker()()
    except Exception as exc:
        log.warning("operational store unavailable", error=str(exc))
        yield None
        return
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def current_caller(
    request: Request,
    session: Annotated[Session | None, Depends(db_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> Caller:
    """The caller behind this request.

    The single place identity becomes an entitlement. Stashed on the request so
    the audit middleware can name the principal without resolving it a second
    time — and resolving twice is how two call sites come to disagree.
    """
    try:
        caller = resolve(authorization, session, cookie=request.cookies.get(SESSION_COOKIE))
    except Exception as exc:
        log.warning("caller resolution failed", error=str(exc))
        caller = anonymous()

    if session is not None:
        # Commit the "this token was used" touch now, rather than carrying an
        # open write transaction through the rest of the request.
        #
        # Two reasons, and the second is the one that bites. A token *was* used
        # even if the request it authenticated is then refused, so the fact
        # belongs outside the request's transaction. And holding the write lock
        # for the whole request means the audit row for a refusal — which has to
        # be written on a second connection, or it rolls back with the refusal
        # it records — blocks on the very request being refused, so the log is
        # empty for exactly the events it exists for.
        try:
            session.commit()
        except Exception as exc:  # pragma: no cover - a store that just answered
            log.warning("could not record token use", error=str(exc))
            session.rollback()

    request.state.caller = caller
    return caller


SettingsDep = Annotated[Settings, Depends(settings_dep)]
StoreDep = Annotated[GraphStore, Depends(graph_store)]
RecordsDep = Annotated[RecordStore, Depends(records)]
SearchDep = Annotated[SearchBackend, Depends(search_backend)]
SessionDep = Annotated["Session | None", Depends(db_session)]
CallerDep = Annotated[Caller, Depends(current_caller)]

__all__ = [
    "CallerDep",
    "RecordsDep",
    "SearchDep",
    "SessionDep",
    "SettingsDep",
    "StoreDep",
    "current_caller",
    "db_session",
    "graph_store",
    "records",
    "reset",
    "search_backend",
]
