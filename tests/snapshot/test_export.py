"""What the static export publishes — and what it must never publish.

`datahub snapshot export` is the one place in this system that turns a catalog
into world-readable files. Everything else can be un-shared by revoking a
token; a file on GitHub Pages is on somebody's disk the moment it is fetched,
and a mistake here cannot be taken back.

So the entitlement matrix from `tests/api/test_entitlement_matrix.py` is
re-asserted at the level of the artifact, against the tree the deploy actually
uploads. The API tests prove the rules hold for a request; these prove the
exporter asked as an anonymous caller and wrote down nothing more than it got.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PUBLIC = "ecmwf-era5"
RESTRICTED = "caiso-nodal-lmp-restricted"
HIDDEN = "utility-load-shapes-allowlisted"

DETAIL = ("schema", "quality", "distributions", "links")


@pytest.fixture
def exported(loaded, tmp_path):
    """A snapshot of the fixture corpus, written the way CI writes it."""
    from datahub.snapshot import export

    directory = tmp_path / "site"
    result = export(directory)
    return result, directory


def read(directory: Path, *parts: str):
    return json.loads((directory.joinpath(*parts)).read_text())


def test_index_holds_the_anonymous_catalog(exported):
    result, directory = exported
    index = read(directory, "index.json")

    ids = {row["id"] for row in index["results"]}
    assert PUBLIC in ids
    assert index["total"] == len(index["results"]), (
        "the index is the whole catalog, not a page of it — the static site "
        "filters it in the browser and cannot fetch a second page"
    )
    assert result.datasets == len(ids)


def test_allowlisted_existence_is_absent_entirely(exported):
    """The hard cell of the matrix, at the level of the filesystem.

    Not "returns 404" — *there is no file*. A restricted-existence record that
    left a directory behind would leak through a directory listing, a sitemap,
    or a 404 that is a different length from the others.
    """
    _, directory = exported

    index = read(directory, "index.json")
    assert HIDDEN not in {row["id"] for row in index["results"]}
    assert not (directory / "datasets" / HIDDEN).exists()

    published = (directory).rglob("*")
    assert not any(HIDDEN in path.name for path in published)


def test_allowlisted_id_appears_nowhere_in_the_bytes(exported):
    """Including inside a facet count, a link target or a related-dataset edge.

    A record's *existence* is the secret. An edge pointing at it from a public
    record's connections would disclose it as surely as a listing would.
    """
    _, directory = exported

    for path in directory.rglob("*.json"):
        assert HIDDEN not in path.read_text(), f"{path} names an allow-listed record"


def test_restricted_metadata_is_a_stub_with_no_detail(exported):
    """The middle cell: the record exists and says almost nothing."""
    result, directory = exported

    record = read(directory, "datasets", RESTRICTED, "record.json")
    assert record["title"]
    assert record["data_domains"], "the domain is public — it is how the record is found"
    assert record.get("summary") in (None, ""), (
        "a summary is metadata, and this record's is not public"
    )

    for part in DETAIL:
        assert not (directory / "datasets" / RESTRICTED / f"{part}.json").exists()

    assert RESTRICTED in result.restricted, "a stub is a reportable outcome, not an error"


def test_public_record_is_complete(exported):
    """The easy cell, which is the one that proves the others mean something.

    Without it, an exporter that wrote nothing at all would pass every test
    above.
    """
    _, directory = exported

    record = read(directory, "datasets", PUBLIC, "record.json")
    assert record["id"] == PUBLIC
    for part in DETAIL:
        assert (directory / "datasets" / PUBLIC / f"{part}.json").exists(), part

    quality = read(directory, "datasets", PUBLIC, "quality.json")
    assert quality["facets"], (
        "grades are computed, not stored — an empty facet list means the graph was not read"
    )


def test_shapes_match_the_api_exactly(exported, client):
    """The snapshot is the API's own output, not a re-serialisation of it.

    This is what lets one set of React components read both. If the exporter
    ever starts reshaping — flattening a list, dropping a null, renaming a key
    for the UI's convenience — the two modes diverge and only one of them is
    tested by everything else in this repo.
    """
    _, directory = exported

    assert (
        read(directory, "datasets", PUBLIC, "record.json")
        == client.get(f"/v1/datasets/{PUBLIC}").json()
    )
    assert (
        read(directory, "datasets", PUBLIC, "schema.json")
        == client.get(f"/v1/datasets/{PUBLIC}/schema").json()
    )
    assert read(directory, "domains.json") == client.get("/v1/domains").json()


def test_facets_are_written_for_the_browser_side_filter(exported):
    _, directory = exported
    facets = read(directory, "facets.json")
    assert facets["data_domain"], "the domain facet drives the static site's filter panel"
    for buckets in facets.values():
        for bucket in buckets:
            assert set(bucket) >= {"value", "count"}


def test_rate_limiting_is_restored_after_an_export(loaded, tmp_path):
    """The exporter turns the limiter off to make ~100 requests in a loop.

    It must turn it back on. A process that exported and then served would
    otherwise serve unthrottled, and the only symptom is the absence of a 429
    that should have happened.
    """
    from datahub.config import get_settings
    from datahub.snapshot import export

    assert get_settings().rate_limit_enabled
    export(tmp_path / "site")
    assert get_settings().rate_limit_enabled


def test_export_reports_what_it_wrote(exported):
    result, directory = exported
    assert result.files > 0
    assert result.bytes_written > 0
    assert result.directory == directory
    assert result.as_dict()["datasets"] == result.datasets
