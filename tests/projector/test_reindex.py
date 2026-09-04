"""Full reindex: PRD M2's "survives a full reindex unchanged".

PRD §3.1 asks for reindex-from-scratch to be one command and to be routinely
exercised, "because the index is derived state and treating it as precious is
how it drifts." These tests are that routine exercise.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.search.backend import (
    Entitlement,
    InMemorySearchBackend,
    SearchRequest,
)
from datahub.graph.loader import bootstrap
from datahub.graph.records import RecordStore
from datahub.graph.store import RdflibStore
from datahub.projector import Projector, reindex
from fixtures.loader import load_record, record_names


@pytest.fixture(scope="module")
def loaded():
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    for name in record_names():
        records.put(load_record(name))
    return records


def _comparable(backend: InMemorySearchBackend) -> dict[str, dict]:
    """Documents keyed by id, with the fields a rebuild cannot reproduce
    stripped. ``indexed_at`` is a timestamp of the run, not of the record."""
    out = {}
    for doc in backend.all_documents():
        data = doc.model_dump(mode="json")
        data.pop("indexed_at", None)
        out[doc.id] = data
    return out


def test_reindex_indexes_every_confirmed_record(loaded) -> None:
    backend = InMemorySearchBackend()
    result = reindex(loaded, backend)
    assert result.total_records == len(record_names())
    assert result.indexed == len(record_names())
    assert not result.errors
    assert backend.count() == len(record_names())


def test_reindex_reproduces_incremental_projection_exactly(loaded) -> None:
    """The M2 done-criterion. If these differ, one of the two paths is writing
    something the other does not — and since the index is derived, the
    difference is a bug in whichever path is doing more."""
    incremental = InMemorySearchBackend()
    projector = Projector(loaded, incremental)
    for name in record_names():
        projector.project(name)

    rebuilt = InMemorySearchBackend()
    reindex(loaded, rebuilt)

    before, after = _comparable(incremental), _comparable(rebuilt)
    assert set(before) == set(after)
    for doc_id in before:
        assert before[doc_id] == after[doc_id], f"{doc_id} differs after a rebuild"


def test_reindex_is_idempotent(loaded) -> None:
    backend = InMemorySearchBackend()
    reindex(loaded, backend)
    first = _comparable(backend)
    reindex(loaded, backend)
    assert _comparable(backend) == first


def test_reindex_drops_records_no_longer_in_the_graph(loaded) -> None:
    """``clear`` is what makes this a rebuild rather than a merge. Without it a
    record deleted from the graph survives in the index indefinitely, and the
    only symptom is a search hit that 404s."""
    backend = InMemorySearchBackend()
    reindex(loaded, backend)
    ghost = next(iter(backend.all_documents())).model_copy(update={"id": "deleted-record"})
    backend.index([ghost])
    assert backend.get("deleted-record") is not None

    reindex(loaded, backend)
    assert backend.get("deleted-record") is None


def test_reindex_skips_unconfirmed_records(loaded) -> None:
    loaded.demote("lbnl-queued-up", reason="testing")
    try:
        backend = InMemorySearchBackend()
        result = reindex(loaded, backend)
        assert result.skipped_unconfirmed == 0, (
            "a demoted record left the catalog graph, so it is not skipped — "
            "it is simply absent, which is the stronger guarantee"
        )
        assert backend.get("lbnl-queued-up") is None
    finally:
        loaded.promote("lbnl-queued-up", validate=False)


def test_a_rebuilt_index_answers_the_same_searches(loaded) -> None:
    backend = InMemorySearchBackend()
    reindex(loaded, backend)
    for query in ("era5", "wind", "transmission", "cost", "interconnection"):
        response = backend.search(SearchRequest(entitlement=Entitlement.anonymous(), q=query))
        rebuilt = InMemorySearchBackend()
        reindex(loaded, rebuilt)
        again = rebuilt.search(SearchRequest(entitlement=Entitlement.anonymous(), q=query))
        assert [h.document.id for h in response.hits] == [h.document.id for h in again.hits], (
            f"search for {query!r} is not stable across a rebuild"
        )


def test_reindex_reports_what_it_did(loaded) -> None:
    result = reindex(loaded, InMemorySearchBackend())
    assert "reindexed" in result.summary
    assert str(len(record_names())) in result.summary
