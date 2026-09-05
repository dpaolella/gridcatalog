"""Fixtures for the link suite.

A real store, a real index and real records: the interesting failures in a
ranking are the ones where a signal reads the wrong field, and a hand-built
document hides exactly that.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GRADED_AT = datetime(2026, 9, 4, tzinfo=UTC)


@pytest.fixture(scope="session")
def corpus_nquads(tmp_path_factory) -> Path:
    """The corpus with the semantic layer already run over it.

    Links depend on grades (the quality contribution) and on resolved concepts,
    so a link fixture built on an ungraded corpus would test the ranker against
    inputs it never sees in production.
    """
    from datahub.graph.loader import bootstrap
    from datahub.graph.records import RecordStore
    from datahub.graph.store import RdflibStore
    from datahub.semantic.runner import SemanticRunner
    from fixtures.loader import load_record, record_names

    path = tmp_path_factory.mktemp("link-corpus") / "corpus.nq"
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    for name in record_names():
        records.put(load_record(name))
    SemanticRunner(records).run_all(now=GRADED_AT)
    path.write_text(store.dataset.serialize(format="nquads"))
    store.close()
    return path


@pytest.fixture
def store(corpus_nquads):
    from datahub.graph.store import RdflibStore

    s = RdflibStore()
    s.dataset.parse(corpus_nquads.as_posix(), format="nquads")
    yield s
    s.close()


@pytest.fixture
def records(store):
    from datahub.graph.records import RecordStore

    return RecordStore(store)


@pytest.fixture
def backend(records):
    from datahub.api.search.backend import InMemorySearchBackend
    from datahub.projector import reindex

    b = InMemorySearchBackend()
    reindex(records, b)
    return b


@pytest.fixture
def service(backend, store):
    from datahub.linksvc import LinkService

    return LinkService(backend=backend, store=store)


@pytest.fixture
def doc(backend):
    def _get(dataset_id: str):
        document = backend.get(dataset_id)
        assert document is not None, f"no indexed document for {dataset_id}"
        return document

    return _get
