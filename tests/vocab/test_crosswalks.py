"""The four crosswalks and the honesty rules X1-X4 they exist to obey."""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import Graph, URIRef
from rdflib.namespace import DCTERMS, OWL, SKOS

from datahub.namespaces import OG

REPO_ROOT = Path(__file__).resolve().parents[2]
CROSSWALKS = REPO_ROOT / "vocab" / "crosswalks"
GC = "https://schema.opengrid.org/concept/grid-concept/"
NAMES = ["cim-cgmes", "pypsa", "matpower", "sienna"]

MATCH_PREDICATES = (
    SKOS.exactMatch,
    SKOS.closeMatch,
    SKOS.broadMatch,
    SKOS.narrowMatch,
    SKOS.relatedMatch,
)
DIFFERENCE_MARKERS = (OG.unitDiffers, OG.basisDiffers, OG.granularityDiffers)


@pytest.fixture(scope="module")
def concept_scheme() -> Graph:
    g = Graph()
    g.parse((REPO_ROOT / "vocab" / "og-grid-concept.ttl").as_posix(), format="turtle")
    return g


@pytest.fixture(scope="module")
def crosswalks() -> dict[str, Graph]:
    out: dict[str, Graph] = {}
    for name in NAMES:
        g = Graph()
        g.parse((CROSSWALKS / f"{name}.ttl").as_posix(), format="turtle")
        out[name] = g
    return out


@pytest.mark.parametrize("name", NAMES)
def test_crosswalk_is_a_versioned_scheme(crosswalks: dict[str, Graph], name: str) -> None:
    g = crosswalks[name]
    scheme = URIRef(f"https://schema.opengrid.org/crosswalk/{name}")
    assert (scheme, None, SKOS.ConceptScheme) in g
    assert g.value(scheme, OWL.versionInfo), f"{name}: unversioned"
    assert g.value(scheme, DCTERMS.source), f"{name}: does not cite the external standard"
    assert g.value(scheme, OG.targetScheme), f"{name}: does not name the target version"


@pytest.mark.parametrize("name", NAMES)
def test_no_dangling_concept_references(
    crosswalks: dict[str, Graph], concept_scheme: Graph, name: str
) -> None:
    """A mapping from a concept that does not exist is worse than no mapping:
    it looks like coverage."""
    known = {str(c) for c in concept_scheme.subjects(None, SKOS.Concept)}
    g = crosswalks[name]
    for predicate in MATCH_PREDICATES:
        for subject in g.subjects(predicate, None):
            if str(subject).startswith(GC):
                assert str(subject) in known, f"{name}: maps unknown concept {subject}"
    for target in g.objects(None, OG.gapConcept):
        assert str(target) in known, f"{name}: gap marker names unknown concept {target}"


# ---- X1 -----------------------------------------------------------------


def test_x1_mappings_live_only_in_crosswalks(concept_scheme: Graph) -> None:
    """X1: concept-to-external-scheme mappings are authored once as shared
    versioned schemes, never per dataset. The grid-concept file itself may carry
    matches only to the two schemes it was bootstrapped from."""
    allowed_prefixes = (
        "http://openenergy-platform.org/ontology/",
        "https://schema.opengrid.org/",
    )
    for predicate in (SKOS.exactMatch, SKOS.closeMatch):
        for _, target in concept_scheme.subject_objects(predicate):
            assert str(target).startswith(allowed_prefixes), (
                f"og-grid-concept.ttl asserts a mapping to {target}; external-scheme "
                "mappings belong in vocab/crosswalks/ (X1)"
            )


# ---- X2: this is PRD query Q5, written as a regression test -------------


@pytest.mark.parametrize("name", NAMES)
def test_x2_exact_match_never_carries_a_difference(crosswalks: dict[str, Graph], name: str) -> None:
    """X2: exactMatch means identity of quantity, unit AND granularity. A
    statement that asserts exactMatch while also recording a unit, basis or
    granularity difference is self-contradictory."""
    g = crosswalks[name]
    for subject in set(g.subjects(SKOS.exactMatch, None)):
        differences = [m for m in DIFFERENCE_MARKERS if g.value(subject, m) is not None]
        assert not differences, (
            f"{name}: {subject} asserts exactMatch but also declares "
            f"{[str(d) for d in differences]}"
        )


@pytest.mark.parametrize("name", NAMES)
def test_x2_close_match_states_why(crosswalks: dict[str, Graph], name: str) -> None:
    """A closeMatch with no stated difference is an exactMatch someone was
    unsure about, and the uncertainty is the useful part."""
    g = crosswalks[name]
    for subject in set(g.subjects(SKOS.closeMatch, None)):
        has_reason = any(g.value(subject, m) is not None for m in DIFFERENCE_MARKERS)
        has_note = g.value(subject, SKOS.editorialNote) is not None
        assert has_reason or has_note, (
            f"{name}: {subject} is a closeMatch with no stated difference or note"
        )


def test_x2_nodal_and_zonal_are_never_exact_matches(crosswalks: dict[str, Graph]) -> None:
    """The PRD's own example: 'A nodal voltage and a zonal average voltage are
    not an exact match.' Transfer capacity is the zonal quantity in this scheme;
    nothing may claim identity with it."""
    zonal = URIRef(GC + "transferCapacity")
    for name, g in crosswalks.items():
        assert not list(g.objects(zonal, SKOS.exactMatch)), (
            f"{name}: zonal transfer capacity claims an exactMatch"
        )


# ---- X3 -----------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_x3_every_mapping_is_sourced_or_flagged(crosswalks: dict[str, Graph], name: str) -> None:
    """X3: an inferred mapping is flagged as inferred with a stated basis. There
    is no unmarked third category — every mapping either cites documentation or
    admits it was inferred."""
    g = crosswalks[name]
    subjects = {s for p in MATCH_PREDICATES for s in g.subjects(p, None)}
    for subject in subjects:
        sourced = g.value(subject, DCTERMS.source) is not None
        inferred = g.value(subject, OG.enrichmentBasis) is not None
        explained = any(g.value(subject, m) is not None for m in DIFFERENCE_MARKERS)
        assert sourced or inferred or explained, (
            f"{name}: {subject} is neither sourced, flagged inferred, nor explained"
        )


@pytest.mark.parametrize("name", NAMES)
def test_x3_inferred_mappings_state_a_basis(crosswalks: dict[str, Graph], name: str) -> None:
    g = crosswalks[name]
    for subject in g.subjects(OG.enrichmentBasis, None):
        assert g.value(subject, SKOS.editorialNote) is not None, (
            f"{name}: {subject} is flagged inferred but states no basis"
        )


# ---- X4 -----------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_x4_gaps_are_explicit(crosswalks: dict[str, Graph], name: str) -> None:
    """X4: a concept with no confident mapping carries an explicit gap marker.
    It is never silently omitted."""
    g = crosswalks[name]
    gaps = list(g.subjects(None, OG.MappingGap))
    assert gaps, f"{name}: declares no gaps at all, which cannot be true"
    for gap in gaps:
        assert list(g.objects(gap, OG.gapConcept)), f"{name}: gap names no concept"
        assert g.value(gap, OG.gapReason) is not None, f"{name}: gap states no reason"
        assert g.value(gap, OG.gapInScheme) is not None, f"{name}: gap names no scheme"


def test_x4_gap_and_mapping_are_mutually_exclusive(crosswalks: dict[str, Graph]) -> None:
    """A concept cannot both map and be a gap in the same scheme."""
    for name, g in crosswalks.items():
        gapped = {
            str(o) for gap in g.subjects(None, OG.MappingGap) for o in g.objects(gap, OG.gapConcept)
        }
        mapped = {str(s) for p in MATCH_PREDICATES for s in g.subjects(p, None)}
        overlap = gapped & mapped
        assert not overlap, f"{name}: {sorted(overlap)} are both mapped and marked as gaps"


def test_sienna_has_the_highest_exact_match_density(crosswalks: dict[str, Graph]) -> None:
    """PRD §F7 says the scheme is bootstrapped from Sienna, so Sienna should show
    the closest correspondence. If it does not, either the bootstrapping did not
    happen or the discipline slipped elsewhere — both worth knowing."""

    def ratio(g: Graph) -> float:
        exact = len(set(g.subjects(SKOS.exactMatch, None)))
        close = len(set(g.subjects(SKOS.closeMatch, None)))
        return exact / max(exact + close, 1)

    ratios = {name: ratio(g) for name, g in crosswalks.items()}
    assert ratios["sienna"] >= ratios["cim-cgmes"], ratios
