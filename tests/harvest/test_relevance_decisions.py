"""The relevance decision file, and the classifier that reads it.

Two things are being defended. That the file is well-formed and says what it
means — a malformed entry here silently changes what the catalog publishes.
And that the decisions actually reach the filter, because the whole failure
this replaces was a third stage that existed on paper and ran nowhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.harvest.filters.decisions import (
    DECIDED_BY,
    StaticClassifier,
    load_decisions,
)
from datahub.harvest.filters.relevance import (
    ACCEPT_AT,
    REJECT_BELOW,
    RelevanceFilter,
    Undecided,
    text_of,
)

DECISIONS = Path(__file__).resolve().parents[2] / "data" / "relevance-decisions.yaml"


@pytest.fixture(scope="module")
def decisions() -> dict:
    return load_decisions(str(DECISIONS))


# ---- the file ------------------------------------------------------------


def test_the_shipped_file_parses(decisions) -> None:
    assert decisions, "the committed decision file is not empty"


def test_every_entry_is_a_boolean_with_a_reason(decisions) -> None:
    """A reason on every entry, including the accepts.

    `relevance.py` opens by saying a recall audit needs to compare what was
    taken against what was passed over. An entry with no reason cannot be
    argued with, which makes it not a decision but an assertion.
    """
    for key, entry in decisions.items():
        assert isinstance(entry["relevant"], bool), key
        assert entry.get("reason"), f"{key} has no reason"
        assert len(entry["reason"]) > 20, f"{key}'s reason is too thin to argue with"


def test_keys_are_source_scoped(decisions) -> None:
    """`<source>:<identifier>`, so two sources carrying the same dataset under
    the same slug are decided separately rather than colliding."""
    for key in decisions:
        assert ":" in key, f"{key} is not scoped to a source"
        source, _, identifier = key.partition(":")
        assert source and identifier, key


def test_a_non_boolean_relevant_is_refused(tmp_path) -> None:
    """`relevant: "false"` is a truthy string. Refusing it here is cheaper than
    finding out from a catalog that published everything."""
    bad = tmp_path / "bad.yaml"
    bad.write_text('decisions:\n  a:b:\n    relevant: "false"\n    reason: x\n')
    with pytest.raises(ValueError, match="non-boolean"):
        load_decisions(str(bad))


def test_a_missing_file_is_empty_rather_than_fatal(tmp_path) -> None:
    """A deployment with no file is where every deployment was before this
    existed, and should behave the same way rather than failing a harvest."""
    assert load_decisions(str(tmp_path / "nope.yaml")) == {}


# ---- the classifier ------------------------------------------------------


def test_a_recorded_decision_comes_back_as_a_verdict() -> None:
    classifier = StaticClassifier(DECISIONS)
    verdict = classifier.classify("", key="aws_open_data:ai3")
    assert verdict.relevant is False
    assert verdict.model == DECIDED_BY
    assert verdict.reason


def test_an_unknown_key_is_undecided_not_irrelevant() -> None:
    """The distinction the whole design turns on. Returning "not relevant" for
    a record nobody has looked at would make the file a deny-by-default list,
    and a dataset excluded by an omission is invisible in the way `relevance.py`
    opens by warning about."""
    classifier = StaticClassifier(DECISIONS)
    with pytest.raises(Undecided):
        classifier.classify("", key="aws_open_data:nothing-by-this-name")


def test_no_key_is_undecided() -> None:
    classifier = StaticClassifier(DECISIONS)
    with pytest.raises(Undecided):
        classifier.classify("some text", title="Some title")


# ---- end to end ----------------------------------------------------------


def test_the_default_filter_reads_the_committed_file() -> None:
    """The regression that let half a catalog through.

    `RelevanceFilter()` with no arguments is what the harvest runner builds. It
    used to default to no classifier at all, so every ambiguous record took the
    accept-everything branch. If this fails, the third stage is dead again and
    nothing else in the suite will say so.
    """
    assert isinstance(RelevanceFilter().classifier, StaticClassifier)


REGISTRY = Path("var/harvest/aws_open_data/datasets")


@pytest.mark.skipif(not REGISTRY.is_dir(), reason="no AWS registry clone in var/harvest")
@pytest.mark.parametrize(
    ("slug", "accepted"),
    [
        # Published in the real catalog and none of them power-system data.
        # Which stage refuses each is deliberately not asserted: the vocabulary
        # fix moved several of these below the rejection floor, so they never
        # reach the third stage at all. What must hold is the outcome.
        ("tabula-sapiens", False),
        ("humancellatlas", False),
        ("allenai-tablestore-questions", False),
        ("ai3", False),
        ("allen-cell-imaging-collections", False),
        ("pdb-3d-structural-biology-data", False),
        ("brainminds-marmoset-connectivity", False),
        # And the ones that belong, so this is not just a deny list.
        ("era5-for-wrf", True),
        ("noaa-hrrr-pds", True),
        ("gadal", True),
        ("asset-data-africa-power-generation", True),
        ("esa-worldcover", True),
        ("nrel-pds-windai", True),
    ],
)
def test_the_filter_settles_named_records_as_intended(slug: str, accepted: bool) -> None:
    """End to end over the real registry entry, not over a contrived string."""
    import yaml

    path = REGISTRY / f"{slug}.yaml"
    if not path.exists():
        pytest.skip(f"{slug} is not in this registry clone")
    entry = yaml.safe_load(path.read_text())
    decision = RelevanceFilter().decide(
        text_of(entry), title=entry.get("Name", ""), key=f"aws_open_data:{slug}"
    )
    assert decision.accepted is accepted, decision.reason


@pytest.mark.skipif(not REGISTRY.is_dir(), reason="no AWS registry clone in var/harvest")
def test_a_decided_record_reaches_the_verdict_through_the_filter() -> None:
    """The wiring, not the file: a decision is worthless if `decide` never
    consults it, and that is exactly the state this replaces.

    Over a real registry entry, because a contrived payload does not land in
    the ambiguous middle — it scores below the floor and is refused by the
    keyword stage, which would make this test pass without the third stage
    running at all.
    """
    import yaml

    entry = yaml.safe_load((REGISTRY / "ai3.yaml").read_text())
    decision = RelevanceFilter().decide(
        text_of(entry), title=entry.get("Name", ""), key="aws_open_data:ai3"
    )
    assert not decision.accepted
    assert decision.stage == "decided"
    assert decision.model == DECIDED_BY


@pytest.mark.skipif(not REGISTRY.is_dir(), reason="no AWS registry clone in var/harvest")
def test_the_file_decides_the_ambiguous_middle_and_only_that(decisions) -> None:
    """The third stage sees the ambiguous middle and nothing else.

    A decision for a record the keyword stage already settles is dead weight at
    best, and at worst two mechanisms disagreeing with no way for a reader to
    tell which one applied. A record in the middle with *no* decision is the
    gap this file exists to close.

    Needs the registry clone the harvest reads, so it skips without one rather
    than asserting against an empty set and passing for the wrong reason.
    """
    import yaml
    from datahub.harvest.filters.relevance import _normalise

    rfilter = RelevanceFilter(classifier=None)
    middle, settled = set(), set()
    for path in sorted(REGISTRY.glob("*.yaml")):
        entry = yaml.safe_load(path.read_text())
        if not isinstance(entry, dict):
            continue
        name = entry.get("Name", "")
        score, _ = rfilter.score(_normalise(f"{name} {name} {text_of(entry)}"))
        key = f"aws_open_data:{path.stem}"
        (middle if REJECT_BELOW <= score < ACCEPT_AT else settled).add(key)

    covered = set(decisions)
    assert not (middle - covered), (
        f"{len(middle - covered)} ambiguous records have no decision, so they are "
        f"published on nothing having judged them: {sorted(middle - covered)[:5]}"
    )
    assert not (covered & settled), (
        f"{len(covered & settled)} decisions duplicate a verdict the keyword stage "
        f"already reaches: {sorted(covered & settled)[:5]}"
    )
