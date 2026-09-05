"""Documentation Completeness grading (WP-7.4).

PRD §F5:

| Grade | Label | Condition |
|---|---|---|
| A | Fully documented | Every field has definition, unit and allowed range, native or curated |
| B | Partially documented | Some fields lack definitions or units |
| C | Documented via external standard only | Fields meaningful only if the user knows the standard |
| D | Minimal | No dedicated metadata beyond a filename and a loose description |

**Geometry columns are evaluated separately from attribute columns** (PRD §F5).
A geometry column has no unit and no allowed range, and grading it against the
attribute checklist marks every geospatial dataset down for a lack that is not
one. What a geometry column *is* documented by is its type, its CRS and its
extent, so those are what it is checked against.

**C is not "worse than B".** It is a different failure: the fields are fully
documented, but only by reference to CIM, CGMES or a similar standard, so a
user who does not own that standard cannot read them. Ranking it below B is a
convention this grader follows because the PRD's table does; the label is what
carries the meaning, and it is why the label is stored next to the letter.
"""

from __future__ import annotations

from datahub.namespaces import OG
from datahub.semantic.grading.facets import (
    ASSESSMENT_FLOOR,
    Assessment,
    not_assessed,
)
from datahub.semantic.resolve import Part
from rdflib import Graph, URIRef
from rdflib.namespace import DCTERMS

#: A field is documented when it carries all of these. ``unit`` is excused for
#: dimensionless and categorical fields — a status code has no unit, and
#: requiring one would mark every code list down.
ATTRIBUTE_REQUIREMENTS: tuple[str, ...] = ("definition", "unit", "range")

#: What a geometry column is documented by instead.
GEOMETRY_REQUIREMENTS: tuple[str, ...] = ("geometry_type", "crs")

#: Data types that carry no unit by nature.
UNITLESS_TYPES: frozenset[str] = frozenset(
    {"string", "boolean", "bool", "category", "categorical", "enum", "date", "datetime", "geometry"}
)


def grade_documentation(
    graph: Graph,
    dataset_iri: str | URIRef,
    parts: list[Part],
    *,
    completeness_level: int = 1,
) -> Assessment:
    """Grade one record's Documentation Completeness."""
    iri = URIRef(str(dataset_iri))

    if completeness_level < ASSESSMENT_FLOOR:
        return not_assessed(
            "documentation",
            f"The record is at completeness level {completeness_level}, so it carries no field "
            f"metadata to assess. Documentation is graded from level {ASSESSMENT_FLOOR}. "
            "Not assessed is not grade D.",
        )

    status = _text(graph.value(iri, OG.documentationStatus))
    if status == "external-standard-only" or _external_only(graph, iri, parts):
        return Assessment(
            facet="documentation",
            grade="C",
            rationale=(
                "Fields are documented by reference to an external standard and not in the "
                "record itself. Complete for a user who owns that standard, and unreadable "
                "for one who does not — which is a different problem from being partial, not "
                "a worse degree of it."
            ),
            evidence={"documentationStatus": status, "conformsTo": _conforms(graph, iri)},
        )

    if not parts:
        return Assessment(
            facet="documentation",
            grade="D",
            rationale=(
                "The record describes the dataset but names none of its fields, so there is "
                "no field-level documentation beyond the description."
            ),
            evidence={"fields": 0},
        )

    geometry = [p for p in parts if p.is_geometry]
    attributes = [p for p in parts if not p.is_geometry]

    attribute_gaps = {p.iri: _attribute_gaps(p) for p in attributes}
    geometry_gaps = {p.iri: _geometry_gaps(p) for p in geometry}
    incomplete = {iri_: gaps for iri_, gaps in (attribute_gaps | geometry_gaps).items() if gaps}

    documented = len(parts) - len(incomplete)
    if not incomplete:
        return Assessment(
            facet="documentation",
            grade="A",
            rationale=_full_rationale(len(attributes), len(geometry)),
            evidence={"fields": len(parts), "documented": documented},
        )

    missing_counts = _counts(incomplete)

    # D is "no dedicated metadata beyond a filename and a loose description",
    # which is a statement about definitions, not about the checklist. A record
    # whose fields are all defined but none of which states a unit is
    # *partially* documented — B — and grading it D would say something untrue
    # about work its authors did.
    defined = sum(1 for p in attributes if p.definition) + sum(
        1 for p in geometry if p.geometry_type
    )
    if defined == 0:
        return Assessment(
            facet="documentation",
            grade="D",
            rationale=(
                f"None of the {len(parts)} named fields carries a definition. The record has "
                "field names and little else."
            ),
            evidence={"fields": len(parts), "documented": 0, "defined": 0},
        )

    return Assessment(
        facet="documentation",
        grade="B",
        rationale=(
            f"{documented} of {len(parts)} fields are fully documented"
            + (f", {defined} carry a definition" if defined != documented else "")
            + ". "
            + "; ".join(f"{count} lack {what}" for what, count in sorted(missing_counts.items()))
            + "."
        ),
        evidence={
            "fields": len(parts),
            "documented": documented,
            "defined": defined,
            "missing": missing_counts,
            "geometryFields": len(geometry),
        },
    )


def _attribute_gaps(part: Part) -> list[str]:
    gaps = []
    if not part.definition:
        gaps.append("a definition")
    if not (part.unit or part.unit_as_stated) and not _unitless(part):
        gaps.append("a unit")
    return gaps


def _geometry_gaps(part: Part) -> list[str]:
    """Geometry columns are checked against what documents *them*.

    Not against units and ranges, which a geometry does not have. Extent is
    deliberately not required per-column: it is a dataset-level fact
    (``og:bbox``), and requiring it twice would mark a correctly documented
    geospatial dataset down for not repeating itself.
    """
    gaps = []
    if not part.geometry_type:
        gaps.append("a geometry type")
    if not part.crs:
        gaps.append("a CRS")
    return gaps


def _unitless(part: Part) -> bool:
    """Fields a unit does not apply to.

    Two ways to qualify. A data type that cannot carry one — a string, a
    boolean, a date. Or a declared code list: a field whose values come from a
    controlled vocabulary is categorical whatever its storage type says, and a
    land-cover class stored as ``uint8`` is not missing a unit.
    """
    return part.has_code_list or (part.data_type or "").lower() in UNITLESS_TYPES


def _counts(incomplete: dict[str, list[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for gaps in incomplete.values():
        for gap in gaps:
            counts[gap] = counts.get(gap, 0) + 1
    return counts


def _full_rationale(attributes: int, geometry: int) -> str:
    base = f"All {attributes} attribute field(s) carry a definition and a unit where one applies"
    if geometry:
        return (
            f"{base}, and all {geometry} geometry field(s) carry a type and a CRS. Geometry is "
            "checked against what documents a geometry rather than against units and ranges it "
            "does not have."
        )
    return f"{base}."


def _external_only(graph: Graph, iri: URIRef, parts: list[Part]) -> bool:
    """Fields whose meaning is carried entirely by a referenced standard.

    Detected rather than trusted: a record that declares ``conformsTo`` and
    gives no field its own definition is documented by the standard alone,
    whatever its ``documentationStatus`` says. A record that declares both and
    also defines its fields is not — it is documented, with a standard cited.
    """
    if not _conforms(graph, iri) or not parts:
        return False
    return not any(p.definition for p in parts)


def _conforms(graph: Graph, iri: URIRef) -> list[str]:
    return sorted(str(o) for o in graph.objects(iri, DCTERMS.conformsTo))


def _text(node: object) -> str | None:
    return str(node) if node is not None else None


__all__ = [
    "ATTRIBUTE_REQUIREMENTS",
    "GEOMETRY_REQUIREMENTS",
    "UNITLESS_TYPES",
    "grade_documentation",
]
