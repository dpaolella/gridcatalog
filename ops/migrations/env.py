"""Alembic environment.

Two decisions worth stating.

**The URL comes from settings, never from alembic.ini.** A migration run
against a different database from the one the application reads is the worst
kind of success: it reports "ok" and nothing changed where it mattered.

**``render_as_batch`` is on.** SQLite cannot ``ALTER COLUMN``; without batch
mode a migration that adds a constraint works in Postgres and fails in local
development, which is exactly where it should have been caught.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from datahub.api.models import operational  # noqa: E402,F401  (registers every mapper)
from datahub.api.models.base import Base, UTCDateTime  # noqa: E402
from datahub.config import get_settings  # noqa: E402

config = context.config
# Only when alembic owns the process. Run from inside the application — the
# `datahub db upgrade` command — reconfiguring logging from alembic.ini would
# tear down handlers the caller is still writing to.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_url = get_settings().database_url
config.set_main_option("sqlalchemy.url", _url)

if _url.startswith("sqlite") and ":memory:" not in _url:
    # `alembic upgrade head` on a fresh checkout must not fail because nobody
    # has created var/ yet. The application's engine does the same thing.
    Path(_url.split("///", 1)[-1]).parent.mkdir(parents=True, exist_ok=True)


def render_item(type_: str, obj: object, autogen_context: object) -> str | bool:  # noqa: ARG001
    """Render ``UTCDateTime`` as the plain SQLAlchemy type it wraps.

    A migration must keep running after the application class it was generated
    from is renamed or deleted. Alembic would otherwise emit
    ``datahub.api.models.base.UTCDateTime(...)`` into the migration file and
    bind the schema history to a Python import path — so a refactor two years
    from now breaks `alembic upgrade head` on a fresh database.

    The wrapper only normalises tzinfo in Python; the column it produces is a
    timezone-aware DateTime and nothing else, so nothing is lost by saying so.
    """
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
