"""Shared fixtures.

Every fixture here uses an in-process backend (ADR-0002), so the default suite
runs with no container runtime. Container-backed equivalents live in
``tests/integration/conftest.py`` and are selected by ``-m integration``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point every path-valued setting at a temp dir and reset the cache.

    Autouse: a test that accidentally writes to the developer's real store is a
    test that will pass locally and fail in CI, or worse.
    """
    from datahub.config import reset_settings

    for key in list(os.environ):
        if key.startswith("DATAHUB_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATAHUB_GRAPH_BACKEND", "rdflib")
    monkeypatch.setenv("DATAHUB_SEARCH_BACKEND", "memory")
    monkeypatch.setenv("DATAHUB_QUEUE_BACKEND", "eager")
    monkeypatch.setenv("DATAHUB_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/test.sqlite3")
    monkeypatch.setenv("DATAHUB_ENRICHMENT_ENABLED", "false")
    reset_settings()
    yield
    reset_settings()


@pytest.fixture
def settings():
    from datahub.config import get_settings

    return get_settings()


@pytest.fixture
def store(settings):
    """An empty in-process graph store."""
    from datahub.graph.store import RdflibStore

    with RdflibStore() as s:
        yield s


@pytest.fixture
def search_backend():
    from datahub.api.search.backend import InMemorySearchBackend

    backend = InMemorySearchBackend()
    yield backend
    backend.clear()


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT
