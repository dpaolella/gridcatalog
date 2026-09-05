"""Lineage walking: who derives from whom, and how far away (PRD §F4.5-6, §F6.8).

Q1 finds that two datasets share an upstream origin. It cannot say *how far
up*, because SPARQL 1.1 property paths match without reporting path length —
the query's own comment says so and points here.

Depth matters for the shared-origin warning. Two datasets one hop from ERA5 are
correlated in a way a modeler will feel immediately; two datasets six hops away
through different intermediate products are related in a way that may not
matter at all. A warning that treated those alike would be ignored within a
week, and a warning nobody reads is worse than none, because it occupies the
place where a real one would go.

The index is built once per pass and walked in Python. A traversal per pair
would be O(pairs × depth) queries against the store; this is one query.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from datahub.graph.graphs import NamedGraph
from datahub.graph.store import GraphStore
from datahub.namespaces import OG
from rdflib import Graph, URIRef
from rdflib.namespace import PROV

#: The predicates that mean "this came from that". Both, because DCAT-shaped
#: records use `prov:wasDerivedFrom` and og-shaped ones use `og:upstreamSource`,
#: and a walker that read one would silently find nothing in half the catalog.
LINEAGE_PREDICATES: tuple[URIRef, ...] = (OG.upstreamSource, PROV.wasDerivedFrom)

#: A guard, not a limit anybody should hit. A lineage chain longer than this is
#: a cycle or a data error, and walking it forever helps nobody.
MAX_DEPTH = 32


@dataclass(frozen=True, slots=True)
class SharedOrigin:
    """One origin two datasets both derive from, with each one's distance."""

    origin: str
    depth_a: int
    depth_b: int
    title: str | None = None

    @property
    def nearest(self) -> int:
        return min(self.depth_a, self.depth_b)

    @property
    def furthest(self) -> int:
        return max(self.depth_a, self.depth_b)


@dataclass
class LineageIndex:
    """Every ``derived from`` edge in the catalog, walkable in both directions."""

    parents: dict[str, set[str]] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_store(
        cls, store: GraphStore, *, graphs: tuple[NamedGraph, ...] = (NamedGraph.CATALOG,)
    ) -> LineageIndex:
        index = cls()
        for name in graphs:
            index.absorb(store.get_graph(name))
        return index

    def absorb(self, graph: Graph) -> None:
        from rdflib.namespace import DCTERMS

        for predicate in LINEAGE_PREDICATES:
            for child, _, parent in graph.triples((None, predicate, None)):
                self.parents.setdefault(str(child), set()).add(str(parent))
        for subject, _, title in graph.triples((None, DCTERMS.title, None)):
            self.titles.setdefault(str(subject), str(title))

    # -- walking -----------------------------------------------------------

    def ancestors(self, iri: str) -> dict[str, int]:
        """Every ancestor of *iri*, mapped to its shortest distance.

        Breadth-first, so the recorded depth is the *shortest* chain. A dataset
        that reaches ERA5 both directly and through an intermediate is one hop
        away, not two: the closest path is the one that determines how much the
        two datasets have in common.

        Cycle-safe. A record that (wrongly) derives from itself produces a
        wrong lineage, not a hung recompute.
        """
        seen: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque((p, 1) for p in self.parents.get(iri, ()))
        while queue:
            current, depth = queue.popleft()
            if current in seen or current == iri or depth > MAX_DEPTH:
                continue
            seen[current] = depth
            queue.extend((p, depth + 1) for p in self.parents.get(current, ()))
        return seen

    def depth_to(self, iri: str, origin: str) -> int | None:
        """Hops from *iri* up to *origin*, or ``None`` if it does not reach it.

        The function Q1's comment names. Q1 binds a depth of 1 or 2 because a
        property path cannot report length; this walks it.
        """
        return self.ancestors(iri).get(origin)

    def shared_origins(self, a: str, b: str) -> list[SharedOrigin]:
        """Origins both datasets derive from, nearest first.

        Nearest first because a warning should lead with the origin that
        matters most, and "most" here means "fewest hops from both".
        """
        left, right = self.ancestors(a), self.ancestors(b)
        shared = [
            SharedOrigin(
                origin=origin,
                depth_a=left[origin],
                depth_b=right[origin],
                title=self.titles.get(origin),
            )
            for origin in left.keys() & right.keys()
        ]
        # One of them being an ancestor of the other is a different
        # relationship — derivation, not shared origin — and is reported as a
        # typed relation rather than as a correlation warning. Including it
        # here would tell a user that a dataset is correlated with its own
        # source, which is true and useless.
        shared = [s for s in shared if s.origin not in (a, b)]
        return sorted(shared, key=lambda s: (s.nearest, s.furthest, s.origin))

    def derives_from(self, child: str, ancestor: str) -> bool:
        return ancestor in self.ancestors(child)

    def __len__(self) -> int:
        return len(self.parents)


__all__ = ["LINEAGE_PREDICATES", "MAX_DEPTH", "LineageIndex", "SharedOrigin"]
