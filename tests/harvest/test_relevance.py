"""The grid-relevance filter (WP-3.4).

PRD §7.2 states the policy these tests enforce: *err toward inclusion; a
wrongly excluded dataset is invisible, a wrongly included one is a review-queue
cost.* Every test here is about that asymmetry or about the audit trail that
makes it checkable.

The corpus is drawn from the seed inventory and from the kinds of dataset the
harvest sources actually carry, because a filter tested only on obvious cases
passes and then rejects ESA WorldCover in production.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.harvest.filters.relevance import (
    RelevanceFilter,
    Verdict,
    text_of,
    vocabulary_phrases,
)


@pytest.fixture
def rfilter() -> RelevanceFilter:
    return RelevanceFilter()


#: Datasets the catalog exists to hold. Every one is either in the seed
#: inventory or the kind of thing DD1–DD10 describe.
GRID = [
    ("PyPSA-Eur", "An open optimisation model of the European transmission system"),
    ("MATPOWER case archive", "Test cases for steady-state power system simulation"),
    ("PLEXOS-World 2015", "A global electricity model dataset"),
    ("Global Power Plant Database", "Open database of power plants with capacity in MW"),
    ("EIA-930", "Hourly electricity demand and generation by balancing authority"),
    ("LBNL Queued Up", "Interconnection queue data for generation and storage projects"),
    ("ENTSO-E Transparency", "Actual load and renewable generation per bidding zone"),
    ("NREL ATB", "Annual technology baseline cost and performance for generation"),
    ("Global Wind Atlas", "Mean wind speed and power density at 100m"),
    ("NREL NSRDB", "Solar irradiance time series for photovoltaic resource assessment"),
    ("GridKit", "Extracted transmission network topology from OpenStreetMap"),
    ("WECC transmission data", "Transmission line ratings and substation locations"),
]

#: Datasets that are not, including the collisions that make a naive keyword
#: filter useless: "solar wind", "grid computing", "energy" in nutrition.
NOT_GRID = [
    ("National Bus Timetable", "Bus and line schedules for the metropolitan area"),
    ("Dietary energy intake survey", "Caloric energy expenditure by household"),
    ("Sentinel-2 L2A", "Multispectral satellite imagery at 10m resolution"),
    ("Cloud storage pricing", "Storage and compute pricing for object storage"),
    ("Municipal library catalogue", "Books, periodicals and loan records"),
    ("Road traffic counts", "Vehicle counts on the trunk road network by hour"),
]


# ---- the two ends --------------------------------------------------------


@pytest.mark.parametrize(("title", "description"), GRID)
def test_grid_datasets_are_accepted(rfilter, title: str, description: str) -> None:
    decision = rfilter.decide(description, title=title)
    assert decision.accepted, f"{title} was rejected: {decision.reason}"


@pytest.mark.parametrize(("title", "description"), NOT_GRID)
def test_obviously_unrelated_datasets_are_rejected(rfilter, title: str, description: str) -> None:
    """Precision matters only so far — the queue absorbs mistakes — but a filter
    that accepts a library catalogue is not filtering."""
    decision = rfilter.decide(description, title=title)
    assert not decision.accepted, f"{title} was accepted: {decision.reason}"


def test_the_filter_errs_toward_inclusion(rfilter) -> None:
    """The asymmetry, stated as a test. A wrongly excluded dataset is invisible
    by construction: nobody searches for a record that was never created, so
    the mistake is never reported. A wrongly included one costs thirty
    seconds."""
    ambiguous = rfilter.decide(
        "Global land cover map at 10m resolution, suitable for siting analysis",
        title="ESA WorldCover",
    )
    assert ambiguous.accepted
    assert ambiguous.stage != "keyword", "an ambiguous record should not be decided by a rule"


# ---- the collisions that break naive filters -----------------------------


def test_solar_wind_is_not_wind_power(rfilter) -> None:
    heliophysics = rfilter.decide(
        "Heliospheric solar wind speed and coronal mass ejection observations",
        title="Solar wind observations from ACE",
    )
    wind_power = rfilter.decide(
        "Mean wind speed and wind power density for turbine siting",
        title="Global Wind Atlas",
    )
    assert wind_power.score > heliophysics.score


def test_grid_computing_is_not_a_power_grid(rfilter) -> None:
    decision = rfilter.decide(
        "Job scheduling traces from a grid computing cluster, with a grid search over "
        "hyperparameters",
        title="Cluster scheduling traces",
    )
    assert not decision.accepted


def test_statistical_power_is_not_electrical_power(rfilter) -> None:
    decision = rfilter.decide(
        "Statistical power analysis and power law fits for survey responses",
        title="Survey methodology notes",
    )
    assert not decision.accepted


def test_a_counter_term_never_rejects_on_its_own(rfilter) -> None:
    """A reliability study that discusses statistical power is still a grid
    dataset. Counter-terms subtract; they do not veto."""
    decision = rfilter.decide(
        "Statistical power of outage-frequency estimates for transmission line "
        "reliability on the bulk power system",
        title="Transmission reliability study data",
    )
    assert decision.accepted


# ---- tokenisation --------------------------------------------------------


def test_hyphenated_names_match(rfilter) -> None:
    """Whole-token matching is what stops "iso" hitting "isotope"; it also
    stopped "pypsa" hitting "pypsa-eur", which zeroed the single most
    recognisable name in European power-system modelling."""
    assert rfilter.decide("An open model", title="PyPSA-Eur").accepted
    assert rfilter.decide("Global model", title="PLEXOS-World").accepted


def test_substrings_do_not_match(rfilter) -> None:
    decision = rfilter.decide(
        "Stable isotope ratios in winding riverbeds, with isolation measurements",
        title="Isotope geochemistry",
    )
    assert decision.matched_terms == [], f"spurious matches: {decision.matched_terms}"


def test_a_title_counts_for_more_than_body_text(rfilter) -> None:
    """A grid term in the title is a stronger signal than the same term in the
    fourth paragraph of a boilerplate licence notice."""
    in_title = rfilter.decide("Some data about things.", title="Transmission network topology")
    in_body = rfilter.decide("Transmission network topology.", title="Some data about things")
    assert in_title.score > in_body.score


# ---- the audit trail -----------------------------------------------------


def test_every_decision_explains_itself(rfilter) -> None:
    """PRD §7.2: log every rejection with its reason so recall can be audited.
    A score nobody can explain is not auditable."""
    for title, description in GRID + NOT_GRID:
        decision = rfilter.decide(description, title=title)
        assert decision.reason, title
        assert "score" in decision.reason
        assert decision.stage in ("keyword", "vocabulary", "llm")


def test_a_rejection_says_which_kind_of_rejection_it_is(rfilter) -> None:
    """ "Nothing matched" and "only generic words matched" call for different
    fixes when an audit finds the rejection was wrong."""
    nothing = rfilter.decide("Books, periodicals and loan records", title="Library catalogue")
    generic = rfilter.decide("Storage and compute pricing", title="Cloud storage pricing")

    assert "no grid vocabulary term matched" in nothing.reason
    assert "only generic terms" in generic.reason


def test_the_decision_maps_onto_the_audit_row(rfilter) -> None:
    """The stored row and the decision cannot drift apart if one is built from
    the other."""
    from datahub.api.models.repositories import RelevanceRepository

    row = rfilter.decide("Hourly electricity demand", title="EIA-930").as_row()
    assert set(row) <= set(RelevanceRepository.record.__code__.co_varnames)


# ---- the vocabulary is the filter ---------------------------------------


def test_the_filter_reads_the_skos_vocabulary() -> None:
    """Widening the vocabulary widens the filter. That is the intended way to
    improve recall: a term worth filtering on is usually a term worth having as
    a concept."""
    phrases = vocabulary_phrases()
    assert len(phrases) > 300
    assert "transmission network" in phrases
    assert "capacity factor" in phrases


def test_single_word_concept_labels_are_excluded() -> None:
    """ "Bus", "line" and "node" are concept labels and also match a bus
    timetable, a queueing study and a graph-theory paper."""
    phrases = vocabulary_phrases()
    assert "bus" not in phrases
    assert "line" not in phrases
    assert all(" " in phrase for phrase in phrases)


def test_a_custom_vocabulary_changes_the_outcome() -> None:
    """The seam that makes the previous two tests more than decoration."""
    narrow = RelevanceFilter(vocabulary_terms=[])
    wide = RelevanceFilter(vocabulary_terms=["widget calibration"])
    text = "A dataset of widget calibration constants"

    assert wide.decide(text).score > narrow.decide(text).score


# ---- the classifier stage ------------------------------------------------


class _Rejecting:
    def classify(self, text: str, *, title: str | None = None) -> Verdict:
        return Verdict(relevant=False, reason="not about power systems", confidence=0.9, model="t")


class _Exploding:
    def classify(self, text: str, *, title: str | None = None) -> Verdict:
        raise RuntimeError("upstream 503")


AMBIGUOUS = ("ESA WorldCover", "Global land cover map at 10m resolution")


def test_the_classifier_sees_only_the_ambiguous_middle(monkeypatch, settings) -> None:
    """The expensive stage runs on the records the cheap stages could not
    settle, and on no others."""
    monkeypatch.setenv("DATAHUB_ENRICHMENT_ENABLED", "true")
    from datahub.config import get_settings, reset_settings

    reset_settings()
    rfilter = RelevanceFilter(get_settings(), classifier=_Rejecting())

    clear = rfilter.decide("Hourly electricity demand by balancing authority", title="EIA-930")
    junk = rfilter.decide("Books and loan records", title="Library catalogue")
    middle = rfilter.decide(AMBIGUOUS[1], title=AMBIGUOUS[0])

    assert clear.accepted and clear.stage != "llm"
    assert not junk.accepted and junk.stage != "llm"
    assert middle.stage == "llm"
    assert not middle.accepted
    assert middle.model == "t"
    assert middle.prompt_version


def test_a_broken_classifier_includes_rather_than_drops(monkeypatch) -> None:
    """The rule that matters most here. An unavailable third party must never
    quietly start shrinking the catalog."""
    monkeypatch.setenv("DATAHUB_ENRICHMENT_ENABLED", "true")
    from datahub.config import get_settings, reset_settings

    reset_settings()
    rfilter = RelevanceFilter(get_settings(), classifier=_Exploding())

    decision = rfilter.decide(AMBIGUOUS[1], title=AMBIGUOUS[0])

    assert decision.accepted
    assert "unavailable" in decision.reason
    assert decision.stage == "vocabulary", "a failed call is not an LLM decision"


def test_no_classifier_configured_includes(rfilter) -> None:
    decision = rfilter.decide(AMBIGUOUS[1], title=AMBIGUOUS[0])
    assert decision.accepted
    assert "no classifier configured" in decision.reason


def test_enrichment_disabled_skips_the_classifier(settings) -> None:
    """A configured classifier is not a licence to call it: the enrichment
    switch is off by default and governs every model call."""
    rfilter = RelevanceFilter(settings, classifier=_Rejecting())
    decision = rfilter.decide(AMBIGUOUS[1], title=AMBIGUOUS[0])
    assert decision.accepted
    assert decision.stage != "llm"


# ---- pulling text out of a source payload -------------------------------


def test_text_of_flattens_nested_source_shapes() -> None:
    """CKAN puts its tags in a list of dicts. A record whose only grid signal
    is its tags still has a grid signal."""
    payload = {
        "title": "Some dataset",
        "notes": "A description",
        "tags": [{"name": "transmission"}, {"name": "electricity"}],
        "extras": [{"key": "sector", "value": "power"}],
    }
    text = text_of(payload, "title", "notes", "tags", "extras")
    assert "transmission" in text
    assert "power" in text


def test_text_of_defaults_to_the_whole_payload() -> None:
    assert "widget" in text_of({"anything": "a widget"})


# ---- the recall check that uses real data --------------------------------


def test_no_seed_dataset_is_rejected(rfilter) -> None:
    """The strongest recall evidence available without a live harvest.

    All 114 curated anchor datasets are, by construction, datasets the catalog
    exists to hold. A filter that drops any of them would drop its
    equivalents when they arrive from a real source — and that failure would
    be invisible, because nobody searches for a record that was never created.
    """
    from datahub.harvest.adapters.curated import CuratedAdapter

    records, _ = CuratedAdapter().harvest()
    rejected = []
    for record in records:
        entry = record.payload
        text = text_of(entry, "note", "pointer_rationale", "format", "provenance", "domain_name")
        decision = rfilter.decide(text, title=entry["name"])
        if not decision.accepted:
            rejected.append(f"{entry['data_domain']} {entry['name']}: {decision.reason}")

    assert rejected == [], "seed datasets rejected:\n" + "\n".join(rejected)


def test_every_domain_survives_the_filter(rfilter) -> None:
    """Per-domain, so a filter that is strong on DD1 and blind to DD9 shows up
    as a blind spot rather than as a good average."""
    from collections import defaultdict

    from datahub.harvest.adapters.curated import CuratedAdapter

    records, _ = CuratedAdapter().harvest()
    accepted: dict[str, list[bool]] = defaultdict(list)
    for record in records:
        entry = record.payload
        text = text_of(entry, "note", "pointer_rationale", "format", "provenance", "domain_name")
        accepted[entry["data_domain"]].append(rfilter.decide(text, title=entry["name"]).accepted)

    assert len(accepted) == 10
    for domain, results in sorted(accepted.items()):
        assert all(results), f"{domain}: {results.count(False)} of {len(results)} rejected"
