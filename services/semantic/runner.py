"""Running the semantic layer and writing its output back (WP-7.1, 7.3, 7.4).

Everything this module writes goes to ``og:graph/computed`` and nowhere else.
That is what makes a full recompute a graph drop rather than a migration, and
it is why a bug in a grader costs a rerun rather than a restore (PRD principle
8: derived state is droppable).

Three properties the writer is responsible for:

**Deterministic node names.** Every node it mints is named from the record and
the signal — ``…/ds/eia-930#grade-currency`` — so a recompute overwrites in
place. Content-hashed or counter-based names would accumulate: a record graded
weekly for a year would carry fifty-two grade nodes and the reader would have
to work out which one is current (ADR-0008 makes the same argument for
skolemisation).

**A recompute that changes nothing writes nothing.** ``og:lastComputedAt`` is
only meaningful if it moves when the answer moves. A writer that stamped every
pass would make the freshness lag unreadable — every signal would look
recomputed a moment ago whether or not anything had been looked at.

**Provenance for the grade, not just the grade.** Each assessment carries the
rationale that produced it. PRD §F5: *every grade derives from recorded facts*
— which is only checkable if the facts travel with the grade.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

from datahub.graph.graphs import NamedGraph
from datahub.graph.records import RecordStore, slug_of
from datahub.graph.store import GraphStore
from datahub.logging import get_logger
from datahub.namespaces import OG
from datahub.semantic.grading import (
    Assessment,
    grade_currency,
    grade_documentation,
    grade_provenance,
)
from datahub.semantic.grading.facets import GRADE_LABELS
from datahub.semantic.resolve import Part, Resolution, ResolutionReport, Resolver
from datahub.semantic.triggers import Trigger, signals_for
from datahub.semantic.vocabulary import Vocabulary
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDF, XSD

log = get_logger(__name__)


@dataclass
class RecordOutcome:
    """What the semantic layer decided about one record."""

    dataset_iri: str
    resolution: ResolutionReport
    assessments: list[Assessment] = field(default_factory=list)
    triples_written: int = 0
    changed: bool = False

    def grade(self, facet: str) -> str | None:
        for assessment in self.assessments:
            if assessment.facet == facet:
                return assessment.grade
        return None


@dataclass
class PassSummary:
    """One run over some or all of the catalog."""

    records: int = 0
    changed: int = 0
    resolved_parts: int = 0
    gaps: int = 0
    grades: dict[str, int] = field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def observe(self, outcome: RecordOutcome) -> None:
        self.records += 1
        self.changed += int(outcome.changed)
        self.resolved_parts += len(outcome.resolution.resolved)
        self.gaps += len(outcome.resolution.gaps)
        for assessment in outcome.assessments:
            key = f"{assessment.facet}:{assessment.grade or 'not-assessed'}"
            self.grades[key] = self.grades.get(key, 0) + 1

    def as_dict(self) -> dict[str, object]:
        return {
            "records": self.records,
            "changed": self.changed,
            "resolved_parts": self.resolved_parts,
            "gaps": self.gaps,
            "grades": dict(sorted(self.grades.items())),
            "seconds": (
                round((self.finished_at - self.started_at).total_seconds(), 2)
                if self.started_at and self.finished_at
                else None
            ),
        }


class SemanticRunner:
    """Resolves and grades records, and writes the result to the computed graph."""

    def __init__(
        self,
        records: RecordStore,
        *,
        vocabulary: Vocabulary | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self.records = records
        self.store: GraphStore = records.store
        self.vocabulary = vocabulary or Vocabulary.from_store(self.store)
        self.resolver = resolver or Resolver(self.vocabulary)

    # -- one record --------------------------------------------------------

    def run_record(
        self,
        dataset_id: str,
        *,
        now: datetime | None = None,
        graph: NamedGraph | None = None,
        write: bool = True,
    ) -> RecordOutcome:
        now = now or datetime.now(UTC)
        subgraph = self.records.get_graph(dataset_id, graph=graph)
        iri = URIRef(str(self.records._iri(dataset_id)))

        parts = self.resolver.parts(subgraph, iri)
        resolution = ResolutionReport(
            dataset_iri=str(iri), resolutions=[self.resolver.resolve(p) for p in parts]
        )
        level = _level(subgraph, iri)
        shape = parts[0].shape if parts else None

        assessments = [
            grade_provenance(subgraph, iri, parts, completeness_level=level, shape=shape),
            grade_documentation(subgraph, iri, parts, completeness_level=level),
            grade_currency(subgraph, iri, now=now),
        ]

        outcome = RecordOutcome(
            dataset_iri=str(iri), resolution=resolution, assessments=assessments
        )
        if write:
            computed = self.build(iri, resolution, assessments, now=now)
            outcome.triples_written = len(computed)
            outcome.changed = self._write(iri, computed, [p.iri for p in parts])
        return outcome

    # -- batches -----------------------------------------------------------

    def run_all(
        self,
        *,
        graph: NamedGraph = NamedGraph.CATALOG,
        now: datetime | None = None,
        limit: int | None = None,
    ) -> PassSummary:
        summary = PassSummary(started_at=datetime.now(UTC))
        for count, dataset_id in enumerate(self.records.list_ids(graph=graph, limit=limit)):
            summary.observe(self.run_record(dataset_id, now=now, graph=graph))
            if limit is not None and count + 1 >= limit:
                break
        summary.finished_at = datetime.now(UTC)
        log.info("semantic pass complete", **summary.as_dict())  # type: ignore[arg-type]
        return summary

    def run_scheduled(self, **kwargs: object) -> PassSummary:
        """The scheduled batch (PRD §F4.3).

        Runs over every record because that is what a scheduled signal needs: a
        dataset goes stale by *not* being written, so there is no set of
        recently-touched records to narrow to. Narrowing this to recent writes
        is the exact bug the trigger split exists to prevent, so it is named
        here rather than left as an optimisation somebody might make.
        """
        log.info(
            "scheduled semantic pass",
            signals=[s.name for s in signals_for(Trigger.SCHEDULE)],
        )
        return self.run_all(**kwargs)  # type: ignore[arg-type]

    # -- building the computed graph ---------------------------------------

    def build(
        self,
        dataset_iri: URIRef,
        resolution: ResolutionReport,
        assessments: Iterable[Assessment],
        *,
        now: datetime,
    ) -> Graph:
        """The triples this record's computed graph should hold."""
        graph = Graph()
        slug = slug_of(str(dataset_iri))

        for assessment in assessments:
            _emit_assessment(graph, dataset_iri, slug, assessment, now)

        for item in resolution.resolutions:
            _emit_resolution(graph, item)

        for signal in ("concept-resolution", "provenance-grade", "documentation-grade"):
            _emit_signal(graph, dataset_iri, slug, signal, now)
        _emit_signal(graph, dataset_iri, slug, "currency-grade", now)
        return graph

    def _write(self, dataset_iri: URIRef, computed: Graph, part_iris: list[str]) -> bool:
        """Replace this record's computed triples. Returns whether anything changed.

        The delete is scoped to subjects this module owns — the record, its
        parts, and the nodes named after either. A ``DROP GRAPH`` would take
        every other record's computed state with it, and a delete of
        "everything mentioning this record" would take the inbound links other
        records computed *about* it.
        """
        existing = self._existing(dataset_iri, part_iris)
        if _isomorphic(existing, computed):
            return False

        graph = self.store.get_graph(NamedGraph.COMPUTED)
        for triple in existing:
            graph.remove(triple)
        self.store.add_graph(NamedGraph.COMPUTED, computed)
        return True

    def _existing(self, dataset_iri: URIRef, part_iris: list[str]) -> Graph:
        """What this record already has in the computed graph.

        Part-level statements hang off field IRIs, which live under a different
        path than the dataset (``…/field/eia-930/demand``, not
        ``…/ds/eia-930#…``), so the record's own prefix does not find them. The
        part IRIs come from the record as it is *now*.

        The consequence is that computed state for a field since removed from a
        record is not swept by an incremental pass. That is the right trade:
        scanning the whole computed graph for orphans on every record write
        would make a single-record recompute cost the size of the catalog, and
        the computed graph is droppable by design — a full rebuild removes
        them.
        """
        owned = Graph()
        computed = self.store.get_graph(NamedGraph.COMPUTED)
        prefixes = [str(dataset_iri), *part_iris]
        for subject, predicate, obj in computed:
            if not isinstance(subject, URIRef):
                continue
            text = str(subject)
            if any(text == prefix or text.startswith(f"{prefix}#") for prefix in prefixes):
                owned.add((subject, predicate, obj))
        return owned


# ---------------------------------------------------------------------------
# Emitters
# ---------------------------------------------------------------------------


def _emit_assessment(
    graph: Graph, dataset_iri: URIRef, slug: str, assessment: Assessment, now: datetime
) -> None:
    """One ``og:QualityGrade`` node, or nothing at all.

    Nothing at all when the facet was not assessed: an absent grade and a grade
    of ``None`` are the same claim, and writing a node that says "no grade"
    invites a reader to render it as one. The rationale for *why* it was not
    assessed is still written, on its own node, because a dataset owner needs
    to know what to add.
    """
    node = URIRef(f"{dataset_iri}#grade-{assessment.facet}")
    graph.add((dataset_iri, OG.qualityGrade, node))
    graph.add((node, RDF.type, OG.QualityGrade))
    graph.add((node, OG.facet, Literal(assessment.facet)))
    graph.add((node, OG.gradedAt, Literal(now.isoformat(), datatype=XSD.dateTime)))
    graph.add((node, OG.gradeRationale, Literal(assessment.rationale, lang="en")))

    if assessment.grade is None:
        graph.add((node, OG.notYetAssessed, Literal(True)))
        return

    graph.add((node, OG.grade, Literal(assessment.grade)))
    graph.add(
        (
            node,
            OG.gradeLabel,
            Literal(GRADE_LABELS[assessment.facet][assessment.grade], lang="en"),
        )
    )
    for part_iri, part_grade in sorted(assessment.per_part.items()):
        part_node = URIRef(f"{part_iri}#grade-{assessment.facet}")
        graph.add((URIRef(part_iri), OG.qualityGrade, part_node))
        graph.add((part_node, RDF.type, OG.QualityGrade))
        graph.add((part_node, OG.facet, Literal(assessment.facet)))
        graph.add((part_node, OG.grade, Literal(part_grade)))
        graph.add((part_node, OG.gradedAt, Literal(now.isoformat(), datatype=XSD.dateTime)))
    _ = slug


def _emit_resolution(graph: Graph, item: Resolution) -> None:
    """A concept assignment, or an explicit gap. Never silence.

    A source-confirmed assignment is skipped: it is already on the record in
    the catalog graph, and re-asserting it here would make a steward's decision
    indistinguishable from this module's, which is precisely the distinction
    PRD §F4.8 requires be kept.
    """
    part = URIRef(item.part.iri)
    if item.basis == "source-confirmed on the record":
        return

    if item.concept:
        graph.add((part, OG.concept, URIRef(item.concept)))
        graph.add((part, OG.inferredAssignment, Literal(True)))
        graph.add((part, OG.inferenceBasis, Literal(item.basis, lang="en")))
        graph.add(
            (part, OG.conceptConfidence, Literal(round(item.confidence, 4), datatype=XSD.double))
        )
        if item.unit:
            graph.add((part, OG.unit, URIRef(item.unit)))
        return

    gap = URIRef(f"{item.part.iri}#conceptGap")
    graph.add((part, OG.conceptGap, gap))
    graph.add((gap, RDF.type, OG.ConceptGap))
    graph.add((gap, OG.gapReason, Literal(item.gap_reason or "no confident mapping", lang="en")))
    for alternative in item.alternatives:
        graph.add((gap, OG.candidateConcept, URIRef(alternative)))


def _emit_signal(graph: Graph, dataset_iri: URIRef, slug: str, name: str, now: datetime) -> None:
    """``og:lastComputedAt``, so the freshness lag is visible rather than hidden.

    PRD §F4: *evaluators need this; modelers can ignore it.* A grade with no
    timestamp cannot be told apart from one computed against a vocabulary two
    versions old.
    """
    node = URIRef(f"{dataset_iri}#signal-{name}")
    graph.add((dataset_iri, OG.lastComputedAt, node))
    graph.add((node, RDF.type, OG.ComputedSignal))
    graph.add((node, OG.signalName, Literal(name)))
    graph.add((node, OG.computedAt, Literal(now.isoformat(), datatype=XSD.dateTime)))
    _ = slug


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _level(graph: Graph, iri: URIRef) -> int:
    value = graph.value(iri, OG.completenessLevel)
    try:
        return int(value) if value is not None else 1
    except (TypeError, ValueError):
        return 1


def _isomorphic(left: Graph, right: Graph) -> bool:
    """Same triples once timestamps are set aside.

    Timestamps are excluded deliberately. Comparing them would make every pass
    look like a change — which is the failure this comparison exists to
    prevent, since a signal that is always "just recomputed" carries no
    information about freshness at all.
    """
    return _stable(left) == _stable(right)


def _stable(graph: Graph) -> set[tuple[str, str, str]]:
    skip = {OG.gradedAt, OG.computedAt}
    return {(str(s), str(p), str(o)) for s, p, o in graph if p not in skip}


def parts_of(resolver: Resolver, graph: Graph, dataset_iri: URIRef) -> Iterator[Part]:
    yield from resolver.parts(graph, dataset_iri)


__all__ = ["PassSummary", "RecordOutcome", "SemanticRunner"]
