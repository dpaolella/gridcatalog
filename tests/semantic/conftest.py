"""Fixtures for the semantic suite.

The vocabulary is loaded once per session and the corpus once per session,
because bootstrapping is a vocabulary parse, a SHACL load and a materialisation
pass, and doing it per test is several seconds each. The *store* is per test —
several of these tests write computed state, and a shared store makes them
order-dependent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="session")
def corpus_nquads(tmp_path_factory) -> Path:
    from datahub.graph.loader import bootstrap
    from datahub.graph.records import RecordStore
    from datahub.graph.store import RdflibStore
    from fixtures.loader import load_record, record_names

    path = tmp_path_factory.mktemp("semantic-corpus") / "corpus.nq"
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    for name in record_names():
        records.put(load_record(name))
    path.write_text(store.dataset.serialize(format="nquads"))
    store.close()
    return path


@pytest.fixture
def loaded_store(corpus_nquads):
    from datahub.graph.store import RdflibStore

    store = RdflibStore()
    store.dataset.parse(corpus_nquads.as_posix(), format="nquads")
    yield store
    store.close()


@pytest.fixture
def records(loaded_store):
    from datahub.graph.records import RecordStore

    return RecordStore(loaded_store)


@pytest.fixture
def vocabulary(loaded_store):
    from datahub.semantic.vocabulary import Vocabulary

    return Vocabulary.from_store(loaded_store)


@pytest.fixture
def resolver(vocabulary):
    from datahub.semantic.resolve import Resolver

    return Resolver(vocabulary)


@pytest.fixture
def runner(records, vocabulary, resolver):
    from datahub.semantic.runner import SemanticRunner

    return SemanticRunner(records, vocabulary=vocabulary, resolver=resolver)
