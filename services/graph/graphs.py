"""Named-graph constants and helpers (PRD §3.3).

The PRD writes these as ``og:graph/catalog``. A ``/`` is not legal in an
unescaped Turtle/SPARQL local name, so they are always written out in full
angle-bracket form. The IRI is identical to what ``og:graph/catalog`` denotes.

Keeping computed output in its own graph means a full recompute is a graph drop
and rebuild rather than a surgical update. Keeping draft separate from catalog
means entitlement and visibility rules only ever reason over ``catalog``.
"""

from __future__ import annotations

from enum import StrEnum

from datahub.namespaces import OG
from rdflib import URIRef

_BASE = str(OG) + "graph/"


class NamedGraph(StrEnum):
    """The five graphs. Nothing writes outside them."""

    CATALOG = _BASE + "catalog"
    """Published, steward-confirmed dataset records."""

    DRAFT = _BASE + "draft"
    """Harvested and enriched records pending review."""

    VOCAB = _BASE + "vocab"
    """SKOS concept schemes, versioned."""

    INFERRED = _BASE + "inferred"
    """Materialised entailments. Regenerated on vocabulary change; never edited."""

    COMPUTED = _BASE + "computed"
    """Semantic-layer output: links, grades, resolutions. Droppable and rebuildable."""

    SHAPES = _BASE + "shapes"
    """SHACL shapes, held in the store so batch validation can run server-side."""

    PROVENANCE = _BASE + "provenance"
    """Revision history and audit statements about records, not about datasets."""

    def uri(self) -> URIRef:
        return URIRef(str(self))

    def sparql(self) -> str:
        """Angle-bracket form for interpolation into a query."""
        return f"<{self}>"


#: Graphs a record read may legitimately span. Ordered by precedence: a triple
#: in ``COMPUTED`` augments the record, it never contradicts ``CATALOG``.
RECORD_READ_GRAPHS: tuple[NamedGraph, ...] = (
    NamedGraph.CATALOG,
    NamedGraph.COMPUTED,
    NamedGraph.INFERRED,
)

#: Graphs that are derived state and may be dropped and rebuilt at any time.
#: If losing one would lose information, it is in the wrong place (PRD principle 8).
DERIVED_GRAPHS: tuple[NamedGraph, ...] = (NamedGraph.INFERRED, NamedGraph.COMPUTED)

#: Graphs holding authored state that must be backed up.
AUTHORED_GRAPHS: tuple[NamedGraph, ...] = (
    NamedGraph.CATALOG,
    NamedGraph.DRAFT,
    NamedGraph.VOCAB,
    NamedGraph.SHAPES,
    NamedGraph.PROVENANCE,
)


def record_graph(review_state: str) -> NamedGraph:
    """Which graph a record belongs in, given its review state.

    ``confirmed`` records live in the catalog; everything else is draft. This is
    the only place that mapping is expressed.
    """
    return NamedGraph.CATALOG if review_state == "confirmed" else NamedGraph.DRAFT
