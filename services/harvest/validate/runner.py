"""pySHACL runner (PRD §7.5, §4.5).

Two things this module exists to get right:

**Level parameterisation.** A level-3 constraint must not block a level-1
record (ADR-0004). The shapes file annotates every property shape with
``og:appliesAtLevel``; this runner builds a shapes graph for a target level by
detaching everything above it. There is deliberately no list here of which
constraint applies at which level — the annotation is the mechanism, so adding
a constraint at a level is a one-line change to the shapes file.

**Pointing at the failing triple.** PRD §10 makes this the M1 done-criterion.
A report that says "invalid" is useless to a steward; one that says
*which node, which path, which value, and what to do* is the difference between
a queue that moves and one that does not.
"""

from __future__ import annotations

import functools
import threading
from dataclasses import dataclass, field
from typing import Any

from datahub.config import Settings, get_settings
from datahub.errors import ValidationFailed, Violation
from datahub.namespaces import OG, SH
from pyshacl import validate as pyshacl_validate
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF, SKOS

#: Property shapes and node shapes carrying a level above the target are
#: detached before validation.
LEVEL_PREDICATE = OG.appliesAtLevel

#: ``sh:or`` on a node shape cannot be annotated per-branch, so the shape
#: carries the level of its whole disjunction under this predicate instead.
OR_LEVEL_PREDICATE = OG.orAppliesAtLevel


@dataclass(slots=True)
class ValidationReport:
    """The outcome of validating one record at one completeness level."""

    conforms: bool
    target_level: int
    violations: list[Violation] = field(default_factory=list)
    warnings: list[Violation] = field(default_factory=list)
    #: The raw pySHACL report graph, kept so a caller can ask a question the
    #: Violation projection did not anticipate.
    report_graph: Graph | None = None

    @property
    def blocking(self) -> list[Violation]:
        """Violations that stop a record reaching the review queue's ready state."""
        return [v for v in self.violations if v.severity == "Violation"]

    def raise_if_invalid(self, message: str = "record failed validation") -> None:
        if not self.conforms:
            raise ValidationFailed(
                message, violations=self.violations, target_level=self.target_level
            )


class ValidationRunner:
    """Loads the shapes once and validates many records against them.

    Parsing SHACL and the vocabulary on every record is the obvious performance
    mistake here: the shapes graph is 600 triples and the vocabulary is 3,000,
    and the harvest pipeline validates thousands of records per run.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        # Reentrant: _build_level_graph reads self.shapes while holding the
        # lock, and a plain Lock deadlocks there. The failure mode is a hang
        # with no output, which is expensive to diagnose — see
        # tests/test_validation_runner.py::test_cold_cache_does_not_deadlock.
        self._lock = threading.RLock()
        self._shapes: Graph | None = None
        self._ontology: Graph | None = None
        self._by_level: dict[int, Graph] = {}
        self._validation_ontology: Graph | None = None
        self._context: dict[str, Any] | None = None

    # ---- loading --------------------------------------------------------

    @property
    def shapes(self) -> Graph:
        """The full shapes graph, all levels."""
        if self._shapes is None:
            with self._lock:
                if self._shapes is None:
                    graph = Graph()
                    graph.parse(self.settings.shapes_path.as_posix(), format="turtle")
                    self._shapes = graph
        return self._shapes

    @property
    def ontology(self) -> Graph:
        """The full vocabulary graph. Read by :attr:`validation_ontology`."""
        if self._ontology is None:
            with self._lock:
                if self._ontology is None:
                    graph = Graph()
                    for path in sorted(self.settings.vocab_dir.glob("*.ttl")):
                        graph.parse(path.as_posix(), format="turtle")
                    crosswalks = self.settings.vocab_dir / "crosswalks"
                    if crosswalks.is_dir():
                        for path in sorted(crosswalks.glob("*.ttl")):
                            graph.parse(path.as_posix(), format="turtle")
                    self._ontology = graph
        return self._ontology

    @property
    def validation_ontology(self) -> Graph:
        """The slice of the vocabulary a validation actually needs.

        ``sh:node`` constraints check that a concept is ``skos:inScheme`` the
        right scheme, so scheme membership and concept typing are the only
        vocabulary facts any shape reads. Two reasons this is a projection
        rather than the whole vocabulary:

        * pySHACL's ``ont_graph`` does **not** make the vocabulary visible to
          ``sh:node`` — it feeds inferencing, and with inference off it
          contributes nothing. The vocabulary has to be merged into the data
          graph, and merging 3,200 triples into every record is the difference
          between validating a harvest run in a minute and in an hour.
        * A record graph carrying the whole vocabulary would make every
          debugging dump unreadable.
        """
        if self._validation_ontology is None:
            with self._lock:
                if self._validation_ontology is None:
                    slim = Graph()
                    for triple in self.ontology.triples((None, SKOS.inScheme, None)):
                        slim.add(triple)
                    for triple in self.ontology.triples((None, RDF.type, SKOS.Concept)):
                        slim.add(triple)
                    for triple in self.ontology.triples((None, RDF.type, SKOS.ConceptScheme)):
                        slim.add(triple)
                    self._validation_ontology = slim
        return self._validation_ontology

    @property
    def context(self) -> dict[str, Any]:
        """The JSON-LD context, for validating records supplied as JSON."""
        if self._context is None:
            import json

            with self._lock:
                if self._context is None:
                    self._context = json.loads(self.settings.context_path.read_text())
        return self._context

    def shapes_for_level(self, target_level: int) -> Graph:
        """Shapes applicable at *target_level*, with higher levels detached.

        Detaching rather than filtering results: a level-3 property shape left
        attached would fire on a level-1 record, and a level-3 node shape left
        targeted would fire on every node of its class.
        """
        if target_level not in self._by_level:
            with self._lock:
                if target_level not in self._by_level:
                    self._by_level[target_level] = self._build_level_graph(target_level)
        return self._by_level[target_level]

    def _build_level_graph(self, target_level: int) -> Graph:
        graph = Graph()
        for triple in self.shapes:
            graph.add(triple)

        for shape, level in list(graph.subject_objects(LEVEL_PREDICATE)):
            if int(level) <= target_level:
                continue
            for parent in list(graph.subjects(SH.property, shape)):
                graph.remove((parent, SH.property, shape))
            # Excise the shape entirely rather than only unlinking it. An
            # orphaned sh:SPARQLTarget left behind in the graph sends pySHACL's
            # advanced mode into a non-terminating scan — a hang, not an error,
            # which is the worst way for this to fail. Discovered the hard way;
            # tests/test_validation_runner.py::test_level_filtering_leaves_no_orphans
            # keeps it discovered.
            _excise(graph, shape)

        for shape, level in list(graph.subject_objects(OR_LEVEL_PREDICATE)):
            if int(level) > target_level:
                for disjunction in list(graph.objects(shape, SH["or"])):
                    graph.remove((shape, SH["or"], disjunction))
                    _excise_list(graph, disjunction)
                graph.remove((shape, SH.message, None))

        return graph

    # ---- validating -----------------------------------------------------

    def validate(
        self,
        data: Graph,
        target_level: int = 1,
        *,
        include_ontology: bool = True,
    ) -> ValidationReport:
        """Validate *data* against the shapes applicable at *target_level*."""
        if not 1 <= target_level <= 3:
            raise ValueError(f"completeness level must be 1, 2 or 3, got {target_level}")

        if include_ontology:
            merged = Graph()
            for triple in data:
                merged.add(triple)
            for triple in self.validation_ontology:
                merged.add(triple)
            data = merged

        conforms, report_graph, _ = pyshacl_validate(
            data_graph=data,
            shacl_graph=self.shapes_for_level(target_level),
            advanced=True,  # SPARQL targets and constraints; see shapes header
            inference="none",  # entailment is materialised deliberately, not here
            abort_on_first=False,
            allow_infos=True,
            allow_warnings=True,
            meta_shacl=False,
        )
        violations, warnings = _project_results(report_graph)
        # pySHACL reports conforms=True when only warnings are present, which is
        # the behaviour we want: a warning informs the steward, a violation
        # blocks the record.
        return ValidationReport(
            conforms=conforms and not violations,
            target_level=target_level,
            violations=violations,
            warnings=warnings,
            report_graph=report_graph,
        )

    def validate_jsonld(
        self, record: dict[str, Any] | str, target_level: int = 1
    ) -> ValidationReport:
        """Validate a record supplied as JSON-LD.

        The record's own ``@context`` is used when it has one; otherwise the
        project context is injected. A record referring to the context by URL is
        resolved locally rather than fetched, so validation never depends on a
        network round trip to schema.opengrid.org.
        """
        import json

        document = json.loads(record) if isinstance(record, str) else dict(record)
        declared = document.get("@context")
        if declared is None or (
            isinstance(declared, str) and declared.startswith("https://schema.opengrid.org")
        ):
            document["@context"] = self.context["@context"]
        graph = Graph()
        graph.parse(data=json.dumps(document), format="json-ld")
        return self.validate(graph, target_level)

    def level_of(self, record: dict[str, Any]) -> int:
        """The level a record claims, defaulting to 1."""
        value = record.get("completenessLevel") or record.get("og:completenessLevel")
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 1

    def highest_passing_level(self, data: Graph) -> int:
        """The highest level the record actually satisfies.

        Used by the promotion path: a steward promotes a record by adding
        detail, and this says whether the detail is now sufficient. Returns 0
        when the record fails even at level 1.
        """
        highest = 0
        for level in (1, 2, 3):
            if self.validate(data, level).conforms:
                highest = level
            else:
                break
        return highest


# ---------------------------------------------------------------------------
# Graph surgery for level filtering
# ---------------------------------------------------------------------------


def _excise(graph: Graph, node: Any) -> None:
    """Remove a node's triples and every blank node reachable from it.

    SHACL shapes nest heavily in blank nodes — a property shape, its target, its
    ``sh:or`` list, the shapes inside that list. Removing only the top triple
    leaves the rest as unowned fragments, and an unowned ``sh:SPARQLTarget`` is
    the specific fragment that hangs pySHACL's advanced mode.

    Blank nodes are not shared between SHACL shapes in this file, so a recursive
    delete is safe; the ``seen`` set guards against a cycle regardless.
    """
    seen: set[Any] = set()
    stack = [node]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for _, obj in list(graph.predicate_objects(current)):
            if isinstance(obj, BNode):
                stack.append(obj)
        graph.remove((current, None, None))


def _excise_list(graph: Graph, head: Any) -> None:
    """Remove an rdf:List and everything its cells point at."""
    seen: set[Any] = set()
    node = head
    while node is not None and node != RDF.nil and node not in seen:
        seen.add(node)
        first = graph.value(node, RDF.first)
        nxt = graph.value(node, RDF.rest)
        if first is not None:
            _excise(graph, first)
        graph.remove((node, None, None))
        node = nxt


# ---------------------------------------------------------------------------
# Report projection and rendering
# ---------------------------------------------------------------------------


def _project_results(report: Graph) -> tuple[list[Violation], list[Violation]]:
    violations: list[Violation] = []
    warnings: list[Violation] = []
    for result in report.subjects(RDF.type, SH.ValidationResult):
        severity = _local_name(report.value(result, SH.resultSeverity))
        item = Violation(
            focus_node=str(report.value(result, SH.focusNode) or ""),
            path=_render_path(report, report.value(result, SH.resultPath)),
            message=_first_message(report, result),
            severity=severity or "Violation",
            value=_render_value(report.value(result, SH.value)),
            source_shape=str(report.value(result, SH.sourceShape) or "") or None,
            constraint=_local_name(report.value(result, SH.sourceConstraintComponent)),
        )
        (warnings if severity in ("Warning", "Info") else violations).append(item)
    violations.sort(key=lambda v: (v.focus_node, v.path or "", v.message))
    warnings.sort(key=lambda v: (v.focus_node, v.path or "", v.message))
    return violations, warnings


def _first_message(report: Graph, result: URIRef) -> str:
    messages = [str(m) for m in report.objects(result, SH.resultMessage)]
    return messages[0] if messages else "constraint violated"


def _render_path(report: Graph, path: Any) -> str | None:
    """Render a property path, compacting a known prefix so the message reads
    like the record does."""
    if path is None:
        return None
    if isinstance(path, URIRef):
        return _compact(str(path))
    # A path expression (alternative, sequence, inverse) is a blank node; the
    # rendered form is best-effort because the common case is a plain IRI.
    for predicate in (SH.inversePath, SH.alternativePath, SH.zeroOrMorePath, SH.oneOrMorePath):
        inner = report.value(path, predicate)
        if inner is not None:
            return f"{_local_name(predicate)}({_render_path(report, inner)})"
    return str(path)


def _render_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Literal):
        return (
            f'"{value}"'
            if value.datatype is None
            else f'"{value}"^^{_compact(str(value.datatype))}'
        )
    return _compact(str(value))


_PREFIXES = {
    "https://schema.opengrid.org/ns#": "og:",
    "http://www.w3.org/ns/dcat#": "dcat:",
    "http://purl.org/dc/terms/": "dct:",
    "http://www.w3.org/ns/prov#": "prov:",
    "http://www.w3.org/2004/02/skos/core#": "skos:",
    "http://www.w3.org/2001/XMLSchema#": "xsd:",
    "http://qudt.org/vocab/unit/": "unit:",
    "https://catalog.opengrid.org/ds/": "ds:",
    "https://catalog.opengrid.org/field/": "field:",
    "https://catalog.opengrid.org/dist/": "dist:",
}


def _compact(iri: str) -> str:
    for base, prefix in _PREFIXES.items():
        if iri.startswith(base):
            return prefix + iri[len(base) :]
    return f"<{iri}>"


def _local_name(node: Any) -> str | None:
    if node is None:
        return None
    text = str(node)
    for separator in ("#", "/"):
        if separator in text:
            text = text.rsplit(separator, 1)[-1]
    return text.removesuffix("ConstraintComponent") or text


def format_report(report: ValidationReport, *, colour: bool = False) -> str:
    """Human-readable validation output that points at the failing triple.

    PRD §7.5: "Validation output is human-readable and points at the specific
    triple that failed."
    """
    bold = "\033[1m" if colour else ""
    red = "\033[31m" if colour else ""
    yellow = "\033[33m" if colour else ""
    reset = "\033[0m" if colour else ""

    if report.conforms and not report.warnings:
        return f"conforms at completeness level {report.target_level}"

    lines: list[str] = []
    header = "conforms" if report.conforms else "FAILED"
    lines.append(
        f"{bold}{header} at completeness level {report.target_level}{reset} — "
        f"{len(report.violations)} violation(s), {len(report.warnings)} warning(s)"
    )
    for group, items, colour_code in (
        ("violation", report.violations, red),
        ("warning", report.warnings, yellow),
    ):
        for item in items:
            lines.append("")
            lines.append(f"{colour_code}{group}{reset}: {item.message}")
            lines.append(f"    node   {_compact(item.focus_node)}")
            if item.path:
                lines.append(f"    path   {item.path}")
            if item.value is not None:
                lines.append(f"    value  {item.value}")
            if item.constraint:
                lines.append(f"    check  {item.constraint}")
    return "\n".join(lines)


@functools.lru_cache(maxsize=1)
def get_runner() -> ValidationRunner:
    """Process-wide runner. Cleared by tests via ``get_runner.cache_clear()``."""
    return ValidationRunner()
