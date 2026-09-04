"""Full rebuild of the search index from the graph.

PRD §3.1: "Reindex-from-scratch must be a single command and must be routinely
exercised, because the index is derived state and treating it as precious is how
it drifts."

So this streams rather than loading the catalog into memory, reports what it
did, and is called by ``datahub index reindex`` and by a test on every run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from datahub.api.search.backend import SearchBackend
from datahub.api.search.document import SearchDocument
from datahub.config import Settings, get_settings
from datahub.graph.graphs import NamedGraph
from datahub.graph.records import RecordStore, slug_of
from datahub.logging import get_logger
from datahub.projector.index import Projector

log = get_logger(__name__)


@dataclass(slots=True)
class ReindexResult:
    total_records: int
    indexed: int
    skipped_unconfirmed: int
    errors: list[str]
    duration_s: float

    @property
    def summary(self) -> str:
        line = f"reindexed {self.indexed} of {self.total_records} records in {self.duration_s:.1f}s"
        if self.skipped_unconfirmed:
            line += f"; {self.skipped_unconfirmed} not confirmed"
        if self.errors:
            line += f"; {len(self.errors)} errors"
        return line


def reindex(
    records: RecordStore,
    backend: SearchBackend,
    *,
    settings: Settings | None = None,
    session_factory: object | None = None,
    batch: int = 200,
    clear: bool = True,
) -> ReindexResult:
    """Rebuild the index from the catalog graph.

    ``clear`` drops the index first, which is what makes this a rebuild rather
    than a merge: a record deleted from the graph since the last index would
    otherwise survive forever.
    """
    settings = settings or get_settings()
    projector = Projector(records, backend, settings, session_factory)
    started = time.perf_counter()

    if clear:
        backend.clear()

    ids = records.list_ids(graph=NamedGraph.CATALOG)
    pending: list[SearchDocument] = []
    indexed = skipped = 0
    errors: list[str] = []

    for dataset_id in ids:
        try:
            doc = projector.document_for(dataset_id)
        except Exception as exc:
            errors.append(f"{slug_of(dataset_id)}: {type(exc).__name__}: {exc}")
            log.warning("reindex skipped a record", dataset=dataset_id, error=str(exc))
            continue
        if doc is None or doc.review_state != "confirmed":
            skipped += 1
            continue
        pending.append(doc)
        if len(pending) >= batch:
            indexed += backend.index(pending)
            pending.clear()

    if pending:
        indexed += backend.index(pending)
    backend.refresh()
    backend.flush()
    projector._record_full_reindex()

    result = ReindexResult(
        total_records=len(ids),
        indexed=indexed,
        skipped_unconfirmed=skipped,
        errors=errors,
        duration_s=time.perf_counter() - started,
    )
    log.info(
        "reindex complete",
        total=result.total_records,
        indexed=result.indexed,
        skipped=result.skipped_unconfirmed,
        errors=len(result.errors),
        seconds=round(result.duration_s, 2),
    )
    return result
