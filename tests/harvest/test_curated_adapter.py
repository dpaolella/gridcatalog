"""The curated adapter (WP-2.5).

The adapter's job is narrow — read ``data/seed-sources.yaml`` and emit it in
the shape every other adapter emits — so these tests are mostly about identity
and idempotency. A ``source_id`` that changes between runs turns re-harvest
into duplication, which is the failure that costs the most to undo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.harvest.adapters.base import RateLimiter, slugify
from datahub.harvest.adapters.curated import CuratedAdapter

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_PATH = REPO_ROOT / "data" / "seed-sources.yaml"


@pytest.fixture(scope="module")
def seed_document() -> dict:
    return yaml.safe_load(SEED_PATH.read_text())


@pytest.fixture
def adapter(settings) -> CuratedAdapter:
    return CuratedAdapter(settings)


# ---- what the file contains ---------------------------------------------


def test_emits_every_seed_row(adapter, seed_document) -> None:
    expected = sum(
        len(block.get("datasets", [])) for block in seed_document["seed_datasets"].values()
    )
    records, summary = adapter.harvest()

    assert expected == 114, "the seed inventory should hold 114 anchor datasets"
    assert len(records) == expected
    assert summary.emitted == summary.seen == expected
    assert summary.errors == []


def test_every_domain_is_represented(adapter) -> None:
    records, _ = adapter.harvest()
    domains = {r.payload["data_domain"] for r in records}
    assert domains == {f"DD{n}" for n in range(1, 11)}


def test_the_verified_split_is_carried_through_untouched(adapter) -> None:
    """56 verified / 58 unverified, per the seed file's own header.

    Asserted on the adapter as well as on the loader because the split has to
    survive transport: an adapter that dropped ``verified`` would hand the
    loader rows it could only treat as unreviewed, and 56 reviewed datasets
    would quietly disappear from the catalog.
    """
    records, _ = adapter.harvest()
    verified = [r for r in records if r.payload.get("verified") is True]
    unverified = [r for r in records if r.payload.get("verified") is False]

    assert len(verified) == 56
    assert len(unverified) == 58
    assert len(verified) + len(unverified) == len(records), "every row states `verified`"


def test_the_harvest_source_registry_is_readable(adapter) -> None:
    sources = adapter.harvest_sources()
    assert len(sources) == 11
    assert all("adapter" in s and "endpoint" in s for s in sources)


def test_domain_metadata_carries_the_structural_notes(adapter) -> None:
    domains = adapter.domains()
    assert len(domains) == 10
    assert all(d["structural_note"] for d in domains.values())
    assert sum(d["dataset_count"] for d in domains.values()) == 114


# ---- identity and idempotency -------------------------------------------


def test_source_ids_are_unique(adapter) -> None:
    records, _ = adapter.harvest()
    ids = [r.source_id for r in records]
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"a duplicate source_id makes re-harvest lossy: {duplicates}"


def test_source_ids_are_stable_across_runs(adapter, settings) -> None:
    first, _ = adapter.harvest()
    second, _ = CuratedAdapter(settings).harvest()
    assert [r.source_id for r in first] == [r.source_id for r in second]
    assert [r.content_hash for r in first] == [r.content_hash for r in second]


def test_source_id_encodes_domain_and_name(adapter) -> None:
    records, _ = adapter.harvest()
    for record in records:
        domain = record.payload["data_domain"]
        assert record.source_id == f"curated:{domain}:{slugify(record.payload['name'])}"
        assert record.source == "curated"


def test_content_hash_ignores_key_order() -> None:
    from datahub.harvest.adapters.base import HarvestedRecord

    a = HarvestedRecord("x", "curated", {"name": "A", "tier": 1})
    b = HarvestedRecord("x", "curated", {"tier": 1, "name": "A"})
    assert a.content_hash == b.content_hash


# ---- limit and checkpoint ------------------------------------------------


def test_limit_truncates(adapter) -> None:
    records, summary = adapter.harvest(limit=7)
    assert len(records) == 7
    assert summary.limit_applied == 7


def test_checkpoint_resumes_after_the_named_record(adapter) -> None:
    """A 2,100-record source at one request a second is a 35-minute run; a
    harvest that cannot resume is a harvest that never finishes."""
    everything, _ = adapter.harvest()
    marker = everything[9].source_id

    resumed, _ = adapter.harvest(checkpoint={"after": marker})

    assert [r.source_id for r in resumed] == [r.source_id for r in everything[10:]]


# ---- slugs ---------------------------------------------------------------


def test_slug_is_deterministic_and_url_safe() -> None:
    assert slugify("ECMWF ERA5") == "ecmwf-era5"
    assert slugify("  Mixed / Case & Punctuation!  ") == "mixed-case-punctuation"
    assert slugify("") == "unnamed"
    assert slugify("é" * 5) == "unnamed", "a slug that is all non-ASCII must still be usable"


def test_long_titles_sharing_a_prefix_do_not_collide() -> None:
    prefix = "A very long dataset title that goes on and on and on for ages indeed"
    a = slugify(f"{prefix} variant alpha")
    b = slugify(f"{prefix} variant beta")
    assert a != b
    assert len(a) <= 80 and len(b) <= 80
    assert a == slugify(f"{prefix} variant alpha"), "still deterministic"


# ---- politeness ----------------------------------------------------------


def test_rate_limiter_enforces_a_floor_between_calls() -> None:
    import time

    limiter = RateLimiter(per_second=50)
    start = time.monotonic()
    for _ in range(4):
        limiter.wait()
    assert time.monotonic() - start >= 0.06, "three gaps of 20ms"


def test_curated_adapter_does_not_rate_limit_itself() -> None:
    """It reads a local file. A one-second floor would make loading 114 seed
    rows a two-minute operation for no benefit to anyone."""
    adapter = CuratedAdapter()
    assert adapter.limiter.interval == 0.0


def test_adapter_opens_no_http_client_for_a_local_file(adapter) -> None:
    adapter.harvest()
    assert adapter._client is None
