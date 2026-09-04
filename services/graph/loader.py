"""Loading vocabularies and shapes into the graph.

Two responsibilities, and the second is the interesting one:

1. Put every ``vocab/*.ttl`` into ``og:graph/vocab`` and the shapes into
   ``og:graph/shapes``, atomically, idempotently.
2. Record a checksum of what was loaded, so ``datahub.graph.reason`` can tell
   whether materialised entailments are stale. PRD §F4.3 asks for recompute on
   vocabulary change; without a recorded checksum that trigger is a hope rather
   than a mechanism.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from datahub.config import Settings, get_settings
from datahub.graph.graphs import NamedGraph
from datahub.graph.store import GraphStore
from datahub.logging import get_logger
from datahub.namespaces import OG, PREFIXES
from rdflib import Graph, Literal, URIRef
from rdflib.compare import to_isomorphic

log = get_logger(__name__)

#: Where the vocabulary checksum is recorded. A node in the vocab graph rather
#: than in Postgres: it is a statement about the graph's contents, and it must
#: travel with a backup of the graph or a restore would look up to date.
VOCAB_STATE = URIRef(str(OG) + "state/vocabulary")


@dataclass(slots=True)
class LoadResult:
    files: dict[str, int]
    total_triples: int
    checksum: str
    changed: bool

    @property
    def summary(self) -> str:
        verb = "loaded" if self.changed else "unchanged"
        return (
            f"{verb}: {len(self.files)} files, {self.total_triples} triples, {self.checksum[:12]}"
        )


def canonical_checksum(graph: Graph) -> str:
    """A stable hash of a graph's contents.

    Canonicalised rather than merely sorted. The crosswalks express X4 gap
    markers as blank nodes, and a blank node gets a fresh label on every parse,
    so sorted N-Triples produce a different hash for byte-identical input. That
    would make every startup look like a vocabulary change and re-materialise
    entailments for nothing — a slow no-op that also destroys the value of the
    staleness check, since everything is always stale.

    Roughly 70 ms for the current 3,200-triple vocabulary, paid once per load.
    """
    digest = hashlib.sha256()
    digest.update(str(to_isomorphic(graph).graph_digest()).encode("utf-8"))
    return digest.hexdigest()


def _bound_graph() -> Graph:
    graph = Graph()
    for prefix, namespace in PREFIXES.items():
        graph.bind(prefix, namespace)
    return graph


def read_vocabularies(settings: Settings | None = None) -> tuple[Graph, dict[str, int]]:
    """Parse the core concept schemes into one graph, with per-file counts.

    Crosswalks are excluded: they go into their own graphs (see
    :func:`read_crosswalks` and ``NamedGraph.crosswalk``).
    """
    settings = settings or get_settings()
    combined = _bound_graph()
    counts: dict[str, int] = {}
    for path in sorted(settings.vocab_dir.glob("*.ttl")):
        before = len(combined)
        combined.parse(path.as_posix(), format="turtle")
        counts[path.name] = len(combined) - before
    return combined, counts


def read_crosswalks(settings: Settings | None = None) -> dict[str, Graph]:
    """Parse each crosswalk into its own graph, keyed by scheme name."""
    settings = settings or get_settings()
    directory = settings.vocab_dir / "crosswalks"
    out: dict[str, Graph] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.ttl")):
        graph = _bound_graph()
        graph.parse(path.as_posix(), format="turtle")
        out[path.stem] = graph
    return out


def load_vocabularies(
    store: GraphStore, settings: Settings | None = None, *, force: bool = False
) -> LoadResult:
    """Replace ``og:graph/vocab`` with the contents of ``vocab/``.

    Idempotent: loading twice leaves the same triples and reports
    ``changed=False`` the second time, so a startup hook can call it
    unconditionally.
    """
    settings = settings or get_settings()
    combined, counts = read_vocabularies(settings)
    crosswalks = read_crosswalks(settings)
    # The checksum covers crosswalks too: a crosswalk edit changes what the
    # exact-match bridge entails, so it has to invalidate materialisation.
    checksum_input = _bound_graph()
    for triple in combined:
        checksum_input.add(triple)
    for graph in crosswalks.values():
        for triple in graph:
            checksum_input.add(triple)
    checksum = canonical_checksum(checksum_input)
    previous = recorded_checksum(store)

    if previous == checksum and not force:
        log.debug("vocabulary unchanged", checksum=checksum[:12])
        return LoadResult(counts, len(combined), checksum, changed=False)

    stamped = Graph()
    for triple in combined:
        stamped.add(triple)
    stamped.add((VOCAB_STATE, OG.vocabularyChecksum, Literal(checksum)))
    stamped.add((VOCAB_STATE, OG.loadedAt, Literal(datetime.now(UTC))))
    stamped.add((VOCAB_STATE, OG.fileCount, Literal(len(counts))))

    store.put_graph(NamedGraph.VOCAB, stamped)
    for scheme, graph in crosswalks.items():
        store.put_graph(NamedGraph.crosswalk(scheme), graph)
        counts[f"crosswalks/{scheme}.ttl"] = len(graph)

    total = len(combined) + sum(len(g) for g in crosswalks.values())
    log.info(
        "vocabulary loaded",
        files=len(counts),
        triples=total,
        crosswalks=len(crosswalks),
        checksum=checksum[:12],
    )
    return LoadResult(counts, total, checksum, changed=True)


def load_shapes(store: GraphStore, settings: Settings | None = None) -> int:
    """Put the SHACL shapes in the store, so Fuseki's batch SHACL endpoint can
    use them without a second copy of the file being shipped to it."""
    settings = settings or get_settings()
    shapes = Graph()
    shapes.parse(settings.shapes_path.as_posix(), format="turtle")
    store.put_graph(NamedGraph.SHAPES, shapes)
    log.info("shapes loaded", triples=len(shapes))
    return len(shapes)


def recorded_checksum(store: GraphStore) -> str | None:
    """The checksum of the vocabulary currently in the store, if any."""
    rows = store.select(
        """
        SELECT ?checksum WHERE {
          GRAPH ??g { ??state og:vocabularyChecksum ?checksum }
        }
        """,
        {"g": NamedGraph.VOCAB.uri(), "state": VOCAB_STATE},
    )
    return str(rows[0]["checksum"]) if rows else None


def bootstrap(
    store: GraphStore, settings: Settings | None = None, *, materialize: bool = True
) -> LoadResult:
    """Bring a fresh store to a usable state: vocabulary, shapes, entailments.

    The one call a new developer, a test fixture and the container entrypoint
    all make.
    """
    settings = settings or get_settings()
    # Every named graph must exist before anything queries across them: rdflib
    # reads a FROM clause naming an unknown graph as a remote fetch.
    store.ensure_graphs(list(NamedGraph))
    result = load_vocabularies(store, settings)
    load_shapes(store, settings)
    if materialize and result.changed:
        from datahub.graph.reason import materialize as run_materialize

        run_materialize(store)
    return result


def vocab_files(settings: Settings | None = None) -> list[Path]:
    settings = settings or get_settings()
    paths = sorted(settings.vocab_dir.glob("*.ttl"))
    crosswalks = settings.vocab_dir / "crosswalks"
    if crosswalks.is_dir():
        paths += sorted(crosswalks.glob("*.ttl"))
    return paths
