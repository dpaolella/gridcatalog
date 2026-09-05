"""Currency & Maintenance (WP-7.3). Fully automatic, never manual.

PRD §F5:

| Grade | Label | Condition |
|---|---|---|
| A | Current | Latest known vintage, last update within stated cadence |
| B | Aging | Past due against its own cadence, no newer version known |
| D | Superseded | Explicit `supersededBy` link to a newer catalog entry |

C is deliberately unused.

**This facet goes stale from time passing, with no write event to hook.** That
is the whole reason for the trigger split in PRD §F4.3, and it is why this
module takes ``now`` as an argument: the grade is a function of the record *and
the clock*, and a function of the clock that reads the clock itself cannot be
tested at a boundary.

**The cadence is reported alongside the grade.** A correctly-maintained annual
dataset eleven months after its last release is Current, and must not read as
stale next to an hourly one. The rationale therefore always names the cadence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from datahub.namespaces import OG
from datahub.semantic.grading.facets import Assessment, not_assessed
from rdflib import Graph, URIRef
from rdflib.namespace import DCAT, DCTERMS

#: Cadence values that are not durations. PRD's controlled tokens.
NON_DURATION_CADENCE: frozenset[str] = frozenset({"irregular", "on-demand", "discontinued"})

_DURATION = re.compile(
    r"^P(?:(?P<years>\d+)Y)?(?:(?P<months>\d+)M)?(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)

#: How far past due before Aging. A dataset published on a cadence is not late
#: the instant the interval elapses — a monthly release landing on the 3rd
#: rather than the 1st is on schedule, and grading it Aging for two days would
#: make the facet flap and be ignored.
GRACE_FRACTION = 0.25

#: …but not unboundedly. A quarter of a decade is not a grace period.
MAX_GRACE = timedelta(days=90)


@dataclass(frozen=True, slots=True)
class Cadence:
    """A parsed update cadence, or an honest statement that it is not one."""

    stated: str
    interval: timedelta | None = None
    token: str | None = None

    @property
    def comparable(self) -> bool:
        return self.interval is not None

    def due_after(self, last_update: datetime) -> datetime | None:
        if self.interval is None:
            return None
        return last_update + self.interval + min(self.interval * GRACE_FRACTION, MAX_GRACE)


def parse_cadence(value: str | None) -> Cadence | None:
    """Parse ``P1D``/``P1Y``/``PT1H`` or one of the controlled tokens."""
    if not value:
        return None
    text = value.strip()
    if text in NON_DURATION_CADENCE:
        return Cadence(stated=text, token=text)
    match = _DURATION.match(text)
    if not match or not any(match.groupdict().values()):
        return None
    parts = {k: int(v) for k, v in match.groupdict().items() if v}
    # Calendar-exact month and year arithmetic is not worth its complexity for
    # a staleness check whose grace period is a quarter of the interval. 365
    # and 30 are stated here rather than hidden in a library so the
    # approximation is visible to whoever reads a borderline grade.
    days = (
        parts.get("years", 0) * 365
        + parts.get("months", 0) * 30
        + parts.get("weeks", 0) * 7
        + parts.get("days", 0)
    )
    seconds = parts.get("hours", 0) * 3600 + parts.get("minutes", 0) * 60 + parts.get("seconds", 0)
    return Cadence(stated=text, interval=timedelta(days=days, seconds=seconds))


def grade_currency(
    graph: Graph,
    dataset_iri: str | URIRef,
    *,
    now: datetime | None = None,
) -> Assessment:
    """Grade one record's Currency & Maintenance.

    Graded at any completeness level. Unlike Provenance and Documentation it
    needs no field-level metadata — only a cadence and a vintage, both of which
    a level 1 record carries — so withholding it below level 2 would hide a
    fact the catalog actually knows.
    """
    iri = URIRef(str(dataset_iri))
    now = now or datetime.now(UTC)

    superseded_by = graph.value(iri, OG.supersededBy)
    if superseded_by is not None:
        return Assessment(
            facet="currency",
            grade="D",
            rationale=(
                f"Superseded by {superseded_by}. The record names its own successor, "
                "so this is a statement the catalog holds rather than an inference."
            ),
            evidence={"supersededBy": str(superseded_by)},
            computed_at=now,
        )

    cadence = parse_cadence(_text(graph.value(iri, OG.updateCadence)))
    last_update = _latest(graph, iri, now)

    if cadence is None:
        return not_assessed(
            "currency",
            "No update cadence is recorded, so there is nothing to be past due against. "
            "Absent means not captured, not that the dataset has no cadence.",
        )

    if cadence.token == "discontinued":
        # Discontinued is not superseded: nothing replaces it. It is also not
        # aging, because it is not late for anything. The honest grade is the
        # one the record supports, which is none, with the state said plainly.
        return Assessment(
            facet="currency",
            grade=None,
            rationale=(
                "The publisher states the dataset is discontinued. It is not late against a "
                "cadence and nothing supersedes it, so neither Aging nor Superseded applies."
            ),
            evidence={"updateCadence": cadence.stated},
            computed_at=now,
        )

    if not cadence.comparable:
        return not_assessed(
            "currency",
            f"The stated cadence is {cadence.stated!r}, which is not an interval, so "
            "'past due' has no meaning for this dataset.",
        )

    if last_update is None:
        return not_assessed(
            "currency",
            f"The dataset states a cadence of {cadence.stated} but records no modification "
            "or issue date, so there is no vintage to measure the interval from.",
        )

    due = cadence.due_after(last_update)
    assert due is not None  # comparable cadence, checked above
    overdue = now > due
    # Note what the rationale deliberately does *not* say: how many days
    # overdue. That number changes every day without the dataset changing at
    # all, so a rationale carrying it would differ on every pass — and a
    # recompute that always differs writes a new `og:lastComputedAt` every
    # time, which makes the freshness lag it exists to expose meaningless. The
    # due date is in the evidence; a reader can subtract.
    return Assessment(
        facet="currency",
        grade="B" if overdue else "A",
        rationale=(
            f"Last updated {last_update.date().isoformat()} against a stated cadence of "
            f"{cadence.stated}; "
            + (
                f"it was due by {due.date().isoformat()} and has not been updated since."
                if overdue
                else f"next due {due.date().isoformat()}."
            )
        ),
        evidence={
            "updateCadence": cadence.stated,
            "lastUpdate": last_update.isoformat(),
            "dueBy": due.isoformat(),
        },
        computed_at=now,
    )


def _latest(graph: Graph, iri: URIRef, now: datetime) -> datetime | None:
    """The most recent vintage the record states.

    ``dct:modified``, else ``dct:issued``, else the end of the temporal
    coverage — in that order of preference, not as a maximum. The distinction
    matters for the third: an operational feed commonly declares coverage
    running to the end of the current year, and taking the maximum would let a
    coverage window that extends into the future mark a dataset Current on the
    strength of a date that has not happened yet.

    Coverage end is used only when no timestamp exists at all, and only when it
    is in the past. It is a real signal there: an annual snapshot whose coverage
    stops in 2019 and which carries no modified date is aging, whatever its
    silence about timestamps.
    """
    for candidate in (graph.value(iri, DCTERMS.modified), graph.value(iri, DCTERMS.issued)):
        stamp = _datetime(candidate)
        if stamp is not None:
            return stamp
    end = _datetime(_temporal_end(graph, iri))
    return end if end is not None and end <= now else None


def _temporal_end(graph: Graph, iri: URIRef) -> object:
    for period in graph.objects(iri, DCTERMS.temporal):
        end = graph.value(period, DCAT.endDate)
        if end is not None:
            return end
    return None


def _datetime(node: object) -> datetime | None:
    """Coerce an RDF term to an aware datetime, or ``None``.

    Untyped string literals are parsed too, not only ``xsd:dateTime`` ones. A
    harvested record whose ``dct:modified`` arrived as a bare string is
    extremely common, and refusing to read it would leave the dataset
    permanently ungraded for a reason no operator could see — the record has a
    date, it is on the screen, and the facet says there is no vintage.
    """
    if node is None:
        return None
    try:
        value = node.toPython()  # type: ignore[attr-defined]
    except AttributeError:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        return _parse_iso(value)
    if hasattr(value, "year"):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    return None


def _parse_iso(text: str) -> datetime | None:
    candidate = text.strip().replace("Z", "+00:00")
    for parse in (datetime.fromisoformat, _date_only):
        try:
            parsed = parse(candidate)
        except ValueError:
            continue
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _date_only(text: str) -> datetime:
    return datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=UTC)


def _text(node: object) -> str | None:
    return str(node) if node is not None else None


__all__ = ["GRACE_FRACTION", "NON_DURATION_CADENCE", "Cadence", "grade_currency", "parse_cadence"]
