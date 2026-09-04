"""``/v1/concepts`` and ``/v1/domains`` (WP-4.4)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---- domains -------------------------------------------------------------


def test_all_ten_domains_are_returned(client) -> None:
    body = client.get("/v1/domains").json()
    assert len(body) == 10
    assert {d["id"] for d in body} == {f"DD{n}" for n in range(1, 11)}


def test_domains_come_back_in_their_own_order(client) -> None:
    """DD1 through DD10 is the ordering the PRD gives them. "DD10, DD1, DD2" is
    nobody's idea of a list of ten things."""
    body = client.get("/v1/domains").json()
    assert [d["id"] for d in body] == [f"DD{n}" for n in range(1, 11)]


def test_every_domain_carries_its_structural_note(client) -> None:
    """PRD §5 treats these as a product feature, not a disclaimer. A catalog
    that says "transmission line impedances for most of the world are not
    public, and here is why" is more useful than one that returns an empty list
    and lets the user conclude they searched badly."""
    body = client.get("/v1/domains").json()
    for domain in body:
        assert domain["structural_note"], domain["id"]
        assert len(domain["structural_note"]) > 80


def test_domain_counts_come_from_the_index(client) -> None:
    """Entitlement-scoped, so a count cannot confirm a record the caller may
    not see."""
    body = client.get("/v1/domains").json()
    total_in_search = client.get("/v1/datasets", params={"limit": 0}).json()["total"]

    counted = sum(d["dataset_count"] for d in body)
    assert counted >= total_in_search, "a dataset in two domains counts in both"
    assert any(d["dataset_count"] > 0 for d in body)


# ---- concepts ------------------------------------------------------------


def test_the_concept_list_is_paged_and_filterable(client) -> None:
    everything = client.get("/v1/concepts", params={"limit": 1000}).json()
    domains_only = client.get(
        "/v1/concepts",
        params={"scheme": "https://schema.opengrid.org/concept/data-domain", "limit": 1000},
    ).json()

    assert len(everything) > 150
    assert len(domains_only) == 10
    assert len(domains_only) < len(everything)


def test_a_label_search_narrows_the_list(client) -> None:
    body = client.get("/v1/concepts", params={"q": "capacity", "limit": 100}).json()
    assert body
    assert all("capacity" in c["label"].lower() for c in body)


def test_one_concept_carries_its_hierarchy(client) -> None:
    body = client.get("/v1/concepts/activePower").json()

    assert body["label"]
    assert body["iri"].endswith("/activePower")
    assert body["broader"] or body["narrower"]


def test_a_concept_can_be_named_by_its_full_iri(client) -> None:
    by_tail = client.get("/v1/concepts/activePower").json()
    by_iri = client.get(
        "/v1/concepts/https://schema.opengrid.org/concept/grid-concept/activePower"
    ).json()
    assert by_tail["iri"] == by_iri["iri"]


def test_crosswalks_are_grouped_by_match_strength(client) -> None:
    """X2 makes the strength load-bearing: an exactMatch says two concepts are
    interchangeable and a closeMatch says they are not. A client that saw one
    flat list would treat them alike, which is the error Q5 exists to catch."""
    for tail in ("activePower", "reactivePower", "nominalVoltage"):
        body = client.get(f"/v1/concepts/{tail}").json()
        if body["external_matches"]:
            assert set(body["external_matches"]) <= {
                "exactMatch",
                "closeMatch",
                "broadMatch",
                "narrowMatch",
            }
            return
    raise AssertionError("no crosswalked concept found; the crosswalks are not being read")


def test_an_unknown_concept_is_a_404(client) -> None:
    response = client.get("/v1/concepts/notAConcept")
    assert response.status_code == 404


def test_an_ambiguous_short_name_is_refused_not_guessed(client, loaded) -> None:
    """Two schemes can legitimately use the same last segment. Picking one
    silently would make the endpoint return a different concept depending on
    load order."""
    from datahub.graph.graphs import NamedGraph
    from rdflib import Literal, URIRef
    from rdflib.namespace import RDF, SKOS

    graph = loaded.store.dataset.graph(URIRef(str(NamedGraph.VOCAB)))
    for base in (
        "https://schema.opengrid.org/concept/scheme-a/",
        "https://schema.opengrid.org/concept/scheme-b/",
    ):
        iri = URIRef(f"{base}ambiguous")
        graph.add((iri, RDF.type, SKOS.Concept))
        graph.add((iri, SKOS.prefLabel, Literal("Ambiguous", lang="en")))

    response = client.get("/v1/concepts/ambiguous")

    assert response.status_code == 404
    assert "full IRI" in response.json()["title"]
