"""Source-native metadata onto the OpenGrid schema (WP-3.5).

The payloads here are written from the published API schemas rather than
captured from live services — the build environment cannot reach any of the
eleven harvest sources. That is a real limitation and worth naming: a
synthesised fixture cannot catch a field a source has quietly renamed. What it
does catch is everything on this side of the boundary — the mapping, the
transforms, the level calculation, and the two rules that matter most:

* a field the source did not carry is left out, never defaulted;
* a licence that does not map becomes a LicenseRef carrying the original text.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.harvest.adapters.base import HarvestedRecord
from datahub.harvest.normalizers.engine import (
    TRANSFORMS,
    Normalizer,
    load_mapping,
    mapping_names,
    resolve,
)

SPDX = "http://spdx.org/licenses/"


def harvested(source: str, payload: dict, source_id: str = "src:1") -> HarvestedRecord:
    return HarvestedRecord(source_id=source_id, source=source, payload=payload)


CKAN = {
    "name": "nrel-wind-toolkit",
    "title": "NREL Wind Integration National Dataset (WIND) Toolkit",
    "notes": "<p>Modeled <b>wind speed</b> and power output time series at 2km resolution.</p>",
    "license_id": "CC-BY-4.0",
    "metadata_created": "2019-03-04T12:00:00Z",
    "metadata_modified": "2024-11-02T09:14:00Z",
    "version": "3.0",
    "url": "https://www.nrel.gov/grid/wind-toolkit.html",
    "tags": [{"name": "wind"}, {"name": "renewable energy"}],
    "extras": {"doi": "10.7799/1350003", "frequency": "P1Y"},
    "_public": True,
    "resources": [
        {
            "url": "https://oedi-data-lake.s3.amazonaws.com/wtk/v1/",
            "format": "HDF5",
            "mimetype": "application/x-hdf5",
            "size": "500000000000",
            "hash": "abc123",
        },
        {"url": "https://developer.nrel.gov/api/wind-toolkit/v2/", "format": "API"},
    ],
}

ZENODO = {
    "conceptrecid": "3517949",
    "doi": "10.5281/zenodo.3601881",
    "conceptdoi": "10.5281/zenodo.3517949",
    "updated": "2024-06-01T08:00:00Z",
    "metadata": {
        "title": "PyPSA-Eur: An open optimisation model of the European transmission system",
        "description": "<p>Cutouts and networks for the PyPSA-Eur workflow.</p>",
        "publication_date": "2024-05-30",
        "version": "0.10.0",
        "license": {"id": "CC-BY-4.0"},
        "keywords": ["power systems", "transmission"],
        "access_right": "open",
    },
    "links": {"self_html": "https://zenodo.org/records/3601881"},
    "files": [
        {
            "key": "networks.zip",
            "size": 8_400_000,
            "checksum": "md5:deadbeef",
            "links": {"self": "https://zenodo.org/api/files/abc/networks.zip"},
        }
    ],
}


# ---- every mapping is loadable and coherent ------------------------------


def test_there_is_a_mapping_for_every_adapter() -> None:
    """Eight adapters, eight mappings (PRD §7.1). A missing one is a source
    whose records cannot be normalised at all."""
    assert set(mapping_names()) == {
        "ckan",
        "zenodo_api",
        "datacite_api",
        "stac",
        "yaml_repo",
        "dcat_sparql",
        "oep_api",
        "cds_catalogue",
    }


@pytest.mark.parametrize("name", mapping_names())
def test_a_mapping_uses_only_known_transforms(name: str) -> None:
    """A mapping is data, not code, and its vocabulary is closed. An open one
    becomes a second programming language that only looks like configuration."""
    mapping = load_mapping(name)
    specs = list(mapping.fields.values()) + list(mapping.distributions.get("fields", {}).values())
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        transforms = spec.get("transform") or []
        for transform in transforms if isinstance(transforms, list) else [transforms]:
            assert transform in TRANSFORMS, f"{name}: unknown transform {transform!r}"


@pytest.mark.parametrize("name", mapping_names())
def test_a_mapping_targets_only_real_terms(name: str) -> None:
    """A mapping that writes a term the context does not define produces a
    record whose extra field silently vanishes on the way into RDF."""
    import json

    context = json.loads(
        (Path(__file__).parents[2] / "schemas" / "opengrid-datahub.jsonld").read_text()
    )["@context"]
    mapping = load_mapping(name)
    for term in list(mapping.fields) + list(mapping.defaults):
        assert term in context, f"{name}: {term} is not a context term"


@pytest.mark.parametrize("name", mapping_names())
def test_every_mapping_explains_itself(name: str) -> None:
    """Whoever notices that a source moved a field is a steward reading a
    harvest report, not the person who wrote the mapping."""
    assert len(load_mapping(name).notes) > 80, name


# ---- CKAN ----------------------------------------------------------------


@pytest.fixture
def ckan() -> Normalizer:
    return Normalizer("ckan", source_domains=["DD1", "DD2", "DD5"])


def test_ckan_maps_dataset_level_metadata(ckan) -> None:
    result = ckan.normalize(harvested("ckan", CKAN))
    document = result.document

    assert document["title"].startswith("NREL Wind Integration")
    assert document["license"] == f"{SPDX}CC-BY-4.0"
    assert document["modified"] == "2024-11-02T09:14:00Z"
    assert document["persistentId"] == "https://doi.org/10.7799/1350003"
    assert document["keyword"] == ["wind", "renewable energy"]
    assert document["updateCadence"] == "P1Y"


def test_html_is_stripped_from_descriptions(ckan) -> None:
    """CKAN and Zenodo both put HTML in description fields, and it renders as
    literal markup everywhere it is displayed."""
    description = ckan.normalize(harvested("ckan", CKAN)).document["description"]
    assert "<" not in description
    assert "Modeled wind speed" in description


def test_each_ckan_resource_becomes_a_distribution(ckan) -> None:
    distributions = ckan.normalize(harvested("ckan", CKAN)).document["distribution"]
    assert len(distributions) == 2
    assert distributions[0]["byteSize"] == 500_000_000_000
    assert distributions[0]["mediaType"] == "application/x-hdf5"
    assert all(d["hostedByOpenGrid"] is False for d in distributions)
    assert len({d["id"] for d in distributions}) == 2


def test_the_slug_comes_from_the_stable_name_not_the_title(ckan) -> None:
    """A source that corrects a typo in a title must not thereby create a
    second record."""
    corrected = {**CKAN, "title": "NREL Wind Integration National Dataset (WIND) Toolkit v3"}
    assert (
        ckan.normalize(harvested("ckan", CKAN)).dataset_id
        == ckan.normalize(harvested("ckan", corrected)).dataset_id
    )


# ---- Zenodo --------------------------------------------------------------


@pytest.fixture
def zenodo() -> Normalizer:
    return Normalizer("zenodo_api", source_domains=["DD1"])


def test_zenodo_keeps_concept_and_version_dois_apart(zenodo) -> None:
    """The trap PRD §4.1 D1 calls out by name. The concept DOI names the
    dataset across versions; the version DOI names one release. Conflating them
    makes every version of a dataset look like the same record — or every
    record look like a new dataset, depending which way you conflate."""
    document = zenodo.normalize(harvested("zenodo_api", ZENODO)).document

    assert document["conceptDoi"] == "https://doi.org/10.5281/zenodo.3517949"
    assert document["versionDoi"] == "https://doi.org/10.5281/zenodo.3601881"
    assert document["conceptDoi"] != document["versionDoi"]


def test_zenodo_access_right_maps_to_the_access_model(zenodo) -> None:
    open_record = zenodo.normalize(harvested("zenodo_api", ZENODO)).document
    assert open_record["anonymousAccess"] is True
    assert open_record["accessRestriction"].endswith("/none")

    embargoed = {**ZENODO, "metadata": {**ZENODO["metadata"], "access_right": "embargoed"}}
    closed = zenodo.normalize(harvested("zenodo_api", embargoed)).document
    assert closed["anonymousAccess"] is False
    assert closed["accessRestriction"].endswith("/embargoed")


def test_the_zenodo_slug_is_the_concept_record(zenodo) -> None:
    """Every version of a Zenodo deposit shares a concept record id. Slugging
    on it means version 0.11 updates the record rather than forking it."""
    later = {**ZENODO, "doi": "10.5281/zenodo.9999999"}
    assert (
        zenodo.normalize(harvested("zenodo_api", ZENODO)).dataset_id
        == zenodo.normalize(harvested("zenodo_api", later)).dataset_id
    )


# ---- absence -------------------------------------------------------------


def test_a_field_the_source_omits_is_left_out(ckan) -> None:
    """PRD principle 2: absent means "not captured", never "no source". A
    normaliser that defaulted a licence to CC-BY would produce a level 1 record
    that lies, and nothing downstream could tell."""
    sparse = {"name": "x", "title": "Transmission line ratings", "_public": True}
    result = ckan.normalize(harvested("ckan", sparse))

    assert "description" not in result.document
    assert "version" not in result.document
    assert "persistentId" not in result.document
    assert "description" in result.missing
    assert result.document["license"] == f"{SPDX}LicenseRef-Unstated"
    assert result.document["redistributionAllowed"] is False


def test_absence_is_recorded_not_merely_silent(ckan) -> None:
    """ "The source has no licence" is a fact a steward needs, and it is
    invisible if absence is silent."""
    result = ckan.normalize(harvested("ckan", {"name": "x", "title": "Grid data"}))
    assert result.missing
    assert "license" in result.missing


def test_an_unmappable_licence_keeps_its_original_text(ckan) -> None:
    payload = {**CKAN, "license_id": "ask the data owner"}
    document = ckan.normalize(harvested("ckan", payload)).document

    assert document["license"].startswith(f"{SPDX}LicenseRef-Unreviewed-")
    assert "ask the data owner" in document["licenseNote"]
    assert document["redistributionAllowed"] is False


def test_a_licence_iri_is_taken_as_given(ckan) -> None:
    """DCAT and STAC sources carry licence IRIs already."""
    payload = {**CKAN, "license_id": "https://creativecommons.org/licenses/by/4.0/"}
    document = ckan.normalize(harvested("ckan", payload)).document
    assert document["license"] == "https://creativecommons.org/licenses/by/4.0/"


def test_a_record_without_a_title_is_refused(ckan) -> None:
    """Without a title there is no slug, and without a slug there is no stable
    identity. Refusing beats minting `unnamed-3`."""
    result = ckan.normalize(harvested("ckan", {"notes": "Some grid data", "resources": []}))
    assert result.document == {}
    assert result.warnings


def test_a_ckan_slug_stands_in_for_a_missing_title(ckan) -> None:
    """CKAN's `name` is a URL slug, so it makes an ugly title — and an ugly
    title a steward can fix beats a dropped record nobody knows was dropped."""
    result = ckan.normalize(harvested("ckan", {"name": "grid-topology-2024"}))
    assert result.document["title"] == "grid-topology-2024"


# ---- what a harvested record may claim -----------------------------------


def test_a_harvested_record_is_never_confirmed(ckan) -> None:
    """A steward confirms records (PRD §7.6). Nothing in this pipeline may
    shortcut that, whatever the source says about itself."""
    payload = {**CKAN, "reviewState": "confirmed", "state": "active"}
    assert ckan.normalize(harvested("ckan", payload)).document["reviewState"] == "draft"


def test_the_completeness_level_is_computed_not_declared(ckan) -> None:
    """A declared level is a claim; this one has to be true, because the whole
    point of PRD §6 is that a user can trust the label."""
    full = ckan.normalize(harvested("ckan", CKAN))
    sparse = ckan.normalize(harvested("ckan", {"name": "x", "title": "Grid topology data"}))

    assert full.completeness_level == 1
    assert sparse.completeness_level == 1
    assert "distribution" in sparse.missing


def test_a_harvested_record_never_reaches_level_three(ckan) -> None:
    """Level 3 needs unit IRIs and concept resolution per field, which is the
    semantic layer's job (M7). A normaliser claiming it would be claiming work
    nobody has done."""
    assert ckan.level(dict.fromkeys([*ckan.LEVEL_1, *ckan.LEVEL_2], "x")) == 2


# ---- classification ------------------------------------------------------


def test_a_domain_is_inferred_and_marked_as_inferred(ckan) -> None:
    """A domain is a filing decision: getting it wrong puts a record in the
    wrong drawer, which a steward fixes in seconds. So it is inferred — and
    marked, so nobody mistakes it for a curator's judgement."""
    document = ckan.normalize(harvested("ckan", CKAN)).document

    assert document["dataDomain"] == ["https://schema.opengrid.org/concept/data-domain/DD5"]
    assert document["inferredAssignment"] is True
    assert "term-signature" in document["inferenceBasis"]


def test_a_provenance_class_is_never_guessed(ckan) -> None:
    """It caps the Provenance grade (PRD §6), so a wrong one is the catalog
    asserting something false about how the numbers came to exist. Where the
    source's words do not determine it, it is left absent and the record fails
    level 1 — which costs throughput and is the right trade."""
    stated = ckan.normalize(harvested("ckan", CKAN)).document
    assert stated["provenanceClass"].endswith("/modeled"), "the description says 'Modeled'"

    silent = {**CKAN, "notes": "Time series at 2km resolution for the continental United States."}
    result = ckan.normalize(harvested("ckan", silent))
    assert "provenanceClass" not in result.document
    assert "provenanceClass" in result.missing
    assert any("guessing one" in w for w in result.warnings)


def test_an_inferred_value_states_its_basis(ckan) -> None:
    """ADR-0005: a drafted value with no basis fails validation. 'Inferred'
    without a basis is unfalsifiable."""
    document = ckan.normalize(harvested("ckan", CKAN)).document
    assert document["inferenceBasis"]
    assert "Modeled" in document["inferenceBasis"] or "modeled" in document["inferenceBasis"]


def test_the_source_domain_list_is_a_prior_not_a_gate() -> None:
    """A source that says it carries DD1 and DD2 can still publish a DD5
    dataset. Silently refiling that one would be worse than the source's list
    being incomplete."""
    narrow = Normalizer("ckan", source_domains=["DD1"])
    payload = {**CKAN, "_public": True}
    domains = narrow.normalize(harvested("ckan", payload)).document["dataDomain"]
    assert any(d.endswith("/DD5") for d in domains)


# ---- path resolution -----------------------------------------------------


def test_dotted_paths_walk_nested_payloads() -> None:
    assert resolve({"a": {"b": {"c": 1}}}, "a.b.c") == 1
    assert resolve({"a": {"b": {}}}, "a.b.c") is None


def test_alternatives_take_the_first_that_yields_something() -> None:
    """How one mapping copes with a source that renamed a field and left the
    old one in place."""
    assert resolve({"new": "v"}, "old|new") == "v"
    assert resolve({"old": "v"}, "old|new") == "v"
    assert resolve({"old": "", "new": "v"}, "old|new") == "v"


def test_list_paths_map_over_the_list() -> None:
    payload = {"resources": [{"url": "a"}, {"url": "b"}, {}]}
    assert resolve(payload, "resources[].url") == ["a", "b"]


def test_a_path_into_a_scalar_yields_nothing() -> None:
    assert resolve({"a": "text"}, "a.b") is None


# ---- transforms ----------------------------------------------------------


def test_an_unparseable_date_is_dropped_not_guessed() -> None:
    """A wrong `modified` timestamp is worse than a missing one: the Currency
    grade is computed from it, so a bad guess becomes a confident quality
    claim."""
    assert TRANSFORMS["datetime"]("sometime in 2019") is None
    assert TRANSFORMS["datetime"]("2019-03-04") == "2019-03-04T00:00:00Z"
    assert TRANSFORMS["datetime"]("2019") == "2019-01-01T00:00:00Z"


def test_a_relative_iri_is_dropped() -> None:
    """A relative IRI resolves against whatever base is in scope, which for a
    record loaded from a file is the file's own directory — that is how
    "CC BY 4.0" once became file:///home/user/gridcatalog/."""
    assert TRANSFORMS["iri"]("CC BY 4.0") is None
    assert TRANSFORMS["iri"]("/datasets/1") is None
    assert TRANSFORMS["iri"]("https://example.org/x") == "https://example.org/x"


def test_names_handles_every_shape_a_source_uses_for_tags() -> None:
    assert TRANSFORMS["names"]([{"name": "a"}, {"display_name": "b"}, "c"]) == ["a", "b", "c"]
    assert TRANSFORMS["names"]("a, b") == ["a", "b"]
    assert TRANSFORMS["names"]([]) is None


def test_a_bad_number_yields_nothing_rather_than_zero() -> None:
    """Zero is a value. "The source said 'unknown'" is not."""
    assert TRANSFORMS["integer"]("unknown") is None
    assert TRANSFORMS["integer"]("0") == 0
    assert TRANSFORMS["number"]("") is None


# ---- the whole thing validates -------------------------------------------


def test_a_normalised_record_passes_shacl_at_level_one(ckan) -> None:
    """The end the pipeline is judged by. A record that normalises but does not
    validate is a record that cannot be published, and finding that out at the
    review queue rather than here would waste a steward's time."""
    from datahub.harvest.validate import ValidationRunner, format_report

    document = ckan.normalize(harvested("ckan", CKAN)).document
    report = ValidationRunner().validate_jsonld(document, 1)
    assert report.conforms, format_report(report)


def test_a_zenodo_record_passes_shacl_at_level_one(zenodo) -> None:
    from datahub.harvest.validate import ValidationRunner, format_report

    payload = {
        **ZENODO,
        "metadata": {
            **ZENODO["metadata"],
            "description": "Networks compiled from OpenStreetMap for the PyPSA-Eur workflow.",
        },
    }
    document = zenodo.normalize(harvested("zenodo_api", payload)).document
    report = ValidationRunner().validate_jsonld(document, 1)
    assert report.conforms, format_report(report)


def test_validation_refuses_to_fetch_a_remote_context() -> None:
    """A record is untrusted input. Following its @context would let a
    harvested payload name any URL and have the validator fetch it — and a
    context the attacker controls remaps every term, so the shapes would then
    be checking something other than what the record says."""
    from datahub.harvest.validate import ValidationRunner

    runner = ValidationRunner()
    with pytest.raises(ValueError, match="refusing to fetch"):
        runner.validate_jsonld({"@context": "https://attacker.example/ctx.jsonld", "id": "x"}, 1)
