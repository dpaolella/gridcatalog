"""Comparing two numbers that were written in different units (#83).

The registry in `vocab/og-units.ttl` carries a quantity kind and an SI
multiplier for every unit the catalog uses. That is enough to answer the
question a diff between two assumption sets keeps asking: *is this a
disagreement, or is it the same number spelled differently?*

Three answers, and keeping them apart is the whole value:

* **Same unit** — compare the numbers.
* **Same quantity kind, different unit** — convert both to SI and compare.
  `4.2 Mt` and `4200000 t` are one value written two ways, and reporting that
  as a 999,999× discrepancy would be worse than useless.
* **Different quantity kind** — not comparable at all. A price and a mass have
  no delta between them, and a number is the wrong way to say so.

Currency is the fourth case and it is a refusal rather than a conversion. The
registry gives every currency a multiplier of 1.0 with a comment saying why:
a fixed factor cannot relate 2015 dollars to 2024 dollars, because that is a
deflator question and the dataset has to carry the price year separately. So
two different currency units are reported as incomparable rather than quietly
treated as equal — which is the error that would flatter every cost comparison
in the catalog.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from datahub.graph.graphs import NamedGraph
from datahub.graph.sparql import values_clause
from rdflib import URIRef

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime
    from datahub.graph.records import RecordStore

CURRENCY = "http://qudt.org/vocab/quantitykind/Currency"

#: How two quantities relate. Ordered from "the same" to "no relation".
Relation = Literal["identical", "equivalent", "different", "incomparable", "unknown"]


@dataclass(frozen=True, slots=True)
class Unit:
    """One row of the unit registry."""

    iri: str
    label: str | None = None
    symbol: str | None = None
    quantity_kind: str | None = None
    multiplier: float | None = None

    @property
    def display(self) -> str | None:
        """What belongs beside a number: the symbol where there is one."""
        return self.symbol or self.label


@dataclass(frozen=True, slots=True)
class Comparison:
    relation: Relation
    #: Absolute difference in SI, where one could be computed.
    delta: float | None = None
    #: Difference as a fraction of the left value, where that is meaningful.
    relative: float | None = None
    #: Why the relation is what it is, when it needs saying.
    note: str | None = None


def resolve(records: RecordStore, iris: list[str]) -> dict[str, Unit]:
    """Look up the registry entry for each unit IRI, in one query."""
    wanted = sorted({iri for iri in iris if iri})
    if not wanted:
        return {}

    rows = records.store.select(
        f"""
        SELECT ?iri ?label ?symbol ?kind ?multiplier WHERE {{
          GRAPH ??vocab {{
            ?iri a qudt:Unit .
            OPTIONAL {{ ?iri rdfs:label ?label }}
            OPTIONAL {{ ?iri qudt:symbol ?symbol }}
            OPTIONAL {{ ?iri qudt:hasQuantityKind ?kind }}
            OPTIONAL {{ ?iri og:conversionMultiplier ?multiplier }}
          }}
          {values_clause("iri", [URIRef(i) for i in wanted])}
        }}
        """,
        {"vocab": NamedGraph.VOCAB.uri()},
    )

    found: dict[str, Unit] = {}
    for row in rows:
        iri = str(row["iri"])
        found[iri] = Unit(
            iri=iri,
            label=_text(row.get("label")),
            symbol=_text(row.get("symbol")),
            quantity_kind=_text(row.get("kind")),
            multiplier=_number(row.get("multiplier")),
        )
    return found


def compare(
    left: float | None,
    left_unit: Unit | None,
    right: float | None,
    right_unit: Unit | None,
) -> Comparison:
    """How two quantities relate, once their units are accounted for."""
    if left is None or right is None:
        return Comparison("unknown", note="one side has no numeric value")

    left_iri = left_unit.iri if left_unit else None
    right_iri = right_unit.iri if right_unit else None

    if left_iri == right_iri:
        return _numeric(left, right, "identical")

    # One side carries a unit and the other does not. Not a conversion problem
    # — it is a record that did not say, and guessing that the bare number is
    # in the other's unit is exactly the assumption a catalog must not make.
    if left_unit is None or right_unit is None:
        return Comparison("unknown", note="only one side states a unit")

    if left_unit.quantity_kind != right_unit.quantity_kind:
        return Comparison(
            "incomparable",
            note="different quantity kinds; there is no delta between these",
        )

    if left_unit.quantity_kind == CURRENCY:
        # The registry's own comment: a fixed factor cannot relate 2015 dollars
        # to 2024 dollars. Two currency units are a deflator question and the
        # record has to carry the price year separately.
        return Comparison(
            "incomparable",
            note="different currencies are a deflator question, not a unit conversion",
        )

    if left_unit.multiplier is None or right_unit.multiplier is None:
        return Comparison("unknown", note="no conversion factor recorded for one of these units")

    return _numeric(left * left_unit.multiplier, right * right_unit.multiplier, "equivalent")


def _numeric(left: float, right: float, same: Relation) -> Comparison:
    delta = right - left
    if delta == 0:
        note = None if same == "identical" else "same value, written in different units"
        return Comparison(same, delta=0.0, relative=0.0, note=note)
    # Relative to the left, which is the side being compared *against* — the
    # filing in a filing-versus-intervention diff. Undefined against zero
    # rather than infinite, because "infinitely larger than nothing" is not a
    # number a reader can act on.
    relative = delta / left if left else None
    return Comparison("different", delta=delta, relative=relative)


def _text(term: object) -> str | None:
    return None if term is None else str(term)


def _number(term: object) -> float | None:
    if term is None:
        return None
    try:
        return float(str(term))
    except ValueError:
        return None
