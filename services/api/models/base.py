"""SQLAlchemy foundation for the operational store.

The line between here and the graph is the one PRD §3.3 draws: *if it would be
meaningless to publish as RDF, it belongs in Postgres.* Users, sessions,
allow-lists, harvest run state, the review queue, reports, submissions and probe
history are all operational. Catalog records are not.

Distribution revision history is the one judgement call. It is arguably PROV-
shaped, but it is high-churn append-only history *about how a record changed*
rather than a statement about the dataset, and publishing it would bloat the
catalog. It lives here; the current value lives in the graph.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from datahub.config import Settings, get_settings
from sqlalchemy import DateTime, MetaData, String, TypeDecorator, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

#: Explicit naming so Alembic autogenerate produces stable, reviewable diffs
#: instead of database-assigned constraint names that differ per dialect.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UTCDateTime(TypeDecorator):
    """A timestamp that is timezone-aware on the way out, on every dialect.

    SQLite drops tzinfo. A naive datetime compared against an aware one raises,
    and the comparison that raises is usually the one deciding whether an access
    plan has expired — so normalise here rather than at forty call sites.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:  # noqa: ARG002 - SQLAlchemy TypeDecorator API
        if value is None:
            return None
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:  # noqa: ARG002 - SQLAlchemy TypeDecorator API
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {datetime: UTCDateTime}


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class IdMixin:
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)


_engine: Engine | None = None
_sessionmaker: sessionmaker[Session] | None = None


def get_engine(settings: Settings | None = None) -> Engine:
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        url = settings.database_url
        kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            # A single connection shared across threads is what the in-process
            # dev server needs; without this, every request gets its own file
            # handle and the eager queue's writes are invisible to the request
            # that queued them.
            kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" not in url:
                from pathlib import Path

                path = url.split("///", 1)[-1]
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):
            _configure_sqlite(_engine)
    return _engine


def _configure_sqlite(engine: Engine) -> None:
    """Two pragmas SQLite needs and does not set itself.

    ``foreign_keys=ON`` because SQLite ignores foreign keys unless told not to,
    which turns a broken reference into silently orphaned rows instead of an
    error.

    ``journal_mode=WAL`` because the default rollback journal makes a reader
    block a writer. That is not a theoretical concern here: an audit row for a
    refusal has to be written in its own transaction — otherwise it rolls back
    with the refusal it records — and under the default journal that write
    blocks on the read transaction of the very request being refused, so the
    log is empty for exactly the events it exists for. WAL also survives a
    crash better, which is the usual reason to want it.
    """
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        # A writer that finds the database busy waits rather than failing
        # immediately: five seconds is far longer than any write here takes and
        # far shorter than a user notices.
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def get_sessionmaker(settings: Settings | None = None) -> sessionmaker[Session]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = sessionmaker(bind=get_engine(settings), expire_on_commit=False, future=True)
    return _sessionmaker


@contextmanager
def session_scope(settings: Settings | None = None) -> Iterator[Session]:
    """A transactional scope. Commits on success, rolls back on any exception."""
    session = get_sessionmaker(settings)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Drop cached engine and sessionmaker. Test helper."""
    global _engine, _sessionmaker
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _sessionmaker = None


def create_all(settings: Settings | None = None) -> None:
    """Create every table. Development and test only — production uses Alembic."""
    from datahub.api.models import operational  # noqa: F401  (registers mappers)

    Base.metadata.create_all(get_engine(settings))
