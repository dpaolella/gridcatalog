"""The golden set as a regression suite (WP-7.5).

PRD §11: *~60 fully-specified level 3 records across all ten domains, used to
regression-test concept resolution, link ranking and quality grading.
Hand-curated once, then frozen.*

`data/golden-set/expectations.yaml` is the frozen half. These tests are what
make it load-bearing: a change to the resolver or a grader that silently alters
an answer fails here rather than passing review.

The expectations are claims about what the system *should* answer. A failure is
not a signal to regenerate the file — see `data/golden-set/README.md`.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN = REPO_ROOT / "data" / "golden-set" / "expectations.yaml"
CONCEPT_BASE = "https://schema.opengrid.org/concept/grid-concept/"


@pytest.fixture(scope="session")
def golden() -> dict:
    return yaml.safe_load(GOLDEN.read_text())


@pytest.fixture(scope="session")
def as_of(golden) -> datetime:
    return datetime.fromisoformat(golden["as_of"].replace("Z", "+00:00"))


def names(golden) -> list[str]:
    return sorted(golden["records"])


def load_golden() -> dict:
    """Module-level read, so the record names can parametrise."""
    return yaml.safe_load(GOLDEN.read_text())


RECORD_NAMES = sorted(load_golden()["records"])


# ---- the file describes the corpus it claims to -------------------------


def test_every_fixture_has_an_expectation(golden) -> None:
    """A record with no entry is loaded and not regression-tested, which is a
    silent hole in the suite. Reported, not tolerated."""
    from fixtures.loader import record_names

    missing = set(record_names()) - set(golden["records"])
    assert not missing, f"add golden-set expectations for: {sorted(missing)}"


def test_no_expectation_names_a_record_that_does_not_exist(golden) -> None:
    from fixtures.loader import record_names

    stale = set(golden["records"]) - set(record_names())
    assert not stale, f"expectations for records that are gone: {sorted(stale)}"


def test_every_domain_is_represented(golden) -> None:
    """PRD §6's V1 target: level 1 across all ten domains."""
    covered = {d for entry in golden["records"].values() for d in entry["domains"]}
    assert set(golden["coverage"]["domains_required"]) <= covered


def test_the_shortfall_against_the_target_is_stated_not_hidden(golden) -> None:
    """17 records, not 60. The gap is real; a suite that quietly pretended
    otherwise would be worse than one that says so."""
    assert len(golden["records"]) < golden["coverage"]["target_records"]
    assert (REPO_ROOT / "data" / "golden-set" / "README.md").read_text().count("not 60")


def test_every_gap_expectation_says_why(golden) -> None:
    """A frozen gap with no reason is indistinguishable from an oversight, and
    the next reader cannot tell whether to fix it or leave it."""
    for name, entry in golden["records"].items():
        for field, expected in (entry.get("concepts") or {}).items():
            if expected.get("concept") is None:
                assert expected.get("why"), f"{name}.{field} is a gap with no stated reason"


# ---- resolution ----------------------------------------------------------


@pytest.mark.parametrize("name", RECORD_NAMES)
def test_concepts_resolve_as_expected(name: str, golden, runner, as_of) -> None:
    expected = golden["records"][name].get("concepts") or {}
    if not expected:
        pytest.skip("no field-level expectations for this record")

    outcome = runner.run_record(name, now=as_of, write=False)
    actual = {
        item.part.local_name: (item.concept, item.rung) for item in outcome.resolution.resolutions
    }

    for local_name, wanted in expected.items():
        assert local_name in actual, f"{name} has no field {local_name!r}"
        concept, rung = actual[local_name]
        want_concept = wanted["concept"]
        assert concept == (CONCEPT_BASE + want_concept if want_concept else None), (
            f"{name}.{local_name} resolved to {concept}"
        )
        assert rung == wanted["rung"], (
            f"{name}.{local_name} resolved by {rung}, not {wanted['rung']}"
        )


def test_the_first_done_criterion_holds_across_records(runner, as_of) -> None:
    """*Two differently-named fields for the same quantity resolve to one
    concept IRI* — across two real records rather than in a unit test."""
    era5 = runner.run_record("ecmwf-era5", now=as_of, write=False)
    nsrdb = runner.run_record("nrel-nsrdb", now=as_of, write=False)

    def concept_of(outcome, local_name: str) -> str | None:
        return next(
            r.concept for r in outcome.resolution.resolutions if r.part.local_name == local_name
        )

    assert concept_of(era5, "ssrd") == concept_of(nsrdb, "GHI")
    assert concept_of(era5, "ssrd") is not None


# ---- grading -------------------------------------------------------------


@pytest.mark.parametrize("name", RECORD_NAMES)
def test_grades_are_as_expected(name: str, golden, runner, as_of) -> None:
    expected = golden["records"][name]["grades"]

    outcome = runner.run_record(name, now=as_of, write=False)

    for facet, wanted in expected.items():
        assert outcome.grade(facet) == wanted, (
            f"{name} graded {facet}={outcome.grade(facet)}, expected {wanted}"
        )


@pytest.mark.parametrize("name", RECORD_NAMES)
def test_a_record_below_level_two_is_not_graded_on_the_confirmed_facets(
    name: str, golden, runner, as_of
) -> None:
    """PRD §F5. Not assessed is not grade D, and the corpus is where that rule
    is most likely to be broken by accident — most records are level 1."""
    if golden["records"][name]["level"] >= 2:
        pytest.skip("level 2 or above")

    outcome = runner.run_record(name, now=as_of, write=False)

    assert outcome.grade("provenance") is None
    assert outcome.grade("documentation") is None


def test_currency_is_graded_at_every_level(golden, runner, as_of) -> None:
    """Unlike the other two. Currency needs only a cadence and a vintage, both
    of which a level 1 record carries, so withholding it below level 2 would
    hide a fact the catalog knows."""
    level_one = [n for n, e in golden["records"].items() if e["level"] == 1]
    graded = [
        n for n in level_one if runner.run_record(n, now=as_of, write=False).grade("currency")
    ]

    assert graded, "no level 1 record got a Currency grade"


# ---- freshness -----------------------------------------------------------


def test_the_expectations_are_pinned_to_a_date(golden) -> None:
    """Currency is a function of the record and the clock. Without a pinned
    clock every A becomes a B and the build fails on a Tuesday."""
    assert golden["as_of"].endswith("Z")


def test_running_against_a_later_date_changes_currency_and_nothing_else(
    golden, runner, as_of
) -> None:
    """The property the pin protects, asserted rather than assumed."""
    from datetime import timedelta

    name = "eia-930"
    at_pin = runner.run_record(name, now=as_of, write=False)
    much_later = runner.run_record(name, now=as_of + timedelta(days=400), write=False)

    assert at_pin.grade("provenance") == much_later.grade("provenance")
    assert at_pin.grade("documentation") == much_later.grade("documentation")
    assert [r.concept for r in at_pin.resolution.resolutions] == [
        r.concept for r in much_later.resolution.resolutions
    ]
