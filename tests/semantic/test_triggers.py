"""The trigger split (WP-7.3).

PRD §F4.3 calls getting this wrong *the most likely correctness bug in the
whole build*, and it is the kind of bug that never raises: hang Currency off
the write event and an abandoned dataset is graded Current forever, because the
grade is recomputed only by the thing that cannot change it.

These tests assert the *rules*, not the contents of the table. A test that
listed the signals would pass for a table that had been edited into
incoherence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.semantic.triggers import (
    BY_NAME,
    SIGNALS,
    Trigger,
    is_self_contained,
    signals_for,
    stale_after,
)

#: Signals whose value can change while no record changes.
TIME_DEPENDENT = {"currency-grade", "link-health"}

#: Signals whose value depends on more than one record.
COMPARATIVE = {"inter-dataset-links", "shared-origin-warning"}


def test_a_dataset_does_not_become_stale_because_someone_edited_it() -> None:
    """The sentence from PRD §F4.3, as an assertion. Currency must not be
    triggered by a write, because the absence of a write is exactly what makes
    the grade change."""
    assert BY_NAME["currency-grade"].trigger is Trigger.SCHEDULE


@pytest.mark.parametrize("name", sorted(TIME_DEPENDENT))
def test_every_time_dependent_signal_is_scheduled(name: str) -> None:
    assert BY_NAME[name].trigger is Trigger.SCHEDULE
    assert stale_after(name), "a scheduled signal needs a staleness budget or it is never due"


@pytest.mark.parametrize("name", sorted(COMPARATIVE))
def test_every_comparative_signal_recomputes_on_a_related_write(name: str) -> None:
    """The inverse failure, and cheaper but real: on a schedule, a new record
    sits in the catalog unconnected to it until the next batch."""
    assert BY_NAME[name].trigger is Trigger.RELATED_WRITE


def test_no_scheduled_signal_is_comparative_and_no_comparative_one_is_scheduled() -> None:
    scheduled = {s.name for s in signals_for(Trigger.SCHEDULE)}
    related = {s.name for s in signals_for(Trigger.RELATED_WRITE)}

    assert not (scheduled & COMPARATIVE)
    assert not (related & TIME_DEPENDENT)


def test_every_signal_states_why_it_has_the_trigger_it_has() -> None:
    """The classification is what goes wrong. A reader changing one needs the
    argument in front of them, not in a commit message."""
    for signal in SIGNALS:
        assert len(signal.because) > 40, f"{signal.name} does not say why"


def test_a_write_triggered_signal_has_no_staleness_budget() -> None:
    """Event-driven signals are fresh by construction. A max age on one implies
    a sweeper that does not exist."""
    for signal in signals_for(Trigger.WRITE):
        assert signal.max_age_days is None


def test_currency_is_self_contained_and_still_not_write_triggered() -> None:
    """The distinction the whole split turns on: Currency depends on one record
    *and the clock*, which makes it self-contained and impossible to trigger
    from a write."""
    assert is_self_contained("currency-grade")
    assert BY_NAME["currency-grade"].trigger is not Trigger.WRITE


def test_the_confirmed_and_automatic_facets_do_not_overlap() -> None:
    from datahub.semantic.grading.facets import AUTOMATIC_FACETS, CONFIRMED_FACETS

    assert not (AUTOMATIC_FACETS & CONFIRMED_FACETS)
    assert "currency" in AUTOMATIC_FACETS, "PRD §F5: never manual, never a re-trigger"


def test_every_signal_name_is_unique() -> None:
    assert len({s.name for s in SIGNALS}) == len(SIGNALS)
