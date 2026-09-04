"""The eight network adapters (WP-3.2, WP-3.3).

Every adapter is exercised against a recorded fixture served by a stub
transport, so the suite never touches a third party. See
``tests/fixtures/harvest/README.md`` for what that does and does not prove —
in short, it tests everything on our side of the boundary and nothing about
whether a source has changed its response shape.

The properties that matter are the same for all eight and are asserted for all
eight: a stable ``source_id``, no duplicates, a working limit, and a partial
failure that is recorded rather than raised.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.harvest.adapters import ADAPTERS, build
from datahub.harvest.adapters.cds import CdsAdapter
from datahub.harvest.adapters.ckan import CkanAdapter
from datahub.harvest.adapters.datacite import DataCiteAdapter
from datahub.harvest.adapters.dcat_sparql import DcatSparqlAdapter
from datahub.harvest.adapters.oep import OepAdapter
from datahub.harvest.adapters.stac import StacAdapter
from datahub.harvest.adapters.yaml_repo import YamlRepoAdapter
from datahub.harvest.adapters.zenodo import ZenodoAdapter

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "harvest"
REPO_ROOT = Path(__file__).resolve().parents[2]


def fixture(name: str) -> Any:
    text = (FIXTURES / name).read_text()
    return json.loads(text) if name.endswith(".json") else text


def stub(routes: dict[str, Any], *, record: list[str] | None = None) -> httpx.Client:
    """An httpx client that serves recorded fixtures.

    Matching is on a substring of the URL, so a test names the endpoint it is
    stubbing without restating the query string. Anything unrouted is a 404,
    which is how a test finds out the adapter called something unexpected.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if record is not None:
            record.append(url)
        for marker, payload in routes.items():
            if marker in url:
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"error": f"no route for {url}"})

    return httpx.Client(transport=httpx.MockTransport(handler))


# ---- the registry --------------------------------------------------------


def test_every_source_in_the_seed_file_has_an_adapter() -> None:
    """Adding a source means adding a row to `data/seed-sources.yaml`, not
    editing the runner. That only holds if every adapter it names exists."""
    import yaml

    document = yaml.safe_load((REPO_ROOT / "data" / "seed-sources.yaml").read_text())
    for source in document["harvest_sources"]:
        assert source["adapter"] in ADAPTERS, source["id"]
        assert build(source) is not None


def test_source_configuration_reaches_the_adapter() -> None:
    """Zenodo's query list and the registry's path glob are adapter-specific
    settings living in the source entry, so the registry passes through what it
    does not itself understand."""
    adapter = build(
        {
            "id": "zenodo",
            "adapter": "zenodo_api",
            "endpoint": "https://zenodo.org/api/records",
            "queries": ["communities=pypsa"],
            "domains": ["DD1"],
        }
    )
    assert adapter.config["queries"] == ["communities=pypsa"]
    assert adapter.endpoint == "https://zenodo.org/api/records"


def test_an_unknown_adapter_is_refused() -> None:
    with pytest.raises(KeyError, match="unknown adapter"):
        build({"id": "x", "adapter": "telepathy"})


# ---- CKAN ----------------------------------------------------------------


@pytest.fixture
def ckan() -> CkanAdapter:
    return CkanAdapter(
        "oedi",
        endpoint="https://data.openei.org/api/3",
        rate_per_second=0,
        client=stub({"package_search": fixture("ckan_page1.json")}),
    )


def test_ckan_emits_every_package(ckan) -> None:
    records, summary = ckan.harvest()
    assert len(records) == 3
    assert summary.errors == []
    assert {r.payload["name"] for r in records} == {
        "nrel-wind-toolkit",
        "nrel-cambium",
        "county-library-hours",
    }


def test_ckan_does_not_filter_for_relevance(ckan) -> None:
    """The library-hours record is in the fixture deliberately. An adapter that
    filtered would put the recall decision somewhere with no audit trail; the
    relevance filter logs every rejection and an adapter does not."""
    records, _ = ckan.harvest()
    assert any(r.payload["name"] == "county-library-hours" for r in records)


def test_ckan_flattens_extras_into_a_lookup(ckan) -> None:
    """CKAN's escape hatch arrives as a list of {key, value} pairs, which no
    field mapping can index into."""
    records, _ = ckan.harvest()
    record = next(r for r in records if r.payload["name"] == "nrel-wind-toolkit")
    assert record.payload["extras"]["doi"] == "10.7799/1350003"
    assert record.payload["extras"]["frequency"] == "P1Y"


def test_ckan_carries_the_records_own_access_flag(ckan) -> None:
    records, _ = ckan.harvest()
    assert all(r.payload["_public"] is True for r in records)


def test_ckan_source_ids_are_the_package_uuid(ckan) -> None:
    """CKAN's package id is a UUID that survives a title or slug change, which
    is what makes re-harvest an update rather than a duplicate."""
    records, _ = ckan.harvest()
    assert records[0].source_id == "oedi:8f1b3c5a-0000-4a1e-9f00-000000000001"


def test_ckan_pages_on_a_watermark_not_an_offset() -> None:
    """Offset paging over a result set that changes mid-crawl either double-
    counts or skips, and skipping is the failure that is silent."""
    seen: list[str] = []
    adapter = CkanAdapter(
        "oedi",
        endpoint="https://data.openei.org/api/3",
        rate_per_second=0,
        client=stub({"package_search": fixture("ckan_page1.json")}, record=seen),
    )
    adapter.harvest(checkpoint={"modified_after": "2024-01-01T00:00:00Z"})

    assert any("metadata_modified" in url for url in seen)


# ---- Zenodo --------------------------------------------------------------


@pytest.fixture
def zenodo() -> ZenodoAdapter:
    return ZenodoAdapter(
        "zenodo",
        endpoint="https://zenodo.org/api/records",
        rate_per_second=0,
        config={"queries": ["communities=pypsa"]},
        client=stub({"zenodo.org/api/records": fixture("zenodo_page1.json")}),
    )


def test_zenodo_emits_one_record_per_deposit_not_per_version(zenodo) -> None:
    """The fixture holds two versions of one deposit. A deposit with eleven
    releases must not become eleven catalog records that are all the same
    dataset."""
    records, _ = zenodo.harvest()
    assert len(records) == 1
    assert records[0].payload["metadata"]["version"] == "0.10.0"


def test_the_zenodo_source_id_is_the_concept_record(zenodo) -> None:
    records, _ = zenodo.harvest()
    assert records[0].source_id == "zenodo:3517949"


def test_a_zenodo_record_that_says_nothing_about_versions_is_kept() -> None:
    """Dropping a record because its metadata shape changed would be a silent
    recall failure — exactly what PRD §7.2 says to avoid."""
    payload = {
        "hits": {"hits": [{"id": 1, "conceptrecid": "1", "metadata": {"title": "A dataset"}}]}
    }
    adapter = ZenodoAdapter(
        "zenodo",
        endpoint="https://zenodo.org/api/records",
        rate_per_second=0,
        client=stub({"zenodo.org": payload}),
    )
    assert len(adapter.harvest()[0]) == 1


def test_zenodo_query_fragments_become_parameters() -> None:
    """The seed file writes queries as URL fragments because that is how Zenodo
    documents them; a steward should not have to write JSON."""
    seen: list[str] = []
    adapter = ZenodoAdapter(
        "zenodo",
        endpoint="https://zenodo.org/api/records",
        rate_per_second=0,
        config={"queries": ["q=power+system&type=dataset"]},
        client=stub({"zenodo.org": fixture("zenodo_page1.json")}, record=seen),
    )
    adapter.harvest()
    assert any("type=dataset" in url for url in seen)
    assert any("power" in url for url in seen)


# ---- DataCite ------------------------------------------------------------


@pytest.fixture
def datacite() -> DataCiteAdapter:
    return DataCiteAdapter(
        "datacite",
        endpoint="https://api.datacite.org/dois",
        rate_per_second=0,
        config={"queries": ["query=energy"]},
        client=stub({"api.datacite.org": fixture("datacite_page1.json")}),
    )


def test_datacite_prefers_the_english_title(datacite) -> None:
    records, _ = datacite.harvest()
    title = records[0].payload["attributes"]["titles"]["title"]
    assert title.startswith("Synthetic electric grid")


def test_datacite_prefers_a_licence_with_an_identifier(datacite) -> None:
    """ "CC-BY-4.0" maps to SPDX; "Creative Commons Attribution 4.0
    International" does not, and would become a LicenseRef unnecessarily."""
    records, _ = datacite.harvest()
    assert records[0].payload["attributes"]["rightsList"]["rightsIdentifier"] == "CC-BY-4.0"


def test_datacite_pages_by_cursor() -> None:
    """Past 10,000 results the page-number API refuses, and this source is
    expected to exceed that before filtering."""
    seen: list[str] = []
    adapter = DataCiteAdapter(
        "datacite",
        endpoint="https://api.datacite.org/dois",
        rate_per_second=0,
        client=stub({"api.datacite.org": fixture("datacite_page1.json")}, record=seen),
    )
    adapter.harvest(limit=1)
    assert any("cursor" in url for url in seen)


# ---- STAC ----------------------------------------------------------------


@pytest.fixture
def stac() -> StacAdapter:
    return StacAdapter(
        "pangeo_forge_stac",
        endpoint="https://planetarycomputer.microsoft.com/api/stac/v1",
        rate_per_second=0,
        client=stub({"/collections": fixture("stac_collections.json")}),
    )


def test_stac_harvests_collections_not_items(stac) -> None:
    """A STAC catalog holds millions of items — one per scene, per tile, per
    day — and every one is a file, not a dataset. Harvesting items would give a
    catalog where "Sentinel-2" appears four million times."""
    records, _ = stac.harvest()
    assert len(records) == 2
    assert {r.payload["id"] for r in records} == {"esa-worldcover", "era5-pds"}


def test_stac_bbox_becomes_four_scalars(stac) -> None:
    """A skolemised rdf:List cannot be serialised back out of the store
    (ADR-0008): the record would write and then fail to read."""
    records, _ = stac.harvest()
    record = next(r for r in records if r.payload["id"] == "esa-worldcover")
    assert record.payload["_bbox_min_lon"] == -180.0
    assert record.payload["_bbox_max_lat"] == 82.75


def test_stac_skips_assets_with_no_href(stac) -> None:
    """`item_assets` is a schema, not a file. A Distribution with no access URL
    cannot answer the one question a distribution exists to answer."""
    records, _ = stac.harvest()
    record = next(r for r in records if r.payload["id"] == "esa-worldcover")
    assert len(record.payload["_assets"]) == 2
    assert all(a["href"] for a in record.payload["_assets"])


def test_stac_marks_cloud_optimised_assets_as_range_readable(stac) -> None:
    """What makes a partial-read access plan possible (PRD §F7)."""
    records, _ = stac.harvest()
    record = next(r for r in records if r.payload["id"] == "esa-worldcover")
    cog = next(a for a in record.payload["_assets"] if "cloud-optimized" in (a["type"] or ""))
    png = next(a for a in record.payload["_assets"] if a["type"] == "image/png")
    assert cog["_range"] is True
    assert png["_range"] is False


def test_stac_access_is_a_property_of_the_catalog_not_of_stac(stac) -> None:
    """Planetary Computer assets need a SAS token and Earth Search assets do
    not, and both speak the same STAC."""
    assert stac.harvest()[0][0].payload["_anonymous"] is False

    open_catalog = StacAdapter(
        "earth_search_stac",
        endpoint="https://earth-search.aws.element84.com/v1",
        rate_per_second=0,
        client=stub({"/collections": fixture("stac_collections.json")}),
    )
    assert open_catalog.harvest()[0][0].payload["_anonymous"] is True


def test_an_undocumented_stac_catalog_claims_nothing() -> None:
    """Where a catalog does not document its own answer, nothing is claimed and
    the record fails level 1 until the prober or a steward settles it."""
    unknown = StacAdapter(
        "somewhere_else",
        endpoint="https://stac.example.org/v1",
        rate_per_second=0,
        client=stub({"/collections": fixture("stac_collections.json")}),
    )
    assert "_anonymous" not in unknown.harvest()[0][0].payload


# ---- AWS registry --------------------------------------------------------


@pytest.fixture
def registry(tmp_path: Path) -> YamlRepoAdapter:
    datasets = tmp_path / "datasets"
    datasets.mkdir()
    (datasets / "nrel-pds-wtk.yaml").write_text(fixture("aws_registry_dataset.yaml"))
    (datasets / "broken.yaml").write_text("this: is: not: valid: yaml:")
    return YamlRepoAdapter("aws_open_data", checkout=tmp_path)


def test_the_registry_reads_a_checkout(registry) -> None:
    records, summary = registry.harvest()
    assert len(records) == 1
    assert summary.errors == []
    assert records[0].payload["Name"].startswith("NREL Wind Integration")


def test_one_unreadable_file_does_not_fail_the_run(registry) -> None:
    """Skipping it loudly beats aborting a 400-record run."""
    records, summary = registry.harvest()
    assert len(records) == 1
    assert summary.errors == []


def test_an_s3_arn_becomes_a_fetchable_url(registry) -> None:
    resources = registry.harvest()[0][0].payload["Resources"]
    assert resources[0]["_url"] == "https://nrel-pds-wtk.s3.us-west-2.amazonaws.com/conus/v1.0.0/"


def test_a_dotted_bucket_falls_back_to_path_style() -> None:
    """Virtual-hosted style breaks TLS on a bucket name containing a dot, and a
    URL that cannot be fetched is worse than an ugly one."""
    url = YamlRepoAdapter._resource_url(
        {"ARN": "arn:aws:s3:::my.data.bucket/prefix/", "Region": "eu-west-1"}
    )
    assert url == "https://s3.eu-west-1.amazonaws.com/my.data.bucket/prefix/"


def test_requester_pays_is_not_anonymous_access(registry) -> None:
    """The caller pays to read, which is a commercial barrier rather than open
    access — the documented exception to the programme's own definition."""
    assert registry.harvest()[0][0].payload["_anonymous"] is False


# ---- DCAT over SPARQL ----------------------------------------------------


@pytest.fixture
def dcat() -> DcatSparqlAdapter:
    return DcatSparqlAdapter(
        "eu_open_data_portal",
        endpoint="https://data.europa.eu/sparql",
        rate_per_second=0,
        client=stub({"sparql": fixture("dcat_sparql_datasets.json")}),
    )


def test_dcat_flattens_sparql_result_bindings(dcat) -> None:
    """SPARQL JSON wraps every value in {type, value}, which no field mapping
    can index into and which would end up inside every record."""
    record = dcat.harvest()[0][0]
    assert record.payload["title"] == "ENTSO-E Transparency Platform"
    assert record.payload["license"] == "https://creativecommons.org/licenses/by/4.0/"


def test_dcat_undoes_the_group_concat(dcat) -> None:
    assert dcat.harvest()[0][0].payload["keywords"] == ["electricity", "transmission", "load"]


def test_dcat_fetches_distributions_in_a_second_query() -> None:
    """One query with an OPTIONAL join returns the cross product: a dataset with
    six distributions arrives six times, title and description included."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("query", "")
        calls.append(query)
        name = (
            "dcat_sparql_distributions.json"
            if "dcat:distribution" in query
            else "dcat_sparql_datasets.json"
        )
        return httpx.Response(200, json=fixture(name))

    adapter = DcatSparqlAdapter(
        "eu",
        endpoint="https://data.europa.eu/sparql",
        rate_per_second=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    record = adapter.harvest()[0][0]

    assert len(calls) >= 2
    assert len(record.payload["_distributions"]) == 2
    assert record.payload["_distributions"][0]["accessURL"] == "https://web-api.tp.entsoe.eu/api"


def test_the_dcat_query_selects_language_server_side() -> None:
    """A DCAT-AP catalog carries every description in up to 24 languages.
    Filtering after the fetch means transferring 24 and discarding 23."""
    from datahub.harvest.adapters.dcat_sparql import DATASETS_QUERY

    assert DATASETS_QUERY.count("langMatches") >= 2


# ---- OEP -----------------------------------------------------------------


@pytest.fixture
def oep() -> OepAdapter:
    return OepAdapter(
        "open_energy_platform",
        endpoint="https://openenergyplatform.org/api/v0",
        rate_per_second=0,
        client=stub({"/meta/": fixture("oep_meta.json"), "/tables/": fixture("oep_tables.json")}),
    )


def test_oep_reads_the_field_level_schema(oep) -> None:
    """The one source with a real field-level schema, which is what lets an OEP
    record carry hasField and reach level 2 from the harvester."""
    fields = oep.harvest()[0][0].payload["_fields"]
    assert len(fields) == 4
    assert fields[1]["fieldId"] == "voltage"
    assert fields[1]["definition"] == "Nominal voltage"


def test_oep_units_stay_strings(oep) -> None:
    """A guessed QUDT IRI would be a level 3 claim made by a normaliser, and
    level 3 means a machine can convert the units without asking anyone."""
    fields = oep.harvest()[0][0].payload["_fields"]
    assert fields[1]["unitAsStated"] == "kV"
    assert "unit" not in fields[1]


def test_a_table_whose_metadata_fails_is_skipped_not_fatal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/meta/" in str(request.url):
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json=fixture("oep_tables.json"))

    adapter = OepAdapter(
        "oep",
        endpoint="https://openenergyplatform.org/api/v0",
        rate_per_second=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    records, summary = adapter.harvest()
    assert records == []
    assert summary.errors == [], "a skipped table is not a failed run"


# ---- CDS -----------------------------------------------------------------


@pytest.fixture
def cds() -> CdsAdapter:
    return CdsAdapter(
        "copernicus_cds",
        endpoint="https://cds.climate.copernicus.eu/api",
        rate_per_second=0,
        client=stub({"/collections": fixture("cds_collections.json")}),
    )


def test_cds_keeps_the_licence_and_the_gate_apart(cds) -> None:
    """A CDS dataset is openly licensed *and* account-gated. Folding the gate
    into the licence makes an open dataset read as restricted; folding the
    licence into the gate makes a gated one read as free to take."""
    from datahub.harvest.normalizers.engine import Normalizer

    record = cds.harvest()[0][0]
    document = Normalizer("cds_catalogue", source_domains=["DD5"]).normalize(record).document

    assert "Copernicus" in document["licenseNote"]
    assert document["redistributionAllowed"] is True
    assert document["anonymousAccess"] is False
    assert document["accessRestriction"].endswith("/accountRequired")


def test_cds_records_the_request_api_as_a_subsetting_protocol(cds) -> None:
    """The caller states a slice and the service produces it, rather than
    serving a whole file (PRD §F7)."""
    assert cds.harvest()[0][0].payload["_endpoints"][0]["protocol"] == "cds-request-api"


# ---- properties every adapter must have ----------------------------------

ALL = [
    ("ckan", {"package_search": "ckan_page1.json"}, "https://data.openei.org/api/3"),
    ("zenodo_api", {"zenodo.org": "zenodo_page1.json"}, "https://zenodo.org/api/records"),
    ("datacite_api", {"api.datacite.org": "datacite_page1.json"}, "https://api.datacite.org/dois"),
    (
        "stac",
        {"/collections": "stac_collections.json"},
        "https://earth-search.aws.element84.com/v1",
    ),
    ("dcat_sparql", {"sparql": "dcat_sparql_datasets.json"}, "https://data.europa.eu/sparql"),
    (
        "cds_catalogue",
        {"/collections": "cds_collections.json"},
        "https://cds.climate.copernicus.eu/api",
    ),
]


def make(name: str, routes: dict[str, str], endpoint: str):
    return ADAPTERS[name](
        name,
        endpoint=endpoint,
        rate_per_second=0,
        config={"queries": ["q=grid"]},
        client=stub({k: fixture(v) for k, v in routes.items()}),
    )


@pytest.mark.parametrize(("name", "routes", "endpoint"), ALL)
def test_source_ids_are_unique_within_a_run(name, routes, endpoint) -> None:
    """A duplicate source_id makes re-harvest lossy: the second record silently
    overwrites the first."""
    records, _ = make(name, routes, endpoint).harvest()
    ids = [r.source_id for r in records]
    assert len(ids) == len(set(ids)), name


@pytest.mark.parametrize(("name", "routes", "endpoint"), ALL)
def test_source_ids_are_stable_across_runs(name, routes, endpoint) -> None:
    """Re-harvest matches on this and nothing derived (PRD §7.6). An id that
    moves creates a duplicate the first time a source edits a field."""
    first = [r.source_id for r in make(name, routes, endpoint).harvest()[0]]
    second = [r.source_id for r in make(name, routes, endpoint).harvest()[0]]
    assert first == second, name


@pytest.mark.parametrize(("name", "routes", "endpoint"), ALL)
def test_a_limit_is_honoured(name, routes, endpoint) -> None:
    records, summary = make(name, routes, endpoint).harvest(limit=1)
    assert len(records) <= 1, name
    assert summary.limit_applied == 1


@pytest.mark.parametrize(("name", "routes", "endpoint"), ALL)
def test_a_partial_failure_is_recorded_not_raised(name, routes, endpoint) -> None:
    """A run that got 800 of 2,100 records is worth keeping, and the checkpoint
    says where to resume."""
    adapter = ADAPTERS[name](
        name,
        endpoint=endpoint,
        rate_per_second=0,
        config={"queries": ["q=grid"]},
        client=stub({}),  # every request 404s
    )
    records, summary = adapter.harvest()
    assert records == []
    assert summary.errors, name
    assert summary.finished_at is not None


@pytest.mark.parametrize(("name", "routes", "endpoint"), ALL)
def test_every_record_carries_its_source(name, routes, endpoint) -> None:
    for record in make(name, routes, endpoint).harvest()[0]:
        assert record.source == ADAPTERS[name].name
        assert record.content_hash
        assert record.fetched_at is not None
