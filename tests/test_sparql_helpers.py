from __future__ import annotations

import pytest
from datahub.graph.graphs import AUTHORED_GRAPHS, DERIVED_GRAPHS, NamedGraph, record_graph
from datahub.graph.sparql import bind, iri, n3, prologue, values_clause
from rdflib import URIRef


def test_bind_escapes_literals() -> None:
    out = bind("SELECT * { ?s ?p ??title }", {"title": 'a "quoted" } brace'})
    assert "} brace" in out
    assert out.count("{") == 1  # the literal's brace did not open a group


def test_bind_rejects_unbound_placeholder() -> None:
    with pytest.raises(KeyError, match="missing"):
        bind("SELECT * { ??missing }", {})


def test_iri_rejects_breakout() -> None:
    with pytest.raises(ValueError, match="malformed"):
        iri("urn:a> . ?x ?y ?z . <urn:b")


def test_n3_rejects_unsupported_types() -> None:
    with pytest.raises(TypeError):
        n3(object())


def test_empty_values_clause_matches_nothing() -> None:
    """An empty allow-list is a closed door, not an open one (ADR-0006)."""
    assert values_clause("v", []).strip() == "VALUES ?v { }"


def test_prologue_binds_og() -> None:
    assert "PREFIX og: <https://schema.opengrid.org/ns#>" in prologue("SELECT * {}")


def test_named_graphs_are_distinct_and_angle_bracketed() -> None:
    values = [str(g) for g in NamedGraph]
    assert len(values) == len(set(values))
    assert NamedGraph.CATALOG.sparql().startswith("<https://schema.opengrid.org/ns#graph/")


def test_derived_and_authored_graphs_do_not_overlap() -> None:
    assert not set(DERIVED_GRAPHS) & set(AUTHORED_GRAPHS)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("confirmed", NamedGraph.CATALOG),
        ("draft", NamedGraph.DRAFT),
        ("in-review", NamedGraph.DRAFT),
        ("flagged", NamedGraph.DRAFT),
    ],
)
def test_record_graph_mapping(state: str, expected: NamedGraph) -> None:
    assert record_graph(state) is expected


def test_store_roundtrip(store) -> None:
    store.update(
        "INSERT DATA { GRAPH ??g { <urn:a> <urn:p> 'v' } }",
        {"g": URIRef(str(NamedGraph.CATALOG))},
    )
    assert store.count(NamedGraph.CATALOG) == 1
    rows = store.select(
        "SELECT ?s WHERE { GRAPH ??g { ?s ?p ?o } }",
        {"g": URIRef(str(NamedGraph.CATALOG))},
    )
    assert [str(r["s"]) for r in rows] == ["urn:a"]
    store.drop_graph(NamedGraph.CATALOG)
    assert store.count(NamedGraph.CATALOG) == 0
