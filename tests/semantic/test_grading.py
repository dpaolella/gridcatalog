"""The three quality facets (WP-7.3, WP-7.4).

PRD §F5. The rules being guarded here are the ones that are easy to violate
without noticing: that the facets never combine, that "not assessed" is not
grade D, that a hierarchical dataset grades per variable, and that a geometry
column is not marked down for lacking a unit it cannot have.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCTERMS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.namespaces import OG
from datahub.semantic.grading import (
    GRADE_LABELS,
    grade_currency,
    grade_documentation,
    grade_provenance,
)
from datahub.semantic.grading.currency import GRACE_FRACTION, parse_cadence
from datahub.semantic.grading.facets import Assessment
from datahub.semantic.resolve import Part

DS = URIRef("urn:test:dataset")
NOW = datetime(2026, 9, 4, tzinfo=UTC)


def dataset(**terms) -> Graph:
    graph = Graph()
    for predicate, value in terms.items():
        node = (
            OG[predicate] if not predicate.startswith("dct_") else getattr(DCTERMS, predicate[4:])
        )
        graph.add((DS, node, value if isinstance(value, URIRef) else Literal(value)))
    return graph


def col(name: str, **kwargs) -> Part:
    return Part(iri=f"urn:test:f/{name}", shape="tabular", local_name=name, **kwargs)


# ---- the rule that outranks all the others -------------------------------


def test_there_is_no_composite_score(records, runner) -> None:
    """ADR-0007. Asserted structurally: nothing in an assessment carries more
    than one facet, so there is no place a composite could be assembled."""
    outcome = runner.run_record("ecmwf-era5", now=NOW, write=False)

    facets = [a.facet for a in outcome.assessments]
    assert sorted(facets) == ["currency", "documentation", "provenance"]
    assert all(isinstance(a, Assessment) for a in outcome.assessments)
    assert not any(hasattr(a, "overall") or hasattr(a, "score") for a in outcome.assessments)


def test_not_assessed_is_not_grade_d() -> None:
    """PRD §F5. Conflating "we have not looked" with "we looked and it is poor"
    would systematically defame every harvested record, of which there are
    thousands."""
    provenance = grade_provenance(dataset(), DS, [], completeness_level=1)
    documentation = grade_documentation(dataset(), DS, [], completeness_level=1)

    assert provenance.grade is None and documentation.grade is None
    assert provenance.label == documentation.label == "Not yet assessed"
    assert "not the same as" in provenance.rationale


# ---- currency ------------------------------------------------------------


def test_superseded_beats_everything() -> None:
    graph = dataset(
        supersededBy=URIRef("urn:test:newer"),
        updateCadence="P1D",
        dct_modified=Literal("2026-09-04T00:00:00Z"),
    )
    assert grade_currency(graph, DS, now=NOW).grade == "D"


def test_a_dataset_within_its_cadence_is_current() -> None:
    graph = dataset(updateCadence="P1Y")
    graph.add((DS, DCTERMS.modified, Literal("2026-03-01T00:00:00Z")))

    assessment = grade_currency(graph, DS, now=NOW)

    assert assessment.grade == "A"
    assert "P1Y" in assessment.rationale, "the cadence travels with the grade"


def test_a_correctly_maintained_annual_dataset_does_not_read_as_stale() -> None:
    """PRD §F5: currency displays the dataset's own stated cadence, so an
    annual dataset eleven months on is Current, not stale next to an hourly
    one."""
    annual = dataset(updateCadence="P1Y")
    annual.add((DS, DCTERMS.modified, Literal("2025-10-04T00:00:00Z")))
    hourly = dataset(updateCadence="PT1H")
    hourly.add((DS, DCTERMS.modified, Literal("2025-10-04T00:00:00Z")))

    assert grade_currency(annual, DS, now=NOW).grade == "A"
    assert grade_currency(hourly, DS, now=NOW).grade == "B"


def test_past_due_is_aging() -> None:
    graph = dataset(updateCadence="P1M")
    graph.add((DS, DCTERMS.modified, Literal("2026-01-01T00:00:00Z")))

    assessment = grade_currency(graph, DS, now=NOW)

    assert assessment.grade == "B"
    assert "due by" in assessment.rationale


def test_a_grace_period_stops_the_grade_flapping() -> None:
    """A monthly release landing on the 3rd rather than the 1st is on schedule.
    Grading it Aging for two days makes the facet flap, and a signal that flaps
    is one nobody trusts."""
    graph = dataset(updateCadence="P30D")
    graph.add((DS, DCTERMS.modified, Literal("2026-08-03T00:00:00Z")))

    assert grade_currency(graph, DS, now=NOW).grade == "A"
    assert 0 < GRACE_FRACTION < 1


def test_no_cadence_is_not_assessed_rather_than_aging() -> None:
    """Absent means "not captured". A dataset with no stated cadence is not
    past due against anything."""
    graph = dataset()
    graph.add((DS, DCTERMS.modified, Literal("2001-01-01T00:00:00Z")))

    assessment = grade_currency(graph, DS, now=NOW)

    assert assessment.grade is None
    assert "not captured" in assessment.rationale


def test_an_irregular_cadence_has_no_due_date() -> None:
    graph = dataset(updateCadence="irregular")
    graph.add((DS, DCTERMS.modified, Literal("2001-01-01T00:00:00Z")))

    assert grade_currency(graph, DS, now=NOW).grade is None


def test_discontinued_is_neither_aging_nor_superseded() -> None:
    """Nothing replaces it, and it is not late for anything."""
    graph = dataset(updateCadence="discontinued")
    graph.add((DS, DCTERMS.modified, Literal("2019-01-01T00:00:00Z")))

    assessment = grade_currency(graph, DS, now=NOW)

    assert assessment.grade is None
    assert "discontinued" in assessment.rationale


def test_coverage_running_into_the_future_does_not_mark_a_dataset_current() -> None:
    """An operational feed commonly declares coverage to the end of the year.
    Reading that as a vintage would grade it Current on a date that has not
    happened."""
    from rdflib.namespace import DCAT

    graph = dataset(updateCadence="P1D")
    period = URIRef("urn:test:dataset#temporal")
    graph.add((DS, DCTERMS.temporal, period))
    graph.add((period, DCAT.endDate, Literal("2027-12-31T00:00:00Z")))

    assert grade_currency(graph, DS, now=NOW).grade is None, "no vintage, not Current"


def test_grade_c_is_unused_on_currency() -> None:
    """PRD §F5 leaves it deliberately empty. Inventing a level to fill it would
    be a convention nobody asked for."""
    assert "C" not in GRADE_LABELS["currency"]


@pytest.mark.parametrize(
    ("text", "days"),
    [("P1D", 1), ("P7D", 7), ("P1M", 30), ("P1Y", 365), ("P6M", 180)],
)
def test_cadence_parsing(text: str, days: int) -> None:
    cadence = parse_cadence(text)
    assert cadence is not None and cadence.interval == timedelta(days=days)


def test_an_hourly_cadence_parses_as_time_not_months() -> None:
    """`PT1M` is one minute and `P1M` is one month. Getting this wrong makes an
    hourly feed look annually maintained."""
    assert parse_cadence("PT1M").interval == timedelta(minutes=1)
    assert parse_cadence("P1M").interval == timedelta(days=30)


# ---- provenance ----------------------------------------------------------


def test_untraced_is_d_however_good_the_data() -> None:
    """The D row is about tracing, not quality. A carefully measured dataset
    with no recorded upstream is D, and the rationale says so."""
    assessment = grade_provenance(
        dataset(), DS, [col("x", value_basis="measured")], completeness_level=2
    )

    assert assessment.grade == "D"
    assert "not about how the data was produced" in assessment.rationale


def test_primary_and_traced_is_a() -> None:
    graph = dataset(upstreamSource=URIRef("urn:test:origin"))
    assessment = grade_provenance(
        graph, DS, [col("x", value_basis="measured")], completeness_level=2
    )
    assert assessment.grade == "A"


def test_derived_and_traced_is_b() -> None:
    graph = dataset(upstreamSource=URIRef("urn:test:origin"))
    assessment = grade_provenance(
        graph, DS, [col("x", value_basis="modeled")], completeness_level=2
    )
    assert assessment.grade == "B"


def test_traced_with_an_unconfirmed_basis_is_c() -> None:
    graph = dataset(upstreamSource=URIRef("urn:test:origin"))
    assessment = grade_provenance(
        graph, DS, [col("x", value_basis="measured"), col("y")], completeness_level=2
    )
    assert assessment.grade == "C"


def test_a_primary_observation_may_say_it_has_no_upstream() -> None:
    """`upstreamSourceUncaptured false` is a claim a steward made. Silence is
    not, and the catalog must never read one as the other."""
    graph = dataset(upstreamSourceUncaptured=Literal(False))
    assessment = grade_provenance(
        graph, DS, [col("x", value_basis="measured")], completeness_level=2
    )
    assert assessment.grade == "A"


def test_a_hierarchical_dataset_grades_per_variable() -> None:
    """PRD §F5. One NetCDF mixes directly-observed and bias-corrected
    variables, and one grade would lie about both."""
    graph = dataset(upstreamSource=URIRef("urn:test:origin"))
    parts = [
        Part(iri="urn:v/obs", shape="hierarchical", local_name="obs", value_basis="measured"),
        Part(iri="urn:v/bc", shape="hierarchical", local_name="bc", value_basis="modeled"),
    ]

    assessment = grade_provenance(graph, DS, parts, completeness_level=2, shape="hierarchical")

    assert assessment.per_part == {"urn:v/obs": "A", "urn:v/bc": "B"}
    assert assessment.grade == "B", "the dataset grade is the worst, not the mean"
    assert "variable" in assessment.rationale


def test_the_dataset_grade_is_the_worst_not_the_average() -> None:
    """An average lets nine clean variables hide one that is untraceable, and
    the user who needs the tenth is who the facet is for."""
    graph = dataset(upstreamSource=URIRef("urn:test:origin"))
    parts = [col(f"m{i}", value_basis="measured") for i in range(9)]
    parts.append(col("modelled", value_basis="synthetic"))

    assert grade_provenance(graph, DS, parts, completeness_level=2).grade == "B"


# ---- documentation -------------------------------------------------------


def test_every_field_documented_is_a() -> None:
    parts = [col("x", definition="what x is", unit="urn:unit:mw")]
    assert grade_documentation(dataset(), DS, parts, completeness_level=2).grade == "A"


def test_some_fields_undocumented_is_b() -> None:
    parts = [
        col("x", definition="what x is", unit="urn:unit:mw"),
        col("y", data_type="float64"),
    ]
    assessment = grade_documentation(dataset(), DS, parts, completeness_level=2)

    assert assessment.grade == "B"
    assert "1 of 2" in assessment.rationale


def test_field_names_and_nothing_else_is_d() -> None:
    parts = [col("x", data_type="float64"), col("y", data_type="float64")]
    assert grade_documentation(dataset(), DS, parts, completeness_level=2).grade == "D"


def test_defined_fields_missing_only_units_are_b_not_d() -> None:
    """D is "no dedicated metadata beyond a filename". A record whose fields are
    all defined but none of which states a unit is partially documented, and
    grading it D says something untrue about work its authors did."""
    parts = [col(n, definition=f"what {n} is", data_type="float64") for n in ("a", "b")]

    assert grade_documentation(dataset(), DS, parts, completeness_level=2).grade == "B"


def test_documented_only_by_an_external_standard_is_c() -> None:
    """Complete for a user who owns CGMES, unreadable for one who does not.
    A different problem from being partial, not a worse degree of it."""
    graph = dataset()
    graph.add((DS, DCTERMS.conformsTo, URIRef("urn:standard:cgmes")))
    parts = [col("p", data_type="float64"), col("q", data_type="float64")]

    assessment = grade_documentation(graph, DS, parts, completeness_level=2)

    assert assessment.grade == "C"
    assert "owns that standard" in assessment.rationale


def test_citing_a_standard_and_also_defining_the_fields_is_not_c() -> None:
    graph = dataset()
    graph.add((DS, DCTERMS.conformsTo, URIRef("urn:standard:cgmes")))
    parts = [col("p", definition="active power", unit="urn:unit:mw")]

    assert grade_documentation(graph, DS, parts, completeness_level=2).grade == "A"


def test_a_geometry_column_is_not_marked_down_for_having_no_unit() -> None:
    """PRD §F5 evaluates geometry separately. Grading it against the attribute
    checklist marks every geospatial dataset down for a lack that is not one."""
    parts = [
        Part(
            iri="urn:f/geom",
            shape="geospatial",
            local_name="geometry",
            geometry_type="Polygon",
            crs="EPSG:4326",
            data_type="geometry",
        )
    ]

    assessment = grade_documentation(dataset(), DS, parts, completeness_level=2)

    assert assessment.grade == "A"
    assert "geometry" in assessment.rationale


def test_a_geometry_column_without_a_crs_is_incomplete() -> None:
    """What documents a geometry is its type, CRS and extent. Missing one of
    those is a real gap, and excusing geometry from units must not excuse it
    from everything."""
    parts = [
        Part(iri="urn:f/geom", shape="geospatial", local_name="geometry", geometry_type="Polygon"),
        Part(iri="urn:f/a", shape="geospatial", local_name="a", definition="x", unit="urn:u"),
    ]

    assessment = grade_documentation(dataset(), DS, parts, completeness_level=2)

    assert assessment.grade == "B"
    assert "a CRS" in assessment.rationale


def test_a_code_list_field_needs_no_unit() -> None:
    """A land-cover class stored as `uint8` is categorical, not a number
    missing its unit."""
    parts = [col("Map", definition="land cover class", data_type="uint8", has_code_list=True)]

    assert grade_documentation(dataset(), DS, parts, completeness_level=2).grade == "A"


# ---- every grade has a label ---------------------------------------------


@pytest.mark.parametrize("facet", ["provenance", "documentation", "currency"])
def test_every_grade_has_a_label(facet: str) -> None:
    """The letter is what a filter matches and the label is what a user reads.
    A letter with no label renders as a bare 'C'."""
    assert GRADE_LABELS[facet]
    for grade, label in GRADE_LABELS[facet].items():
        assert grade in "ABCD" and label


def test_a_changed_grade_replaces_the_old_one_instead_of_joining_it(tmp_path):
    """Recomputing must replace computed state, not accumulate it.

    `_write` used to remove triples from `get_graph`'s return value, which is a
    *copy*, so the retraction went nowhere and the new triples were added
    alongside the old.

    The value has to actually change for this to show: `_write` short-circuits
    on `_isomorphic` when a pass recomputes the same answer, so a second run
    over unchanged input is a no-op and proves nothing. Currency is graded
    against `now`, so running the same record two years apart changes it — and
    a record that then carries two currency grades is one where the query
    decides which is true.
    """
    from datetime import UTC, datetime

    from datahub.graph.graphs import NamedGraph
    from datahub.graph.loader import bootstrap
    from datahub.graph.records import RecordStore
    from datahub.graph.store import RdflibStore
    from datahub.semantic.runner import SemanticRunner
    from fixtures.loader import load_record
    from rdflib import URIRef

    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    records.put(load_record("ecmwf-era5"))
    runner = SemanticRunner(records)

    def currency_grades() -> list[str]:
        rows = store.select(
            """
            SELECT ?grade WHERE {
              GRAPH ??g { ?node og:facet "currency" ; og:grade ?grade . }
            }
            """,
            {"g": URIRef(str(NamedGraph.COMPUTED))},
        )
        return sorted(str(row["grade"]) for row in rows)

    runner.run_all(now=datetime(2024, 1, 1, tzinfo=UTC))
    early = currency_grades()
    assert len(early) == 1, f"one currency grade per record, got {early}"

    runner.run_all(now=datetime(2031, 1, 1, tzinfo=UTC))
    late = currency_grades()

    assert len(late) == 1, (
        f"recomputing left {len(late)} currency grades on one record ({late}); the "
        "old one was never retracted, so which grade the API reports depends on "
        "which the query returns first"
    )
    assert late != early, (
        "seven years on, the currency grade should have moved — if it did not, "
        "this test is no longer exercising a change and cannot catch the bug"
    )
    store.close()
