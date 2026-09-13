"""The place scheme: small, externally identified, and careful about ISO.

A reference network is a network *of somewhere*, so `dct:spatial` is the key
the Reference Models section is entered by (#97). That makes this scheme load
bearing in a way a label never was: a picker keyed on `og:spatialLabel` offers
"Great Britain", "GB" and "Britain" as three choices, and only an IRI says they
are one place.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rdflib import Graph, URIRef
from rdflib.namespace import SKOS

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEME = URIRef("https://schema.opengrid.org/concept/place")
WIKIDATA = "http://www.wikidata.org/entity/"


@pytest.fixture(scope="module")
def places() -> Graph:
    graph = Graph()
    graph.parse((REPO_ROOT / "vocab" / "og-place.ttl").as_posix(), format="turtle")
    return graph


def concepts(graph: Graph) -> list[URIRef]:
    return sorted(graph.subjects(SKOS.inScheme, SCHEME), key=str)  # type: ignore[arg-type]


def test_every_concept_is_in_the_scheme_and_labelled(places: Graph) -> None:
    found = concepts(places)
    assert found, "the scheme has no concepts"
    for concept in found:
        assert places.value(concept, SKOS.prefLabel), f"{concept} has no prefLabel"
        assert places.value(concept, SKOS.definition), f"{concept} has no definition"


def test_every_place_is_identified_outside_this_repository(places: Graph) -> None:
    """A concept with no external identifier is a private string with an IRI on.

    Wikidata is the authority here because it is the one that answers what a
    place *is* — borders, names, relationships — which this scheme deliberately
    does not restate.
    """
    for concept in concepts(places):
        matches = [str(m) for m in places.objects(concept, SKOS.exactMatch)]
        assert matches, f"{concept} matches nothing outside this repository"
        assert all(m.startswith(WIKIDATA) for m in matches), f"{concept}: {matches}"


def test_great_britain_is_not_filed_under_the_united_kingdom(places: Graph) -> None:
    """The over-claim this scheme exists to refuse.

    ISO 3166-1 "GB" is the United Kingdom of Great Britain and Northern
    Ireland. The reference network filed under Great Britain excludes Northern
    Ireland — deliberately, because it is a separate synchronous area operating
    with Ireland. Filing the island under the state's code would offer a reader
    "United Kingdom" in a picker and hand them a network with a nation missing,
    and they would have no way to notice.

    So the notation is absent, and this asserts the absence rather than
    trusting a comment: `skos:notation` carries ISO only where the concept is
    that country, and a future edit that "completes" the record by adding "GB"
    fails here with the reason.
    """
    britain = URIRef("https://schema.opengrid.org/concept/place/greatBritain")
    assert britain in set(concepts(places))
    assert places.value(britain, SKOS.notation) is None, (
        "Great Britain carries an ISO 3166-1 code. It is not a country in that "
        "scheme — 'GB' is the United Kingdom, which includes the Northern "
        "Ireland this model excludes."
    )
    assert places.value(britain, SKOS.scopeNote), "the exclusion needs its reason recorded"

    germany = URIRef("https://schema.opengrid.org/concept/place/germany")
    assert str(places.value(germany, SKOS.notation)) == "DE", (
        "Germany is exactly ISO 'DE' and should say so; the point is that the "
        "code is carried where it is true, not that it is never carried"
    )


def test_every_place_a_record_names_exists_in_the_scheme(places: Graph) -> None:
    """The join has to resolve, or the picker is keyed on nothing.

    A record may name a place IRI the scheme has never heard of and nothing
    downstream complains: it projects, it facets, it filters, and it renders as
    a token with no label behind it. Checked over the committed corpus for the
    same reason the undefined-term guard is.
    """
    known = {str(concept) for concept in concepts(places)}
    prefix = "https://schema.opengrid.org/concept/place/"
    named: dict[str, list[str]] = {}

    fixtures = REPO_ROOT / "tests" / "fixtures"
    for path in sorted(fixtures.glob("records/*.jsonld")) + sorted(
        fixtures.glob("registry/*.jsonld")
    ):
        for node in json.loads(path.read_text()).get("@graph", []):
            for iri in node.get("spatial", []):
                if iri.startswith(prefix) and iri not in known:
                    named.setdefault(iri, []).append(path.name)

    assert not named, "\n".join(
        f"{iri} is named by {sorted(set(where))} and is not in vocab/og-place.ttl"
        for iri, where in sorted(named.items())
    )
