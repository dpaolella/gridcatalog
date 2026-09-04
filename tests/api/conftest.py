"""Fixtures for the API suite.

A real app against a real store, populated with the fixture corpus and
reindexed — not a mock. The interesting failures in an HTTP layer are the ones
where the wiring is wrong, and wiring is exactly what a mocked dependency hides.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    from datahub.api import deps
    from datahub.api.models.base import create_all, reset_engine
    from datahub.config import reset_settings

    monkeypatch.setenv("DATAHUB_GRAPH_STORE_PATH", str(tmp_path / "graph.nq"))
    monkeypatch.setenv("DATAHUB_SEARCH_STORE_PATH", str(tmp_path / "index.json"))
    monkeypatch.setenv("DATAHUB_DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/ops.sqlite3")
    reset_settings()
    reset_engine()
    deps.reset()
    create_all()
    yield tmp_path
    deps.reset()
    reset_engine()
    reset_settings()


@pytest.fixture(scope="session")
def corpus_nquads(tmp_path_factory) -> Path:
    """The bootstrapped store with the corpus in it, serialised once.

    Bootstrapping is a vocabulary parse, a SHACL load and a materialisation
    pass, and loading the corpus validates every fixture — several seconds,
    which is fine once and intolerable per test. Each test parses the resulting
    N-Quads instead.

    Sharing the store itself would be the wrong fix: several tests write to it,
    and a shared store makes them order-dependent. Sharing the expensive
    computation and not the state is the point.
    """
    from datahub.graph.loader import bootstrap
    from datahub.graph.records import RecordStore
    from datahub.graph.store import RdflibStore
    from fixtures.loader import load_record, record_names

    path = tmp_path_factory.mktemp("corpus") / "corpus.nq"
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    for name in record_names():
        records.put(load_record(name))
    path.write_text(store.dataset.serialize(format="nquads"))
    store.close()
    return path


@pytest.fixture
def loaded(api_env, corpus_nquads):
    """The fixture corpus, in this test's own store and index."""
    from datahub.api import deps
    from datahub.graph.records import RecordStore
    from datahub.projector import reindex

    store = deps.graph_store()
    store.dataset.parse(corpus_nquads.as_posix(), format="nquads")
    records = RecordStore(store)
    reindex(records, deps.search_backend())
    return records


@pytest.fixture
def client(loaded) -> Iterator[TestClient]:
    from datahub.api.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def empty_client(api_env) -> Iterator[TestClient]:
    """An app with a bootstrapped but empty catalog."""
    from datahub.api import deps
    from datahub.api.app import create_app
    from datahub.graph.loader import bootstrap

    bootstrap(deps.graph_store())
    with TestClient(create_app()) as test_client:
        yield test_client
