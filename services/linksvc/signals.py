"""Per-pair signals (WP-8.1).

PRD §F6 combines six signals and one penalty into a strength score. This module
computes them and nothing else — it does no ranking, applies no weights and
makes no judgement about what the numbers mean.

Every signal is in ``[0, 1]`` and every one has an *evidence* payload, because
PRD §F6 ends with: *every surfaced pairing carries at least one concrete
human-readable reason. A bare numeric score is not sufficient and should fail
review.* A signal that could not say what it saw could not contribute a reason,
so the evidence is produced here rather than reconstructed later from the
score.

Signals read the **search document**, not the graph. The document is the
denormalised projection of a record and already carries concepts, coverage and
grades; reading the graph per pair would be one query per candidate against a
list that is quadratic before capping. Lineage is the exception — it is a walk,
not a field — and comes from :class:`~datahub.semantic.provenance.LineageIndex`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from datahub.api.search.document import SearchDocument
from datahub.semantic.provenance import LineageIndex, SharedOrigin
from datahub.semantic.vocabulary import Vocabulary

#: Grades, as numbers, for the ranking input only.
#:
#: **This is not a quality score** (ADR-0007). It never leaves this module as a
#: dataset attribute, is never persisted on a record, and is never rendered. It
#: exists because PRD §F6 lists quality as a contributing factor to *link
#: strength*: given two equally related datasets, the better-documented one is
#: the more useful suggestion. The moment this number is attached to a dataset
#: rather than to a pairing it becomes the composite score the ADR forbids.
_GRADE_VALUE: dict[str, float] = {"A": 1.0, "B": 0.7, "C": 0.4, "D": 0.1}


@dataclass(frozen=True, slots=True)
class Signal:
    """One computed signal, with what it saw."""

    name: str
    value: float
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PairSignals:
    """Everything computed about one ordered pair."""

    source: str
    target: str
    signals: dict[str, Signal]
    shared_origins: tuple[SharedOrigin, ...] = ()

    def value(self, name: str) -> float:
        signal = self.signals.get(name)
        return signal.value if signal else 0.0

    def evidence(self, name: str) -> dict[str, Any]:
        signal = self.signals.get(name)
        return dict(signal.evidence) if signal else {}

    @property
    def correlated(self) -> bool:
        return bool(self.shared_origins)


def compute(
    source: SearchDocument,
    target: SearchDocument,
    *,
    lineage: LineageIndex | None = None,
    vocabulary: Vocabulary | None = None,
) -> PairSignals:
    """Every signal for one pair."""
    signals = [
        concept_overlap(source, target),
        geographic_overlap(source, target),
        temporal_overlap(source, target),
        joinable_key_present(source, target, vocabulary),
        workflow_tag_overlap(source, target),
        quality_contribution(source, target),
    ]
    origins = tuple(lineage.shared_origins(source.iri, target.iri)) if lineage is not None else ()
    return PairSignals(
        source=source.id,
        target=target.id,
        signals={s.name: s for s in signals},
        shared_origins=origins,
    )


# ---------------------------------------------------------------------------
# The six
# ---------------------------------------------------------------------------


def concept_overlap(a: SearchDocument, b: SearchDocument) -> Signal:
    """How much of the same *stuff* the two describe.

    Over the expanded concept closure rather than the leaf concepts, so a
    dataset carrying nodal LMP and one carrying day-ahead LMP overlap through
    their shared parent. Comparing leaves only would call two datasets about
    the same quantity unrelated because one is more specific.
    """
    left = set(a.concept_iris_expanded) or {c.iri for c in a.concepts}
    right = set(b.concept_iris_expanded) or {c.iri for c in b.concepts}
    shared = left & right
    value = len(shared) / len(left | right) if (left and right) else 0.0

    labels = {c.iri: c.label for c in (*a.concepts, *b.concepts) if c.label}
    return Signal(
        "concept_overlap",
        value,
        {
            "shared": sorted(shared)[:12],
            "shared_labels": sorted({labels[i] for i in shared if i in labels})[:8],
            "shared_count": len(shared),
        },
    )


def geographic_overlap(a: SearchDocument, b: SearchDocument) -> Signal:
    """Bounding-box intersection over union, or named-place overlap.

    IoU rather than "do they intersect", because a global dataset intersects
    everything. A continental reanalysis and a single substation's telemetry
    do overlap, and saying so at full strength would put the reanalysis at the
    top of every list in the catalog.
    """
    if a.spatial.bbox and b.spatial.bbox:
        value = _iou(a.spatial.bbox, b.spatial.bbox)
        return Signal(
            "geographic_overlap",
            value,
            {"method": "bbox-iou", "a": a.spatial.bbox, "b": b.spatial.bbox},
        )

    left, right = set(a.spatial.place_iris), set(b.spatial.place_iris)
    if left and right:
        shared = left & right
        return Signal(
            "geographic_overlap",
            len(shared) / len(left | right),
            {"method": "place-iris", "shared": sorted(shared)[:8]},
        )
    # Not "no overlap" — not captured. Contributing 0 is right for ranking and
    # the evidence says which of the two it is, so a descriptor never claims
    # the datasets cover different places when nobody recorded where either is.
    return Signal("geographic_overlap", 0.0, {"method": "none", "reason": "coverage not captured"})


def temporal_overlap(a: SearchDocument, b: SearchDocument) -> Signal:
    """Overlap of the two coverage windows, as a fraction of their union."""
    left = _window(a.temporal.start, a.temporal.end)
    right = _window(b.temporal.start, b.temporal.end)
    if left is None or right is None:
        return Signal(
            "temporal_overlap", 0.0, {"reason": "temporal coverage not captured on both sides"}
        )

    start = max(left[0], right[0])
    end = min(left[1], right[1])
    if end <= start:
        return Signal("temporal_overlap", 0.0, {"reason": "coverage windows do not overlap"})

    union_start, union_end = min(left[0], right[0]), max(left[1], right[1])
    span = (union_end - union_start).total_seconds()
    value = (end - start).total_seconds() / span if span else 0.0
    return Signal(
        "temporal_overlap",
        value,
        {"from": start.date().isoformat(), "to": end.date().isoformat()},
    )


def joinable_key_present(
    a: SearchDocument, b: SearchDocument, vocabulary: Vocabulary | None = None
) -> Signal:
    """Whether the two share a concept the vocabulary marks as a join key.

    Not "do they share any concept" — that is `concept_overlap`. A join key is
    a concept the vocabulary has flagged as something you can actually join on
    (a plant identifier, a bus id, a balancing-authority code), and its
    presence is the difference between "these are about similar things" and
    "you can put these in the same table".

    Without a vocabulary this reports 0 with a reason rather than guessing.
    Inferring join-ability from a name would produce a claim a user acts on and
    then discovers to be false at the point of the join.
    """
    if vocabulary is None:
        return Signal("joinable_key_present", 0.0, {"reason": "no vocabulary available"})

    shared = {c.iri for c in a.concepts} & {c.iri for c in b.concepts}
    keys = [iri for iri in sorted(shared) if (c := vocabulary.get(iri)) and c.is_join_key]
    labels = [c.pref_label for iri in keys if (c := vocabulary.get(iri))]
    return Signal(
        "joinable_key_present",
        1.0 if keys else 0.0,
        {"keys": keys, "key_labels": labels},
    )


def workflow_tag_overlap(a: SearchDocument, b: SearchDocument) -> Signal:
    """Analyses both datasets support. PRD §F6.6: *"Both feed: Capacity
    Expansion Modeling, Production Cost Modeling."*"""
    left = {c.iri for c in a.supported_analysis}
    right = {c.iri for c in b.supported_analysis}
    if not left or not right:
        return Signal("workflow_tag_overlap", 0.0, {"reason": "supported analyses not captured"})

    shared = left & right
    labels = {c.iri: c.label for c in (*a.supported_analysis, *b.supported_analysis) if c.label}
    return Signal(
        "workflow_tag_overlap",
        len(shared) / len(left | right),
        {
            "shared": sorted(shared),
            "shared_labels": sorted({labels[i] for i in shared if i in labels}),
        },
    )


def quality_contribution(a: SearchDocument, b: SearchDocument) -> Signal:
    """A ranking nudge toward the better-documented of two equal suggestions.

    **Not a quality score** (ADR-0007), and the distinction is not a formality:
    this number describes a *pairing's usefulness as a suggestion*, never a
    dataset. It is computed here, consumed by the ranker, and discarded. It is
    not written to a record, not projected into the index and not returned by
    the API.

    Only assessed facets count. A record below completeness level 2 has no
    Provenance or Documentation grade, and treating "not assessed" as a low
    score would bury every harvested record — the same conflation PRD §F5
    forbids on the display side, arriving through the ranking instead.
    """
    grades = [
        g
        for doc in (a, b)
        for g in (doc.quality.provenance, doc.quality.documentation, doc.quality.currency)
        if g
    ]
    if not grades:
        return Signal("quality_contribution", 0.0, {"reason": "neither record is graded yet"})
    value = sum(_GRADE_VALUE.get(g, 0.0) for g in grades) / len(grades)
    return Signal("quality_contribution", value, {"graded_facets": len(grades)})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iou(a: list[float], b: list[float]) -> float:
    """Intersection over union of two [minLon, minLat, maxLon, maxLat] boxes."""
    if len(a) != 4 or len(b) != 4:
        return 0.0
    lon = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    lat = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = lon * lat
    if intersection <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def _window(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime] | None:
    if start is None or end is None:
        return None
    lo, hi = (start, end) if start <= end else (end, start)
    return (_aware(lo), _aware(hi))


def _aware(value: datetime) -> datetime:
    from datetime import UTC

    return value if value.tzinfo else value.replace(tzinfo=UTC)


__all__ = ["PairSignals", "Signal", "compute"]
