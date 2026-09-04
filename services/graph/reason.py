"""Entailment materialisation over the concept schemes.

ADR-0002 commits to computing entailments with explicit SPARQL forward chaining
rather than depending on a store-side reasoner, so behaviour is identical on
rdflib and on Fuseki. Jena's reasoner stays available in production as an
optimisation, not as a correctness dependency.

What is materialised, and why each one earns its place:

``og:broaderTransitive``
    The transitive closure of ``skos:broader``. The projector reads it to build
    the search index's expanded concept list, so a filter on a parent concept is
    a term lookup rather than a graph walk on every query.

``skos:narrower``
    Asserted only in one direction in the source vocabularies. Materialising the
    inverse means a query can walk either way without every author remembering
    to state both.

``skos:related`` symmetry
    Same reason, smaller.

``og:resolvesTo`` across crosswalks
    Two external terms that both ``skos:exactMatch`` the same OpenGrid concept
    can be resolved to each other *through* it. **Only through exactMatch.**
    ``skos:closeMatch`` is not transitive, and chaining it is precisely the
    modelling hazard PRD X2 warns about: three hops of "close enough" produce a
    mapping nobody would assert directly.

``skos:inScheme``
    Entailed from top-concept membership where an author stated only
    ``skos:topConceptOf``.

Materialisation drops and rebuilds ``og:graph/inferred`` wholesale. That graph
is derived state: if losing it would lose information, the information is in
the wrong graph (PRD principle 8).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from datahub.graph.graphs import CROSSWALK_GRAPH_PREFIX, NamedGraph
from datahub.graph.loader import recorded_checksum
from datahub.graph.store import GraphStore
from datahub.logging import get_logger
from datahub.namespaces import OG, PREFIXES
from datahub.semantic.queries import scoped
from rdflib import Graph, Literal, URIRef

log = get_logger(__name__)

INFERRED_STATE = URIRef(str(OG) + "state/inferred")

#: Each rule is a CONSTRUCT run against the vocabulary graph. Keeping them as
#: text rather than as code means a reviewer sees exactly what is entailed, and
#: means adding a rule does not mean editing a loop.
RULES: dict[str, str] = {
    "broader-transitive": """
        CONSTRUCT { ?narrow og:broaderTransitive ?broad }
        WHERE {
          ?narrow skos:broader+ ?broad .
          FILTER (?narrow != ?broad)
        }
    """,
    "narrower-inverse": """
        CONSTRUCT { ?broad skos:narrower ?narrow }
        WHERE {
          ?narrow skos:broader ?broad .
          FILTER NOT EXISTS { ?broad skos:narrower ?narrow }
        }
    """,
    "related-symmetry": """
        CONSTRUCT { ?b skos:related ?a }
        WHERE {
          ?a skos:related ?b .
          FILTER NOT EXISTS { ?b skos:related ?a }
        }
    """,
    "in-scheme-from-top": """
        CONSTRUCT { ?c skos:inScheme ?scheme }
        WHERE {
          ?c skos:topConceptOf ?scheme .
          FILTER NOT EXISTS { ?c skos:inScheme ?scheme }
        }
    """,
    "scheme-membership-inherited": """
        CONSTRUCT { ?narrow skos:inScheme ?scheme }
        WHERE {
          ?narrow skos:broader+ ?top .
          ?top skos:topConceptOf ?scheme .
          FILTER NOT EXISTS { ?narrow skos:inScheme ?scheme }
        }
    """,
    # Exact matches are transitive because identity is, and the bridge is worth
    # having precisely because it spans schemes: a CIM attribute and a PyPSA
    # attribute that are each identical to the same OpenGrid concept are
    # identical to each other, and a tool holding one can accept the other.
    #
    # Close matches are NOT transitive, and chaining them is the X2 hazard —
    # three hops of "close enough" produce a mapping nobody would assert
    # directly. No rule here reads skos:closeMatch, deliberately.
    "exact-match-bridge": """
        CONSTRUCT { ?a og:resolvesTo ?b }
        WHERE {
          ?concept skos:exactMatch ?a , ?b .
          FILTER (?a != ?b)
        }
    """,
    "exact-match-inverse": """
        CONSTRUCT { ?external og:resolvesTo ?concept }
        WHERE {
          ?concept skos:exactMatch ?external .
        }
    """,
    "top-concept-inverse": """
        CONSTRUCT { ?scheme skos:hasTopConcept ?c }
        WHERE {
          ?c skos:topConceptOf ?scheme .
          FILTER NOT EXISTS { ?scheme skos:hasTopConcept ?c }
        }
    """,
}


def vocabulary_graphs(store: GraphStore) -> tuple[str, ...]:
    """Every graph the rules read: the core schemes plus each crosswalk.

    Crosswalks live in per-scheme graphs so the Q5 audit can tell which scheme
    made a claim (see ``NamedGraph.crosswalk``). The reasoner reads across all
    of them, because the exact-match bridge is only useful when it spans
    schemes.
    """
    names = [str(NamedGraph.VOCAB)]
    names += sorted(name for name in store.graph_names() if name.startswith(CROSSWALK_GRAPH_PREFIX))
    return tuple(names)


@dataclass(slots=True)
class MaterializeResult:
    triples_by_rule: dict[str, int]
    total: int
    source_checksum: str | None

    @property
    def summary(self) -> str:
        parts = ", ".join(f"{name} {count}" for name, count in sorted(self.triples_by_rule.items()))
        return f"{self.total} entailed triples ({parts})"


def materialize(store: GraphStore) -> MaterializeResult:
    """Rebuild ``og:graph/inferred`` from the current vocabulary."""
    entailed = Graph()
    for prefix, namespace in PREFIXES.items():
        entailed.bind(prefix, namespace)

    graphs = vocabulary_graphs(store)
    counts: dict[str, int] = {}
    for name, template in RULES.items():
        produced = store.construct(scoped(template, *graphs))
        counts[name] = len(produced)
        for triple in produced:
            entailed.add(triple)

    # Record the checksum of the vocabulary as it actually is, not the value
    # the loader wrote: those differ the moment anyone edits the store directly,
    # and it is that case the staleness check exists for.
    checksum = live_checksum(store)
    entailed.add((INFERRED_STATE, OG.materializedFrom, Literal(checksum or "")))
    entailed.add((INFERRED_STATE, OG.materializedAt, Literal(datetime.now(UTC))))

    _guard_target(entailed)
    store.put_graph(NamedGraph.INFERRED, entailed)

    result = MaterializeResult(counts, len(entailed) - 2, checksum)
    log.info("entailments materialised", **counts, total=result.total)
    return result


def materialized_from(store: GraphStore) -> str | None:
    """The vocabulary checksum the current entailments were built from."""
    rows = store.select(
        "SELECT ?from WHERE { GRAPH ??g { ??state og:materializedFrom ?from } }",
        {"g": NamedGraph.INFERRED.uri(), "state": INFERRED_STATE},
    )
    return str(rows[0]["from"]) if rows else None


def is_stale(store: GraphStore, *, fast: bool = False) -> bool:
    """True when the vocabulary has changed since entailments were built.

    This is the concrete form of PRD §F4.3's vocabulary-change trigger: a
    comparison of checksums, not an assumption that whoever edited the
    vocabulary remembered to rerun the reasoner.

    By default the *live* vocabulary graph is re-checksummed rather than the
    value recorded at load time. The recorded value only detects a change made
    through :func:`~datahub.graph.loader.load_vocabularies`, and Fuseki accepts
    SPARQL Update against any graph — so a concept added directly to the store,
    which is exactly what a hurried vocabulary fix looks like, would otherwise
    leave entailments silently wrong. Canonicalising 3,000 triples costs roughly
    70 ms, which is nothing on the scheduled job that calls this and too much
    for a request path; ``fast=True`` compares the recorded values instead and
    is honest about what it misses.
    """
    built_from = materialized_from(store)
    current = recorded_checksum(store) if fast else live_checksum(store)
    if built_from is None:
        return current is not None
    return built_from != (current or "")


def live_checksum(store: GraphStore) -> str | None:
    """Checksum the whole vocabulary space as it currently stands in the store.

    Covers the crosswalk graphs as well as the core schemes: a crosswalk edit
    changes what the exact-match bridge entails, so it has to invalidate
    materialisation exactly as a concept edit does.

    Excludes the loader's own state triples, which change on every load and
    would otherwise make every graph look different from every other.
    """
    from datahub.graph.loader import VOCAB_STATE, canonical_checksum

    combined = Graph()
    for name in vocabulary_graphs(store):
        for triple in store.get_graph(name):
            combined.add(triple)
    if not len(combined):
        return None
    combined.remove((VOCAB_STATE, None, None))
    return canonical_checksum(combined)


def materialize_if_stale(store: GraphStore, *, fast: bool = False) -> MaterializeResult | None:
    return materialize(store) if is_stale(store, fast=fast) else None


def _guard_target(entailed: Graph) -> None:
    """Materialisation must never write outside the inferred graph.

    The rules construct triples, not quads, so the graph they land in is chosen
    here. This asserts the invariant anyway: a future rule that constructed a
    statement about a *record* rather than about a *concept* would quietly make
    the inferred graph un-droppable, which is the property that makes it safe.
    """
    catalog_bases = (
        "https://catalog.opengrid.org/ds/",
        "https://catalog.opengrid.org/dist/",
        "https://catalog.opengrid.org/field/",
    )
    for subject in entailed.subjects():
        if isinstance(subject, URIRef) and str(subject).startswith(catalog_bases):
            raise RuntimeError(
                f"materialisation produced a statement about a catalog record ({subject}). "
                "Entailments are dropped and rebuilt wholesale, so a record fact placed "
                "there would be lost on the next vocabulary change."
            )
