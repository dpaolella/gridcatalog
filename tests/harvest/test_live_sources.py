"""Live smoke tests, one per source. Skipped by default.

    pytest -m network tests/harvest/test_live_sources.py

The recorded fixtures in ``tests/fixtures/harvest`` test everything on our side
of the boundary. They cannot test whether a source still returns the shape they
record, because they were written from each API's published schema rather than
captured from a live service — a field a source has quietly renamed passes every
one of them and returns nothing in production.

These are the tests that catch that, and they are the ones to run when a harvest
starts returning fewer records than it did last week. Each asks its source for a
handful of records and asserts only what the adapter genuinely depends on: that
records come back, that they carry a stable id, and that the fields the mapping
reads are present. Not the values — the values are the source's business and
change constantly.

They are deliberately not part of the default suite. A test that fails because
somebody else's server is down is a test that trains people to ignore failures.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.harvest.adapters import build
from datahub.harvest.normalizers.engine import Normalizer, resolve
from datahub.harvest.runner import harvest_sources

pytestmark = pytest.mark.network

#: The mapping paths each adapter cannot work without. A source that stops
#: returning one of these has broken the adapter, whatever else still works.
REQUIRED_PATHS: dict[str, tuple[str, ...]] = {
    "ckan": ("name", "title"),
    "zenodo_api": ("conceptrecid", "metadata.title"),
    "datacite_api": ("id", "attributes.titles"),
    "stac": ("id", "description"),
    "yaml_repo": ("Name", "Description"),
    "dcat_sparql": ("title",),
    "oep_api": ("name",),
    "cds_catalogue": ("id", "title"),
}


def sources() -> list[dict]:
    return [s for s in harvest_sources() if s["adapter"] != "curated"]


def ids() -> list[str]:
    return [s["id"] for s in sources()]


@pytest.mark.parametrize("source", sources(), ids=ids())
def test_the_source_answers(source: dict) -> None:
    """The most basic question, and the one that goes wrong most often: is the
    endpoint still where the seed file says it is?"""
    with build(source) as adapter:
        records, summary = adapter.harvest(limit=3)

    assert summary.errors == [], f"{source['id']} returned errors: {summary.errors}"
    assert records, f"{source['id']} returned no records"


@pytest.mark.parametrize("source", sources(), ids=ids())
def test_the_fields_the_mapping_reads_are_still_there(source: dict) -> None:
    """A renamed field is a silent failure: the adapter succeeds, the mapping
    finds nothing, and the record arrives empty."""
    with build(source) as adapter:
        records, _ = adapter.harvest(limit=3)
    if not records:
        pytest.skip(f"{source['id']} returned nothing; see the previous test")

    for path in REQUIRED_PATHS[adapter.name]:
        assert any(resolve(r.payload, path) for r in records), (
            f"{source['id']}: no record carries {path!r} any more"
        )


@pytest.mark.parametrize("source", sources(), ids=ids())
def test_a_live_record_normalises(source: dict) -> None:
    """The end-to-end question the fixtures cannot answer: does what this source
    actually returns today still become a record?"""
    with build(source) as adapter:
        records, _ = adapter.harvest(limit=3)
    if not records:
        pytest.skip(f"{source['id']} returned nothing; see the first test")

    normalizer = Normalizer(adapter.name, source_domains=source.get("domains"))
    documents = [normalizer.normalize(r) for r in records]

    assert any(d.document for d in documents), (
        f"{source['id']}: nothing normalised. Warnings: "
        + "; ".join(w for d in documents for w in d.warnings)
    )


@pytest.mark.parametrize("source", sources(), ids=ids())
def test_the_source_is_not_rate_limiting_us(source: dict) -> None:
    """Politeness, checked from the other side. Three requests at the
    configured rate should never draw a 429; one that does means the rate in
    `data/seed-sources.yaml` is too fast for this source."""
    with build(source) as adapter:
        _, summary = adapter.harvest(limit=3)
    assert not any("429" in error for error in summary.errors), source["id"]
