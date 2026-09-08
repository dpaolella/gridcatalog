"""`bulk` survives a row with no access path (#57).

`seed.py::_distribution` returns `None` for a row with no access URL, no DOI
and no secondary access — 23 reference-only rows since #18 — and
`bulkDownload` was written onto the distribution only. So a row stating
`bulk: true` with no access path had the fact dropped: it validated, it sat in
`data/seed-sources.yaml`, and nothing read it.

Eight of the eleven affected rows say `bulk: false`, where nothing is really
lost — a reference-only record offers no download at all. Three say **true**,
and each is reference-only because no access *path* could be verified rather
than because the data cannot be had in bulk. Those three are the defect.

`og:bulkDownload` is now allowed on the dataset as well as the distribution,
optional (unlike `og:anonymousAccess`, which is `sh:minCount 1` because a Tier
1 evaluator must know whether an account is needed). The projector prefers a
distribution that says yes and falls back to the dataset's own statement.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.graph.graphs import NamedGraph
from datahub.graph.loader import bootstrap
from datahub.graph.records import RecordStore
from datahub.graph.store import RdflibStore
from datahub.harvest.seed import SeedLoader
from datahub.projector.build import build_document

#: Reference-only rows the inventory says are available in bulk. Each is a
#: pointer because no access URL could be verified, not because the bytes are
#: unobtainable — which is exactly the distinction the dataset-level field
#: exists to carry.
BULK_WITHOUT_A_PATH = {
    "plexos-world-2015",
    "scigrid-german-hv-tx-network",
    "gridpath-ra-toolkit-weather-component",
}


@pytest.fixture(scope="module")
def documents() -> dict[str, object]:
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    SeedLoader(records).load()
    return {
        dataset_id.rsplit("/", 1)[-1]: build_document(
            records.get_graph(dataset_id, graph=graph), dataset_id
        )
        for graph in (NamedGraph.CATALOG, NamedGraph.DRAFT)
        for dataset_id in records.list_ids(graph=graph)
    }


def test_a_reference_only_row_still_reports_bulk_availability(documents) -> None:
    for dataset_id in sorted(BULK_WITHOUT_A_PATH):
        doc = documents[dataset_id]
        assert doc.distribution_count == 0, f"{dataset_id} is no longer reference-only"
        assert doc.bulk_download is True, (
            f"{dataset_id} states bulk availability in the inventory and the projected "
            f"document says {doc.bulk_download!r}"
        )


def test_a_row_that_states_no_bulk_says_so_rather_than_staying_silent(documents) -> None:
    """`False` and `None` are different answers and used to collapse into one.

    The old expression was `any(...) or None`, which can never return False —
    so "the publisher offers no bulk download" and "nobody checked" were the
    same value.
    """
    for dataset_id in ("4c-offshore-wind-database", "hifld", "lazard-lcoe"):
        assert documents[dataset_id].bulk_download is False, dataset_id


def test_a_distribution_that_offers_bulk_still_wins(documents) -> None:
    """The distribution is the specific claim, and it settles the question."""
    assert documents["ecmwf-era5"].bulk_download is True
    assert documents["ecmwf-era5"].distribution_count >= 1


def test_silence_is_still_silence(documents) -> None:
    """PRD §14.2. Most rows say nothing about bulk and must keep saying nothing."""
    unstated = [k for k, doc in documents.items() if doc.bulk_download is None]
    assert len(unstated) > 50, (
        "almost every row now claims a bulk-download posture, which means "
        "something is defaulting rather than reading the inventory"
    )
