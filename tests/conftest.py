"""Shared fixtures.

Every fixture here uses an in-process backend (ADR-0002), so the default suite
runs with no container runtime. The container-backed equivalents are in
``tests/parity/test_containers.py`` and are selected by ``-m integration``; they
run the same assertions as ``tests/parity/test_in_process.py``, so a failure
there means a backend genuinely differs rather than a test being wrong.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolated_settings(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Point every path-valued setting at a temp dir and reset the cache.

    Autouse: a test that accidentally writes to the developer's real store is a
    test that will pass locally and fail in CI, or worse.

    **Except for the container-backed tests**, which are the one kind that has
    to keep its environment. This deletes every `DATAHUB_*` variable, and those
    variables are how the integration job says where Fuseki, OpenSearch and
    Postgres are and what credentials they take. Wiping them left the parity
    tests on the defaults — `http://localhost:3030` and `http://localhost:9200`,
    which happen to be right, so it looked like it worked — with
    `DATAHUB_FUSEKI_USER` and `DATAHUB_FUSEKI_PASSWORD` gone. Fuseki's Shiro
    filter rejected every unauthenticated request with a 401 before the request
    reached anything that logs it, which is why the job's Fuseki container
    recorded no requests at all from a suite that was making them.
    `DATAHUB_DATABASE_URL` went the same way, so the job's Postgres service was
    never touched: those tests ran against SQLite and reported themselves as
    integration coverage.
    """
    from datahub.config import reset_settings

    if request.node.get_closest_marker("integration"):
        reset_settings()
        yield
        reset_settings()
        return

    for key in list(os.environ):
        if key.startswith("DATAHUB_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATAHUB_GRAPH_BACKEND", "rdflib")
    monkeypatch.setenv("DATAHUB_SEARCH_BACKEND", "memory")
    monkeypatch.setenv("DATAHUB_QUEUE_BACKEND", "eager")
    monkeypatch.setenv("DATAHUB_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/test.sqlite3")
    monkeypatch.setenv("DATAHUB_ENRICHMENT_ENABLED", "false")
    # Off for the same reason enrichment is: the default suite runs with no
    # outbound network (ADR-0002), and a stage that reaches the open web would
    # otherwise spend the whole run waiting for connect timeouts. Tests that
    # exercise the probe pass their own transport.
    monkeypatch.setenv("DATAHUB_HARVEST_PROBE_SCHEMAS", "false")
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
