"""Behaviour every backend must share, written once.

ADR-0002 makes the backends interchangeable: RdflibStore or Fuseki, an
in-process index or OpenSearch, SQLite or Postgres. "Interchangeable" is a
claim about behaviour, and a claim nobody checks is a hope.

Nothing here is a test. These are assertions taken as functions so the *same*
ones run twice: against the in-process backends in the default suite, where
they are cheap and always run, and against real services in the
container-backed job. That is what makes them trustworthy — the assertions are
exercised on every commit, and the integration job adds only the wiring.

The alternative, a separate suite that only ever runs against containers, is
how the integration job came to run zero tests without anyone noticing: nobody
saw it fail, because nobody saw it do anything.
"""

from __future__ import annotations

from typing import Any

from datahub.api.search.backend import Entitlement, SearchRequest, SortSpec
from datahub.api.search.document import SearchDocument
from datahub.graph.graphs import NamedGraph
from rdflib import Graph, Literal, URIRef

OG = "https://schema.opengrid.org/ns#"
DS = "https://catalog.opengrid.org/ds/"


# ---------------------------------------------------------------------------
# Graph stores
# ---------------------------------------------------------------------------


def _triple(slug: str, predicate: str, value: str) -> tuple[Any, Any, Any]:
    return (URIRef(f"{DS}{slug}"), URIRef(f"{OG}{predicate}"), Literal(value))


def assert_graph_store_round_trips(store: Any) -> None:
    """Write a named graph, read it back, and see the same triples."""
    graph = Graph()
    graph.add(_triple("parity-a", "title", "A"))
    graph.add(_triple("parity-a", "summary", "first"))
    store.put_graph(NamedGraph.CATALOG, graph)

    back = store.get_graph(NamedGraph.CATALOG)
    assert _triple("parity-a", "title", "A") in back
    assert _triple("parity-a", "summary", "first") in back


def assert_graph_store_replaces_rather_than_merges(store: Any) -> None:
    """`put_graph` is a replacement.

    The distinction matters more than it looks: a store that merged would leave
    a corrected record carrying both the old value and the new, and every
    downstream reader would pick whichever the query happened to return first.
    """
    first = Graph()
    first.add(_triple("parity-b", "title", "before"))
    store.put_graph(NamedGraph.DRAFT, first)

    second = Graph()
    second.add(_triple("parity-b", "title", "after"))
    store.put_graph(NamedGraph.DRAFT, second)

    back = store.get_graph(NamedGraph.DRAFT)
    assert _triple("parity-b", "title", "after") in back
    assert _triple("parity-b", "title", "before") not in back


def assert_graph_store_isolates_named_graphs(store: Any) -> None:
    """A write to one graph is invisible in another.

    The draft/catalog split is the publication boundary — an unconfirmed record
    leaking into the catalog graph is a record published without review.
    """
    catalog, draft = Graph(), Graph()
    catalog.add(_triple("parity-c", "title", "published"))
    draft.add(_triple("parity-d", "title", "unpublished"))
    store.put_graph(NamedGraph.CATALOG, catalog)
    store.put_graph(NamedGraph.DRAFT, draft)

    assert _triple("parity-d", "title", "unpublished") not in store.get_graph(NamedGraph.CATALOG)
    assert _triple("parity-c", "title", "published") not in store.get_graph(NamedGraph.DRAFT)


def assert_graph_store_counts_and_drops(store: Any) -> None:
    graph = Graph()
    graph.add(_triple("parity-e", "title", "E"))
    store.put_graph(NamedGraph.CATALOG, graph)
    assert store.count(NamedGraph.CATALOG) >= 1

    store.drop_graph(NamedGraph.CATALOG)
    assert store.count(NamedGraph.CATALOG) == 0


GRAPH_ASSERTIONS = (
    assert_graph_store_round_trips,
    assert_graph_store_replaces_rather_than_merges,
    assert_graph_store_isolates_named_graphs,
    assert_graph_store_counts_and_drops,
)


# ---------------------------------------------------------------------------
# Search backends
# ---------------------------------------------------------------------------


def _doc(id_: str, title: str, **kw: Any) -> SearchDocument:
    # `review_state` defaults to "draft", and search excludes unconfirmed
    # records unless asked for them — correctly, since the draft graph is the
    # pre-publication one. A parity corpus of drafts is a corpus that is
    # invisible to every query, which is a confusing way for these assertions
    # to fail.
    kw.setdefault("review_state", "confirmed")
    return SearchDocument(id=id_, iri=f"{DS}{id_}", title=title, **kw)


CORPUS = [
    _doc("p-wind", "Global Wind Atlas", license_id="CC-BY-4.0", provenance_class="curated"),
    _doc("p-solar", "Solar Resource Atlas", license_id="CC-BY-4.0", provenance_class="modeled"),
    _doc("p-load", "Hourly Load Series", license_id="ODbL-1.0", provenance_class="primary"),
]


def _seed(backend: Any) -> None:
    backend.index(CORPUS)
    backend.flush()


def assert_search_finds_by_text(backend: Any) -> None:
    _seed(backend)
    hits = backend.search(SearchRequest(q="wind", entitlement=Entitlement.anonymous())).hits
    assert [h.document.id for h in hits] == ["p-wind"]


def assert_search_filters_exactly(backend: Any) -> None:
    _seed(backend)
    response = backend.search(
        SearchRequest(filters={"license": ["CC-BY-4.0"]}, entitlement=Entitlement.anonymous())
    )
    assert {h.document.id for h in response.hits} == {"p-wind", "p-solar"}


def assert_search_facets_count_the_whole_result_set(backend: Any) -> None:
    """Facet counts describe the matching set, not the returned page.

    A backend that counted only the page would make the filter panel disagree
    with the result count, and the disagreement would grow with the catalog.
    """
    _seed(backend)
    response = backend.search(
        SearchRequest(facets=("license",), limit=1, entitlement=Entitlement.anonymous())
    )
    counts = {b.value: b.count for b in response.facets["license"]}
    assert counts["CC-BY-4.0"] == 2
    assert counts["ODbL-1.0"] == 1


def assert_search_sorts_by_title(backend: Any) -> None:
    """Every sortable field must actually sort on every backend.

    `title` is the one that broke: it is an analysed text field in the
    OpenSearch mapping, which cannot be sorted without a keyword sub-field, so
    `?sort=title` is a 500 there and fine in process.
    """
    _seed(backend)
    response = backend.search(
        SearchRequest(sort=(SortSpec(field="title"),), entitlement=Entitlement.anonymous())
    )
    titles = [h.document.title for h in response.hits]
    assert titles == sorted(titles)


def assert_search_total_is_the_match_count_not_the_page(backend: Any) -> None:
    _seed(backend)
    response = backend.search(SearchRequest(limit=1, entitlement=Entitlement.anonymous()))
    assert response.total == len(CORPUS)
    assert len(response.hits) == 1


SEARCH_ASSERTIONS = (
    assert_search_finds_by_text,
    assert_search_filters_exactly,
    assert_search_facets_count_the_whole_result_set,
    assert_search_sorts_by_title,
    assert_search_total_is_the_match_count_not_the_page,
)


def assert_graph_store_removes_only_what_it_is_given(store: Any) -> None:
    """`remove_graph` retracts specific triples and leaves the rest alone.

    The mirror of `add_graph`, and the operation the semantic runner needs to
    retract computed state. It used to reach for `get_graph` and remove from the
    copy that came back, which discarded the retraction silently.
    """
    graph = Graph()
    graph.add(_triple("parity-f", "grade", "D"))
    graph.add(_triple("parity-f", "title", "keep me"))
    store.put_graph(NamedGraph.COMPUTED, graph)

    retract = Graph()
    retract.add(_triple("parity-f", "grade", "D"))
    store.remove_graph(NamedGraph.COMPUTED, retract)

    back = store.get_graph(NamedGraph.COMPUTED)
    assert _triple("parity-f", "grade", "D") not in back
    assert _triple("parity-f", "title", "keep me") in back


def assert_removing_an_absent_triple_is_not_an_error(store: Any) -> None:
    """A retraction that already happened is the state the caller wanted."""
    absent = Graph()
    absent.add(_triple("parity-g", "grade", "never written"))
    store.remove_graph(NamedGraph.COMPUTED, absent)


GRAPH_ASSERTIONS = (
    *GRAPH_ASSERTIONS,
    assert_graph_store_removes_only_what_it_is_given,
    assert_removing_an_absent_triple_is_not_an_error,
)
