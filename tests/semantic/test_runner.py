"""Running the semantic layer and writing back (WP-7.1, 7.3, 7.4).

The second half of the milestone's done-criterion is here: *a lapsed-cadence
dataset re-grades on the next batch with no write event.* So is the property
that makes `og:lastComputedAt` mean anything — that a recompute changing
nothing writes nothing.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rdflib import URIRef

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.graph.graphs import NamedGraph
from datahub.namespaces import OG

NOW = datetime(2026, 9, 4, tzinfo=UTC)
ERA5 = URIRef("https://catalog.opengrid.org/ds/ecmwf-era5")


def computed(store):
    return store.get_graph(NamedGraph.COMPUTED)


# ---- the done-criterion --------------------------------------------------


def test_a_lapsed_dataset_regrades_with_no_write_event(runner, records, loaded_store) -> None:
    """PRD §F4.3. The record is untouched between the two passes; only the
    clock moves. A Currency grade hung off the write event would be frozen at
    whatever the first pass decided."""
    first = runner.run_record("eia-930", now=datetime(2026, 9, 2, tzinfo=UTC))
    assert first.grade("currency") == "A", "daily feed, updated 2026-09-01"

    later = runner.run_record("eia-930", now=datetime(2026, 10, 1, tzinfo=UTC))

    assert later.grade("currency") == "B"
    assert later.changed, "the new grade reached the store"


def test_the_scheduled_pass_covers_every_record_not_only_recent_writes(runner) -> None:
    """Narrowing the scheduled batch to recently-touched records is the exact
    bug the split exists to prevent: a dataset goes stale by *not* being
    written, so there is no recent-write set to narrow to."""
    summary = runner.run_scheduled(now=NOW)

    assert summary.records == 17, "the whole corpus"


# ---- writing -------------------------------------------------------------


def test_a_recompute_that_changes_nothing_writes_nothing(runner) -> None:
    """`og:lastComputedAt` is only meaningful if it moves when the answer moves.
    A writer that stamped every pass would make every signal look recomputed a
    moment ago whether or not anything was looked at."""
    first = runner.run_record("ecmwf-era5", now=NOW)
    second = runner.run_record("ecmwf-era5", now=NOW + timedelta(hours=6))

    assert first.changed
    assert not second.changed


def test_output_goes_to_the_computed_graph_and_nowhere_else(runner, loaded_store) -> None:
    """Principle 8: derived state is droppable. A grade written into the
    catalog graph could not be dropped without taking the record with it."""
    before = loaded_store.count(NamedGraph.CATALOG)

    runner.run_record("ecmwf-era5", now=NOW)

    assert loaded_store.count(NamedGraph.CATALOG) == before
    assert loaded_store.count(NamedGraph.COMPUTED) > 0


def test_grades_are_addressable_and_carry_their_rationale(runner, loaded_store) -> None:
    """PRD §F5: every grade derives from recorded facts, which is only
    checkable if the facts travel with the grade."""
    runner.run_record("ecmwf-era5", now=NOW)
    graph = computed(loaded_store)

    nodes = list(graph.objects(ERA5, OG.qualityGrade))
    assert nodes
    for node in nodes:
        assert graph.value(node, OG.facet) is not None
        assert graph.value(node, OG.gradeRationale) is not None
        assert graph.value(node, OG.gradedAt) is not None


def test_a_facet_that_was_not_assessed_carries_no_grade(runner, loaded_store) -> None:
    """An absent grade and a grade of None are the same claim. Writing a node
    that says "no grade" invites a reader to render it as one."""
    runner.run_record("caiso-nodal-lmp-restricted", now=NOW)
    graph = computed(loaded_store)
    iri = URIRef("https://catalog.opengrid.org/ds/caiso-nodal-lmp-restricted")

    for node in graph.objects(iri, OG.qualityGrade):
        if graph.value(node, OG.notYetAssessed):
            assert graph.value(node, OG.grade) is None


def test_every_signal_is_timestamped(runner, loaded_store) -> None:
    """PRD §F4: the freshness lag is visible rather than hidden. Evaluators
    need it; modelers can ignore it."""
    runner.run_record("ecmwf-era5", now=NOW)
    graph = computed(loaded_store)

    names = {
        str(graph.value(node, OG.signalName)) for node in graph.objects(ERA5, OG.lastComputedAt)
    }
    assert {"currency-grade", "concept-resolution"} <= names


def test_a_source_confirmed_concept_is_not_re_asserted(runner, loaded_store) -> None:
    """PRD §F4.8. Re-writing a steward's assignment into the computed graph
    would make it indistinguishable from this module's own — which is exactly
    the distinction the requirement asks to keep."""
    runner.run_record("ecmwf-era5", now=NOW)
    graph = computed(loaded_store)
    ssrd = URIRef("https://catalog.opengrid.org/field/ecmwf-era5/ssrd")

    assert graph.value(ssrd, OG.concept) is None


def test_a_gap_is_written_with_its_reason(runner, loaded_store) -> None:
    """Rule X4: never a silent omission."""
    runner.run_record("ecmwf-era5", now=NOW)
    graph = computed(loaded_store)

    gaps = [
        (subject, graph.value(subject, OG.gapReason))
        for subject in graph.subjects(OG.gapReason, None)
    ]
    assert gaps
    for _, reason in gaps:
        assert reason and len(str(reason)) > 30


def test_recomputing_one_record_leaves_another_alone(runner, loaded_store) -> None:
    """A `DROP GRAPH` per record would take every other record's computed state
    with it. The delete has to be scoped."""
    runner.run_record("ecmwf-era5", now=NOW)
    runner.run_record("eia-930", now=NOW)
    graph = computed(loaded_store)

    assert list(graph.objects(ERA5, OG.qualityGrade)), "ERA5 survived the second pass"


def test_a_pass_summarises_what_it_did(runner) -> None:
    summary = runner.run_all(now=NOW)
    payload = summary.as_dict()

    assert payload["records"] == 17
    assert payload["gaps"] >= 0
    assert any(key.startswith("currency:") for key in payload["grades"])
