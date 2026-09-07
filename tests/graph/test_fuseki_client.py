"""`FusekiStore` against a stubbed HTTP endpoint.

The parity suite proves the two backends *behave* the same, and it needs a real
Fuseki to do it. This proves something narrower and complementary: that the
client speaks the protocol — that it posts where it says it posts and can read
back what a SPARQL endpoint actually returns.

Worth having separately because it runs on every commit. The bug below sat in
the production graph backend from M2 and nothing found it: the container job
that would have was collecting zero tests, and every other suite runs against
`RdflibStore`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.graph.graphs import NamedGraph
from datahub.graph.store import FusekiStore
from rdflib import URIRef

SELECT_RESULT = {
    "head": {"vars": ["g", "n"]},
    "results": {
        "bindings": [
            {
                "g": {"type": "uri", "value": "https://schema.opengrid.org/ns#graph/catalog"},
                "n": {
                    "type": "literal",
                    "value": "3",
                    "datatype": "http://www.w3.org/2001/XMLSchema#integer",
                },
            }
        ]
    },
}


@pytest.fixture
def recorded() -> list[httpx.Request]:
    return []


def _store(recorded: list[httpx.Request], response: httpx.Response | None = None) -> FusekiStore:
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        if response is not None:
            return response
        return httpx.Response(
            200,
            json=SELECT_RESULT,
            headers={"Content-Type": "application/sparql-results+json"},
        )

    return FusekiStore(
        "http://fuseki/datahub/query",
        "http://fuseki/datahub/update",
        "http://fuseki/datahub/data",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_a_select_result_can_actually_be_read(recorded) -> None:
    """Every SELECT against Fuseki raised `AttributeError`.

    rdflib's JSON result parser calls `source.read()`. The adapter that used to
    wrap the response body implemented the SAX `InputSource` interface —
    `getByteStream`, `getCharacterStream` — and no `read`, so the parse blew up
    on a perfectly good response. `count`, `graph_names` and every query the API
    makes went through it.
    """
    store = _store(recorded)
    rows = store.select("SELECT ?g ?n WHERE { GRAPH ?g { ?s ?p ?o } }")

    assert rows == [
        {"g": URIRef("https://schema.opengrid.org/ns#graph/catalog"), "n": rows[0]["n"]}
    ]
    assert int(rows[0]["n"]) == 3


def test_count_reads_the_number_out_of_the_response(recorded) -> None:
    """The narrower form of the same thing, on the call the projector makes."""
    assert _store(recorded).count(NamedGraph.CATALOG) == 3


def test_a_query_goes_to_the_query_endpoint_and_an_update_to_the_update_one(recorded) -> None:
    """Swapping them is silent: an update posted to `/query` is a 400 that
    reads like a malformed query, and a query posted to `/update` is a 400 that
    reads like a malformed update."""
    store = _store(recorded)
    store.select("SELECT ?s WHERE { ?s ?p ?o }")
    store.update("INSERT DATA { GRAPH <urn:g> { <urn:s> <urn:p> <urn:o> } }")

    assert [str(r.url) for r in recorded] == [
        "http://fuseki/datahub/query",
        "http://fuseki/datahub/update",
    ]
    assert all(r.method == "POST" for r in recorded)


def test_a_failing_response_names_the_query(recorded) -> None:
    """A 400 from Fuseki says "malformed query" and not which one. The store's
    error carries the query, because the alternative is grepping the source for
    something that might have produced it."""
    from datahub.graph.store import GraphStoreError

    store = _store(recorded, httpx.Response(400, text="Malformed query"))
    with pytest.raises(GraphStoreError) as raised:
        store.select("SELECT ?nope WHERE { ?s ?p ?o }")

    assert "?nope" in str(raised.value)
