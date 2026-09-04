"""Fixtures for the operational store.

SQLite on disk rather than in memory: the schema carries CHECK constraints and
foreign keys, and ``:memory:`` with a fresh connection per test would give each
test a different empty database.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session


@pytest.fixture
def db(settings) -> Iterator[Session]:
    from datahub.api.models.base import create_all, get_sessionmaker, reset_engine

    reset_engine()
    create_all(settings)
    session = get_sessionmaker(settings)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        reset_engine()


@pytest.fixture
def repos(db: Session):
    from datahub.api.models.repositories import Repositories

    return Repositories(db)
