"""The JSON-LD context: the record contract every other component builds on."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rdflib import Graph

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTEXT_PATH = REPO_ROOT / "schemas" / "opengrid-datahub.jsonld"

#: PRD §4 requirement ids mapped to the context terms that satisfy them.
#: Driven from a table so a missing field is a failure rather than an omission —
#: a test that only checks the terms that exist can never notice one that does
#: not.
REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "D1": ("id", "persistentId", "conceptDoi", "versionDoi"),
    "D2": ("title", "description", "summary"),
    "D3": ("dataDomain",),
    "D4": ("upstreamSource", "wasDerivedFrom"),
    "D5": ("upstreamSource", "wasDerivedFrom"),  # multi-hop is a graph property
    "D6": ("provenanceClass",),
    "D7": ("supersedes", "supersededBy", "complements"),
    "D8": ("license",),
    "D9": ("accessRestriction",),
    "D10": ("anonymousAccess",),
    "D11": ("distribution",),
    "D12": ("supportedAnalysis", "excludedAnalysis"),
    "D13": ("qualityFlags",),
    "D14": ("spatial", "bbox", "geometryTypes", "nativeCRS", "featureCount"),
    "D15": ("temporal", "updateCadence", "timeResolution"),
    "D16": ("voltageClass", "hasTopology", "hasImpedance", "spatialGranularity"),
    "D17": ("fieldSchema", "conformsTo"),
    "D18": ("documentationStatus",),
    "D19": ("hasFileGroup", "hasVariable", "hasDimension", "variableShape"),
    "D20": ("hasNodeType", "hasEdgeType", "edgeSource", "edgeTarget"),
    "D21": ("hasLayer", "layerGeometryType", "layerFeatureCount"),
    "C1": ("localName", "fieldId"),
    "C2": ("label", "definition"),
    "C3": ("dataType", "fieldGeometryType", "fieldCRS", "dimensionality"),
    "C4": ("concept",),
    "C5": ("unit",),
    "C6": ("valueBasis",),
    "C7": ("fieldSource",),
    "C8": ("derivedFromField",),
    "C9": ("codeList", "codeValue", "code"),
    "C10": ("required", "completenessCaveats"),
    "C11": ("valueRange", "minValue", "maxValue"),
    "C12": ("joinCandidate",),
    "C13": ("geoJoinKey",),
    "C14": ("sameConceptAsField",),
    "C15": ("variableShape",),
    "C16": ("hasEdgeType",),
}

#: Fields this build adds beyond the Notion spec (PRD §4.1) and the ADRs.
BUILD_ADDITIONS = (
    "completenessLevel",
    "harvestSource",
    "reviewState",
    "lastComputedAt",
    "enrichmentBasis",
    "conceptGap",
)

#: Distribution capabilities from PRD §4.2.
DISTRIBUTION_TERMS = (
    "accessURL",
    "mediaType",
    "byteSize",
    "bulkDownload",
    "supportsRangeRequests",
    "corsEnabled",
    "chunkIndexMethod",
    "subsettingProtocol",
    "credentialRequirement",
    "linkHealth",
)


@pytest.fixture(scope="module")
def context() -> dict:
    return json.loads(CONTEXT_PATH.read_text())["@context"]


@pytest.fixture(scope="module")
def terms(context: dict) -> dict:
    return {k: v for k, v in context.items() if not k.startswith(("__", "@"))}


def iri_of(context: dict, term: str) -> str:
    definition = context[term]
    return definition if isinstance(definition, str) else definition.get("@id", "")


def test_context_is_valid_json_and_declares_version(context: dict) -> None:
    assert context["@version"] == 1.1
    assert context["@protected"] is True


@pytest.mark.parametrize(("requirement", "expected"), sorted(REQUIREMENTS.items()))
def test_every_prd_requirement_has_a_term(
    terms: dict, requirement: str, expected: tuple[str, ...]
) -> None:
    missing = [t for t in expected if t not in terms]
    assert not missing, f"PRD §4 {requirement} needs terms {missing}"


@pytest.mark.parametrize("term", BUILD_ADDITIONS)
def test_build_additions_are_present(terms: dict, term: str) -> None:
    assert term in terms


@pytest.mark.parametrize("term", DISTRIBUTION_TERMS)
def test_distribution_capabilities_are_present(terms: dict, term: str) -> None:
    assert term in terms


def test_every_term_expands_to_an_absolute_iri(context: dict, terms: dict) -> None:
    prefixes = {k for k, v in context.items() if isinstance(v, str) and v.startswith("http")}
    for name, _ in terms.items():
        iri = iri_of(context, name)
        if iri in ("@id", "@type"):
            continue
        assert ":" in iri, f"{name} has no prefix: {iri!r}"
        prefix = iri.split(":", 1)[0]
        assert prefix in prefixes or iri.startswith("http"), (
            f"{name} expands with an undeclared prefix {prefix!r}"
        )


def test_multi_valued_terms_use_a_set_container(context: dict) -> None:
    """A record with one domain and a record with two must not differ in shape.
    Without @container: @set they do, and every consumer has to normalise."""
    should_be_sets = (
        "dataDomain",
        "upstreamSource",
        "wasDerivedFrom",
        "supersedes",
        "complements",
        "distribution",
        "hasField",
        "supportedAnalysis",
        "excludedAnalysis",
        "keyword",
        "creator",
        "fieldSource",
        "derivedFromField",
        "geometryTypes",
        "voltageClass",
    )
    for term in should_be_sets:
        definition = context[term]
        assert isinstance(definition, dict), f"{term} needs a container declaration"
        assert definition.get("@container") == "@set", f"{term} is not @container: @set"


def test_iri_valued_terms_are_typed_as_ids(context: dict) -> None:
    """A term expecting an IRI and typed as a plain literal produces a string
    where a link belongs, and the link is only discovered to be missing when a
    traversal returns nothing."""
    for term in (
        "dataDomain",
        "provenanceClass",
        "accessRestriction",
        "license",
        "concept",
        "unit",
        "upstreamSource",
        "supersededBy",
        "accessURL",
        "fieldSource",
        "derivedFromField",
    ):
        assert context[term]["@type"] == "@id", f"{term} is not typed @id"


def test_scalar_terms_carry_their_datatype(context: dict) -> None:
    expected = {
        "byteSize": "xsd:long",
        "anonymousAccess": "xsd:boolean",
        "supportsRangeRequests": "xsd:boolean",
        "completenessLevel": "xsd:integer",
        "consecutiveFailures": "xsd:integer",
        "lastProbedAt": "xsd:dateTime",
        "issued": "xsd:dateTime",
        "modified": "xsd:dateTime",
        "conceptConfidence": "xsd:decimal",
    }
    for term, datatype in expected.items():
        assert context[term]["@type"] == datatype, f"{term} should be {datatype}"


def test_no_composite_quality_term_exists(terms: dict) -> None:
    """ADR-0007. A composite would be the easiest thing in the world to add and
    the hardest to remove once anything sorts by it."""
    import re

    offenders = [
        t
        for t in terms
        if re.search(r"(overall|composite|total|combined).*(score|grade|quality)", t, re.I)
    ]
    assert not offenders, offenders


def test_standard_terms_are_not_reinvented_under_og(context: dict, terms: dict) -> None:
    """Where DCAT, Dublin Core or PROV has a term, use it. An og: duplicate of
    dcat:accessURL is a term nobody else's tooling understands."""
    standard = {
        "title": "dct:",
        "description": "dct:",
        "license": "dct:",
        "publisher": "dct:",
        "creator": "dct:",
        "spatial": "dct:",
        "temporal": "dct:",
        "conformsTo": "dct:",
        "issued": "dct:",
        "modified": "dct:",
        "distribution": "dcat:",
        "accessURL": "dcat:",
        "downloadURL": "dcat:",
        "mediaType": "dcat:",
        "byteSize": "dcat:",
        "keyword": "dcat:",
        "landingPage": "dcat:",
        "startDate": "dcat:",
        "endDate": "dcat:",
        "wasDerivedFrom": "prov:",
        "wasGeneratedBy": "prov:",
    }
    for term, prefix in standard.items():
        assert iri_of(context, term).startswith(prefix), (
            f"{term} should use the standard {prefix} term, not an og: duplicate"
        )


def test_a_record_round_trips_through_rdf(context: dict) -> None:
    document = {
        "@context": context,
        "id": "https://catalog.opengrid.org/ds/x",
        "type": "Dataset",
        "title": "T",
        "dataDomain": ["https://schema.opengrid.org/concept/data-domain/DD5"],
        "completenessLevel": 3,
        "anonymousAccess": True,
        "bbox": [-180.0, -90.0, 180.0, 90.0],
        "distribution": [
            {
                "type": "Distribution",
                "id": "https://catalog.opengrid.org/dist/x--1",
                "accessURL": "https://example.org/x",
                "byteSize": 4398046511104,
            }
        ],
    }
    graph = Graph()
    graph.parse(data=json.dumps(document), format="json-ld")
    assert len(graph) > 8

    recompacted = Graph()
    recompacted.parse(data=graph.serialize(format="json-ld"), format="json-ld")
    # Isomorphism, not set equality: og:bbox is an ordered rdf:List, and its
    # cells are blank nodes that get fresh labels on every parse. Comparing
    # labels would fail on a round trip that is in fact lossless.
    from rdflib.compare import isomorphic

    assert isomorphic(graph, recompacted), "the record does not survive a round trip"


def test_bbox_is_an_ordered_list(context: dict) -> None:
    """A bounding box is minLon, minLat, maxLon, maxLat in that order. An
    unordered set of four numbers is four numbers, not a box."""
    assert context["bbox"]["@container"] == "@list"


def test_single_and_multi_valued_records_produce_the_same_shape(context: dict) -> None:
    """The concrete consequence of @container: @set."""

    def triples(value: object) -> set:
        graph = Graph()
        graph.parse(
            data=json.dumps(
                {
                    "@context": context,
                    "id": "https://catalog.opengrid.org/ds/x",
                    "type": "Dataset",
                    "dataDomain": value,
                }
            ),
            format="json-ld",
        )
        return set(graph)

    one = "https://schema.opengrid.org/concept/data-domain/DD5"
    assert triples(one) == triples([one])
