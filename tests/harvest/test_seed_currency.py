"""A seed record's vintage is the dataset's, or absent (#50).

`seed.py` used to write ``"modified": datetime.now(UTC)`` on every row — the
moment the loader ran. `dct:modified` on a `dcat:Dataset` means the *dataset*
changed, and `grade_currency` reads it that way, as the vintage it measures a
cadence against. So the field said "this data is from today" about 114 rows,
including one last released in 2015.

Nothing graded wrongly on it, but only by accident: no seed row carried an
`og:updateCadence` either, and with no cadence there is nothing to be past due
against. The two halves of #50 land together for that reason — adding cadences
to a loader that mints "now" would have turned a dormant fact into 114 records
reading Current.

The same fallback is in `normalizers/engine.py:733` for harvested records,
where 37 published records do grade A on a harvest timestamp. That is the
ingestion pipeline's to fix; this covers the seed loader.
"""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.graph.graphs import NamedGraph
from datahub.graph.loader import bootstrap
from datahub.graph.records import RecordStore, dataset_node
from datahub.graph.store import RdflibStore
from datahub.harvest.seed import SeedLoader, _cadence, _vintage
from datahub.semantic.grading.currency import grade_currency


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


def test_no_seed_record_claims_it_was_updated_today(catalog) -> None:
    """The regression, stated as the thing a reader would notice."""
    today = datetime.now(UTC).date().isoformat()
    claiming = sorted(
        dataset_id
        for dataset_id, node in catalog.items()
        if str(node.get("modified") or "").startswith(today)
    )
    assert not claiming, (
        "these records say the dataset changed today, which is the loader's run date "
        f"wearing the clothes of a vintage: {claiming}"
    )


def test_a_vintage_is_present_exactly_where_a_row_states_one(catalog) -> None:
    stated = {
        dataset_id for dataset_id, node in catalog.items() if node.get("modified") is not None
    }
    assert stated == {
        "hifld",
        "entso-e-grid-map",
        "plexos-world-2015",
        "gridkit",
        "scigrid-german-hv-tx-network",
        "wri-global-power-plant-database",
        "opsd-open-power-system-data",
    }, sorted(stated)


def test_a_row_with_no_vintage_is_not_graded_on_one(catalog) -> None:
    """Absent means not captured (PRD §14.2), never "current"."""
    node = catalog["global-transmission-database"]
    assert node.get("updateCadence") == "P1Y"
    assert node.get("modified") is None
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    SeedLoader(records).load()
    iri = f"{node['id']}"
    assessment = grade_currency(records.get_graph(iri, graph=NamedGraph.CATALOG), iri)
    assert assessment.grade is None
    assert "no vintage" in assessment.rationale or "records no modification" in (
        assessment.rationale
    ), assessment.rationale


def test_a_bare_year_reads_as_january(catalog) -> None:
    """The earliest moment consistent with "2016", not the middle of it.

    Rounding to mid-year would make a dataset look up to six months fresher
    than the evidence supports, and this feeds a staleness grade.
    """
    assert _vintage("2016") == "2016-01-01T00:00:00Z"
    assert _vintage("2024-11") == "2024-11-01T00:00:00Z"
    assert _vintage("2025-09-30") == "2025-09-30T00:00:00Z"


def test_an_unparseable_vintage_is_dropped_rather_than_guessed() -> None:
    assert _vintage("sometime in 2019") is None
    assert _vintage("") is None
    assert _vintage(None) is None


def test_prose_that_is_not_an_interval_yields_no_cadence() -> None:
    """The workbook is full of these and none of them is a duration."""
    for stated in ("NA", "?", "-", "Sub-annual", "Real time", "NA. Covered date range varies"):
        assert _cadence(stated) is None, stated


def test_the_duration_a_row_states_survives_verbatim() -> None:
    """`P1M` is a month and `PT1M` a minute, so case is not folded here."""
    for stated in ("P1Y", "P6M", "P1D", "PT15M", "PT1M"):
        assert _cadence(stated) == stated


def test_every_cadence_the_loader_emits_satisfies_the_shape(catalog) -> None:
    """`og:updateCadence`'s `sh:pattern`, applied to what actually comes out.

    A cadence that fails the shape does not reach the store, so without this a
    typo in `CADENCE_MAP` would show up as a record quietly going missing.
    """
    pattern = re.compile(
        r"^(P(\d+Y)?(\d+M)?(\d+W)?(\d+D)?(T(\d+H)?(\d+M)?(\d+S)?)?"
        r"|irregular|on-demand|discontinued)$"
    )
    emitted = {str(node["updateCadence"]) for node in catalog.values() if node.get("updateCadence")}
    assert emitted, "no seed row states a cadence; this test has stopped covering anything"
    assert all(pattern.match(cadence) for cadence in emitted), sorted(emitted)
