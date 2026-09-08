"""The gap register (#56).

A data requirement with no open supplier. PRD §5 says saying what does not
exist is a feature, and the catalog had two ways to say it — a field a source
never described, and a dataset that exists and cannot be obtained. Both hang
off something the catalog already holds. This is the third case: a thing a
modeller needs where nothing open supplies it, with no record to hang it on.

The rule these tests exist to hold: **a gap must never be able to pass as a
dataset.** Separate route, separate model, separate count, no licence, no
access path, no quality grade.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from datahub.gaps import GAPS_PATH, DataGap, register, search

ANALYSIS_CLASSES = {
    "capacityExpansion",
    "productionCost",
    "reliabilityAssessment",
    "acPowerFlow",
}


def test_the_register_loads_and_is_not_empty() -> None:
    gaps = register()
    assert len(gaps) == 19, "the workbook states nineteen; a change here should be deliberate"
    assert len({gap.id for gap in gaps}) == len(gaps), "ids collide"


def test_every_gap_states_a_reason_an_observer_and_a_date() -> None:
    """A gap with no reason is indistinguishable from nobody having looked, and
    an unattributed claim about the whole open landscape cannot be weighed.
    This is ADR-0011's rule applied one level up."""
    for gap in register():
        assert len(gap.reason) > 40, (gap.id, gap.reason)
        assert gap.observed_by, gap.id
        assert gap.observed, gap.id
        assert gap.review_after is not None, (
            f"{gap.id} has no review date, so nothing will ever prompt a re-check "
            "of a claim that goes stale"
        )


def test_a_gap_names_the_domain_and_the_analyses_that_need_it() -> None:
    for gap in register():
        assert gap.domain.startswith("DD"), gap.id
        assert set(gap.needed_by) <= ANALYSIS_CLASSES, (gap.id, gap.needed_by)


def test_the_search_answers_the_query_that_prompted_this() -> None:
    """ "nodal demand" returned nothing and said only "no datasets match"."""
    found = search("nodal demand")
    assert [gap.id for gap in found] == ["nodal-demand"]
    assert "very challenging to estimate" in found[0].reason


def test_search_narrows_by_domain_and_by_analysis_class() -> None:
    assert {gap.domain for gap in search(domain="DD4")} == {"DD4"}
    for gap in search(needed_by="acPowerFlow"):
        assert "acPowerFlow" in gap.needed_by


def test_an_empty_query_matches_nothing_rather_than_everything() -> None:
    """The caller is an empty result set, and answering a blank query with all
    nineteen would put a wall of absences under an unfiltered landing page."""
    assert search("") == list(register())[: len(register())]  # explicit: no query = no filter
    assert search("   ") == list(register())


def test_a_past_review_date_is_surfaced_not_hidden() -> None:
    """Hiding an ageing finding loses the finding *and* the fact it was made."""
    fresh = DataGap(
        id="x",
        title="t",
        domain="DD1",
        category="c",
        reason="r" * 50,
        observed_by="o",
        observed="2025",
        review_after=date.today() + timedelta(days=1),
    )
    aged = DataGap(
        id="y",
        title="t",
        domain="DD1",
        category="c",
        reason="r" * 50,
        observed_by="o",
        observed="2020",
        review_after=date.today() - timedelta(days=1),
    )
    assert not fresh.stale
    assert aged.stale


def test_a_gap_carries_nothing_that_would_let_it_pass_as_a_dataset() -> None:
    """The failure this design exists to prevent."""
    forbidden = {"license", "licence", "access", "distribution", "quality", "completeness"}
    fields = set(DataGap.__slots__)
    assert not (fields & forbidden), sorted(fields & forbidden)

    raw = yaml.safe_load(GAPS_PATH.read_text())
    for row in raw["gaps"]:
        assert not (set(row) & forbidden), (row["id"], sorted(set(row) & forbidden))


@pytest.mark.parametrize("gap", register(), ids=lambda gap: gap.id)
def test_each_reason_reads_as_a_finding_not_a_placeholder(gap: DataGap) -> None:
    """ "TBD" in this file would be worse than an absent entry: it looks like an
    answer."""
    lowered = gap.reason.lower()
    assert not any(filler in lowered for filler in ("tbd", "todo", "n/a", "unknown.")), gap.reason
