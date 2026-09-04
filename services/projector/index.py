"""The projector: graph to search index, on commit.

PRD §3.1 makes this the piece most likely to be the source of a "why is search
stale" bug, and asks for its lag to be instrumented rather than assumed. So lag
is a recorded fact here — ``last_commit_at`` and ``last_indexed_at`` in the
``projector_state`` row — not a guess, and :meth:`Projector.health` answers the
question directly.

The other rule this module exists to enforce: a record whose review state is
not ``confirmed`` is **removed** from the index, never merely skipped. A record
demoted back to draft that stays indexed is a disclosure, and the failure mode
is silent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from datahub.api.search.backend import SearchBackend
from datahub.api.search.document import SearchDocument
from datahub.config import Settings, get_settings
from datahub.graph.graphs import NamedGraph
from datahub.graph.records import RecordStore, slug_of
from datahub.logging import get_logger
from datahub.projector.build import build_document
from datahub.semantic.queries import scoped
from rdflib import Graph, URIRef

if TYPE_CHECKING:
    pass

log = get_logger(__name__)

#: Graphs the projector reads. The catalog holds the record; the vocabulary
#: holds concept labels; the inferred graph holds the transitive concept closure
#: that fills ``concept_iris_expanded``. Computed holds grades written back by
#: the semantic layer.
READ_GRAPHS: tuple[NamedGraph, ...] = (
    NamedGraph.CATALOG,
    NamedGraph.VOCAB,
    NamedGraph.INFERRED,
    NamedGraph.COMPUTED,
)


@dataclass(slots=True)
class ProjectionResult:
    indexed: int = 0
    removed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def summary(self) -> str:
        parts = [f"{self.indexed} indexed"]
        if self.removed:
            parts.append(f"{self.removed} removed")
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        return f"{', '.join(parts)} in {self.duration_s:.2f}s"


@dataclass(slots=True)
class ProjectorHealth:
    lag_seconds: float | None
    budget_seconds: float
    healthy: bool
    pending: int
    last_commit_at: datetime | None
    last_indexed_at: datetime | None
    last_full_reindex_at: datetime | None

    @property
    def summary(self) -> str:
        if self.lag_seconds is None:
            return "projector idle; nothing committed since the last index"
        state = "ok" if self.healthy else "STALE"
        return (
            f"projector {state}: {self.lag_seconds:.1f}s behind "
            f"(budget {self.budget_seconds:.0f}s), {self.pending} pending"
        )


class Projector:
    """Projects catalog records into the search index.

    Takes an optional SQLAlchemy session factory. Without one the projector
    still works and simply does not record lag — which is the right default for
    a test, and the wrong one for a deployment, so the CLI and the API both
    pass one.
    """

    def __init__(
        self,
        records: RecordStore,
        backend: SearchBackend,
        settings: Settings | None = None,
        session_factory: object | None = None,
    ) -> None:
        self.records = records
        self.backend = backend
        self.settings = settings or get_settings()
        self._session_factory = session_factory
        self._construct = _load_construct()

    # ---- projecting ------------------------------------------------------

    def document_for(self, dataset_id: str) -> SearchDocument | None:
        """Build the search document for one record, or None if it is absent."""
        iri = self.records._iri(dataset_id)
        graph = self._read(iri)
        if not len(graph):
            return None
        return build_document(
            graph,
            iri,
            entitled_principals=self._entitled_principals(str(iri)),
            inbound_link_count=self._inbound_links(iri),
        )

    def project(self, dataset_id: str) -> ProjectionResult:
        """Index one record, or remove it if it is no longer publishable."""
        started = time.perf_counter()
        result = ProjectionResult()
        doc = self.document_for(dataset_id)
        slug = slug_of(str(self.records._iri(dataset_id)))

        if doc is None or doc.review_state != "confirmed":
            # Removal, not a skip. A record demoted to draft that stays indexed
            # is visible to every anonymous search, and nothing would surface
            # the mistake.
            removed = self.backend.delete([slug])
            result.removed = removed
            result.duration_s = time.perf_counter() - started
            if removed:
                log.info("record unindexed", dataset=slug, reason="not confirmed")
            return result

        self.backend.index([doc])
        result.indexed = 1
        result.duration_s = time.perf_counter() - started
        self._record_indexed()
        return result

    def project_many(self, dataset_ids: list[str], *, batch: int = 200) -> ProjectionResult:
        started = time.perf_counter()
        result = ProjectionResult()
        pending: list[SearchDocument] = []
        removals: list[str] = []

        for dataset_id in dataset_ids:
            try:
                doc = self.document_for(dataset_id)
            except Exception as exc:
                result.errors.append(f"{dataset_id}: {type(exc).__name__}: {exc}")
                log.warning("projection failed", dataset=dataset_id, error=str(exc))
                continue
            slug = slug_of(str(self.records._iri(dataset_id)))
            if doc is None or doc.review_state != "confirmed":
                removals.append(slug)
                continue
            pending.append(doc)
            if len(pending) >= batch:
                result.indexed += self.backend.index(pending)
                pending.clear()

        if pending:
            result.indexed += self.backend.index(pending)
        if removals:
            result.removed = self.backend.delete(removals)

        self.backend.refresh()
        result.duration_s = time.perf_counter() - started
        self._record_indexed()
        return result

    def on_commit(self, dataset_id: str) -> ProjectionResult:
        """The incremental path, called after a record is written.

        Records the commit time first, so lag measures the gap between the
        write and the index rather than between two index runs.
        """
        self._record_commit()
        return self.project(dataset_id)

    # ---- reading ---------------------------------------------------------

    def _read(self, iri: URIRef) -> Graph:
        """The record plus the vocabulary context the document needs."""
        if self._construct is None:
            return self.records.get_graph(str(iri), include_computed=True)
        # Guard against a store that was never bootstrapped: rdflib reads a FROM
        # clause naming a graph it does not know as a remote fetch, and the
        # resulting error names a URL rather than the missing graph.
        self.records.store.ensure_graphs(READ_GRAPHS)
        return self.records.store.construct(scoped(self._construct, *READ_GRAPHS), {"root": iri})

    def _entitled_principals(self, dataset_iri: str) -> list[str]:
        """Allow-list membership, from the operational store.

        In the index because the entitlement clause is compiled into the query
        (ADR-0006): a second lookup per hit would make a filter over a page of
        results into a page of round trips, and post-filtering is what leaks
        existence through counts.
        """
        if self._session_factory is None:
            return []
        try:
            from datahub.api.models.repositories import AllowlistRepository
        except ImportError:
            return []
        with self._session_factory() as session:  # type: ignore[operator]
            return AllowlistRepository(session).entitled_principals(dataset_iri)

    def _inbound_links(self, iri: URIRef) -> int:
        """How many records point at this one.

        Drives the review queue's ordering (PRD §7.6: high-leverage records get
        reviewed first) and contributes to link ranking.
        """
        rows = self.records.store.select(
            """
            SELECT (COUNT(DISTINCT ?other) AS ?n) WHERE {
              GRAPH ??g {
                ?other (og:upstreamSource|prov:wasDerivedFrom|og:supersedes
                       |og:supersededBy|og:complements) ??target .
              }
            }
            """,
            {"g": NamedGraph.CATALOG.uri(), "target": iri},
        )
        return int(rows[0]["n"]) if rows and rows[0]["n"] is not None else 0

    # ---- lag -------------------------------------------------------------

    def health(self) -> ProjectorHealth:
        budget = self.settings.projector_lag_budget_s
        state = self._state()
        if state is None:
            return ProjectorHealth(None, budget, True, 0, None, None, None)
        commit, indexed = state.last_commit_at, state.last_indexed_at
        if commit is None:
            lag = None
        elif indexed is None or indexed < commit:
            lag = (datetime.now(UTC) - commit).total_seconds()
        else:
            lag = 0.0
        return ProjectorHealth(
            lag_seconds=lag,
            budget_seconds=budget,
            healthy=lag is None or lag <= budget,
            pending=state.pending_count or 0,
            last_commit_at=commit,
            last_indexed_at=indexed,
            last_full_reindex_at=state.last_full_reindex_at,
        )

    def _state(self):  # type: ignore[no-untyped-def]
        if self._session_factory is None:
            return None
        from datahub.api.models.repositories import ProjectorStateRepository

        with self._session_factory() as session:  # type: ignore[operator]
            return ProjectorStateRepository(session).current()

    def _record_commit(self) -> None:
        self._update_state(commit=True)

    def _record_indexed(self) -> None:
        self._update_state(commit=False)

    def _record_full_reindex(self) -> None:
        self._update_state(commit=False, full=True)

    def _update_state(self, *, commit: bool, full: bool = False) -> None:
        if self._session_factory is None:
            return
        try:
            from datahub.api.models.repositories import ProjectorStateRepository
        except ImportError:
            return
        with self._session_factory() as session:  # type: ignore[operator]
            repo = ProjectorStateRepository(session)
            if commit:
                repo.mark_commit()
            else:
                repo.mark_indexed(full_reindex=full)
            session.commit()


def _load_construct() -> str | None:
    """The projector's CONSTRUCT, read once at construction.

    Returns None if the file is missing, in which case the projector falls back
    to reading the record subgraph plus the computed graph. That fallback loses
    concept labels and the transitive concept closure, so it is a degraded mode,
    not an equivalent one — but a missing query file should degrade rather than
    take the whole service down.
    """
    from pathlib import Path

    path = Path(__file__).parent / "construct.rq"
    return path.read_text() if path.exists() else None


def make_projector(
    records: RecordStore,
    backend: SearchBackend,
    settings: Settings | None = None,
    session_factory: object | None = None,
) -> Projector:
    return Projector(records, backend, settings, session_factory)
