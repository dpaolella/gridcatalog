"""A caveat about the data, not about the pipeline (#55).

Measured before this change: 298 records carried 478 caveats over **16
distinct texts**, and fifteen of the sixteen were generated — "harvested
automatically and not yet reviewed", "the access barrier has not been
checked", "normalisation warnings". All true, all about the state of the
catalog. Exactly one was a fact about the data a modeller would hit.

That is worse than an empty section. A reader who opens five records meets the
same four sentences five times and stops reading them, which is where the one
real caveat goes to die.

The Data Domain Assessment's detailed view is a supply of the missing kind:
dataset-level defects, each attributed to a practitioner who hit it. This
covers loading them, and the property that makes them worth loading: they say
who found them.

Ordering — findings ahead of boilerplate — turns out not to be expressible
here at all. See the test below.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.graph.graphs import NamedGraph
from datahub.graph.loader import bootstrap
from datahub.graph.records import RecordStore, dataset_node
from datahub.graph.store import RdflibStore
from datahub.harvest.seed import SeedLoader

#: The generated ones, by an opening phrase each begins with.
STRUCTURAL = (
    "Assembled for the PRD",
    "Access barrier recorded as",
    "The access barrier has not been checked",
    "Documentation status records an absence",
    "Reference only.",
)


@pytest.fixture(scope="module")
def catalog() -> dict[str, dict]:
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    SeedLoader(records).load()
    return {
        dataset_id.rsplit("/", 1)[-1]: dataset_node(records.get(dataset_id, graph=graph))
        for graph in (NamedGraph.CATALOG, NamedGraph.DRAFT)
        for dataset_id in records.list_ids(graph=graph)
    }


def caveats(node: dict) -> list[str]:
    found = (node.get("qualityFlags") or {}).get("caveat") or []
    return [str(text) for text in (found if isinstance(found, list) else [found])]


def test_the_assessment_findings_reach_the_records(catalog) -> None:
    expected = {
        "ecmwf-era5": "30 km grid is too coarse for siting",
        "nrel-atb": "no market clearing prices",
        "lazard-lcoe": "published as a PDF report with no structured artifact",
        "hifld": "no electrical parameters",
        "elcc-studies-by-iso": "3% to 90%",
        "entso-e-transparency-platform-renewable-generation": "gaps and inconsistencies",
        "wood-mackenzie-wind-solar": "paywalled and not integrated",
    }
    for dataset_id, fragment in expected.items():
        assert any(fragment in text for text in caveats(catalog[dataset_id])), (
            f"{dataset_id} should carry the assessment's finding {fragment!r}; "
            f"it has {caveats(catalog[dataset_id])}"
        )


def test_a_finding_names_who_found_it(catalog) -> None:
    """The difference between a claim the Hub makes and one it relays.

    "ENTSO-E has gaps at 15-minute resolution" is the Hub asserting something
    about somebody else's data. "Bruegel and Ember report gaps…" is the Hub
    relaying a finding a reader can go and check, and weigh against their own
    experience. The second is both more useful and more defensible.
    """
    for dataset_id in ("ecmwf-era5", "nrel-atb", "elcc-studies-by-iso"):
        stated = [t for t in caveats(catalog[dataset_id]) if not t.startswith(STRUCTURAL)]
        assert stated, dataset_id
        for text in stated:
            assert "Data Domain Assessment, 2026-05-05" in text, (
                f"{dataset_id} carries an unattributed finding, which a reader cannot "
                f"weigh or check: {text!r}"
            )


def test_ordering_cannot_be_relied_on_and_the_ui_has_to_sort(catalog) -> None:
    """The fix for caveat fatigue is *not* available here, and this says why.

    `_caveats` returns findings first, and that ordering does not survive:
    `og:caveat` is `"@container": "@set"` in the context, so it round-trips
    through RDF as an unordered set and comes back in whatever order the graph
    yields. An earlier version of this test asserted the finding came first and
    passed — by luck, on the iteration order of a particular graph. Adding
    sixteen rows in #49 reordered it and the test failed, which is the only
    reason the assumption got caught rather than shipping.

    So ordering has to be a rendering decision, made where the caveats are
    displayed, from something structural: an attribution field, which is the
    same shape change #55 needs for the source and date. Until then this pins
    the fact rather than a false guarantee — both kinds are present, and which
    comes first is not ours to promise.
    """
    texts = caveats(catalog["elcc-studies-by-iso"])
    assert len(texts) > 1, "this row should carry both kinds, or it tests nothing"
    assert any(t.startswith(STRUCTURAL) for t in texts)
    assert any(not t.startswith(STRUCTURAL) for t in texts)


def test_the_corpus_is_no_longer_one_finding_in_sixteen(catalog) -> None:
    """The measurement the issue was filed on, as a floor."""
    stated = {
        text
        for node in catalog.values()
        for text in caveats(node)
        if not text.startswith(STRUCTURAL)
    }
    assert len(stated) >= 7, (
        f"only {len(stated)} distinct findings-from-use in the whole seed corpus: {stated}"
    )
