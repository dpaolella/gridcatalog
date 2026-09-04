"""The projector: graph to search index.

PRD principle 8 — the graph is the record, the index is derived — is what most
of these tests are really checking. If a fact reaches the index that a full
reindex would not reproduce, it was written in the wrong place.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.search.backend import (
    Entitlement,
    InMemorySearchBackend,
    SearchRequest,
)
from datahub.graph.loader import bootstrap
from datahub.graph.records import RecordStore
from datahub.graph.store import RdflibStore
from datahub.projector import Projector
from fixtures.loader import declared_level, load_record, record_names

DS = "https://catalog.opengrid.org/ds/"
GC = "https://schema.opengrid.org/concept/grid-concept/"


@pytest.fixture(scope="module")
def loaded():
    """The whole fixture corpus, in a store with vocabulary and entailments."""
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    for name in record_names():
        records.put(load_record(name))
    return records


@pytest.fixture
def projector(loaded):
    return Projector(loaded, InMemorySearchBackend())


# ---- every fixture projects ---------------------------------------------


@pytest.mark.parametrize("name", record_names())
def test_every_fixture_projects(projector, name: str) -> None:
    doc = projector.document_for(name)
    assert doc is not None, f"{name} produced no document"
    assert doc.id == name
    assert doc.title
    assert doc.completeness_level == declared_level(name)


@pytest.mark.parametrize("name", record_names())
def test_projection_carries_what_the_record_carries(projector, name: str) -> None:
    """Driven from the fixture rather than from a hand-written expectation, so
    a fixture that gains a field cannot silently stop being projected."""
    record = load_record(name)
    dataset = next(n for n in record["@graph"] if n.get("type") == "Dataset")
    doc = projector.document_for(name)

    assert len(doc.data_domains) == len(dataset.get("dataDomain", []))
    assert doc.distribution_count == len(dataset.get("distribution", []))
    assert doc.field_count == len(dataset.get("hasField", []))
    if "license" in dataset:
        assert doc.license_id, f"{name} has a licence in the record and none in the index"
    if "bboxMinLon" in dataset:
        assert doc.spatial.bbox == [
            dataset["bboxMinLon"],
            dataset["bboxMinLat"],
            dataset["bboxMaxLon"],
            dataset["bboxMaxLat"],
        ]
    if "supersededBy" in dataset:
        assert doc.superseded_by == dataset["supersededBy"]


# ---- the things that are easy to get wrong ------------------------------


def test_distributions_keep_their_own_access_restriction(projector) -> None:
    """PRD §4.2: the same dataset commonly has an anonymous S3 copy and an
    account-gated API, and the classification differs between them. Flattening
    to the dataset's value would make a filter for anonymous access miss the S3
    copy."""
    doc = projector.document_for("ecmwf-era5")
    restrictions = {d.access_restriction for d in doc.distributions}
    assert restrictions == {"none", "accountRequired"}


def test_concept_labels_come_from_the_vocabulary(projector) -> None:
    """An index carrying only IRIs makes a concept filter unreadable in the UI
    and unsearchable by text."""
    doc = projector.document_for("ecmwf-era5")
    labels = {c.label for c in doc.concepts}
    assert "Wind speed" in labels
    assert "Global horizontal irradiance" in labels
    assert all(not c.label.startswith("http") for c in doc.concepts)


def test_expanded_concepts_include_ancestors(projector) -> None:
    """PRD §4.6 Q3, pushed into the index: a filter on a parent concept is a
    term lookup, not a property path on every search."""
    doc = projector.document_for("ecmwf-era5")
    assert f"{GC}windSpeed" in doc.concept_iris_expanded
    assert f"{GC}renewableResource" in doc.concept_iris_expanded, (
        "the ancestor is missing, so a search for renewable-resource datasets "
        "would not find this one"
    )


def test_worst_link_health_is_aggregated(projector, loaded) -> None:
    doc = projector.document_for("ecmwf-era5")
    assert doc.worst_link_health == "verified"
    assert doc.all_distributions_unreachable is False


def test_tier_three_is_reference_only(projector) -> None:
    """PRD §5: tier is internal; ``reference_only`` is the user-facing
    consequence, and it is what explains an absent schema tab."""
    doc = projector.document_for("wecc-ferc-ceii")
    assert doc.tier == 3
    assert doc.reference_only is True


def test_domain_notations_survive(projector) -> None:
    doc = projector.document_for("nrel-atb")
    assert {d.notation for d in doc.data_domains} == {"DD9", "DD6"}


# ---- the defamation guard -----------------------------------------------


def test_a_level_one_record_is_not_yet_assessed(projector) -> None:
    """PRD §F5: a record below completeness level 2 shows Provenance and
    Documentation as "not yet assessed", NEVER as grade D.

    Most of the catalog is harvested and sits at level 1. Reporting D would
    tell every user that the majority of the catalog is untraceable, when the
    truth is that nobody has looked yet.
    """
    doc = projector.document_for("lbnl-queued-up")
    assert doc.completeness_level == 1
    assert doc.quality_assessed is False
    assert doc.quality.provenance is None
    assert doc.quality.documentation is None
    assert doc.quality.provenance_label == "Not yet assessed"
    assert doc.quality.documentation_label == "Not yet assessed"


def test_grades_written_by_the_semantic_layer_are_projected(loaded) -> None:
    """The other side of the guard: once a level 2+ record HAS been assessed,
    the grades must reach the index."""
    from datahub.graph.graphs import NamedGraph
    from rdflib import URIRef

    dataset = URIRef(DS + "nrel-nsrdb")
    for facet, grade in (("provenance", "B"), ("documentation", "A")):
        node = URIRef(f"{dataset}#grade-{facet}")
        loaded.store.update(
            """
            INSERT DATA { GRAPH ??g {
              ??ds og:qualityGrade ??node .
              ??node a og:QualityGrade ; og:facet ??facet ; og:grade ??grade ;
                     og:gradeRationale "Assessed in review."@en .
            } }
            """,
            {
                "g": NamedGraph.COMPUTED.uri(),
                "ds": dataset,
                "node": node,
                "facet": facet,
                "grade": grade,
            },
        )
    doc = Projector(loaded, InMemorySearchBackend()).document_for("nrel-nsrdb")
    assert doc.quality_assessed is True
    assert doc.quality.provenance == "B"
    assert doc.quality.provenance_label == "Derived & Traced"
    assert doc.quality.documentation == "A"
    loaded.store.drop_graph(NamedGraph.COMPUTED)


def test_there_is_no_composite_grade(projector) -> None:
    """ADR-0007, asserted on the shape that reaches the UI."""
    import re

    doc = projector.document_for("ecmwf-era5")
    for field in doc.quality.model_fields:
        assert not re.search(r"(overall|composite|total|combined)", field, re.I)


# ---- indexing and removal ------------------------------------------------


def test_project_indexes_a_confirmed_record(projector) -> None:
    result = projector.project("ecmwf-era5")
    assert result.indexed == 1
    assert projector.backend.get("ecmwf-era5") is not None


def test_an_unconfirmed_record_is_removed_not_skipped(loaded) -> None:
    """A record demoted to draft that stays indexed is visible to every
    anonymous search, and nothing would surface the mistake."""
    backend = InMemorySearchBackend()
    projector = Projector(loaded, backend)
    projector.project("eia-930")
    assert backend.get("eia-930") is not None

    loaded.demote("eia-930", reason="testing the removal path")
    try:
        result = projector.project("eia-930")
        assert result.removed == 1
        assert result.indexed == 0
        assert backend.get("eia-930") is None, "a demoted record is still indexed"
    finally:
        loaded.promote("eia-930", validate=False)


def test_a_bad_record_does_not_stop_a_batch(loaded, monkeypatch) -> None:
    """One unprojectable record must not take down a whole reindex."""
    backend = InMemorySearchBackend()
    projector = Projector(loaded, backend)
    original = projector.document_for

    def explode(dataset_id: str):
        if dataset_id == "eia-930":
            raise RuntimeError("simulated projection failure")
        return original(dataset_id)

    monkeypatch.setattr(projector, "document_for", explode)
    result = projector.project_many(list(record_names()))
    assert result.errors and "eia-930" in result.errors[0]
    assert result.indexed == len(record_names()) - 1


# ---- search over the projection ------------------------------------------


def test_the_projection_is_searchable(loaded) -> None:
    backend = InMemorySearchBackend()
    Projector(loaded, backend).project_many(list(record_names()))
    response = backend.search(SearchRequest(entitlement=Entitlement.anonymous(), q="reanalysis"))
    assert "ecmwf-era5" in {h.document.id for h in response.hits}


def test_all_ten_domains_are_facetable(loaded) -> None:
    """PRD M4's done-criterion in miniature: a search across all ten domains
    returns correctly faceted results."""
    backend = InMemorySearchBackend()
    Projector(loaded, backend).project_many(list(record_names()))
    response = backend.search(
        SearchRequest(entitlement=Entitlement.anonymous(), facets=("data_domain",))
    )
    notations = {f.label for f in response.facets["data_domain"] if f.label}
    assert len(notations) == 10, f"only {len(notations)} domains faceted: {sorted(notations)}"
    assert sum(f.count for f in response.facets["data_domain"]) >= response.total


def test_a_concept_filter_matches_through_the_hierarchy(loaded) -> None:
    """The payoff of concept_iris_expanded: filter on the parent, match the
    children, with no query-time traversal."""
    backend = InMemorySearchBackend()
    Projector(loaded, backend).project_many(list(record_names()))
    response = backend.search(
        SearchRequest(
            entitlement=Entitlement.anonymous(),
            filters={"concept": [f"{GC}windSpeed"]},
        )
    )
    assert {"ecmwf-era5", "global-wind-atlas", "pypsa-eur-weather-cutouts"} <= {
        h.document.id for h in response.hits
    }
