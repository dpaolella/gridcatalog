"""The Q1-Q5 regression suite (PRD §4.6, §10, §11).

PRD §10: "M1 carries the storage risk. Before committing past it, load the
golden set into Fuseki and run Q1 through Q5. If federation against OEP does
not work in practice, or if inference materialisation on a realistic vocabulary
is unworkable, that is the moment to revisit section 0 decision 3, not month
six. Budget a week for this and treat a negative result as a successful
experiment."

This is that experiment, run continuously rather than once. Each query is the
one the PRD names, executed against the real vocabulary and the real fixture
corpus. Q4 needs outbound network and is marked accordingly — a federation
failure is a finding about the substrate, not a flaky test.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from rdflib import URIRef

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.graph.graphs import CROSSWALK_GRAPH_PREFIX, NamedGraph
from datahub.graph.loader import bootstrap
from datahub.graph.store import RdflibStore
from datahub.semantic.queries import load, query_names, run, scoped
from fixtures.loader import corpus_graph

DS = "https://catalog.opengrid.org/ds/"
GC = "https://schema.opengrid.org/concept/grid-concept/"
AT = "https://schema.opengrid.org/concept/analysis-type/"
CAT, VOC = NamedGraph.CATALOG, NamedGraph.VOCAB


@pytest.fixture(scope="module")
def seeded():
    """Vocabulary, entailments and the whole fixture corpus in one store."""
    store = RdflibStore()
    bootstrap(store)
    store.put_graph(NamedGraph.CATALOG, corpus_graph())
    return store


@pytest.fixture(scope="module")
def crosswalk_graphs(seeded) -> tuple[str, ...]:
    return tuple(g for g in seeded.graph_names() if g.startswith(CROSSWALK_GRAPH_PREFIX))


# ---- Q1: shared upstream origin at unbounded depth -----------------------


def test_q1_finds_the_shared_era5_origin(seeded) -> None:
    """The M7 done-criterion, demonstrable at M1: 'Q1 returns the shared ERA5
    origin for the GWA / PyPSA-Eur cutout pair at correct depth.'"""
    rows = run(
        seeded,
        "q1",
        {"a": URIRef(DS + "global-wind-atlas"), "b": URIRef(DS + "pypsa-eur-weather-cutouts")},
        graphs=(CAT,),
    )
    origins = {str(r["sharedOrigin"]) for r in rows}
    assert origins == {DS + "ecmwf-era5"}


def test_q1_reports_different_depths(seeded) -> None:
    """The `+` in the property path is the point: neither depth is known in
    advance. Global Wind Atlas reaches ERA5 through an intermediate mesoscale
    run, so a two-hop approximation would find nothing."""
    rows = run(
        seeded,
        "q1",
        {"a": URIRef(DS + "global-wind-atlas"), "b": URIRef(DS + "pypsa-eur-weather-cutouts")},
        graphs=(CAT,),
    )
    row = rows[0]
    assert int(row["depthA"]) == 2, "Global Wind Atlas should reach ERA5 indirectly"
    assert int(row["depthB"]) == 1, "PyPSA-Eur cutouts derive from ERA5 directly"


def test_q1_finds_nothing_for_genuinely_independent_datasets(seeded) -> None:
    """A false positive here would put a correlation warning on an unrelated
    pair, which erodes trust in every warning."""
    rows = run(
        seeded,
        "q1",
        {"a": URIRef(DS + "lbnl-queued-up"), "b": URIRef(DS + "ember-electricity-review")},
        graphs=(CAT,),
    )
    assert not rows


def test_q1_is_symmetric(seeded) -> None:
    forward = run(
        seeded,
        "q1",
        {"a": URIRef(DS + "global-wind-atlas"), "b": URIRef(DS + "pypsa-eur-weather-cutouts")},
        graphs=(CAT,),
    )
    reverse = run(
        seeded,
        "q1",
        {"a": URIRef(DS + "pypsa-eur-weather-cutouts"), "b": URIRef(DS + "global-wind-atlas")},
        graphs=(CAT,),
    )
    assert {str(r["sharedOrigin"]) for r in forward} == {str(r["sharedOrigin"]) for r in reverse}


# ---- Q2: blast radius ----------------------------------------------------


def test_q2_finds_everything_downstream_of_a_correction(seeded) -> None:
    """'ERA5 issued a correction. What in my study is affected?'"""
    rows = run(seeded, "q2", {"origin": URIRef(DS + "ecmwf-era5")}, graphs=(CAT,))
    affected = {str(r["affected"]) for r in rows}
    assert DS + "pypsa-eur-weather-cutouts" in affected, "the direct derivative is missing"
    assert DS + "global-wind-atlas" in affected, "the two-hop derivative is missing"


def test_q2_does_not_return_the_origin_itself(seeded) -> None:
    rows = run(seeded, "q2", {"origin": URIRef(DS + "ecmwf-era5")}, graphs=(CAT,))
    assert DS + "ecmwf-era5" not in {str(r["affected"]) for r in rows}


def test_q2_follows_supersession_as_well_as_derivation(seeded) -> None:
    """One walk over mixed edge types is the capability the storage decision
    was made for: og:supersedes is a different relation from derivation, and a
    correction propagates along both."""
    rows = run(
        seeded,
        "q2",
        {"origin": URIRef(DS + "wri-global-power-plant-database")},
        graphs=(CAT,),
    )
    assert DS + "gem-global-integrated-power-tracker" in {str(r["affected"]) for r in rows}


# ---- Q3: inference over the concept scheme -------------------------------


def test_q3_returns_datasets_by_narrower_concept(seeded) -> None:
    """The M1 done-criterion. A query for 'renewable resource' returns datasets
    carrying solar irradiance, wind speed and hydro inflow, with nothing
    enumerated in the query or in this test beyond the three the PRD names."""
    rows = run(seeded, "q3", {"concept": URIRef(GC + "renewableResource")}, graphs=(CAT, VOC))
    concepts = {str(r["concept"]) for r in rows}
    for expected in ("globalHorizontalIrradiance", "windSpeed", "hydroInflow"):
        assert GC + expected in concepts, f"Q3 missed {expected}"
    datasets = {str(r["dataset"]) for r in rows}
    assert len(datasets) >= 3


def test_q3_has_no_enumeration_in_the_query(seeded) -> None:
    """Guarded literally: if someone 'fixes' Q3 by listing concepts, this
    fails. The absence of a list is the feature (PRD §4.6)."""
    text = load("q3")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "windSpeed" not in body
    assert "solarIrradiance" not in body
    assert "VALUES" not in body
    assert "skos:broader+" in body, "the property path is what makes it work"


def test_q3_picks_up_a_new_concept_with_no_code_change(seeded) -> None:
    """'Add a concept next year and every existing query picks it up.'"""
    before = {
        str(r["concept"])
        for r in run(seeded, "q3", {"concept": URIRef(GC + "renewableResource")}, graphs=(CAT, VOC))
    }
    novel = URIRef(GC + "tidalStreamVelocity")
    field = URIRef("https://catalog.opengrid.org/field/ecmwf-era5/u100")
    seeded.update(
        "INSERT DATA { GRAPH ??v { ??novel a skos:Concept ; skos:broader ??parent } }",
        {"v": VOC.uri(), "novel": novel, "parent": URIRef(GC + "renewableResource")},
    )
    seeded.update(
        "INSERT DATA { GRAPH ??c { ??field og:concept ??novel } }",
        {"c": CAT.uri(), "field": field, "novel": novel},
    )
    after = {
        str(r["concept"])
        for r in run(seeded, "q3", {"concept": URIRef(GC + "renewableResource")}, graphs=(CAT, VOC))
    }
    assert after == before | {str(novel)}
    seeded.update(
        "DELETE DATA { GRAPH ??c { ??field og:concept ??novel } }",
        {"c": CAT.uri(), "field": field, "novel": novel},
    )


def test_q3_on_a_leaf_concept_returns_only_that_concept(seeded) -> None:
    rows = run(seeded, "q3", {"concept": URIRef(GC + "windSpeed")}, graphs=(CAT, VOC))
    assert {str(r["concept"]) for r in rows} == {GC + "windSpeed"}


# ---- Q4: federation ------------------------------------------------------


@pytest.mark.network
def test_q4_federates_against_the_open_energy_platform(seeded) -> None:
    """PRD §0.3 calls federation the decisive capability behind the storage
    decision, and §10 says to test it before committing past M1.

    Skipped by default because it needs outbound network. A failure here is a
    real finding about the substrate — the fallback in ADR-0001 is a document
    store, and losing federation is what that trade costs.
    """
    endpoint = URIRef("https://openenergyplatform.org/sparql")
    rows = run(seeded, "q4", {"endpoint": endpoint}, graphs=(CAT,))
    assert isinstance(rows, list)


def test_q4_is_syntactically_valid(seeded) -> None:
    """Even without network, the query must parse and carry a SERVICE clause."""
    from datahub.graph.sparql import bind, prologue
    from rdflib.plugins.sparql import prepareQuery

    text = bind(load("q4"), {"endpoint": URIRef("https://example.org/sparql")})
    assert "SERVICE" in text
    prepareQuery(prologue(text))


# ---- Q5: the crosswalk audit --------------------------------------------


def test_q5_finds_no_violations_in_the_shipped_crosswalks(seeded, crosswalk_graphs) -> None:
    """The crosswalks are disciplined, and Q5 is what keeps them so. A
    violation here is an X2 breach: an exactMatch that also declares a unit,
    basis or granularity difference, or one whose two sides disagree on units."""
    rows = run(seeded, "q5", graphs=(*crosswalk_graphs, VOC))
    assert not rows, [(str(r["scheme"]), str(r["concept"]), str(r["justification"])) for r in rows]


def test_q5_catches_an_injected_violation(seeded, crosswalk_graphs) -> None:
    """A query that finds nothing is only reassuring if it would find
    something. Inject an exactMatch that also declares a unit difference."""
    graph = URIRef(NamedGraph.crosswalk("pypsa"))
    concept = URIRef(GC + "airDensity")
    seeded.update(
        """
        INSERT DATA {
          GRAPH ??g {
            ??c skos:exactMatch <urn:ext:bogus> ;
                og:unitDiffers "these are not the same unit at all"@en .
          }
        }
        """,
        {"g": graph, "c": concept},
    )
    try:
        rows = run(seeded, "q5", graphs=(*crosswalk_graphs, VOC))
        assert any(str(r["concept"]) == str(concept) for r in rows), (
            "Q5 did not catch an exactMatch that declares a difference"
        )
    finally:
        seeded.update(
            """
            DELETE DATA {
              GRAPH ??g {
                ??c skos:exactMatch <urn:ext:bogus> ;
                    og:unitDiffers "these are not the same unit at all"@en .
              }
            }
            """,
            {"g": graph, "c": concept},
        )


def test_q5_does_not_confuse_one_crosswalk_with_another(seeded, crosswalk_graphs) -> None:
    """The specific bug the per-crosswalk graph split fixes: gc:resistance has
    an exactMatch in PyPSA and a closeMatch with a basis difference in CIM.
    Merged into one graph, the CIM justification appears to qualify the PyPSA
    exactMatch and Q5 reports a violation that is not there."""
    rows = run(seeded, "q5", graphs=(*crosswalk_graphs, VOC))
    assert not any(str(r["concept"]) == GC + "resistance" for r in rows)


# ---- Q6: analysis-type inference ----------------------------------------


def test_q6_returns_datasets_declared_for_a_narrower_analysis(seeded) -> None:
    """A request for power-flow datasets must return one declared fit for DC
    power flow specifically — the same inference pattern as Q3, applied to the
    analysis vocabulary."""
    rows = run(seeded, "q6", {"analysis": URIRef(AT + "powerFlow")}, graphs=(CAT, VOC))
    assert DS + "pypsa-eur-grid" in {str(r["dataset"]) for r in rows}


def test_q6_respects_an_explicit_exclusion(seeded) -> None:
    """Saying what a dataset is NOT for is the half users cannot get elsewhere,
    so an exclusion has to actually exclude."""
    rows = run(seeded, "q6", {"analysis": URIRef(AT + "acPowerFlow")}, graphs=(CAT, VOC))
    assert DS + "pypsa-eur-grid" not in {str(r["dataset"]) for r in rows}


# ---- Q7 and the machinery -----------------------------------------------


def test_q7_lists_confirmed_records_with_their_cadence(seeded) -> None:
    rows = run(seeded, "q7", graphs=(CAT,))
    assert len(rows) >= 15
    assert any(r["cadence"] is not None for r in rows)
    assert any(r["supersededBy"] is not None for r in rows), (
        "no fixture carries a supersession link, so the Currency grade's D case is untested"
    )


def test_every_query_file_parses(seeded) -> None:
    from datahub.graph.sparql import prologue
    from rdflib.plugins.sparql import prepareQuery

    for name in query_names():
        text = load(name)
        placeholders = text.count("??")
        stub = text
        for _ in range(placeholders):
            stub = stub.replace("??", "?_p_", 1)
        prepareQuery(prologue(stub))


def test_scoping_merges_graphs_rather_than_unioning_them() -> None:
    """A UNION of two self-contained patterns can never join a concept
    hierarchy in one graph to fields in another. It returns nothing, silently."""
    text = scoped(load("q3"), CAT, VOC)
    assert f"FROM <{CAT}>" in text
    assert f"FROM <{VOC}>" in text
    assert "UNION { BIND" in text, "the query's own UNION should survive scoping"
    assert text.count("WHERE") == 1


def test_scoping_refuses_a_query_with_no_where_clause() -> None:
    with pytest.raises(ValueError, match="WHERE"):
        scoped("ASK { ?s ?p ?o }", CAT)


def test_unscoped_reads_do_not_see_the_draft_graph(seeded) -> None:
    """A provenance walk that wandered into og:graph/draft would traverse
    records no user is entitled to see."""
    seeded.update(
        "INSERT DATA { GRAPH ??d { <urn:secret> og:upstreamSource ??era5 } }",
        {"d": NamedGraph.DRAFT.uri(), "era5": URIRef(DS + "ecmwf-era5")},
    )
    rows = run(seeded, "q2", {"origin": URIRef(DS + "ecmwf-era5")}, graphs=(CAT,))
    assert "urn:secret" not in {str(r["affected"]) for r in rows}
