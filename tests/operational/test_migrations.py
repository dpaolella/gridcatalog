"""The Alembic migration (WP-2.3).

A migration is only useful if it produces the schema the application expects.
The way that goes wrong is not dramatic: someone adds a column to a model,
forgets the migration, and every test passes because the tests build the schema
from the models. The last test here is the one that catches that.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "ops" / "alembic.ini"


@pytest.fixture
def alembic_config(tmp_path, monkeypatch):
    from alembic.config import Config

    url = f"sqlite+pysqlite:///{tmp_path}/migrated.sqlite3"
    monkeypatch.setenv("DATAHUB_DATABASE_URL", url)

    from datahub.config import reset_settings

    reset_settings()
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(REPO_ROOT / "ops" / "migrations"))
    return config, url


def test_upgrade_creates_every_table(alembic_config) -> None:
    from alembic import command
    from datahub.api.models.base import Base

    config, url = alembic_config
    command.upgrade(config, "head")

    tables = set(inspect(create_engine(url)).get_table_names())
    assert set(Base.metadata.tables) <= tables
    assert "alembic_version" in tables


def test_downgrade_removes_them_again(alembic_config) -> None:
    """A migration that cannot be undone is a migration nobody dares run."""
    from alembic import command

    config, url = alembic_config
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    assert inspect(create_engine(url)).get_table_names() == ["alembic_version"]


def test_upgrade_is_idempotent_at_head(alembic_config) -> None:
    from alembic import command

    config, _ = alembic_config
    command.upgrade(config, "head")
    command.upgrade(config, "head")  # no-op, must not raise


def test_the_migration_matches_the_models(alembic_config) -> None:
    """The test that catches the forgotten migration.

    Autogenerate against a database already at head must find nothing. If it
    finds something, a model changed and the migration did not — and every
    other test still passes, because they build the schema from the models.
    """
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from datahub.api.models import operational  # noqa: F401  (registers mappers)
    from datahub.api.models.base import Base

    config, url = alembic_config
    command.upgrade(config, "head")

    engine = create_engine(url)
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "render_as_batch": True}
        )
        diff = compare_metadata(context, Base.metadata)

    assert diff == [], f"the models and the migration have diverged: {diff}"


def test_the_migration_does_not_import_application_code() -> None:
    """A migration must keep running after the class it was generated from is
    renamed or deleted; importing ``datahub`` into one binds the schema history
    to a Python import path."""
    versions = REPO_ROOT / "ops" / "migrations" / "versions"
    for path in versions.glob("*.py"):
        assert "datahub" not in path.read_text(), path.name
