"""When each signal recomputes (WP-7.3).

PRD §F4.3 names this as *the most likely correctness bug in the whole build*,
and gives the rule that avoids it:

> **Relational signals** (links, shared-origin flags, anything comparative)
> recompute when a related record is created or updated.
> **Self-contained signals** (facts derivable from the record alone, notably
> Currency & Maintenance) recompute on a scheduled batch, because these go
> stale purely from time passing with no write event to hook.
>
> A dataset does not become stale because someone edited it.

The failure the rule prevents is subtle and permanent. Hang Currency off the
write event and a dataset that nobody touches is graded Current forever: the
grade is only recomputed by the thing that cannot change it, and never by the
thing that can. Nothing errors. The facet simply stops being true, most
visibly for the abandoned datasets it exists to flag.

The inverse failure is cheaper but real: recompute a relational signal on a
schedule and a new record's links appear at the next batch rather than when it
lands, so a dataset is in the catalog and unconnected to it for hours.

This module holds the classification and nothing else. It does no work, which
is the point — the split has to be readable in one screen and assertable by
test, not distributed across the callers that honour it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Trigger(StrEnum):
    """What causes a signal to be recomputed."""

    WRITE = "on-write"
    """The record itself changed. Recompute what depends only on this record."""

    RELATED_WRITE = "on-related-write"
    """Some *other* record changed. Recompute what compares the two."""

    SCHEDULE = "on-schedule"
    """Time passed. Recompute what goes stale without any write at all."""


@dataclass(frozen=True, slots=True)
class Signal:
    """One computed signal and the trigger that owns it."""

    name: str
    trigger: Trigger
    #: Why this trigger and not another. Present on every signal because the
    #: classification is the thing that goes wrong, and a reader changing one
    #: needs the argument in front of them rather than in a commit message.
    because: str
    #: How long the signal may go unrecomputed before it is considered stale.
    #: ``None`` for event-driven signals, which are fresh by construction.
    max_age_days: int | None = None


#: Every signal the semantic layer computes. Adding one without a trigger is
#: not possible; adding one with the wrong trigger is caught by
#: ``tests/semantic/test_triggers.py``, which asserts the two rules below
#: rather than the contents of this table.
SIGNALS: tuple[Signal, ...] = (
    Signal(
        name="concept-resolution",
        trigger=Trigger.WRITE,
        because=(
            "Resolution reads this record's fields and the vocabulary. Neither another "
            "record's edit nor the passage of time can change the answer."
        ),
    ),
    Signal(
        name="unit-resolution",
        trigger=Trigger.WRITE,
        because="Same inputs as concept resolution: this record's fields and the registry.",
    ),
    Signal(
        name="provenance-grade",
        trigger=Trigger.WRITE,
        because=(
            "Derived from this record's upstream links and its fields' value bases. A "
            "steward confirms it once and re-confirms it when the dataset version changes "
            "(PRD §F5), and both of those are writes to this record."
        ),
    ),
    Signal(
        name="documentation-grade",
        trigger=Trigger.WRITE,
        because="Derived from this record's own field metadata and nothing else.",
    ),
    Signal(
        name="currency-grade",
        trigger=Trigger.SCHEDULE,
        because=(
            "A dataset goes stale by not being updated. There is no write event to hook, "
            "because the absence of a write is precisely what makes the grade change. "
            "Hanging this off the write event would grade an abandoned dataset Current "
            "forever, and nothing would error."
        ),
        max_age_days=1,
    ),
    Signal(
        name="inter-dataset-links",
        trigger=Trigger.RELATED_WRITE,
        because=(
            "A pair's strength depends on both records. A new record must be linked when it "
            "lands, not at the next batch, or it sits in the catalog unconnected to it."
        ),
    ),
    Signal(
        name="shared-origin-warning",
        trigger=Trigger.RELATED_WRITE,
        because=(
            "Comparative by definition: whether two datasets share an upstream can change "
            "when either one's lineage is edited."
        ),
    ),
    Signal(
        name="link-health",
        trigger=Trigger.SCHEDULE,
        because=(
            "A URL rots without anybody editing the record that names it. Owned by the "
            "prober (WP-5.3); listed here so the catalog of scheduled signals is complete."
        ),
        max_age_days=7,
    ),
)

BY_NAME: dict[str, Signal] = {s.name: s for s in SIGNALS}


def signals_for(trigger: Trigger) -> tuple[Signal, ...]:
    return tuple(s for s in SIGNALS if s.trigger is trigger)


def is_self_contained(name: str) -> bool:
    """True when a signal depends on one record and nothing else.

    Note that this is not the same as "recomputes on write": Currency depends
    on one record *and the clock*, which makes it self-contained and yet
    impossible to trigger from a write.
    """
    signal = BY_NAME.get(name)
    return signal is not None and signal.trigger in (Trigger.WRITE, Trigger.SCHEDULE)


def stale_after(name: str) -> int | None:
    signal = BY_NAME.get(name)
    return signal.max_age_days if signal else None


__all__ = [
    "BY_NAME",
    "SIGNALS",
    "Signal",
    "Trigger",
    "is_self_contained",
    "signals_for",
    "stale_after",
]
