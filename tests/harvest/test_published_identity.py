"""No dataset appears in the published catalog twice (#20).

The published catalog is the seed inventory plus the curated records, composed
by `.github/workflows/pages.yml`. The two disagreed about identifiers, so six
datasets were listed under two slugs each and NREL ATB under three. A duplicate
is worse than a missing record: nobody can tell which copy is authoritative, the
two carry different completeness levels and different grades, and the link
service treats one dataset as two related ones.

**This composes the catalog the way the workflow does, and that is a second
copy of a decision.** It is the cheaper of two evils — the alternative is a
check that only runs on a Pages deploy, which is where this bug reached
production from — but if the workflow's composition changes, change it here too.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.graph.graphs import NamedGraph
from datahub.graph.loader import bootstrap
from datahub.graph.records import RecordStore, dataset_node
from datahub.graph.store import RdflibStore
from datahub.harvest.seed import SeedLoader

#: Synthetic fixtures for the entitlement matrix. `pages.yml` deletes them
#: before loading, because a public catalog should list datasets that exist.
NOT_PUBLISHED = {"caiso-nodal-lmp-restricted", "utility-load-shapes-allowlisted"}


@pytest.fixture(scope="module")
def published() -> dict[str, dict]:
    """Every record a Pages build would publish, by id."""
    from fixtures.loader import load_record, record_names

    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    SeedLoader(records).load()
    for name in record_names():
        if name not in NOT_PUBLISHED:
            records.put(load_record(name))

    return {
        dataset_id.rsplit("/", 1)[-1]: dataset_node(
            records.get(dataset_id, graph=NamedGraph.CATALOG)
        )
        for dataset_id in records.list_ids(graph=NamedGraph.CATALOG)
    }


def _normalise(title: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def test_no_two_published_records_share_a_title(published) -> None:
    """`pypsa-eur-grid` and `pypsa-eur-grid-dataset-pre-built-osm-network`
    differed only in capitalisation, so search showed the same name twice."""
    by_title: dict[str, list[str]] = {}
    for dataset_id, node in published.items():
        by_title.setdefault(_normalise(node.get("title")), []).append(dataset_id)

    duplicates = {title: ids for title, ids in by_title.items() if len(ids) > 1}
    assert not duplicates, f"the same dataset is published more than once: {duplicates}"


def test_no_published_id_is_a_longer_form_of_another(published) -> None:
    """The shape the seed inventory and the curated set disagreed in: the same
    dataset under a short curated slug and a long slugified title."""
    ids = sorted(published)
    pairs = [(a, b) for a in ids for b in ids if a < b and b.startswith(f"{a}-")]
    assert not pairs, f"one dataset published under two slugs: {pairs}"


def test_no_two_published_records_share_a_persistent_id(published) -> None:
    """A DOI identifies a dataset. Two records carrying one are one dataset."""
    by_pid: dict[str, list[str]] = {}
    for dataset_id, node in published.items():
        pid = node.get("persistentId")
        if isinstance(pid, str):
            by_pid.setdefault(pid, []).append(dataset_id)

    duplicates = {pid: ids for pid, ids in by_pid.items() if len(ids) > 1}
    assert not duplicates, f"two records share a persistent identifier: {duplicates}"


def test_a_curated_record_lands_on_its_seed_row_rather_than_beside_it(published) -> None:
    """The positive form, so the checks above cannot be satisfied by publishing
    neither. A curated record enriches the seed row it describes, and the
    result carries the curated depth — level 2 or 3 — under one id."""
    from fixtures.loader import load_record, record_names

    enriched = 0
    for name in record_names():
        if name in NOT_PUBLISHED or name not in published:
            continue
        curated = load_record(name)["@graph"][0]
        if int(curated.get("completenessLevel", 1)) > 1:
            assert int(published[name].get("completenessLevel", 1)) > 1, (
                f"{name} is published at level 1, so the curated record did not land on it"
            )
            enriched += 1

    assert enriched >= 5, f"only {enriched} curated records reached the catalog"
