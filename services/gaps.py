"""The gap register: a data requirement with no open supplier (#56).

PRD §5 says that saying what does not exist is a feature, and the catalog had
two ways to say it. `og:conceptGap` and `og:provenanceGap` mark a field a
source never described; a reference-only record with an `og:pointerRationale`
marks a dataset that exists and cannot be obtained. Both hang off something the
catalog already holds.

This is the third case and the commonest: **a thing a modeller needs where
nothing open supplies it.** There is no record to hang it on, and inventing a
`dcat:Dataset` for something that does not exist is the opposite of what the
record model is for.

**Data, not a graph.** Following ADR-0013's reasoning for relevance decisions:
a gap is a curated finding, true until the landscape changes, reviewable in a
pull request beside the thing it describes. It needs no SHACL shape, no named
graph and no index — nineteen entries in a YAML file and a substring match are
the whole implementation, and that is proportionate to what it is.

**They are not datasets and nothing here lets them pretend to be.** They are
served from `/v1/gaps`, they carry no licence, access path or quality grade,
and they never enter a dataset count, a search result or the snapshot's dataset
list. A gap that leaked into one would be the catalog asserting a record for
something that does not exist.

**A gap goes stale faster than a dataset does.** It claims something about the
whole open landscape, which is a much stronger claim than one about a single
publisher. Every entry states its observer and the year it was observed, both
of which reach the reader, and a `review_after` date saying when somebody
should check whether it is still true.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

GAPS_PATH = Path(__file__).resolve().parents[1] / "data" / "data-gaps.yaml"


@dataclass(frozen=True, slots=True)
class DataGap:
    """One requirement nothing open supplies, and why."""

    id: str
    title: str
    domain: str
    category: str
    reason: str
    observed_by: str
    observed: str
    review_after: date | None = None
    needed_by: tuple[str, ...] = ()
    kind: str | None = None

    @property
    def stale(self) -> bool:
        """Whether the register says somebody should have re-checked by now.

        Surfaced rather than acted on. An out-of-date "nothing supplies this"
        actively misleads, and hiding it on the review date would be worse
        still — the reader would lose the finding *and* the fact that it was
        ever made.
        """
        return self.review_after is not None and self.review_after < date.today()

    def matches(self, query: str) -> bool:
        haystack = f"{self.title} {self.category} {self.reason}".lower()
        return all(token in haystack for token in query.lower().split())


@functools.lru_cache(maxsize=1)
def _raw() -> tuple[dict[str, Any], ...]:
    if not GAPS_PATH.exists():
        return ()
    document = yaml.safe_load(GAPS_PATH.read_text()) or {}
    return tuple(document.get("gaps") or ())


def register() -> tuple[DataGap, ...]:
    """Every gap, in file order."""
    return tuple(
        DataGap(
            id=str(row["id"]),
            title=str(row["title"]),
            domain=str(row["domain"]),
            category=str(row["category"]),
            reason=" ".join(str(row["reason"]).split()),
            observed_by=str(row["observed_by"]),
            observed=str(row["observed"]),
            review_after=_as_date(row.get("review_after")),
            needed_by=tuple(row.get("needed_by") or ()),
            kind=row.get("kind"),
        )
        for row in _raw()
    )


def search(
    query: str | None = None,
    *,
    domain: str | None = None,
    needed_by: str | None = None,
    limit: int = 20,
) -> list[DataGap]:
    """Gaps matching a free-text query, a domain, or an analysis class.

    Substring matching on purpose. Nineteen entries do not need an index, and
    the caller this exists for is an empty search result — a reader who typed
    "nodal demand", got nothing, and is owed a better answer than "no datasets
    match".
    """
    found = list(register())
    if domain:
        found = [gap for gap in found if gap.domain.upper() == domain.upper()]
    if needed_by:
        found = [gap for gap in found if needed_by in gap.needed_by]
    if query and query.strip():
        found = [gap for gap in found if gap.matches(query)]
    return found[:limit]


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None
