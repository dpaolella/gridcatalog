"""The validation runner: level filtering, report projection, and two
regressions that were expensive to find.
"""

from __future__ import annotations

import threading

import pytest
from datahub.errors import ValidationFailed
from datahub.harvest.validate import ValidationRunner, format_report
from datahub.namespaces import SH
from rdflib import Graph
from rdflib.namespace import RDF

MINIMAL = """
@prefix dcat: <http://www.w3.org/ns/dcat#> .
@prefix dct:  <http://purl.org/dc/terms/> .
@prefix og:   <https://schema.opengrid.org/ns#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
<https://catalog.opengrid.org/ds/t> a dcat:Dataset ;
  dct:title "T"@en ; dct:description "D"@en ;
  og:dataDomain <https://schema.opengrid.org/concept/data-domain/DD1> ;
  og:provenanceClass <https://schema.opengrid.org/concept/provenance-class/curated> ;
  dct:license <https://spdx.org/licenses/CC-BY-4.0> ;
  og:accessRestriction <https://schema.opengrid.org/concept/access-restriction/none> ;
  og:anonymousAccess true ;
  og:documentationStatus "partial" ;
  og:completenessLevel 1 ; og:reviewState "confirmed" ; og:harvestSource "curated" ;
  dcat:distribution <https://catalog.opengrid.org/dist/t--d> .
<https://catalog.opengrid.org/dist/t--d> a dcat:Distribution ;
  dcat:accessURL <https://example.org/t.csv> .
"""


def graph_of(turtle: str) -> Graph:
    graph = Graph()
    graph.parse(data=turtle, format="turtle")
    return graph


@pytest.fixture(scope="module")
def runner() -> ValidationRunner:
    return ValidationRunner()


# ---- regressions ---------------------------------------------------------


def test_cold_cache_does_not_deadlock() -> None:
    """`_build_level_graph` reads `self.shapes` while holding the runner's lock.

    With a plain `threading.Lock` that is a self-deadlock, and the symptom is a
    hang with no output — which cost an hour to find once. A fresh runner whose
    first call is `shapes_for_level` reproduces it exactly.
    """
    fresh = ValidationRunner()
    done = threading.Event()

    def build() -> None:
        fresh.shapes_for_level(1)
        done.set()

    thread = threading.Thread(target=build, daemon=True)
    thread.start()
    assert done.wait(timeout=20), "shapes_for_level deadlocked on a cold cache"


def test_level_filtering_leaves_no_orphaned_sparql_targets(runner: ValidationRunner) -> None:
    """Detaching a shape above the target level must remove its target node too.

    An orphaned `sh:SPARQLTarget` sends pySHACL's advanced mode into a
    non-terminating scan. It hangs rather than erroring, so nothing else in the
    suite would catch it.
    """
    for level in (1, 2, 3):
        graph = runner.shapes_for_level(level)
        orphans = [
            s
            for s in graph.subjects(RDF.type, SH.SPARQLTarget)
            if not list(graph.subjects(SH.target, s))
        ]
        assert not orphans, f"level {level} left {len(orphans)} orphaned SPARQL targets"
        constraints = [
            s
            for s in graph.subjects(RDF.type, SH.SPARQLConstraint)
            if not list(graph.subjects(SH.sparql, s))
        ]
        assert not constraints, f"level {level} left {len(constraints)} orphaned constraints"


def test_level_filtering_leaves_no_dangling_blank_nodes(runner: ValidationRunner) -> None:
    from rdflib import BNode

    for level in (1, 2, 3):
        graph = runner.shapes_for_level(level)
        dangling = [
            o for _, _, o in graph if isinstance(o, BNode) and not list(graph.predicate_objects(o))
        ]
        assert not dangling, f"level {level} has {len(dangling)} dangling blank nodes"


def test_validation_completes_quickly(runner: ValidationRunner) -> None:
    """The harvest pipeline validates thousands of records per run. A validation
    that takes a second each would make M3's 2,000-record target a 30-minute
    step, which is how validation ends up being skipped."""
    import time

    data = graph_of(MINIMAL)
    runner.validate(data, 1)  # warm the caches
    started = time.perf_counter()
    for _ in range(20):
        runner.validate(data, 1)
    per_record = (time.perf_counter() - started) / 20
    assert per_record < 0.5, f"{per_record * 1000:.0f}ms per record is too slow"


# ---- level parameterisation ---------------------------------------------


def test_a_valid_level_one_record_passes_at_level_one(runner: ValidationRunner) -> None:
    assert runner.validate(graph_of(MINIMAL), 1).conforms


def test_the_same_record_fails_at_level_three(runner: ValidationRunner) -> None:
    """Level 3 requires resolved fields. A level-1 record has none, and claiming
    3 without them is the overclaim ADR-0004 exists to prevent."""
    report = runner.validate(graph_of(MINIMAL), 3)
    assert not report.conforms
    assert any("level 3 record has resolved fields" in v.message for v in report.violations)


def test_highest_passing_level(runner: ValidationRunner) -> None:
    assert runner.highest_passing_level(graph_of(MINIMAL)) == 2


def test_invalid_level_is_rejected(runner: ValidationRunner) -> None:
    with pytest.raises(ValueError, match="completeness level"):
        runner.validate(graph_of(MINIMAL), 4)


# ---- the four PRD §4.5 shapes, positively and negatively -----------------

MODELLED_FIELD = """
@prefix og: <https://schema.opengrid.org/ns#> .
<https://catalog.opengrid.org/field/x> a og:Field ;
  og:localName "x" ; og:label "X"@en ; og:definition "A field."@en ;
  og:dataType "float64" ; og:valueBasis "modeled" .
"""


def test_shape_1_modelled_field_without_source_fails(runner: ValidationRunner) -> None:
    report = runner.validate(graph_of(MODELLED_FIELD), 2)
    assert not report.conforms
    assert any("records no origin" in v.message for v in report.violations)


def test_shape_1_modelled_field_with_source_passes(runner: ValidationRunner) -> None:
    with_source = MODELLED_FIELD.replace(
        'og:valueBasis "modeled" .',
        'og:valueBasis "modeled" ; og:fieldSource <https://catalog.opengrid.org/ds/upstream> .',
    )
    assert runner.validate(graph_of(with_source), 2).conforms


def test_shape_1_measured_field_needs_no_source(runner: ValidationRunner) -> None:
    """The rule is about modelled values specifically. A measured value's origin
    is the measurement."""
    measured = MODELLED_FIELD.replace('"modeled"', '"measured"')
    assert runner.validate(graph_of(measured), 2).conforms


GEO = MINIMAL.replace(
    'og:harvestSource "curated" ;',
    'og:harvestSource "curated" ; og:geospatialPrimary true ; '
    'og:bboxMinLon "-1.0"^^xsd:double ; og:bboxMinLat "-1.0"^^xsd:double ; '
    'og:bboxMaxLon "1.0"^^xsd:double ; og:bboxMaxLat "1.0"^^xsd:double ; '
    'og:geometryTypes "raster" ;',
)
assert "geospatialPrimary" in GEO, "the GEO fixture stopped matching MINIMAL"


def test_shape_2_geospatial_primary_without_crs_fails_at_level_one(
    runner: ValidationRunner,
) -> None:
    report = runner.validate(graph_of(GEO), 1)
    assert not report.conforms
    assert any("nativeCRS" in (v.path or "") for v in report.violations)


def test_shape_2_geospatial_primary_with_crs_passes(runner: ValidationRunner) -> None:
    complete = GEO.replace(
        'og:geometryTypes "raster" ;', 'og:geometryTypes "raster" ; og:nativeCRS "EPSG:4326" ;'
    )
    assert runner.validate(graph_of(complete), 1).conforms


def test_shape_2_does_not_fire_on_a_non_geospatial_record(runner: ValidationRunner) -> None:
    """A tabular dataset has no bbox and that is not a defect."""
    assert runner.validate(graph_of(MINIMAL), 1).conforms


RANGE = MINIMAL.replace(
    "dcat:accessURL <https://example.org/t.csv> .",
    "dcat:accessURL <https://example.org/t.csv> ; og:supportsRangeRequests true .",
)


def test_shape_4_range_requests_without_chunk_index_fails(runner: ValidationRunner) -> None:
    report = runner.validate(graph_of(RANGE), 1)
    assert not report.conforms
    assert any("chunkIndexMethod" in (v.path or "") for v in report.violations)


def test_shape_4_range_requests_with_chunk_index_passes(runner: ValidationRunner) -> None:
    complete = RANGE.replace(
        "og:supportsRangeRequests true .",
        'og:supportsRangeRequests true ; og:chunkIndexMethod "zarr-v2" .',
    )
    assert runner.validate(graph_of(complete), 1).conforms


def test_shape_4_does_not_fire_when_range_requests_are_absent(runner: ValidationRunner) -> None:
    assert runner.validate(graph_of(MINIMAL), 1).conforms


# ---- report projection ---------------------------------------------------


def test_violation_carries_a_resolvable_focus_node(runner: ValidationRunner) -> None:
    stripped = MINIMAL.replace("dct:license <https://spdx.org/licenses/CC-BY-4.0> ;", "")
    report = runner.validate(graph_of(stripped), 1)
    violation = next(v for v in report.violations if v.path == "dct:license")
    assert violation.focus_node == "https://catalog.opengrid.org/ds/t"
    assert violation.constraint == "MinCount"
    assert violation.severity == "Violation"


def test_raise_if_invalid_carries_the_violations(runner: ValidationRunner) -> None:
    stripped = MINIMAL.replace("dct:license <https://spdx.org/licenses/CC-BY-4.0> ;", "")
    report = runner.validate(graph_of(stripped), 1)
    with pytest.raises(ValidationFailed) as caught:
        report.raise_if_invalid()
    payload = caught.value.to_payload()
    assert payload["violations"]
    assert payload["target_level"] == 1


def test_format_report_on_a_conforming_record_is_one_line(runner: ValidationRunner) -> None:
    rendered = format_report(runner.validate(graph_of(MINIMAL), 1))
    assert rendered == "conforms at completeness level 1"


def test_validate_jsonld_uses_the_project_context(runner: ValidationRunner) -> None:
    """A record referring to the context by URL must resolve locally: validation
    cannot depend on a network round trip to schema.opengrid.org."""
    record = {
        "@context": "https://schema.opengrid.org/context/opengrid-datahub.jsonld",
        "id": "https://catalog.opengrid.org/ds/j",
        "type": "Dataset",
        "title": "J",
    }
    report = runner.validate_jsonld(record, 1)
    assert not report.conforms
    assert any(v.path == "dct:description" for v in report.violations)


def test_validation_ontology_is_a_projection_not_the_whole_vocabulary(
    runner: ValidationRunner,
) -> None:
    """Merging 3,000 triples into every record is the difference between
    validating a harvest run in a minute and in an hour."""
    assert len(runner.validation_ontology) < len(runner.ontology) / 4
    assert len(runner.validation_ontology) > 100


def test_composite_quality_facet_is_rejected(runner: ValidationRunner) -> None:
    """ADR-0007, enforced at the shape layer rather than only in review."""
    composite = """
    @prefix og: <https://schema.opengrid.org/ns#> .
    <https://catalog.opengrid.org/ds/t#q> a og:QualityGrade ;
      og:facet "overall" ; og:grade "B" ; og:gradeRationale "Average."@en .
    """
    report = runner.validate(graph_of(composite), 2)
    assert not report.conforms
    assert any("no composite" in v.message for v in report.violations)


def test_currency_grade_c_is_rejected(runner: ValidationRunner) -> None:
    """PRD §F5 leaves C unused on the Currency facet deliberately."""
    grade_c = """
    @prefix og: <https://schema.opengrid.org/ns#> .
    <https://catalog.opengrid.org/ds/t#q> a og:QualityGrade ;
      og:facet "currency" ; og:grade "C" ; og:gradeRationale "In between."@en .
    """
    report = runner.validate(graph_of(grade_c), 2)
    assert not report.conforms
    assert any("does not use grade C" in v.message for v in report.violations)
