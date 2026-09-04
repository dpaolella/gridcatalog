"""The five concept schemes, asserted rather than trusted.

These tests exist because vocabulary here carries product behaviour: a domain's
structural note is rendered on its page, a provenance class caps a quality
grade, and a `skos:broader` edge changes what every concept query returns.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from rdflib import Graph, URIRef
from rdflib.namespace import DCTERMS, OWL, SKOS

from datahub.namespaces import OG

REPO_ROOT = Path(__file__).resolve().parents[2]
VOCAB = REPO_ROOT / "vocab"

SCHEME_FILES = {
    "data-domain": "og-data-domain.ttl",
    "provenance-class": "og-provenance-class.ttl",
    "access-restriction": "og-access-restriction.ttl",
    "analysis-type": "og-analysis-type.ttl",
    "grid-concept": "og-grid-concept.ttl",
}


@pytest.fixture(scope="module")
def graphs() -> dict[str, Graph]:
    out: dict[str, Graph] = {}
    for name, filename in SCHEME_FILES.items():
        g = Graph()
        g.parse((VOCAB / filename).as_posix(), format="turtle")
        out[name] = g
    return out


@pytest.fixture(scope="module")
def merged(graphs: dict[str, Graph]) -> Graph:
    g = Graph()
    for part in graphs.values():
        g += part
    return g


@pytest.mark.parametrize("name", sorted(SCHEME_FILES))
def test_scheme_parses_and_is_declared(graphs: dict[str, Graph], name: str) -> None:
    g = graphs[name]
    scheme = URIRef(f"https://schema.opengrid.org/concept/{name}")
    assert (scheme, None, SKOS.ConceptScheme) in g, f"{name}: no ConceptScheme node"
    assert g.value(scheme, DCTERMS.title), f"{name}: scheme has no title"
    assert g.value(scheme, OWL.versionInfo), f"{name}: scheme is unversioned"
    assert list(g.objects(scheme, SKOS.hasTopConcept)), f"{name}: no top concepts"


@pytest.mark.parametrize("name", sorted(SCHEME_FILES))
def test_every_concept_has_label_and_definition(graphs: dict[str, Graph], name: str) -> None:
    g = graphs[name]
    for concept in g.subjects(None, SKOS.Concept):
        assert g.value(concept, SKOS.prefLabel), f"{concept} has no prefLabel"
        assert g.value(concept, SKOS.definition), f"{concept} has no definition"


@pytest.mark.parametrize("name", sorted(SCHEME_FILES))
def test_every_concept_is_in_its_own_scheme(graphs: dict[str, Graph], name: str) -> None:
    g = graphs[name]
    scheme = URIRef(f"https://schema.opengrid.org/concept/{name}")
    for concept in g.subjects(None, SKOS.Concept):
        assert (concept, SKOS.inScheme, scheme) in g, f"{concept} is not inScheme {name}"


@pytest.mark.parametrize("name", sorted(SCHEME_FILES))
def test_every_concept_reaches_a_top_concept(graphs: dict[str, Graph], name: str) -> None:
    """An orphan concept is invisible to a hierarchy query, which is how a
    concept ends up in the vocabulary and out of the product."""
    g = graphs[name]
    tops = set(g.objects(None, SKOS.hasTopConcept))
    for concept in g.subjects(None, SKOS.Concept):
        seen: set[URIRef] = set()
        node = concept
        while node not in tops:
            if node in seen:
                pytest.fail(f"skos:broader cycle reaching {concept}")
            seen.add(node)
            parents = list(g.objects(node, SKOS.broader))
            assert parents, f"{concept} does not reach a top concept (stuck at {node})"
            node = parents[0]


@pytest.mark.parametrize("name", sorted(SCHEME_FILES))
def test_no_broader_cycles(graphs: dict[str, Graph], name: str) -> None:
    g = graphs[name]
    for concept in g.subjects(None, SKOS.Concept):
        seen: set[URIRef] = {concept}
        frontier = list(g.objects(concept, SKOS.broader))
        while frontier:
            node = frontier.pop()
            assert node != concept, f"skos:broader cycle at {concept}"
            if node in seen:
                continue
            seen.add(node)
            frontier.extend(g.objects(node, SKOS.broader))


def test_notations_are_unique_within_a_scheme(graphs: dict[str, Graph]) -> None:
    for name, g in graphs.items():
        seen: dict[str, URIRef] = {}
        for concept, notation in g.subject_objects(SKOS.notation):
            key = str(notation)
            assert key not in seen, f"{name}: notation {key!r} on both {seen[key]} and {concept}"
            seen[key] = concept


# ---- data domains -------------------------------------------------------


def test_ten_data_domains_with_prd_notations(graphs: dict[str, Graph]) -> None:
    g = graphs["data-domain"]
    notations = {str(n) for n in g.objects(None, SKOS.notation)}
    assert notations == {f"DD{i}" for i in range(1, 11)}


def test_structural_notes_match_the_seed_file_exactly(graphs: dict[str, Graph]) -> None:
    """PRD §5 makes these notes a product feature. They are rendered on the
    domain page, so a drift between the vocabulary and the seed inventory would
    show a user one thing while the curation record says another."""
    seed = yaml.safe_load((REPO_ROOT / "data" / "seed-sources.yaml").read_text())
    g = graphs["data-domain"]
    checked = 0
    for domain, block in seed["seed_datasets"].items():
        expected = block.get("structural_note")
        if expected is None:
            continue
        concept = URIRef(f"https://schema.opengrid.org/concept/data-domain/{domain}")
        actual = g.value(concept, OG.structuralNote)
        assert actual is not None, f"{domain} has no og:structuralNote"
        assert str(actual) == expected, f"{domain} structural note differs from the seed file"
        checked += 1
    assert checked == 10


def test_v1_ingestion_scope_matches_prd_table(graphs: dict[str, Graph]) -> None:
    """PRD §5: DD1, DD5, DD8 and DD9 get Tier 2 ETL in v1; the rest are
    harvest-only. All ten are catalogued."""
    g = graphs["data-domain"]
    etl = {
        str(g.value(c, SKOS.notation))
        for c in g.subjects(OG.v1IngestionScope, None)
        if str(g.value(c, OG.v1IngestionScope)) == "etl"
    }
    assert etl == {"DD1", "DD5", "DD8", "DD9"}
    for concept in g.subjects(None, SKOS.Concept):
        assert g.value(concept, OG.v1CatalogScope) is not None


# ---- provenance classes -------------------------------------------------


def test_provenance_classes_match_prd_d6(graphs: dict[str, Graph]) -> None:
    g = graphs["provenance-class"]
    assert {str(n) for n in g.objects(None, SKOS.notation)} == {
        "primary",
        "curated",
        "modeled",
        "reanalysis",
        "derived",
        "synthetic",
        "osm-derived",
        "institutional",
    }


def test_every_provenance_class_caps_a_grade(graphs: dict[str, Graph]) -> None:
    """The F5 Provenance table expressed as data rather than as an if-chain."""
    g = graphs["provenance-class"]
    for concept in g.subjects(None, SKOS.Concept):
        grade = g.value(concept, OG.baseProvenanceGrade)
        assert grade is not None, f"{concept} has no og:baseProvenanceGrade"
        assert str(grade) in {"A", "B", "C", "D"}


def test_modelled_classes_cannot_reach_grade_a(graphs: dict[str, Graph]) -> None:
    """PRD §F5: grade A is 'values measured or primary'. A modelled or synthetic
    dataset with perfect lineage reaches B, never A."""
    g = graphs["provenance-class"]
    for notation in ("modeled", "synthetic", "derived", "reanalysis", "osm-derived"):
        concept = next(
            c for c in g.subjects(SKOS.notation, None) if str(g.value(c, SKOS.notation)) == notation
        )
        assert str(g.value(concept, OG.baseProvenanceGrade)) != "A", notation


# ---- access restrictions ------------------------------------------------


def test_access_restrictions_match_prd_d9(graphs: dict[str, Graph]) -> None:
    g = graphs["access-restriction"]
    assert {str(n) for n in g.objects(None, SKOS.notation)} == {
        "none",
        "account-required",
        "ceii",
        "pii",
        "commercial-paywall",
        "discontinued",
    }


def test_tier_ceiling_follows_the_tier_framework(graphs: dict[str, Graph]) -> None:
    """PRD §5: Tier 1 requires anonymous free access on at least one path, so a
    restriction that blocks anonymous access caps the tier below 1."""
    g = graphs["access-restriction"]
    for concept in g.subjects(None, SKOS.Concept):
        blocks = g.value(concept, OG.blocksAnonymousAccess)
        ceiling = g.value(concept, OG.tierCeiling)
        assert ceiling is not None, f"{concept} has no og:tierCeiling"
        if blocks is not None and bool(blocks):
            assert int(ceiling) >= 2, f"{concept} blocks anonymous access but allows tier 1"


# ---- analysis types -----------------------------------------------------


def test_analysis_types_declare_their_input_domains(graphs: dict[str, Graph]) -> None:
    """PRD §F6.6 derives shared workflow tags from this, so an analysis type
    with no declared inputs contributes nothing to link explanation."""
    g = graphs["analysis-type"]
    for concept in g.subjects(None, SKOS.Concept):
        assert list(g.objects(concept, OG.typicalInputDomain)), f"{concept} declares no inputs"


def test_analysis_input_domains_resolve(merged: Graph, graphs: dict[str, Graph]) -> None:
    domains = set(graphs["data-domain"].subjects(None, SKOS.Concept))
    for _, target in graphs["analysis-type"].subject_objects(OG.typicalInputDomain):
        assert target in domains, f"typicalInputDomain points at unknown domain {target}"


def test_power_flow_requires_nodal_granularity(graphs: dict[str, Graph]) -> None:
    """The nodal-versus-zonal distinction is the one PRD X2 and §F6.4 both turn
    on, so it is asserted rather than assumed."""
    g = graphs["analysis-type"]
    pf = URIRef("https://schema.opengrid.org/concept/analysis-type/powerFlow")
    assert str(g.value(pf, OG.requiresGranularity)) == "nodal"
    cem = URIRef("https://schema.opengrid.org/concept/analysis-type/capacityExpansion")
    assert str(g.value(cem, OG.requiresGranularity)) == "zonal"
