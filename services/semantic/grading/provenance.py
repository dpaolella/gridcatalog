"""Provenance grading (WP-7.4).

PRD §F5:

| Grade | Label | Condition |
|---|---|---|
| A | Primary & Traced | Values measured or primary, upstream origin recorded |
| B | Derived & Traced | Values estimated, modeled or synthetic, full lineage recorded |
| C | Traced, Basis Unconfirmed | Upstream link exists, per-field basis not reviewer-confirmed |
| D | Untraced | No upstream link recorded, regardless of basis |

Read the D row carefully: **it is about tracing, not about quality.** A
carefully measured dataset with no recorded upstream is D. That is not a
judgement about the measurements; it is a statement that the catalog cannot
show where they came from — which is exactly what a user evaluating
reproducibility needs to know, and the thing they cannot get anywhere else.

**Hierarchical datasets grade per variable** (PRD §F5). A single NetCDF mixes
directly-observed and bias-corrected variables, and one grade would lie about
both. The dataset-level grade is then the *worst* of the per-variable grades,
not the mean: an average would let nine clean variables hide one that is
untraceable, and the user who needs the tenth is the one the facet is for.

**Absent means "not captured".** `og:upstreamSourceUncaptured false` is how a
record says "this is a primary observation, there is no upstream" — a claim,
which grades differently from silence. Silence is D.
"""

from __future__ import annotations

from datahub.namespaces import OG
from datahub.semantic.grading.facets import (
    ASSESSMENT_FLOOR,
    Assessment,
    Grade,
    not_assessed,
)
from datahub.semantic.resolve import Part
from rdflib import Graph, URIRef
from rdflib.namespace import PROV

#: Value bases that make a dataset primary rather than derived.
PRIMARY_BASES: frozenset[str] = frozenset({"measured", "primary", "observed"})

#: The rest. Named rather than implied, because "anything else is derived"
#: silently reclassifies any future basis token as derived.
DERIVED_BASES: frozenset[str] = frozenset({"estimated", "modeled", "modelled", "synthetic"})


def grade_provenance(
    graph: Graph,
    dataset_iri: str | URIRef,
    parts: list[Part],
    *,
    completeness_level: int = 1,
    shape: str | None = None,
) -> Assessment:
    """Grade one record's Provenance.

    ``parts`` comes from :meth:`~datahub.semantic.resolve.Resolver.parts`, so
    the grader reads the same view of a record's columns, variables and layers
    that the resolver does. Two readers of "what are this record's fields"
    would eventually disagree, and the disagreement would show up as a grade
    nobody could reproduce.
    """
    iri = URIRef(str(dataset_iri))

    if completeness_level < ASSESSMENT_FLOOR:
        return not_assessed(
            "provenance",
            f"The record is at completeness level {completeness_level}. Provenance is graded "
            f"from level {ASSESSMENT_FLOOR}, where field-level value basis exists. Below that "
            "there is nothing to assess, which is not the same as assessing it poorly.",
        )

    traced = _traced(graph, iri)
    if not traced:
        return Assessment(
            facet="provenance",
            grade="D",
            rationale=(
                "No upstream source is recorded. This is a statement about what the catalog "
                "can show, not about how the data was produced: a carefully measured dataset "
                "with no recorded lineage grades D, because a user cannot trace it."
            ),
            evidence={"upstreamRecorded": False},
        )

    bases = {p.value_basis for p in parts if p.value_basis}
    unconfirmed = [p for p in parts if not p.value_basis]

    if not bases:
        return Assessment(
            facet="provenance",
            grade="C",
            rationale=(
                f"{traced} upstream link(s) are recorded, but no field states its value basis, "
                "so whether the values are primary or derived is unconfirmed."
            ),
            evidence={"upstreamRecorded": True, "fieldsWithBasis": 0, "fields": len(parts)},
        )

    per_part: dict[str, Grade] = {
        p.iri: _grade_for(p.value_basis, traced=True) for p in parts if p.value_basis
    }

    if unconfirmed:
        # Per PRD's C row: the link exists, the per-field basis is not fully
        # confirmed. Reported as C at the dataset level while the parts that
        # *are* confirmed keep their own grade, which is what per-variable
        # grading is for.
        return Assessment(
            facet="provenance",
            grade="C",
            rationale=(
                f"{len(parts) - len(unconfirmed)} of {len(parts)} fields state a value basis. "
                f"Lineage is recorded, so the dataset is traced, but the basis of "
                f"{len(unconfirmed)} field(s) is unconfirmed."
            ),
            evidence={
                "upstreamRecorded": True,
                "fieldsWithBasis": len(parts) - len(unconfirmed),
                "fields": len(parts),
            },
            per_part=per_part,
        )

    worst = max(per_part.values()) if per_part else "C"
    mixed = len(set(per_part.values())) > 1
    return Assessment(
        facet="provenance",
        grade=worst,
        rationale=_rationale(worst, bases, mixed, shape),
        evidence={
            "upstreamRecorded": True,
            "valueBases": sorted(bases),
            "fields": len(parts),
        },
        per_part=per_part,
    )


def _grade_for(basis: str | None, *, traced: bool) -> Grade:
    if not traced:
        return "D"
    if basis in PRIMARY_BASES:
        return "A"
    if basis in DERIVED_BASES:
        return "B"
    # An unrecognised basis token is not silently treated as derived. It is
    # unconfirmed, which is C, and the vocabulary should gain the token rather
    # than the grader guessing at it.
    return "C"


def _rationale(worst: Grade, bases: set[str], mixed: bool, shape: str | None) -> str:
    listed = ", ".join(sorted(bases))
    if mixed:
        per = "variable" if shape == "hierarchical" else "field"
        return (
            f"Lineage is recorded and every {per} states its basis, but they differ ({listed}). "
            f"The dataset grade is the worst of the per-{per} grades: an average would let "
            f"well-traced {per}s hide one that is not, and the user who needs that one is "
            "exactly who this facet is for."
        )
    if worst == "A":
        return (
            f"Values are {listed} and an upstream origin is recorded. Primary and traceable "
            "to where they came from."
        )
    if worst == "B":
        return f"Values are {listed} with full lineage to a recorded origin."
    return f"Lineage is recorded; the stated basis ({listed}) is not one the grader recognises."


def _traced(graph: Graph, iri: URIRef) -> int:
    """How many upstream statements the record carries.

    ``og:upstreamSourceUncaptured false`` counts as one. It is the record
    asserting "this is primary, there is no upstream", which is a claim a
    steward made and which grades differently from silence — silence is the
    absence the catalog must never read as a statement.
    """
    links = set(graph.objects(iri, OG.upstreamSource)) | set(
        graph.objects(iri, PROV.wasDerivedFrom)
    )
    if links:
        return len(links)
    uncaptured = graph.value(iri, OG.upstreamSourceUncaptured)
    if uncaptured is not None and not bool(uncaptured.toPython()):
        return 1
    return 0


__all__ = ["DERIVED_BASES", "PRIMARY_BASES", "grade_provenance"]
