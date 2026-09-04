"""Entailment materialisation, and the PRD §4.6 Q3 property it serves.

These are the M1 storage-risk tests. PRD §10: before committing past M1, load
the vocabulary, run the queries, and treat a negative result as a successful
experiment rather than as something to work around.
"""

from __future__ import annotations

import pytest
from datahub.graph.graphs import NamedGraph
from datahub.graph.loader import bootstrap, load_vocabularies, recorded_checksum
from datahub.graph.reason import RULES, is_stale, materialize
from datahub.graph.store import RdflibStore
from rdflib import URIRef

GC = "https://schema.opengrid.org/concept/grid-concept/"
RENEWABLE = URIRef(GC + "renewableResource")


@pytest.fixture
def loaded(settings):
    """A store with the real vocabularies loaded and entailments materialised."""
    store = RdflibStore()
    bootstrap(store, settings)
    return store


# ---- loading ------------------------------------------------------------


def test_bootstrap_loads_vocabulary_and_shapes(loaded) -> None:
    assert loaded.count(NamedGraph.VOCAB) > 2000
    assert loaded.count(NamedGraph.SHAPES) > 500
    assert loaded.count(NamedGraph.INFERRED) > 100


def test_each_crosswalk_gets_its_own_graph(loaded) -> None:
    """One graph per crosswalk, so the Q5 audit can tell which scheme made a
    claim. Merging them lets one scheme's closeMatch justification appear to
    qualify another scheme's exactMatch."""
    from datahub.graph.reason import vocabulary_graphs

    graphs = vocabulary_graphs(loaded)
    assert str(NamedGraph.VOCAB) in graphs
    schemes = {g.rsplit("/", 1)[-1] for g in graphs if "crosswalk" in g}
    assert schemes == {"cim-cgmes", "pypsa", "matpower", "sienna"}
    for graph in graphs:
        assert loaded.count(graph) > 0, f"{graph} is empty"


def test_the_exact_match_bridge_spans_schemes(loaded) -> None:
    """A CIM attribute and a PyPSA attribute that are each identical to the same
    OpenGrid concept are identical to each other. That bridge is the reason the
    reasoner reads across crosswalk graphs rather than one at a time."""
    rows = loaded.select(
        """
        SELECT ?a ?b WHERE {
          GRAPH ??inf { ?a og:resolvesTo ?b }
          FILTER (CONTAINS(STR(?a), "iec.ch") && CONTAINS(STR(?b), "crosswalk/pypsa"))
        }
        """,
        {"inf": NamedGraph.INFERRED.uri()},
    )
    assert rows, "no CIM term resolves to a PyPSA term through a shared concept"


def test_a_crosswalk_edit_makes_materialisation_stale(loaded) -> None:
    """A crosswalk edit changes what the bridge entails, so it must invalidate
    materialisation exactly as a concept edit does."""
    from datahub.graph.reason import materialize as rematerialize

    assert is_stale(loaded) is False
    loaded.update(
        "INSERT DATA { GRAPH ??g { ??c skos:exactMatch <urn:ext:novel> } }",
        {
            "g": URIRef(NamedGraph.crosswalk("pypsa")),
            "c": URIRef(GC + "windSpeed"),
        },
    )
    assert is_stale(loaded) is True
    rematerialize(loaded)
    assert is_stale(loaded) is False


def test_loading_twice_is_a_no_op(loaded, settings) -> None:
    """The crosswalks carry blank-node gap markers, which get fresh labels on
    every parse. A checksum that did not canonicalise would report a change
    every time and re-materialise for nothing."""
    before = loaded.count(NamedGraph.VOCAB)
    result = load_vocabularies(loaded, settings)
    assert result.changed is False
    assert loaded.count(NamedGraph.VOCAB) == before


def test_checksum_is_recorded(loaded) -> None:
    assert recorded_checksum(loaded) is not None


# ---- PRD §4.6 Q3 --------------------------------------------------------


def test_q3_returns_narrower_concepts_with_no_enumeration(loaded) -> None:
    """The M1 done-criterion: 'Q3 (concept inference) returns narrower concepts
    with no enumeration anywhere in code.'

    The query below is the PRD's, run against the real store. Nothing in this
    test or in the query lists what counts as a renewable resource; the SKOS
    hierarchy does.
    """
    rows = loaded.select(
        """
        SELECT ?concept WHERE {
          GRAPH ??vocab { ?concept skos:broader+ ??parent }
        }
        """,
        {"vocab": NamedGraph.VOCAB.uri(), "parent": RENEWABLE},
    )
    returned = {str(r["concept"]) for r in rows}
    for expected in ("globalHorizontalIrradiance", "windSpeed", "hydroInflow"):
        assert GC + expected in returned, f"Q3 did not return {expected}"
    assert len(returned) > 10


def test_q3_via_materialised_closure_agrees_with_the_property_path(loaded) -> None:
    """The projector reads the materialised closure rather than walking the
    path on every query. If the two disagree, the index is wrong in a way no
    search test would catch."""
    path_rows = loaded.select(
        "SELECT ?c WHERE { GRAPH ??vocab { ?c skos:broader+ ??parent } }",
        {"vocab": NamedGraph.VOCAB.uri(), "parent": RENEWABLE},
    )
    closure_rows = loaded.select(
        "SELECT ?c WHERE { GRAPH ??inf { ?c og:broaderTransitive ??parent } }",
        {"inf": NamedGraph.INFERRED.uri(), "parent": RENEWABLE},
    )
    assert {str(r["c"]) for r in path_rows} == {str(r["c"]) for r in closure_rows}


def test_adding_a_concept_makes_q3_return_more_with_no_code_change(loaded) -> None:
    """PRD §4.6 Q3: 'Add a concept next year and every existing query picks it
    up.' Asserted by inserting one and re-running the identical query."""
    query = "SELECT ?c WHERE { GRAPH ??vocab { ?c skos:broader+ ??parent } }"
    params = {"vocab": NamedGraph.VOCAB.uri(), "parent": RENEWABLE}
    before = {str(r["c"]) for r in loaded.select(query, params)}

    novel = URIRef(GC + "osmoticPressureGradient")
    loaded.update(
        """
        INSERT DATA {
          GRAPH ??vocab {
            ??novel a skos:Concept ;
                    skos:prefLabel "Osmotic pressure gradient"@en ;
                    skos:broader ??parent .
          }
        }
        """,
        {"vocab": NamedGraph.VOCAB.uri(), "novel": novel, "parent": RENEWABLE},
    )
    after = {str(r["c"]) for r in loaded.select(query, params)}
    assert after == before | {str(novel)}


def test_a_direct_store_edit_makes_materialisation_stale(loaded) -> None:
    """A vocabulary edit must be detectable however it arrived.

    Fuseki accepts SPARQL Update against any graph, so a concept added straight
    to the store — which is what a hurried vocabulary fix looks like — must
    still trip the staleness check. Comparing only the checksum the loader
    recorded would miss exactly this case.
    """
    assert is_stale(loaded) is False
    loaded.update(
        """
        INSERT DATA {
          GRAPH ??vocab {
            ??novel a skos:Concept ; skos:prefLabel "New"@en ; skos:broader ??parent .
          }
        }
        """,
        {
            "vocab": NamedGraph.VOCAB.uri(),
            "novel": URIRef(GC + "novelResource"),
            "parent": RENEWABLE,
        },
    )
    assert is_stale(loaded) is True
    materialize(loaded)
    assert is_stale(loaded) is False


def test_fast_staleness_check_is_honest_about_what_it_misses(loaded) -> None:
    """fast=True compares recorded values and cannot see a direct store edit.
    Asserted so the limitation is a documented property rather than a
    surprise."""
    loaded.update(
        "INSERT DATA { GRAPH ??vocab { ??novel a skos:Concept ; skos:broader ??parent } }",
        {
            "vocab": NamedGraph.VOCAB.uri(),
            "novel": URIRef(GC + "sneaked"),
            "parent": RENEWABLE,
        },
    )
    assert is_stale(loaded, fast=True) is False
    assert is_stale(loaded) is True


def test_a_file_change_makes_materialisation_stale(loaded, settings, tmp_path) -> None:
    """The ordinary path: someone edits vocab/ and reloads."""
    from datahub.graph.loader import load_vocabularies as reload

    assert is_stale(loaded) is False
    loaded.update(
        "INSERT DATA { GRAPH ??vocab { ??novel a skos:Concept ; skos:broader ??parent } }",
        {
            "vocab": NamedGraph.VOCAB.uri(),
            "novel": URIRef(GC + "fromFile"),
            "parent": RENEWABLE,
        },
    )
    # Reloading from files reverts the store to what vocab/ says, which is a
    # change relative to what was materialised a moment ago.
    reload(loaded, settings, force=True)
    assert is_stale(loaded) is False, (
        "reloading unchanged files should restore the materialised state"
    )


# ---- the closeMatch discipline, enforced in the reasoner ----------------


def test_close_match_is_not_transitively_closed(loaded) -> None:
    """PRD X2: closeMatch is not transitive, and chaining it produces mappings
    nobody would assert directly. The reasoner bridges only exactMatch.

    Constructed rather than asserted from the shipped crosswalks, so the test
    proves the rule rather than the current data.
    """
    a, b, concept = URIRef("urn:ext:a"), URIRef("urn:ext:b"), URIRef(GC + "windSpeed")
    loaded.update(
        """
        INSERT DATA {
          GRAPH ??vocab {
            ??concept skos:closeMatch ??a , ??b .
          }
        }
        """,
        {"vocab": NamedGraph.VOCAB.uri(), "concept": concept, "a": a, "b": b},
    )
    materialize(loaded)
    bridged = loaded.ask(
        "ASK { GRAPH ??inf { ??a og:resolvesTo ??b } }",
        {"inf": NamedGraph.INFERRED.uri(), "a": a, "b": b},
    )
    assert bridged is False, "closeMatch was bridged; that is the X2 hazard"


def test_exact_match_is_bridged(loaded) -> None:
    """The other half: two external terms that are each identical to the same
    OpenGrid concept are identical to each other."""
    a, b, concept = URIRef("urn:ext:x"), URIRef("urn:ext:y"), URIRef(GC + "windSpeed")
    loaded.update(
        "INSERT DATA { GRAPH ??vocab { ??concept skos:exactMatch ??a , ??b } }",
        {"vocab": NamedGraph.VOCAB.uri(), "concept": concept, "a": a, "b": b},
    )
    materialize(loaded)
    assert loaded.ask(
        "ASK { GRAPH ??inf { ??a og:resolvesTo ??b } }",
        {"inf": NamedGraph.INFERRED.uri(), "a": a, "b": b},
    )


# ---- rebuild semantics --------------------------------------------------


def test_materialisation_is_idempotent(loaded) -> None:
    first = loaded.count(NamedGraph.INFERRED)
    materialize(loaded)
    assert loaded.count(NamedGraph.INFERRED) == first


def test_materialisation_drops_stale_entailments(loaded) -> None:
    """A removed concept's entailments must not survive. This is the property
    that makes drop-and-rebuild the right shape: a surgical update would leave
    them."""
    ghi = URIRef(GC + "globalHorizontalIrradiance")
    assert loaded.ask(
        "ASK { GRAPH ??inf { ??ghi og:broaderTransitive ??parent } }",
        {"inf": NamedGraph.INFERRED.uri(), "ghi": ghi, "parent": RENEWABLE},
    )
    loaded.update(
        "DELETE WHERE { GRAPH ??vocab { ??ghi skos:broader ?parent } }",
        {"vocab": NamedGraph.VOCAB.uri(), "ghi": ghi},
    )
    materialize(loaded)
    assert not loaded.ask(
        "ASK { GRAPH ??inf { ??ghi og:broaderTransitive ??parent } }",
        {"inf": NamedGraph.INFERRED.uri(), "ghi": ghi, "parent": RENEWABLE},
    )


def test_materialisation_never_writes_outside_the_inferred_graph(loaded) -> None:
    """Guarded in code, asserted here: a record fact in a droppable graph would
    be lost on the next vocabulary change."""
    before = {
        str(g): loaded.count(g)
        for g in (NamedGraph.VOCAB, NamedGraph.CATALOG, NamedGraph.SHAPES, NamedGraph.COMPUTED)
    }
    materialize(loaded)
    after = {
        str(g): loaded.count(g)
        for g in (NamedGraph.VOCAB, NamedGraph.CATALOG, NamedGraph.SHAPES, NamedGraph.COMPUTED)
    }
    assert before == after


def test_narrower_is_entailed_from_broader(loaded) -> None:
    """Authors state one direction. A query walking the other must still work."""
    rows = loaded.select(
        "SELECT ?n WHERE { GRAPH ??inf { ??parent skos:narrower ?n } }",
        {"inf": NamedGraph.INFERRED.uri(), "parent": RENEWABLE},
    )
    assert {str(r["n"]) for r in rows} >= {GC + "windSpeed", GC + "solarIrradiance"}


def test_every_rule_is_exercised_by_the_real_vocabulary(loaded) -> None:
    """A rule that produces nothing against the shipped vocabulary is either
    dead or waiting for data that never arrives. Naming the exceptions here
    forces the question to be answered rather than ignored."""
    result = materialize(loaded)
    silent = {name for name, count in result.triples_by_rule.items() if count == 0}
    expected_silent = {
        # The vocabularies assert inScheme and hasTopConcept on every concept
        # already, so these rules are insurance against a future file that does
        # not. Kept deliberately; remove them only if that policy changes.
        "in-scheme-from-top",
        "scheme-membership-inherited",
        "top-concept-inverse",
    }
    assert silent == expected_silent, (
        f"rules producing nothing: {sorted(silent)}; expected {sorted(expected_silent)}"
    )
    assert set(result.triples_by_rule) == set(RULES)


def test_insurance_rules_fire_when_the_data_needs_them(loaded) -> None:
    """The three silent rules above are only defensible if they work. Give them
    data that needs them and check they do."""
    scheme = URIRef("https://schema.opengrid.org/concept/grid-concept")
    orphan = URIRef(GC + "topConceptWithoutScheme")
    loaded.update(
        """
        INSERT DATA {
          GRAPH ??vocab {
            ??orphan a skos:Concept ; skos:prefLabel "Orphan"@en ;
                     skos:topConceptOf ??scheme .
          }
        }
        """,
        {"vocab": NamedGraph.VOCAB.uri(), "orphan": orphan, "scheme": scheme},
    )
    materialize(loaded)
    assert loaded.ask(
        "ASK { GRAPH ??inf { ??orphan skos:inScheme ??scheme } }",
        {"inf": NamedGraph.INFERRED.uri(), "orphan": orphan, "scheme": scheme},
    )
    assert loaded.ask(
        "ASK { GRAPH ??inf { ??scheme skos:hasTopConcept ??orphan } }",
        {"inf": NamedGraph.INFERRED.uri(), "orphan": orphan, "scheme": scheme},
    )
