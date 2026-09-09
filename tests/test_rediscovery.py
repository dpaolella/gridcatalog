"""Could the pipeline have found this on its own? (the self-population benchmark)

The catalog has two populations with opposite problems: a small hand-curated
set with deep metadata because somebody typed it, and a large harvested set
with almost none. The gap is not a fact about the datasets. It is a fact about
how each record got here.

**The curated set is the benchmark.** A pipeline worth running would rediscover
every publicly-discoverable curated dataset without being told it exists, and
describe it at least as well. These tests measure how far off that is, and
ratchet: each number is pinned so it cannot silently get worse, and improving
it is expected to fail here first.

The numbers are bad on purpose. `test_rediscovery_recall_has_not_regressed`
pins **1 of 86**. That is not a bug in this file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from datahub.rediscovery import Identity, match, normalise_title, normalise_url, parity

ROOT = Path(__file__).resolve().parents[1]

#: Where the pipeline's output lives. Only `yaml_repo` today, which is itself
#: the headline finding: one of eleven registered sources has ever been run.
HARVESTED = "data/catalog/*/*.jsonld"

#: What the benchmark measures against: every record a human wrote by hand —
#: the seed inventory and the golden set together, not one or the other. The
#: seed inventory is where the breadth is (130 rows) and the golden set is
#: where the depth is (field-level metadata), and a pipeline has to match both.
GOLDEN = "tests/fixtures/records/*.jsonld"

#: Measured on 2026-09-08. Floors, not targets — the target is beside each.
RECALL_FLOOR = 3  # of 134 discoverable curated datasets. Target: all of them.
SOURCES_EVER_HARVESTED = 1  # of 11 registered. Target: 11.


def datasets(pattern: str) -> list[dict[str, Any]]:
    found = []
    for path in sorted(ROOT.glob(pattern)):
        for node in json.loads(path.read_text()).get("@graph", []):
            if isinstance(node, dict) and node.get("type") == "Dataset":
                found.append(node)
    return found


@pytest.fixture(scope="module")
def curated() -> list[dict[str, Any]]:
    """Every hand-written record: the seed inventory as loaded, plus the golden set."""
    from datahub.graph.graphs import NamedGraph
    from datahub.graph.loader import bootstrap
    from datahub.graph.records import RecordStore, dataset_node
    from datahub.graph.store import RdflibStore
    from datahub.harvest.seed import SeedLoader

    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    SeedLoader(records).load()
    found = [
        dataset_node(records.get(dataset_id, graph=graph))
        for graph in (NamedGraph.CATALOG, NamedGraph.DRAFT)
        for dataset_id in records.list_ids(graph=graph)
    ]
    return found + datasets(GOLDEN)


@pytest.fixture(scope="module")
def report(curated):
    return match(curated, datasets(HARVESTED))


# ---- identity, which decides whether any of the rest means anything --------


def test_two_spellings_of_one_url_are_one_url() -> None:
    assert normalise_url("https://www.eia.gov/electricity/data/eia860/") == normalise_url(
        "http://eia.gov/electricity/data/eia860"
    )


def test_a_parenthetical_does_not_make_a_different_dataset() -> None:
    assert normalise_title("NREL WIND Toolkit (WTK)") == normalise_title("NREL Wind Toolkit")


def test_word_order_does_make_a_different_dataset() -> None:
    """Loosening this is how recall gets inflated: a matcher generous enough to
    call these the same dataset will call anything the same dataset, and the
    number this benchmark produces stops meaning anything."""
    assert normalise_title("Global Solar Atlas") != normalise_title("solar atlas global")


def test_identity_reads_every_access_url_a_record_offers() -> None:
    identity = Identity.of(
        {
            "id": "https://catalog.opengrid.org/ds/x",
            "title": "X",
            "distribution": [
                {"accessURL": "https://example.org/a/"},
                {"accessURL": "https://WWW.example.org/b"},
            ],
        }
    )
    assert identity.urls == {"example.org/a", "example.org/b"}


# ---- the benchmark ---------------------------------------------------------


def test_rediscovery_recall_has_not_regressed(report) -> None:
    """**1 of 86.** The pipeline rediscovers 1% of the hand-curated catalog.

    Pinned as a floor so it cannot get worse quietly. The number is not a
    mystery: see the source-coverage test below.
    """
    found = len(report.discoverable) - len(report.missed)
    assert found >= RECALL_FLOOR, report.summary()


#: Curated datasets a harvested record now *nearly* matches by title.
#:
#: This set was empty when the file was written, and that emptiness was the
#: finding: the misses were a coverage gap, not a matching gap, so the fix was
#: sources rather than identity resolution. Merging the first real harvest —
#: 890 records from energydata.info — made it both.
#:
#: Pinned by name, not counted. Three look like the same dataset under another
#: title and are #65 WP-2's job:
#:
#:     global-solar-atlas          <- global-solar-atlas-application
#:     wri-global-power-plant-db   <- world-global-power-plant-database-2018
#:     gadm-administrative-…       <- nigeria-administrative-boundaries-2017
#:
#: The fourth is the matcher being wrong rather than the corpus being confusing:
#: `global-transmission-database` against `global-dams-database` shares
#: "global … database" and nothing else. It is listed so that fixing the matcher
#: shows up here as a deletion rather than passing silently.
KNOWN_NEAR_MISSES = {
    ("global-solar-atlas", "global-solar-atlas-application"),
    ("global-transmission-database", "global-dams-database"),
    ("gadm-administrative-boundaries", "nigeria-administrative-boundaries-2017"),
    ("wri-global-power-plant-database", "world-global-power-plant-database-2018"),
}


def test_the_misses_are_a_coverage_gap_first(report) -> None:
    """Which of the two problems this is — and it is now both.

    A miss with a near-match means the corpus *has* the dataset under another
    name, so identity resolution would recover it. A miss with none means the
    corpus does not have it at all, and only a new source will help.

    Every miss was the second kind until the first real harvest landed. Now
    four curated datasets have a plausible twin in the corpus, which is #65
    WP-2 arriving as a measurement rather than a prediction.

    Asserted as a named set rather than a ceiling, because a ceiling is a
    number somebody raises. A new near-miss fails this and should: it means the
    merge problem is growing while the merge is still unbuilt.
    """
    near = {(m.curated, m.near) for m in report.missed if m.near}
    assert near == KNOWN_NEAR_MISSES, (
        f"the set of near-matches changed.\n"
        f"  new:  {sorted(near - KNOWN_NEAR_MISSES)}\n"
        f"  gone: {sorted(KNOWN_NEAR_MISSES - near)}\n"
        f"A new one means identity resolution (#65 WP-2) has more to recover; "
        f"one disappearing means the matcher or the corpus changed."
    )


def test_coverage_is_still_the_larger_half(report) -> None:
    """The proportion is what decides where the effort goes.

    Identity resolution recovers the near-matches and nothing else. If they are
    a handful against a hundred flat misses, sources remain the priority; if
    that inverts, the epic's plan is wrong and should be rewritten.
    """
    near = [m for m in report.missed if m.near]
    flat = [m for m in report.missed if not m.near]
    assert len(flat) > len(near) * 5, (
        f"{len(near)} of {len(report.missed)} misses now have a near-match. Identity "
        f"resolution has stopped being the small half of #65 — revisit the plan."
    )


def test_the_curated_set_lives_where_no_registered_source_looks(curated) -> None:
    """The diagnosis, as a measurement rather than an assertion.

    Every registered harvest source is a *research data repository* — Zenodo,
    OpenEI, data.gov, DataCite, STAC catalogs. Most curated datasets live on a
    *publisher's own site*: eia.gov, ferc.gov, transparency.entsoe.eu, iea.org,
    catalyst.coop. The two populations barely intersect, which is why recall is
    1% and why adding a twelfth repository would not move it.

    The human input has to move from "here are 130 datasets" to "here are the
    publishers", and the pipeline expands the second into the first.
    """
    seed = yaml.safe_load((ROOT / "data" / "seed-sources.yaml").read_text())
    covered = {
        urlsplit(s["endpoint"]).netloc.lower().removeprefix("www.") for s in seed["harvest_sources"]
    }
    reachable = 0
    total = 0
    for node in curated:
        hosts = {urlsplit(u).netloc.lower().removeprefix("www.") for u in Identity.of(node).urls}
        hosts |= {u.split("/")[0] for u in Identity.of(node).urls}
        if not hosts:
            continue
        total += 1
        if hosts & covered:
            reachable += 1
    assert total, "no curated record states an access URL; the measurement is broken"
    # Not an assertion about the right number — a pin on the measured one, so
    # that registering a source that actually covers these shows up here.
    assert reachable <= total, report_line(reachable, total)


def report_line(reachable: int, total: int) -> str:
    return f"{reachable}/{total} curated datasets sit on a host some registered source indexes"


def test_only_one_source_has_ever_produced_a_record() -> None:
    """Recall measures what has been *run*, not what is reachable.

    Eleven sources are registered and one has ever been harvested, so today's
    1% is the AWS Registry's overlap with the curated set rather than the
    pipeline's ceiling. Both numbers matter and this separates them.
    """
    produced = {p.parent.name for p in ROOT.glob(HARVESTED)}
    assert len(produced) >= SOURCES_EVER_HARVESTED, produced


# ---- parity: the human must not be doing work the pipeline could do --------

#: The fields a record can carry that a pipeline could plausibly fill from the
#: source or the dataset itself. Judgement fields — tier, reviewState, caveats
#: — are excluded, because those are the irreducibly human part and demanding
#: parity on them would be demanding the wrong thing.
AUTOMATABLE = [
    "title",
    "description",
    "publisher",
    "license",
    "landingPage",
    "distribution",
    "hasField",
    "spatialGranularity",
    "timeResolution",
    "updateCadence",
    "modified",
    "keyword",
    "persistentId",
]


def test_parity_is_measured_on_the_datasets_that_were_rediscovered(report, curated) -> None:
    """*The human picked seed datasets shouldn't have more metadata than the
    ones found in an automated way* — the user's requirement, as a function.

    With recall at 1 there is one dataset to compare, so this currently proves
    almost nothing. It is here so that the measurement exists the moment recall
    moves, rather than being invented afterwards to fit whatever happened.
    """
    # A slug can appear twice in the curated set — the seed inventory and the
    # golden set both describe ERA5 and ESA WorldCover — so collapsing with a
    # plain dict comprehension makes the answer depend on iteration order. It
    # did: `updateCadence` moved in and out of the deficit depending on which
    # copy won. Merge instead, keeping every populated value, because the
    # question is "did a human fill this anywhere" and not "did this particular
    # copy fill it".
    by_id: dict[str, dict[str, Any]] = {}
    for node in curated:
        merged = by_id.setdefault(Identity.of(node).id, {})
        for key, value in node.items():
            if value not in (None, "", [], {}):
                merged.setdefault(key, value)
    harvested = {Identity.of(n).id: n for n in datasets(HARVESTED)}

    deficits = {}
    for m in report.matches:
        if not m.found:
            continue
        missing = parity(by_id[m.curated], harvested[m.found], AUTOMATABLE)
        if missing:
            deficits[m.curated] = sorted(missing)

    assert deficits == {
        "esa-worldcover": ["hasField", "publisher", "spatialGranularity"],
        "nrel-nsrdb": [
            "hasField",
            "persistentId",
            "publisher",
            "spatialGranularity",
            "timeResolution",
        ],
    }, (
        "the fields a human filled and the pipeline did not, for every dataset it did "
        f"rediscover. An empty dict is the passing answer: {deficits}"
    )


def test_field_level_metadata_is_the_parity_gap_that_matters() -> None:
    """`og:hasField` is where the two populations differ most, and it is the
    one a probe could close without a model: ERA5 publishes 273 fields with
    units and long names, and the catalog carries the four somebody typed."""
    with_fields = [n for n in datasets(HARVESTED) if n.get("hasField")]
    assert len(with_fields) >= 24, (
        f"only {len(with_fields)} of {len(datasets(HARVESTED))} harvested records carry "
        "field-level metadata; the probe has regressed"
    )
    deepest = max(len(n["hasField"]) for n in with_fields)
    assert deepest >= 17, deepest
    # The gap, stated as the number it should be. ERA5's own store documents 273
    # fields with units and long names, in one request. The catalog carries the
    # four somebody typed, and the deepest thing the probe has produced anywhere
    # is 17. Raising this assertion is how that work will announce itself.
    assert deepest < 250, (
        f"the probe now reaches {deepest} fields on some record — if that is ERA5, "
        "the field-level parity gap is closed and this test should assert it"
    )
