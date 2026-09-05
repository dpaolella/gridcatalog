"""The three facets: their grades, their labels, and the shape of an assessment.

PRD §F5. **Three independent facets, never combined into a composite score**
(ADR-0007). There is no place in this package where two grades meet, and that
is structural rather than a matter of discipline: nothing here returns more
than one :class:`Assessment` at a time.

Two rules that live here because they apply to all three facets:

* **Every grade derives from recorded facts.** Never an assessor impression,
  never a ranking against other catalog entries. An :class:`Assessment` carries
  the evidence it used, and a grade whose rationale does not name a fact in the
  record is a bug.
* **Not assessed is not grade D.** A record below completeness level 2 has no
  Provenance or Documentation grade at all. Conflating "we have not looked" with
  "we looked and it is poor" would systematically defame every harvested record
  (PRD §F5), which is thousands of them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Grade = Literal["A", "B", "C", "D"]
Facet = Literal["provenance", "documentation", "currency"]

#: Grade → label, per facet. The label is what a user reads; the letter is what
#: a filter matches. Both are in the record so the two cannot drift.
GRADE_LABELS: dict[str, dict[str, str]] = {
    "provenance": {
        "A": "Primary & Traced",
        "B": "Derived & Traced",
        "C": "Traced, Basis Unconfirmed",
        "D": "Untraced",
    },
    "documentation": {
        "A": "Fully documented",
        "B": "Partially documented",
        "C": "Documented via external standard only",
        "D": "Minimal",
    },
    "currency": {
        "A": "Current",
        "B": "Aging",
        # C is deliberately unused on this facet (PRD §F5, §12.3). Leaving a
        # hole is better than inventing a level to fill it.
        "D": "Superseded",
    },
}

#: Facets a steward confirms once and re-confirms on a version change, versus
#: the one that is fully automatic and continuous. This split is what decides
#: which trigger recomputes a facet — see :mod:`datahub.semantic.triggers`.
CONFIRMED_FACETS: frozenset[str] = frozenset({"provenance", "documentation"})
AUTOMATIC_FACETS: frozenset[str] = frozenset({"currency"})

#: Below this completeness level, Provenance and Documentation are not assessed.
ASSESSMENT_FLOOR = 2


@dataclass(frozen=True, slots=True)
class Assessment:
    """One facet of one record.

    ``grade`` of ``None`` means *not assessed* — either the record is below the
    completeness floor, or the facet needs a fact the record does not carry.
    The rationale says which, in words a dataset owner can act on.
    """

    facet: Facet
    grade: Grade | None
    rationale: str
    evidence: dict[str, object] = field(default_factory=dict)
    computed_at: datetime | None = None
    #: Per-part grades, for the facets and shapes that need them. Provenance on
    #: a hierarchical dataset is graded per variable: one NetCDF can mix
    #: directly-observed and bias-corrected variables, and a single grade would
    #: lie about both (PRD §F5).
    per_part: dict[str, Grade] = field(default_factory=dict)

    @property
    def assessed(self) -> bool:
        return self.grade is not None

    @property
    def label(self) -> str:
        if self.grade is None:
            return "Not yet assessed"
        return GRADE_LABELS[self.facet].get(self.grade, self.grade)

    def worse_of(self, other: Grade) -> Grade | None:
        """The lower of this grade and *other*. Used where a cap applies."""
        if self.grade is None:
            return None
        return max(self.grade, other)  # "D" > "C" > "B" > "A" as strings


def not_assessed(facet: Facet, reason: str) -> Assessment:
    return Assessment(facet=facet, grade=None, rationale=reason)


__all__ = [
    "ASSESSMENT_FLOOR",
    "AUTOMATIC_FACETS",
    "CONFIRMED_FACETS",
    "GRADE_LABELS",
    "Assessment",
    "Facet",
    "Grade",
    "not_assessed",
]
