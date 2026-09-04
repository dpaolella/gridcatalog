"""Catalog record read and write.

A "record" is not one node. It is a dataset node plus everything that belongs
to it and nothing that merely links to it: distributions and their link-health
blocks, fields and their gap markers and code lists, quality flags, temporal
extents, the blank nodes those hang off. A link to *another dataset* — an
upstream source, a supersession — is a reference, not a part.

Getting that boundary right is what makes a record independently writable. If
the subgraph reached into upstream datasets, writing one record would rewrite
its neighbours; if it stopped at the dataset node, a distribution would be
orphaned the first time a record was replaced.

This module and :mod:`datahub.graph.store` are the only places catalog RDF is
constructed. Everything above them speaks JSON-LD.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from datahub.config import Settings, get_settings
from datahub.errors import NotFound, ValidationFailed
from datahub.graph.graphs import NamedGraph, record_graph
from datahub.graph.skolem import skolemize
from datahub.graph.store import GraphStore
from datahub.harvest.validate import ValidationReport, ValidationRunner
from datahub.logging import get_logger
from datahub.namespaces import DATASET_BASE, OG
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCAT, DCTERMS, RDF, XSD
from rdflib.query import ResultRow

log = get_logger(__name__)

#: Predicates that *contain* rather than reference: a node reached through one
#: of these belongs to the record and is written and deleted with it.
#:
#: Enumerating containment rather than reference is the safer direction. The set
#: of ways a record can point at something outside itself grows every time a
#: field is added — upstream sources, licences, concepts, units, custodians,
#: workflow tags — and forgetting one silently drags a neighbouring record into
#: the subgraph, so writing one record rewrites another. The set of ways a
#: record contains part of itself is fixed by the schema and short.
CONTAINMENT_PREDICATES: tuple[URIRef, ...] = (
    DCAT.distribution,
    OG.hasField,
    OG.hasFileGroup,
    OG.hasLayer,
    OG.hasVariable,
    OG.hasDimension,
    OG.qualityFlags,
    OG.linkHealth,
    OG.conceptGap,
    OG.codeList,
    OG.codeValue,
    OG.valueRange,
    OG.lastComputedAt,
    OG.qualityGrade,
    OG.hasNodeType,
    OG.hasEdgeType,
    OG.sharedOriginWarning,
    DCTERMS.temporal,
    RDF.first,
    RDF.rest,
)


@dataclass(slots=True)
class DistributionChange:
    """One field of one distribution changing value.

    Carried out of :meth:`RecordStore.put` so the caller can write revision
    history (PRD §F1.11). Emitted rather than written here because history is
    operational state and this module does not talk to Postgres.
    """

    distribution_id: str
    field: str
    old_value: str | None
    new_value: str | None


@dataclass(slots=True)
class PutResult:
    dataset_id: str
    graph_name: str
    created: bool
    triples_written: int
    triples_removed: int
    #: Triples written for shared upstream nodes the record introduced. Merged,
    #: never replaced: another record may point at the same node.
    ancillary_written: int = 0
    validation: ValidationReport | None = None
    distribution_changes: list[DistributionChange] = field(default_factory=list)
    changed_predicates: set[str] = field(default_factory=set)

    @property
    def changed(self) -> bool:
        return self.created or bool(self.changed_predicates) or self.triples_removed > 0


class RecordStore:
    """Read and write catalog records.

    Writes are a single SPARQL Update per record — atomic on Fuseki, and
    effectively atomic on rdflib. A record is never half-replaced.
    """

    def __init__(
        self,
        store: GraphStore,
        settings: Settings | None = None,
        runner: ValidationRunner | None = None,
    ) -> None:
        self.store = store
        self.settings = settings or get_settings()
        self._runner = runner

    @property
    def runner(self) -> ValidationRunner:
        if self._runner is None:
            self._runner = ValidationRunner(self.settings)
        return self._runner

    # ---- reading --------------------------------------------------------

    def exists(self, dataset_id: str, *, graph: NamedGraph | None = None) -> bool:
        graphs = (graph,) if graph else (NamedGraph.CATALOG, NamedGraph.DRAFT)
        for name in graphs:
            if self.store.ask(
                "ASK { GRAPH ??g { ??s a dcat:Dataset } }",
                {"g": URIRef(str(name)), "s": self._iri(dataset_id)},
            ):
                return True
        return False

    def graph_of(self, dataset_id: str) -> NamedGraph | None:
        """Which named graph holds a record, or None if it is absent."""
        for name in (NamedGraph.CATALOG, NamedGraph.DRAFT):
            if self.exists(dataset_id, graph=name):
                return name
        return None

    def get_graph(
        self, dataset_id: str, *, graph: NamedGraph | None = None, include_computed: bool = False
    ) -> Graph:
        """A record's complete subgraph.

        ``include_computed`` merges the semantic layer's output for the record —
        grades, resolutions — from ``og:graph/computed``. Off by default: a
        round trip that included computed state would write it back into the
        catalog graph, where a recompute could no longer drop it.
        """
        name = graph or self.graph_of(dataset_id)
        if name is None:
            raise NotFound(f"no record for {dataset_id}", dataset_id=dataset_id)
        subgraph = self._gather(self._iri(dataset_id), name)
        if include_computed:
            for triple in self._gather(self._iri(dataset_id), NamedGraph.COMPUTED):
                subgraph.add(triple)
        return subgraph

    def get(
        self, dataset_id: str, *, graph: NamedGraph | None = None, include_computed: bool = False
    ) -> dict[str, Any]:
        """A record as JSON-LD, compacted against the project context."""
        subgraph = self.get_graph(dataset_id, graph=graph, include_computed=include_computed)
        return self.to_jsonld(subgraph)

    def to_jsonld(self, subgraph: Graph) -> dict[str, Any]:
        """A record as JSON-LD, compacted against the project context."""
        context = self.runner.context["@context"]
        document: dict[str, Any] = json.loads(
            subgraph.serialize(format="json-ld", context=context, auto_compact=True)
        )
        # rdflib emits every typed literal as a JSON string, even with
        # use_native_types. That is not a cosmetic problem: "false" is truthy in
        # Python and in JavaScript, so a consumer writing
        # `if record["anonymousAccess"]` would read an account-gated dataset as
        # openly accessible. Coerce back to the types the context declares.
        # rdflib cannot compact a term that is both @container: @set and
        # @language, and emits the raw CURIE with expanded values instead. Four
        # of the terms it affects are the multi-valued human-readable ones a UI
        # shows. Repaired here rather than by dropping the language tag from
        # the context, because the context is correct and the serialiser is not.
        document = repair_language_sets(document, context)
        document = nativise(document, context)
        # Nest what the record contains, so a record read back has the shape it
        # was written in. rdflib compacts to a flat @graph of cross-referencing
        # nodes, which is valid JSON-LD and unpleasant to consume: a client
        # asking for a dataset's quality flags would get an IRI and have to go
        # looking for the node it names.
        document = frame(document, context)
        # Absolute IRIs for identifier-valued terms. rdflib compacts an IRI to
        # a CURIE wherever the context happens to declare a matching prefix, so
        # a licence comes back as ``spdx:CC-BY-4.0`` while a data domain, whose
        # base has no prefix, comes back in full. A client should not have to
        # handle both, and the readability that compaction buys is in the term
        # names, not in the values.
        document = absolutise(document, context)
        # rdflib inlines the expanded context; replace it with the URL so a
        # record on the wire is small and consumers cache one copy.
        document["@context"] = f"{self.settings.catalog_base_url}/context/opengrid-datahub.jsonld"
        return document

    def list_ids(
        self,
        *,
        graph: NamedGraph = NamedGraph.CATALOG,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[str]:
        query = """
        SELECT ?s WHERE { GRAPH ??g { ?s a dcat:Dataset } } ORDER BY ?s
        """
        if limit is not None:
            query += f"\nLIMIT {int(limit)} OFFSET {int(offset)}"
        rows = self.store.select(query, {"g": URIRef(str(graph))})
        return [str(r["s"]) for r in rows]

    def count(self, *, graph: NamedGraph = NamedGraph.CATALOG) -> int:
        rows = self.store.select(
            "SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { GRAPH ??g { ?s a dcat:Dataset } }",
            {"g": URIRef(str(graph))},
        )
        return int(rows[0]["n"]) if rows else 0

    def iter_records(
        self, *, graph: NamedGraph = NamedGraph.CATALOG, batch: int = 100
    ) -> Iterator[Graph]:
        """Every record in a graph, one subgraph at a time.

        Batched rather than loaded whole: a full reindex must not need the
        catalog in memory, or reindex-from-scratch stops being routine.
        """
        offset = 0
        while True:
            ids = self.list_ids(graph=graph, limit=batch, offset=offset)
            if not ids:
                return
            for dataset_id in ids:
                yield self.get_graph(dataset_id, graph=graph)
            offset += batch

    # ---- writing --------------------------------------------------------

    def put(
        self,
        record: dict[str, Any] | Graph,
        *,
        graph: NamedGraph | None = None,
        validate: bool = True,
        target_level: int | None = None,
    ) -> PutResult:
        """Write a record, replacing any existing one atomically.

        The graph is chosen from the record's own ``og:reviewState`` unless one
        is given: confirmed records live in the catalog, everything else in
        draft. That mapping lives in one place (``graphs.record_graph``) so
        entitlement rules only ever have to reason over the catalog graph.
        """
        parsed = record if isinstance(record, Graph) else self._parse(record)
        dataset_iri = self._dataset_iri_of(parsed)
        # No blank nodes reach the store (see datahub.graph.skolem). DELETE DATA
        # cannot match a blank node, so keeping them would make every rewrite
        # accumulate the parts it meant to replace.
        incoming = skolemize(parsed, dataset_iri)
        state = self._literal(incoming, dataset_iri, OG.reviewState) or "draft"
        name = graph or record_graph(state)

        level = target_level
        if level is None:
            declared = self._literal(incoming, dataset_iri, OG.completenessLevel)
            level = int(declared) if declared else 1

        report: ValidationReport | None = None
        if validate:
            report = self.runner.validate(incoming, level)
            if not report.conforms:
                raise ValidationFailed(
                    f"{dataset_iri} failed validation at completeness level {level}",
                    violations=report.violations,
                    dataset_id=str(dataset_iri),
                    target_level=level,
                )

        existing = self._gather(dataset_iri, name)
        created = len(existing) == 0
        owned, ancillary = self._split(dataset_iri, incoming)
        changes = self._distribution_changes(existing, owned)
        changed = self._changed_predicates(existing, owned)

        self._replace(name, existing, owned, ancillary)

        log.info(
            "record written",
            dataset=str(dataset_iri),
            graph=str(name),
            created=created,
            triples=len(incoming),
            changed=len(changed),
        )
        return PutResult(
            dataset_id=str(dataset_iri),
            graph_name=str(name),
            created=created,
            triples_written=len(owned),
            ancillary_written=len(ancillary),
            triples_removed=len(existing),
            validation=report,
            distribution_changes=changes,
            changed_predicates=changed,
        )

    def put_many(self, records: Iterable[dict[str, Any] | Graph], **kwargs: Any) -> list[PutResult]:
        return [self.put(record, **kwargs) for record in records]

    def delete(self, dataset_id: str, *, graph: NamedGraph | None = None) -> int:
        name = graph or self.graph_of(dataset_id)
        if name is None:
            return 0
        existing = self._gather(self._iri(dataset_id), name)
        if not len(existing):
            return 0
        self._remove(name, existing)
        log.info("record deleted", dataset=dataset_id, graph=str(name), triples=len(existing))
        return len(existing)

    def promote(
        self, dataset_id: str, *, reviewed_by: str | None = None, validate: bool = True
    ) -> PutResult:
        """Move a draft record into the catalog, marking it confirmed.

        Publication is per record, not per batch (PRD §7.6).
        """
        subgraph = self.get_graph(dataset_id, graph=NamedGraph.DRAFT)
        iri = self._iri(dataset_id)
        subgraph.remove((iri, OG.reviewState, None))
        subgraph.add((iri, OG.reviewState, Literal("confirmed")))
        if reviewed_by:
            subgraph.remove((iri, OG.reviewedBy, None))
            subgraph.add((iri, OG.reviewedBy, URIRef(reviewed_by)))
        subgraph.remove((iri, OG.reviewedAt, None))
        subgraph.add((iri, OG.reviewedAt, Literal(datetime.now(UTC))))

        result = self.put(subgraph, graph=NamedGraph.CATALOG, validate=validate)
        self.delete(dataset_id, graph=NamedGraph.DRAFT)
        return result

    def demote(self, dataset_id: str, *, reason: str | None = None) -> PutResult:
        """Move a published record back to draft, flagged for re-review.

        Used when a re-harvest finds a source change under a steward-confirmed
        field: the record is flagged rather than silently overwritten
        (PRD §7.6).
        """
        subgraph = self.get_graph(dataset_id, graph=NamedGraph.CATALOG)
        iri = self._iri(dataset_id)
        subgraph.remove((iri, OG.reviewState, None))
        subgraph.add((iri, OG.reviewState, Literal("flagged")))
        if reason:
            subgraph.add((iri, OG.knownIssue, Literal(reason, lang="en")))
        result = self.put(subgraph, graph=NamedGraph.DRAFT, validate=False)
        self.delete(dataset_id, graph=NamedGraph.CATALOG)
        return result

    # ---- subgraph boundary ----------------------------------------------

    def _gather(self, dataset_iri: URIRef, graph: NamedGraph) -> Graph:
        """Everything belonging to a record, and nothing merely linked to it.

        One CONSTRUCT: walk outward along the containment predicates only, then
        take every triple of every node reached. Expressing the boundary as a
        property path rather than as a loop of per-node queries matters twice
        over — it is one round trip instead of dozens, and it traverses blank
        nodes natively, which a loop cannot do because a blank node has no name
        to bind into a query.
        """
        path = " | ".join(f"<{p}>" for p in CONTAINMENT_PREDICATES)
        return self.store.construct(
            f"""
            CONSTRUCT {{ ?s ?p ?o }}
            WHERE {{
              GRAPH <{graph}> {{
                ??root ({path})* ?s .
                ?s ?p ?o .
              }}
            }}
            """,
            {"root": dataset_iri},
        )

    def _split(self, dataset_iri: URIRef, incoming: Graph) -> tuple[Graph, Graph]:
        """Separate the record itself from ancillary nodes it introduces.

        A document may describe an uncatalogued upstream — the mesoscale run
        behind the Global Wind Atlas, the satellite retrieval behind NSRDB.
        Those are shared: another record may reference the same node, and PRD
        §4.1 D4 is explicit that an absent upstream link reads as "no source"
        rather than "not catalogued", so they have to be stored.

        They are merged, never replaced. Deleting them with the record that
        happened to introduce them would break every other record pointing at
        the same node.
        """
        path = " | ".join(f"<{p}>" for p in CONTAINMENT_PREDICATES)
        reachable = cast(
            "Iterable[ResultRow]",
            incoming.query(f"SELECT ?s WHERE {{ <{dataset_iri}> ({path})* ?s }}"),
        )
        owned_nodes = {row[0] for row in reachable}
        owned, ancillary = Graph(), Graph()
        for triple in incoming:
            (owned if triple[0] in owned_nodes else ancillary).add(triple)
        return owned, ancillary

    def _replace(self, graph: NamedGraph, old: Graph, owned: Graph, ancillary: Graph) -> None:
        """One update: delete the old subgraph, insert the new one, merge the rest."""
        parts: list[str] = []
        if len(old):
            parts.append(f"DELETE DATA {{ GRAPH <{graph}> {{\n{_ntriples(old)}\n}} }}")
        parts.append(f"INSERT DATA {{ GRAPH <{graph}> {{\n{_ntriples(owned)}\n}} }}")
        if len(ancillary):
            parts.append(f"INSERT DATA {{ GRAPH <{graph}> {{\n{_ntriples(ancillary)}\n}} }}")
        self.store.update(" ;\n".join(parts))

    def _remove(self, graph: NamedGraph, subgraph: Graph) -> None:
        self.store.update(f"DELETE DATA {{ GRAPH <{graph}> {{\n{_ntriples(subgraph)}\n}} }}")

    # ---- diffing --------------------------------------------------------

    def _distribution_changes(self, old: Graph, new: Graph) -> list[DistributionChange]:
        """Per-distribution field changes, for the revision history.

        Tracks the fields a probe or a re-harvest can move: the URL above all,
        because a stable redirect auto-updates it and PRD §F1.12 requires the
        old value to stay readable.
        """
        tracked = {
            DCAT.accessURL: "accessURL",
            DCAT.downloadURL: "downloadURL",
            DCAT.byteSize: "byteSize",
            DCAT.mediaType: "mediaType",
            OG.supportsRangeRequests: "supportsRangeRequests",
            OG.chunkIndexMethod: "chunkIndexMethod",
            OG.accessRestriction: "accessRestriction",
            OG.subsettingProtocol: "subsettingProtocol",
        }
        changes: list[DistributionChange] = []
        for distribution in set(new.subjects(RDF.type, DCAT.Distribution)) | set(
            old.subjects(RDF.type, DCAT.Distribution)
        ):
            for predicate, label in tracked.items():
                before = old.value(distribution, predicate)
                after = new.value(distribution, predicate)
                if before == after:
                    continue
                changes.append(
                    DistributionChange(
                        distribution_id=str(distribution),
                        field=label,
                        old_value=str(before) if before is not None else None,
                        new_value=str(after) if after is not None else None,
                    )
                )
        return changes

    @staticmethod
    def _changed_predicates(old: Graph, new: Graph) -> set[str]:
        """Predicates whose value set differs. Drives the re-review trigger."""

        def by_predicate(graph: Graph) -> dict[str, set[str]]:
            out: dict[str, set[str]] = {}
            for subject, predicate, obj in graph:
                out.setdefault(str(predicate), set()).add(f"{subject}|{obj}")
            return out

        before, after = by_predicate(old), by_predicate(new)
        return {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}

    # ---- parsing --------------------------------------------------------

    def _parse(self, record: dict[str, Any]) -> Graph:
        document = dict(record)
        declared = document.get("@context")
        if declared is None or isinstance(declared, str):
            document["@context"] = self.runner.context["@context"]
        graph = Graph()
        graph.parse(data=json.dumps(document), format="json-ld")
        return normalise_literals(graph)

    @staticmethod
    def _dataset_iri_of(graph: Graph) -> URIRef:
        datasets = [s for s in graph.subjects(RDF.type, DCAT.Dataset) if isinstance(s, URIRef)]
        if not datasets:
            raise ValidationFailed(
                "record contains no dcat:Dataset node with an IRI. A record without an "
                "identity cannot be written, read back or linked to."
            )
        if len(datasets) > 1:
            raise ValidationFailed(
                f"record contains {len(datasets)} dataset nodes: {sorted(map(str, datasets))}. "
                "Write them one at a time; a multi-record document has no single "
                "review state and no single subgraph boundary."
            )
        return datasets[0]

    @staticmethod
    def _literal(graph: Graph, subject: URIRef, predicate: URIRef) -> str | None:
        value = graph.value(subject, predicate)
        return str(value) if value is not None else None

    @staticmethod
    def _iri(dataset_id: str) -> URIRef:
        if dataset_id.startswith(("http://", "https://")):
            return URIRef(dataset_id)
        return URIRef(DATASET_BASE + dataset_id)


#: xsd datatypes that must survive as JSON natives rather than as strings.
_NATIVE_CASTS: dict[str, Any] = {
    "xsd:boolean": lambda v: str(v).strip().lower() in ("true", "1"),
    "xsd:integer": int,
    "xsd:long": int,
    "xsd:int": int,
    "xsd:double": float,
    "xsd:decimal": float,
    "xsd:float": float,
}


def nativise(document: Any, context: dict[str, Any]) -> Any:
    """Restore JSON native types the JSON-LD serialiser flattened to strings.

    rdflib emits every typed literal as a string. For a date that is harmless;
    for a boolean it is a correctness bug, because ``"false"`` is truthy in
    both languages every consumer of this API is written in.
    """
    casts = {
        term: _NATIVE_CASTS[definition["@type"]]
        for term, definition in context.items()
        if isinstance(definition, dict) and definition.get("@type") in _NATIVE_CASTS
    }

    def walk(node: Any, term: str | None = None) -> Any:
        if isinstance(node, dict):
            return {key: walk(value, key) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item, term) for item in node]
        cast = casts.get(term or "")
        if cast is None or not isinstance(node, str):
            return node
        try:
            return cast(node)
        except (TypeError, ValueError):
            # A malformed value is left as it was found rather than dropped:
            # losing it would hide the defect, and the shape is what SHACL is
            # for.
            return node

    return walk(document)


def _expand_term(value: str, context: dict[str, Any]) -> str:
    """Resolve a context term's ``@id`` to a full IRI."""
    prefix, sep, rest = value.partition(":")
    if not sep or rest.startswith("//"):
        return value
    base = context.get(prefix)
    if isinstance(base, str):
        return f"{base}{rest}"
    return value


def containment_terms(context: dict[str, Any]) -> frozenset[str]:
    """The context's term names for the containment predicates.

    Derived from :data:`CONTAINMENT_PREDICATES` rather than listed again, so a
    predicate cannot be containment for the purpose of writing a record and a
    reference for the purpose of reading one back.
    """
    contained = {str(predicate) for predicate in CONTAINMENT_PREDICATES}
    terms = set()
    for term, definition in context.items():
        if term.startswith("@"):
            continue
        iri = definition if isinstance(definition, str) else definition.get("@id")
        if isinstance(iri, str) and _expand_term(iri, context) in contained:
            terms.add(term)
    return frozenset(terms)


def frame(document: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Nest contained nodes inside the node that contains them.

    Only along containment predicates, and only once per node: a reference to
    another *dataset* stays an IRI, because inlining it would make one record's
    document contain another record, and a client could not tell which parts it
    is allowed to edit.

    ``@graph`` is kept even when one node remains, so the shape does not change
    with the contents of a record — a client indexing ``@graph[0]`` should not
    break on the one record that happens to have no distributions.
    """
    nodes = document.get("@graph")
    if not isinstance(nodes, list):
        return document

    terms = containment_terms(context)
    by_id = {n["id"]: n for n in nodes if isinstance(n, dict) and isinstance(n.get("id"), str)}
    consumed: set[str] = set()

    def inline(value: Any) -> Any:
        if isinstance(value, list):
            return [inline(item) for item in value]
        if isinstance(value, str) and value in by_id and value not in consumed:
            consumed.add(value)
            return nest(by_id[value])
        return value

    def nest(node: dict[str, Any]) -> dict[str, Any]:
        return {key: inline(value) if key in terms else value for key, value in node.items()}

    roots = [n for n in nodes if n.get("type") == "Dataset"]
    framed = [nest(root) for root in roots]
    # Anything not reached from a dataset stays at the top level rather than
    # being dropped. A node the framing does not understand is a bug to see,
    # not a triple to lose.
    root_ids = {id(node) for node in roots}
    framed += [
        nest(node) for node in nodes if id(node) not in root_ids and node.get("id") not in consumed
    ]
    return {**{k: v for k, v in document.items() if k != "@graph"}, "@graph": framed}


def repair_language_sets(document: Any, context: dict[str, Any]) -> Any:
    """Compact the terms rdflib leaves expanded.

    A term declared ``{"@container": "@set", "@language": "en"}`` comes back as
    its CURIE carrying ``{"@language": ..., "@value": ...}`` objects. Rewritten
    to the term name and a list of plain strings — but only where the language
    matches what the term declares. A value in another language is left exactly
    as the serialiser produced it: it does not fit the term's definition, and
    quietly folding it in would lose the fact that it is there.
    """
    repairs: dict[str, tuple[str, str]] = {}
    for term, definition in context.items():
        if not isinstance(definition, dict):
            continue
        language = definition.get("@language")
        if definition.get("@container") != "@set" or not language:
            continue
        iri = definition.get("@id", "")
        for key in {iri, _expand_term(iri, context)}:
            if key:
                repairs[key] = (term, language)

    def repair(node: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in node.items():
            target = repairs.get(key)
            if target is None:
                out[key] = walk(value)
                continue
            term, language = target
            items = value if isinstance(value, list) else [value]
            plain = [
                item["@value"]
                for item in items
                if isinstance(item, dict) and item.get("@language") == language
            ]
            if len(plain) != len(items):
                out[key] = walk(value)
                continue
            out[term] = plain
        return out

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return repair(node)
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(document)


def absolutise(document: Any, context: dict[str, Any]) -> Any:
    """Expand CURIE values of identifier-valued terms back to absolute IRIs.

    Term *names* stay compact — that is where compaction earns its keep. Term
    *values* that identify something are expanded, so every IRI in a record
    looks the same whether or not the context happens to declare a prefix for
    its namespace.
    """
    id_terms = {
        term
        for term, definition in context.items()
        if isinstance(definition, dict) and definition.get("@type") == "@id"
    } | {"id", "@id"}

    def walk(node: Any, term: str | None = None) -> Any:
        if isinstance(node, dict):
            return {key: walk(value, key) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item, term) for item in node]
        if term in id_terms and isinstance(node, str):
            return _expand_term(node, context)
        return node

    return walk(document)


def dataset_node(document: dict[str, Any]) -> dict[str, Any]:
    """The ``dcat:Dataset`` node of a JSON-LD record document."""
    if document.get("type") == "Dataset":
        return document
    for node in document.get("@graph", []):
        if node.get("type") == "Dataset":
            return node
    raise NotFound("document contains no dataset node")


def normalise_literals(graph: Graph) -> Graph:
    """Repair literals whose Python value contradicts their declared datatype.

    rdflib's JSON-LD parser applies ``@type`` coercion by stamping the datatype
    onto the value it already parsed, so a JSON number coerced to
    ``xsd:decimal`` arrives as a decimal-typed literal holding a ``float``.
    SHACL's datatype check rejects that, correctly — the term is internally
    inconsistent — and the resulting message points at a value that looks
    perfectly fine, which is close to undebuggable.

    The context now declares ``xsd:double`` wherever values arrive as JSON
    numbers, so this should find nothing. It stays because the next term to be
    added will not necessarily follow that rule, and a silent inconsistency in a
    stored literal is worse than the cost of one pass over a small graph.
    """
    repaired = Graph()
    for prefix, namespace in graph.namespaces():
        repaired.bind(prefix, namespace)
    for subject, predicate, obj in graph:
        if isinstance(obj, Literal) and obj.datatype is not None and obj.ill_typed is None:
            obj = Literal(str(obj), datatype=obj.datatype)
        repaired.add((subject, predicate, obj))
    return repaired


def slug_of(dataset_iri: str) -> str:
    """The stable slug in a dataset IRI, used in URLs and search document ids."""
    return dataset_iri.rstrip("/").rsplit("/", 1)[-1]


def _ntriples(graph: Graph) -> str:
    """Serialise for embedding in a SPARQL Update.

    N-Triples rather than Turtle: no prefixes to declare, no relative IRIs, and
    nothing that changes meaning depending on the surrounding query's base.
    """
    return graph.serialize(format="nt").strip()


def read_bbox(graph: Graph, dataset_iri: URIRef) -> list[float] | None:
    """A dataset's extent as ``[minLon, minLat, maxLon, maxLat]``, or None.

    Stored as four scalar properties rather than as an ``rdf:List``. An ordered
    collection buys ordering nobody needs and costs three things that are
    wanted: a SPARQL spatial filter becomes a plain comparison instead of a list
    walk, JSON-LD serialisation stops depending on blank-node list cells, and
    the four numbers are individually addressable by the semantic layer.
    """
    values: list[float] = []
    for predicate in (OG.bboxMinLon, OG.bboxMinLat, OG.bboxMaxLon, OG.bboxMaxLat):
        value = graph.value(dataset_iri, predicate)
        if value is None:
            return None
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            return None
    return values


def bbox_wkt(bbox: Sequence[float]) -> str:
    """The DCAT-AP-compatible WKT form of a bounding box."""
    min_lon, min_lat, max_lon, max_lat = bbox
    return (
        f"POLYGON(({min_lon} {min_lat}, {max_lon} {min_lat}, "
        f"{max_lon} {max_lat}, {min_lon} {max_lat}, {min_lon} {min_lat}))"
    )


def write_bbox(graph: Graph, dataset_iri: URIRef, bbox: Sequence[float]) -> None:
    """Set a dataset's extent, keeping the scalars and the WKT form in step."""
    if len(bbox) != 4:
        raise ValueError(f"a bounding box has four values, got {len(bbox)}")
    min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox)
    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180):
        raise ValueError(f"longitude out of range in {bbox}")
    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise ValueError(f"latitude out of range in {bbox}")
    for predicate, value in (
        (OG.bboxMinLon, min_lon),
        (OG.bboxMinLat, min_lat),
        (OG.bboxMaxLon, max_lon),
        (OG.bboxMaxLat, max_lat),
    ):
        graph.remove((dataset_iri, predicate, None))
        graph.add((dataset_iri, predicate, Literal(value, datatype=XSD.decimal)))
    graph.remove((dataset_iri, OG.bboxWKT, None))
    graph.add((dataset_iri, OG.bboxWKT, Literal(bbox_wkt([min_lon, min_lat, max_lon, max_lat]))))
