"""Record read and write (WP-2.1).

Two properties carry most of the weight here.

**The subgraph boundary.** A record is a dataset node plus what it contains and
nothing that merely links to it. Get that wrong in one direction and writing a
record rewrites its neighbours; wrong in the other and a distribution is
orphaned the first time a record is replaced.

**The round trip.** A record written, read and written again must be the same
record. That sounds trivial and is not: it is where blank nodes, JSON-LD
compaction and datatype coercion each broke this store at least once.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.errors import NotFound, ValidationFailed
from datahub.graph.graphs import NamedGraph
from datahub.graph.loader import bootstrap
from datahub.graph.records import (
    CONTAINMENT_PREDICATES,
    RecordStore,
    containment_terms,
    dataset_node,
)
from datahub.graph.store import RdflibStore
from fixtures.loader import load_record, record_names
from rdflib import URIRef

ERA5 = "https://catalog.opengrid.org/ds/ecmwf-era5"


@pytest.fixture(scope="module")
def loaded():
    store = RdflibStore()
    bootstrap(store)
    records = RecordStore(store)
    for name in record_names():
        records.put(load_record(name))
    return records


# ---- the round trip ------------------------------------------------------


@pytest.mark.parametrize("name", record_names())
def test_a_record_read_back_is_the_record_that_was_written(loaded, name: str) -> None:
    """Isomorphism, not string equality: term order and skolem names are free
    to differ, the triples are not."""
    written = loaded.get_graph(name)
    loaded.put(loaded.get(name))
    assert loaded.get_graph(name).isomorphic(written), f"{name} changed on rewrite"


@pytest.mark.parametrize("name", record_names())
def test_rewriting_does_not_accumulate_triples(loaded, name: str) -> None:
    """The blank-node trap (ADR-0008). ``DELETE DATA`` cannot match a blank
    node, so before skolemisation every rewrite left behind the parts it meant
    to replace and records grew without bound."""
    before = len(loaded.get_graph(name))
    for _ in range(3):
        loaded.put(loaded.get(name))
    assert len(loaded.get_graph(name)) == before


def test_a_rewrite_reports_itself_as_unchanged(loaded) -> None:
    loaded.put(loaded.get(ERA5))
    result = loaded.put(loaded.get(ERA5))
    assert not result.created
    assert result.changed_predicates == set()


# ---- what a record document looks like -----------------------------------


def test_contained_nodes_are_nested_not_referenced(loaded) -> None:
    """A client asking for a dataset's quality flags should get the flags, not
    an IRI it has to go looking for elsewhere in ``@graph``."""
    node = dataset_node(loaded.get(ERA5))
    assert isinstance(node["qualityFlags"], dict)
    assert node["qualityFlags"]["type"] == "QualityFlags"
    assert all(isinstance(d, dict) for d in node["distribution"])


def test_referenced_datasets_stay_references(loaded) -> None:
    """Inlining a neighbour would make one record's document contain another
    record, and a client could not tell which part it is allowed to edit."""
    for name in record_names():
        node = dataset_node(loaded.get(name))
        for term in ("upstreamSource", "supersedes", "supersededBy", "wasDerivedFrom"):
            values = node.get(term)
            if values is None:
                continue
            for value in values if isinstance(values, list) else [values]:
                assert isinstance(value, str), f"{name} inlined a referenced dataset via {term}"


def test_containment_terms_track_the_predicate_list() -> None:
    """The framing must nest exactly what the writer treats as contained; a
    predicate that is containment for writing and reference for reading would
    produce a document that cannot be written back."""
    from datahub.harvest.validate import ValidationRunner

    terms = containment_terms(ValidationRunner().context["@context"])
    assert {"distribution", "hasField", "qualityFlags", "temporal"} <= terms
    assert "upstreamSource" not in terms
    assert len(terms) <= len(CONTAINMENT_PREDICATES)


def test_the_dataset_node_comes_first(loaded) -> None:
    document = loaded.get(ERA5)
    assert document["@graph"][0]["type"] == "Dataset"


def test_the_context_is_a_url_not_an_inlined_copy(loaded) -> None:
    document = loaded.get(ERA5)
    assert isinstance(document["@context"], str)
    assert document["@context"].endswith("/context/opengrid-datahub.jsonld")


def test_identifier_values_are_absolute_iris(loaded) -> None:
    """rdflib compacts an IRI to a CURIE wherever the context declares a
    matching prefix, so a licence would come back as ``spdx:CC-BY-4.0`` while a
    data domain came back in full. A client should not have to handle both."""
    for name in record_names():
        node = dataset_node(loaded.get(name))
        for term in ("license", "dataDomain", "provenanceClass", "accessRestriction"):
            values = node.get(term)
            for value in values if isinstance(values, list) else [values] if values else []:
                assert value.startswith(("http://", "https://")), f"{name}.{term} = {value}"


def test_multi_valued_language_tagged_terms_compact(loaded) -> None:
    """rdflib cannot compact a term that is both ``@container: @set`` and
    ``@language``; it emits the raw CURIE with expanded values. These are the
    multi-valued human-readable fields, so the defect is directly visible to a
    reader."""
    flags = dataset_node(loaded.get(ERA5))["qualityFlags"]
    assert "og:caveat" not in flags, "the serialiser's expanded form leaked into the API"
    assert isinstance(flags["caveat"], list)
    assert all(isinstance(c, str) for c in flags["caveat"])


def test_booleans_are_booleans(loaded) -> None:
    """``"false"`` is truthy in Python and in JavaScript, so a boolean that
    round-trips as a string is a correctness bug, not a cosmetic one."""
    for name in record_names():
        node = dataset_node(loaded.get(name))
        for term in ("anonymousAccess", "geospatialPrimary", "referenceOnly"):
            if term in node:
                assert isinstance(node[term], bool), f"{name}.{term}"


def test_numbers_are_numbers(loaded) -> None:
    for name in record_names():
        node = dataset_node(loaded.get(name))
        if "completenessLevel" in node:
            assert isinstance(node["completenessLevel"], int)
        if "bboxMinLon" in node:
            assert isinstance(node["bboxMinLon"], float)


# ---- the subgraph boundary ----------------------------------------------


def test_writing_a_record_leaves_its_neighbours_alone(loaded) -> None:
    """The failure this boundary exists to prevent: a record whose subgraph
    reached through ``upstreamSource`` would rewrite the dataset it cites.

    ``pypsa-eur-weather-cutouts`` names ERA5 as its upstream and ERA5 is itself
    catalogued, so the two records are exactly the pair that would collide.
    """
    cutouts = "https://catalog.opengrid.org/ds/pypsa-eur-weather-cutouts"
    assert ERA5 in dataset_node(loaded.get(cutouts))["upstreamSource"]

    before = loaded.get_graph(ERA5)
    loaded.put(loaded.get(cutouts))

    assert loaded.get_graph(ERA5).isomorphic(before)
    assert len(loaded.get_graph(ERA5)) > 0, "the neighbour was not emptied either"


def test_an_uncatalogued_upstream_node_is_written_as_ancillary(loaded) -> None:
    """An upstream that is not itself a dataset — ``upstream/gwa-wrf-mesoscale``
    — has to be written by whoever cites it, or Q1's shared-origin detection
    has nothing to join on. It is merged, never replaced: two records may cite
    the same intermediate."""
    upstream = URIRef("https://catalog.opengrid.org/upstream/gwa-wrf-mesoscale")
    assert loaded.store.ask(
        "ASK { GRAPH ??g { ??s ?p ?o } }",
        {"g": URIRef(str(NamedGraph.CATALOG)), "s": upstream},
    )


def test_deleting_a_record_takes_its_parts_with_it(loaded) -> None:
    document = loaded.get(ERA5)
    distribution = URIRef(dataset_node(document)["distribution"][0]["id"])

    loaded.delete(ERA5)

    assert not loaded.exists(ERA5)
    assert not loaded.store.ask(
        "ASK { GRAPH ??g { ??s ?p ?o } }",
        {"g": URIRef(str(NamedGraph.CATALOG)), "s": distribution},
    ), "a distribution outlived the record that owned it"
    loaded.put(document)


def test_reading_an_absent_record_raises(loaded) -> None:
    with pytest.raises(NotFound):
        loaded.get("https://catalog.opengrid.org/ds/not-a-real-dataset")


# ---- validation and placement -------------------------------------------


def test_a_record_that_fails_validation_is_not_written(loaded) -> None:
    document = loaded.get(ERA5)
    node = dataset_node(document)
    node["license"] = "CC BY 4.0"  # free text, not an IRI

    with pytest.raises(ValidationFailed) as raised:
        loaded.put(document)

    assert raised.value.violations
    assert dataset_node(loaded.get(ERA5))["license"].startswith("http")


def test_review_state_decides_the_graph(loaded) -> None:
    document = loaded.get(ERA5)
    dataset_node(document)["reviewState"] = "draft"
    dataset_node(document)["id"] = f"{ERA5}-draft-copy"
    for dist in dataset_node(document)["distribution"]:
        dist["id"] = dist["id"].replace("--", "-draft--")
    dataset_node(document)["qualityFlags"]["id"] = f"{ERA5}-draft-copy#flags"

    loaded.put(document)

    assert loaded.graph_of(f"{ERA5}-draft-copy") is NamedGraph.DRAFT
    loaded.delete(f"{ERA5}-draft-copy")


def test_validation_can_be_skipped_only_explicitly(loaded) -> None:
    """A bulk load may need to defer validation; it must have to say so.

    Claiming level 3 on a level 1 record is the exact dishonesty the levels
    exist to prevent (PRD §6): level 3 requires unit IRIs and concept
    resolution on every field, and this record has no fields at all.
    """
    minimal = "https://catalog.opengrid.org/ds/eia-natural-gas-prices"
    document = loaded.get(minimal)
    dataset_node(document)["completenessLevel"] = 3

    with pytest.raises(ValidationFailed) as raised:
        loaded.put(document)
    assert raised.value.context["target_level"] == 3

    loaded.put(document, validate=False)
    assert dataset_node(loaded.get(minimal))["completenessLevel"] == 3

    loaded.put(load_record("eia-natural-gas-prices"))
    assert dataset_node(loaded.get(minimal))["completenessLevel"] == 1
