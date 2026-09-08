"""The publisher draft (#70) must stay honest about being a draft.

`data/publishers.yaml` is a proposal, not configuration -- nothing reads it.
It exists so that #65's WP-1 input is something a person corrects rather than
composes, and the risk with a file like that is not that it is wrong. It is
that it stops being marked as unreviewed, or drifts out of step with the seed
inventory it was extracted from, and starts being treated as fact.

If a later change makes something actually consume this file, these tests
should be replaced with ones about that consumer -- but `review_state` must
survive, because a crawl list nobody has checked is a very different input from
one somebody has.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PUBLISHERS = ROOT / "data" / "publishers.yaml"
SEED = ROOT / "data" / "seed-sources.yaml"


@pytest.fixture(scope="module")
def doc() -> dict:
    return yaml.safe_load(PUBLISHERS.read_text())


@pytest.fixture(scope="module")
def entries(doc) -> list[dict]:
    return doc["publishers"]


def test_it_says_it_is_a_draft(doc, entries) -> None:
    """Two claims, and the second is the one that rots.

    A file can carry `review_state: draft-unreviewed` at the top and still have
    had entries quietly marked reviewed underneath, which is how a draft turns
    into a fact nobody decided to accept.
    """
    assert doc["review_state"] == "draft-unreviewed"
    unreviewed = [e for e in entries if not e.get("reviewed")]
    assert len(unreviewed) == len(entries), (
        f"{len(entries) - len(unreviewed)} entries are marked reviewed while the file "
        "still declares itself unreviewed as a whole"
    )


def test_no_entry_claims_a_crawl_method(entries) -> None:
    """`crawl` is how to harvest a publisher, and nobody has checked.

    Whether these sites offer an API, a DCAT feed, a sitemap or only HTML is
    the next question and it is per publisher. A value here would be a guess
    that a harvester would then act on.
    """
    claimed = [e["id"] for e in entries if e.get("crawl") is not None]
    assert not claimed, f"these entries claim an unverified crawl method: {claimed}"


def test_every_entry_has_a_host_it_was_derived_from(entries) -> None:
    """The host is the only field here that is a fact rather than a judgement.

    It comes from a seed row's `access` URL. A name may be null -- an inferred
    publisher name is a claim about who stands behind a dataset -- but an entry
    with no host has no provenance at all and should not exist.
    """
    hostless = [e.get("id") for e in entries if not e.get("host")]
    assert not hostless, f"entries with no host: {hostless}"


def test_the_seed_datasets_counts_still_add_up(entries) -> None:
    """Extracted from the seed inventory, so it goes stale when that changes.

    Not an exact equality: three candidates were dropped as not being data
    publishers (a Nature article, a PyPI path segment, Zenodo's `/records/`
    prefix) and 23 seed rows carry no access URL. The assertion is that the
    file still describes roughly the inventory it was taken from, so adding
    twenty seed rows without regenerating this is visible.
    """
    seed = yaml.safe_load(SEED.read_text())
    rows = sum(len(b.get("datasets") or []) for b in seed["seed_datasets"].values())
    counted = sum(e["seed_datasets"] for e in entries)
    assert counted <= rows, f"the draft accounts for {counted} datasets out of {rows} seed rows"
    assert counted >= rows * 0.6, (
        f"the draft accounts for only {counted} of {rows} seed rows, so it has drifted "
        "out of step with the inventory it was extracted from — regenerate it"
    )


def test_covered_publishers_name_a_real_harvest_source(entries) -> None:
    """`covered_by_source` says "not a new crawl target, an unrun source".

    A stale id here would hide a genuine gap behind a source that does not
    exist, which is the opposite of what the field is for.
    """
    seed = yaml.safe_load(SEED.read_text())
    known = {s["id"] for s in seed["harvest_sources"]}
    bad = [
        (e["id"], e["covered_by_source"])
        for e in entries
        if e.get("covered_by_source") and e["covered_by_source"] not in known
    ]
    assert not bad, f"these name a harvest source that does not exist: {bad}"


def test_the_coverage_gap_is_still_the_headline(entries) -> None:
    """#65's diagnosis, measured from the publisher side.

    Every registered source is a research data repository; the datasets in this
    catalog mostly live on a publisher's own site. If this number ever climbs,
    the epic's premise has changed and the design should be revisited rather
    than the test relaxed.
    """
    covered = [e for e in entries if e.get("covered_by_source")]
    assert len(covered) < len(entries) * 0.25, (
        f"{len(covered)} of {len(entries)} publishers are now indexed by a registered "
        "source — #65 assumes almost none are, so recheck the design"
    )
