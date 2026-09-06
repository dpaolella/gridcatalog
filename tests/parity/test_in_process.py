"""The parity assertions, against the in-process backends.

These run on every commit. Their job is to prove the assertions in
`assertions.py` are *correct* — so that when the container-backed job runs the
same functions against Fuseki, OpenSearch and Postgres, a failure there means
the backend differs rather than the test being wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.search.backend import InMemorySearchBackend
from datahub.graph.store import RdflibStore

from parity.assertions import GRAPH_ASSERTIONS, SEARCH_ASSERTIONS


@pytest.mark.parametrize("check", GRAPH_ASSERTIONS, ids=lambda f: f.__name__)
def test_graph_store(check):
    store = RdflibStore()
    try:
        check(store)
    finally:
        store.close()


@pytest.mark.parametrize("check", SEARCH_ASSERTIONS, ids=lambda f: f.__name__)
def test_search_backend(check):
    check(InMemorySearchBackend())
