from __future__ import annotations

import pytest
from datahub.graph.graphs import AUTHORED_GRAPHS, DERIVED_GRAPHS, NamedGraph, record_graph
from datahub.graph.sparql import bind, iri, n3, placeholders, prologue, values_clause
from rdflib import URIRef


def test_bind_escapes_literals() -> None:
    out = bind("SELECT * { ?s ?p ??title }", {"title": 'a "quoted" } brace'})
    assert "} brace" in out
    assert out.count("{") == 1  # the literal's brace did not open a group


def test_bind_rejects_unbound_placeholder() -> None:
    with pytest.raises(KeyError, match="missing"):
        bind("SELECT * { ??missing }", {})


def test_a_placeholder_inside_data_is_data() -> None:
    """The regression that cost a whole harvest.

    Ground triples cannot be bound as parameters, so `INSERT DATA` is assembled
    by serialising them into the update text — and the finished update still
    goes through `bind` on its way to the store. Scanning for `??name` rather
    than tokenising meant `bind` read the *record* looking for placeholders.

    Three NASA datasets in the AWS registry carry the mojibake `world'??s` in
    their descriptions. Each was rejected with
    `unbound SPARQL placeholders: ['s']`, and because a source reporting errors
    made the harvest exit non-zero, the 521 records that had normalised fine in
    the same run never reached promotion, export or the pull request.
    """
    update = (
        'INSERT DATA { GRAPH <urn:g> { <urn:a> <urn:p> "the world\'??s natural vegetation" . } }'
    )
    assert placeholders(update) == []
    assert bind(update) == update


def test_a_real_placeholder_survives_a_literal_that_looks_like_one() -> None:
    """The other half: tokenising must not blind `bind` to actual work."""
    out = bind('SELECT * { GRAPH ??g { ?s ?p "not a ??placeholder" } }', {"g": URIRef("urn:g")})
    assert "<urn:g>" in out
    assert '"not a ??placeholder"' in out


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        # A fragment IRI is not a comment, and a query IRI is not a placeholder.
        ("SELECT * { GRAPH <https://x/ns#g> { ??s ?p ?o } }", ["s"]),
        ("SELECT * { <http://x/a??b> ?p ??o }", ["o"]),
        # A comment is not a query.
        ("# ??note\nSELECT * { ??s ?p ?o }", ["s"]),
        # `<` as less-than must not be eaten as an IRI, taking the rest with it.
        ("SELECT * { ?s ?p ?o FILTER(?o < 3 && ?o > ??n) }", ["n"]),
        # Long literals close on three quotes, not one.
        ('SELECT * { ?s ?p """a ?? b "c" d""" . ?s ?q ??v }', ["v"]),
    ],
)
def test_placeholders_are_read_from_the_query_not_the_text(
    template: str, expected: list[str]
) -> None:
    assert placeholders(template) == expected


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
