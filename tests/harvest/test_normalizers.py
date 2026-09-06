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
    normalise_licence,
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


def test_an_unrecognised_licence_iri_is_taken_as_given(ckan) -> None:
    """DCAT and STAC sources carry licence IRIs already, and an IRI this
    catalog does not recognise is still the best identifier anyone has."""
    payload = {**CKAN, "license_id": "https://example.org/terms/data-use-v3"}
    document = ckan.normalize(harvested("ckan", payload)).document
    assert document["license"] == "https://example.org/terms/data-use-v3"


def test_a_recognised_licence_iri_is_canonicalised_to_its_spdx_identifier(ckan) -> None:
    """Changed by WP-11.1, deliberately, and worth stating why.

    This used to pass the IRI through untouched. But a licence's *identity* is
    what makes it filterable, and the same licence arrives spelled a dozen
    ways: `https://creativecommons.org/licenses/by/4.0/`, `.../by/4.0`,
    `http://`-not-`https://`, `CC-BY-4.0`, "Creative Commons Attribution 4.0
    International". Passing each through verbatim splits one licence into a
    dozen facet values, and a reader filtering on CC-BY-4.0 silently misses
    most of the catalog.

    Canonicalising loses nothing — the SPDX identifier is strictly the better
    identifier — and it only fires on an unambiguous match, so the test above
    still holds for everything else.
    """
    for spelling in (
        "https://creativecommons.org/licenses/by/4.0/",
        "http://creativecommons.org/licenses/by/4.0",
        "CC-BY-4.0",
        "Creative Commons Attribution 4.0 International License",
    ):
        document = ckan.normalize(harvested("ckan", {**CKAN, "license_id": spelling})).document
        assert document["license"] == f"{SPDX}CC-BY-4.0", spelling


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
    asserting something false about how the numbers came to exist.

    The refusal to guess is unchanged by ADR-0011 and is what this test is
    for. What ADR-0011 changed is the alternative: the record now carries an
    explicit gap rather than a hole, so it can be published saying "the source
    did not say" instead of vanishing. The tests for that are further down.
    """
    stated = ckan.normalize(harvested("ckan", CKAN)).document
    assert stated["provenanceClass"].endswith("/modeled"), "the description says 'Modeled'"

    silent = {**CKAN, "notes": "Time series at 2km resolution for the continental United States."}
    result = ckan.normalize(harvested("ckan", silent))
    assert "provenanceClass" not in result.document
    assert "provenanceClass" in result.missing
    assert result.document["provenanceGap"]["gapReason"]


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


# ---- WP-11.1: cadence, licence prose, last-resort filing ------------------
#
# The three repairs that took a real 1,199-record harvest from 0 conforming
# records to a publishable catalog. See docs/ingestion-plan.md §3 and §5.


@pytest.mark.parametrize(
    ("stated", "expected"),
    [
        # Intervals, in the words sources actually use.
        ("Hourly", "PT1H"),
        ("Daily", "P1D"),
        ("Weekly", "P7D"),
        ("Monthly", "P1M"),
        ("Quarterly", "P3M"),
        ("Annually", "P1Y"),
        ("semi-annually", "P6M"),
        ("Twice daily", "PT12H"),
        # Longest-match: "semi-annually" must not be read as "annually".
        ("Semi-Annually", "P6M"),
        # Updated, but to no schedule.
        ("Varies by dataset", "irregular"),
        ("Periodically", "irregular"),
        ("New data is added as soon as it is available.", "irregular"),
        # Updated when asked. A different claim from `irregular`: an on-demand
        # dataset is not stale for not having changed.
        ("As Needed", "on-demand"),
        ("As required", "on-demand"),
        ("The dataset may be updated on a need-to-update basis.", "on-demand"),
        # Finished. Also not stale — the Currency grade must not penalise a
        # closed archive for being closed.
        ("Not updated", "discontinued"),
        ("Never", "discontinued"),
        ("Not currently being updated", "discontinued"),
        # Already valid: passed through, not round-tripped through the table.
        ("P1D", "P1D"),
        ("PT1H", "PT1H"),
        ("irregular", "irregular"),
        # DCAT-AP carries an authority IRI, not prose.
        ("http://publications.europa.eu/resource/authority/frequency/ANNUAL", "P1Y"),
        ("http://publications.europa.eu/resource/authority/frequency/IRREG", "irregular"),
        ("http://publications.europa.eu/resource/authority/frequency/NEVER", "discontinued"),
    ],
)
def test_a_stated_cadence_becomes_a_value_the_shape_accepts(stated: str, expected: str) -> None:
    assert TRANSFORMS["cadence"](stated) == expected


@pytest.mark.parametrize(
    "stated",
    ["", "N/A", "None", "TBD", "unknown", "every 3 fortnights", "see the documentation"],
)
def test_an_unreadable_cadence_is_dropped_rather_than_guessed(stated: str) -> None:
    """The whole design of the transform is in this test.

    `og:updateCadence` is absent-legal at every completeness level, and the
    Currency grade compares elapsed time against it. So a dropped cadence reads
    as "not captured", and a guessed one becomes a confident quality judgement
    about a schedule the source never stated.
    """
    assert TRANSFORMS["cadence"](stated) is None


def test_the_cadence_transform_is_what_unblocked_the_aws_registry() -> None:
    """Regression for the bug that failed 100% of a 1,199-record harvest.

    `mappings/yaml_repo.yaml` mapped the registry's free-text `UpdateFrequency`
    into a field SHACL constrains to an ISO 8601 duration. These five values
    are the registry's five most common, covering 386 of its 1,199 datasets.
    """
    registry_values = ["Varies by dataset", "As Needed", "Not updated", "Daily", "Monthly"]
    assert all(TRANSFORMS["cadence"](v) is not None for v in registry_values)


@pytest.mark.parametrize(
    ("stated", "expected"),
    [
        ("[Creative Commons BY 4.0](https://creativecommons.org/licenses/by/4.0/)", "CC-BY-4.0"),
        ("Creative Commons Attribution 4.0 International License", "CC-BY-4.0"),
        ("Creative Commons Attribution 4.0 International (CC-BY 4.0)", "CC-BY-4.0"),
        ("cc by 4.0", "CC-BY-4.0"),
        ("Creative Commons Attribution Non Commercial 4.0", "CC-BY-NC-4.0"),
        ("CC BY-SA 4.0", "CC-BY-SA-4.0"),
        ("Apache License, Version 2.0", "Apache-2.0"),
    ],
)
def test_licence_prose_resolves_to_the_identifier_it_names(stated: str, expected: str) -> None:
    assert normalise_licence(stated) == expected


def test_a_more_permissive_licence_never_wins_over_a_narrower_one() -> None:
    """Ordering is the safety property here.

    `by-nc-sa/4.0` contains `by`, so a pattern list tested in the wrong order
    resolves a non-commercial share-alike licence as plain CC-BY — which grants
    two permissions nobody gave. The specific patterns are tested first, and
    this is the test that says so.
    """
    for url, expected in (
        ("https://creativecommons.org/licenses/by-nc-sa/4.0/", "CC-BY-NC-SA-4.0"),
        ("https://creativecommons.org/licenses/by-nc/4.0/", "CC-BY-NC-4.0"),
        ("https://creativecommons.org/licenses/by-sa/4.0/", "CC-BY-SA-4.0"),
        ("https://creativecommons.org/licenses/by-nd/4.0/", "CC-BY-ND-4.0"),
        ("https://creativecommons.org/licenses/by/4.0/", "CC-BY-4.0"),
    ):
        assert normalise_licence(url) == expected, url


@pytest.mark.parametrize(
    "stated",
    [
        "Creative Commons",
        "Open Data. There are no restrictions on the use of this data.",
        "open access, no formal license stated",
        "See the data provider's terms",
        "NIH Genomic Data Sharing Policy: https://gdc.cancer.gov/access-data/data-access-policies",
    ],
)
def test_an_ambiguous_licence_string_stays_unresolved(stated: str) -> None:
    """PRD §7.4, and the reason the pattern list names a version every time.

    "Creative Commons" is not a licence. CC-BY and CC-BY-NC-SA are different
    permissions, and a reader shown the wrong one has been actively misled
    rather than merely underserved — so the string comes back untouched and
    fails exactly as it did before this resolver existed.
    """
    assert normalise_licence(stated) == stated


def test_an_unresolved_licence_quotes_the_source_not_the_normalised_form(ckan) -> None:
    """A steward resolving a LicenseRef needs to see what the record said."""
    stated = "Licensed under our standard terms, see the portal"
    result = ckan.normalize(harvested("ckan", {**CKAN, "license_id": stated}))
    assert stated in result.document["licenseNote"]
    assert any(stated in warning for warning in result.warnings)


def test_a_record_the_classifier_cannot_file_falls_back_to_the_source(ckan) -> None:
    """Last resort, and marked as the weaker claim it is.

    30% of a real harvest carried no text a domain term matched, and stayed
    below level 1 — invisible rather than mis-filed. Filing under the
    catalogue's own declaration is recoverable and visible; absence is neither.
    """
    normalizer = Normalizer("ckan", source_domains=["DD8"])
    opaque = {
        "name": "series-4417",
        "title": "Series 4417",
        "notes": "Tabular records, quarterly.",
        "license_id": "CC-BY-4.0",
        "_public": True,
        "resources": [{"url": "https://example.org/series-4417.csv", "format": "CSV"}],
    }
    result = normalizer.normalize(harvested("ckan", opaque))

    assert result.document["dataDomain"] == ["https://schema.opengrid.org/concept/data-domain/DD8"]
    assert result.document["inferredAssignment"] is True
    assert "harvest source" in result.document["inferenceBasis"]
    assert "claim about the catalogue" in result.document["inferenceBasis"]
    assert any("declared domains" in warning for warning in result.warnings)


def test_the_fallback_does_not_fire_when_the_source_declares_nothing() -> None:
    """No declaration is not a licence to invent one. The record stays below
    level 1 and goes to a steward, as it did before."""
    normalizer = Normalizer("ckan", source_domains=[])
    opaque = {
        "name": "series-4417",
        "title": "Series 4417",
        "notes": "Tabular records, quarterly.",
        "license_id": "CC-BY-4.0",
        "_public": True,
        "resources": [{"url": "https://example.org/series-4417.csv", "format": "CSV"}],
    }
    result = normalizer.normalize(harvested("ckan", opaque))
    assert "dataDomain" not in result.document
    assert "dataDomain" in result.missing


def test_the_fallback_never_overrides_the_classifier(ckan) -> None:
    """A source declaring DD1 that publishes a DD5 dataset still files DD5.
    The fallback is for silence, not for disagreement."""
    normalizer = Normalizer("ckan", source_domains=["DD1"])
    document = normalizer.normalize(harvested("ckan", CKAN)).document
    assert any(d.endswith("/DD5") for d in document["dataDomain"])
    assert not any(d.endswith("/DD1") for d in document["dataDomain"])


@pytest.mark.parametrize(
    ("stated", "expected"),
    [
        # A stated interval beats vague "finished" wording in the same sentence.
        ("Updated monthly with complete records", "P1M"),
        ("Updated daily until the final release", "P1D"),
        ("Data is complete through 2023 and updated annually", "P1Y"),
        ("Monthly, dataset is complete", "P1M"),
        # An explicit negation beats a stated interval, which is the opposite
        # precedence and the reason both directions are tested.
        ("No longer updated, previously monthly", "discontinued"),
        ("Not currently being updated", "discontinued"),
        # Unambiguous closure with no interval in sight still reads as closed.
        ("Static snapshot", "discontinued"),
        ("one-time release", "discontinued"),
        # "Complete" alone carries no claim either way.
        ("Complete", None),
        ("Final", None),
    ],
)
def test_a_stated_interval_and_a_closure_word_resolve_by_precedence(
    stated: str, expected: str | None
) -> None:
    """Both orderings, because getting either backwards mis-grades Currency.

    A first cut scanned for closure words before intervals, and read "Updated
    monthly with complete records" as a dead archive. Moving the scan wholesale
    the other way would have read "No longer updated, previously monthly" as a
    live monthly dataset. Neither is a scan order; it is two, with explicit
    negations first and vague closure last.
    """
    assert TRANSFORMS["cadence"](stated) == expected


# ---- ADR-0011: an absent provenance class is a gap marker, not a blank ----


@pytest.fixture
def mute() -> dict:
    """A CKAN payload whose text determines no provenance class."""
    return {
        "name": "series-9002",
        "title": "Regional electricity demand series",
        "notes": "Hourly demand by balancing area for the continental United States.",
        "license_id": "CC-BY-4.0",
        "_public": True,
        "resources": [{"url": "https://example.org/demand.csv", "format": "CSV"}],
    }


def test_a_silent_source_gets_a_gap_marker_not_a_hole(ckan, mute) -> None:
    """The class is still never guessed. What changed is the alternative.

    Before ADR-0011 the record simply had no provenance, which is
    indistinguishable from nobody having looked, and failed level 1. Now it
    carries an explicit gap that says the source was mute.
    """
    result = ckan.normalize(harvested("ckan", mute))

    assert "provenanceClass" not in result.document
    gap = result.document["provenanceGap"]
    assert gap["type"] == "ProvenanceGap"
    assert gap["id"].endswith("#provenance-gap")
    assert gap["gapReason"]


def test_the_gap_reason_says_why_rather_than_merely_that(ckan, mute) -> None:
    """`og:gapReason` is sh:minCount 1 for the same reason og:conceptGap's is:
    a bare gap is indistinguishable from not having looked (PRD X4)."""
    reason = ckan.normalize(harvested("ckan", mute)).document["provenanceGap"]["gapReason"]
    assert "does not state" in reason
    assert "guessed" in reason


def test_a_stated_provenance_class_still_wins_and_gets_no_gap(ckan) -> None:
    """The gap is for silence. A source whose words determine a class gets the
    class, and carrying both would be a contradiction."""
    document = ckan.normalize(harvested("ckan", CKAN)).document
    assert document["provenanceClass"].endswith("/modeled")
    assert "provenanceGap" not in document


def test_the_gap_satisfies_the_level_calculation(ckan, mute) -> None:
    """A level calculation that disagreed with the shapes would mint records
    that validate at 2 and are labelled 1."""
    complete = {
        **mute,
        "extras": {"frequency": "Hourly"},
        "metadata_modified": "2025-01-01T00:00:00Z",
    }
    document = ckan.normalize(harvested("ckan", complete)).document
    assert "provenanceGap" in document
    assert document["completenessLevel"] >= 1


def test_a_gap_is_still_recorded_as_a_missing_class(ckan, mute) -> None:
    """The gap publishes the record; it does not pretend the class is known.
    Anything counting captured fields must still see this one as absent."""
    result = ckan.normalize(harvested("ckan", mute))
    assert "provenanceClass" in result.missing
    assert any("provenance gap is recorded" in w for w in result.warnings)
