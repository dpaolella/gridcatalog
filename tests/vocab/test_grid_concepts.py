"""The grid-concept scheme, and the PRD §4.6 Q3 property it exists to support."""

from __future__ import annotations

from pathlib import Path

import pytest
from datahub.namespaces import OG
from rdflib import Graph, URIRef
from rdflib.namespace import SKOS

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEME = "https://schema.opengrid.org/concept/grid-concept"
GC = f"{SCHEME}/"
QUDT_UNIT = "http://qudt.org/vocab/unit/"
OG_UNIT = "https://schema.opengrid.org/unit/"


@pytest.fixture(scope="module")
def g() -> Graph:
    graph = Graph()
    graph.parse((REPO_ROOT / "vocab" / "og-grid-concept.ttl").as_posix(), format="turtle")
    return graph


def concepts(g: Graph) -> list[URIRef]:
    return list(g.subjects(None, SKOS.Concept))


def is_abstract(g: Graph, c: URIRef) -> bool:
    value = g.value(c, OG.abstract)
    return value is not None and bool(value)


def is_leaf(g: Graph, c: URIRef) -> bool:
    return not any(g.subjects(SKOS.broader, c))


def test_scheme_has_substantial_coverage(g: Graph) -> None:
    """A thin scheme cannot resolve real column names, which is the only thing
    it is for. The threshold is a floor, not a target."""
    assert len(concepts(g)) >= 150


def test_hierarchy_is_deep_not_flat(g: Graph) -> None:
    """A flat scheme makes Q3 useless: every query for a parent returns nothing
    but the parent."""

    def depth(c: URIRef, seen: frozenset[URIRef] = frozenset()) -> int:
        parents = [p for p in g.objects(c, SKOS.broader) if p not in seen]
        if not parents:
            return 0
        return 1 + max(depth(p, seen | {c}) for p in parents)

    depths = [depth(c) for c in concepts(g)]
    assert max(depths) >= 3, f"deepest chain is only {max(depths)}"


# ---- PRD §4.6 Q3: inference over the concept scheme ---------------------


def test_q3_renewable_resource_returns_narrower_concepts(g: Graph) -> None:
    """PRD §4.6 Q3, verbatim in its intent: a query for 'renewable resource'
    fields returns solar irradiance, wind speed and hydro inflow because the
    SKOS hierarchy says so, with nobody maintaining an expansion list.

    The query below enumerates nothing. The three concepts asserted are the
    three the PRD names; the query returns considerably more, which is the
    point.
    """
    query = """
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    SELECT ?c WHERE { ?c skos:broader+ <https://schema.opengrid.org/concept/grid-concept/renewableResource> }
    """
    returned = {str(row[0]) for row in g.query(query)}
    for expected in ("globalHorizontalIrradiance", "windSpeed", "hydroInflow"):
        assert GC + expected in returned, f"Q3 did not return {expected}"
    assert len(returned) > 10, "Q3 returned too few concepts to be doing real work"


def test_q3_picks_up_a_new_concept_with_no_code_change(g: Graph) -> None:
    """PRD §4.6 Q3: 'Adding geothermal gradient next year makes this query
    return more, with no code change.'

    Asserted literally: insert a concept into a copy of the graph and re-run the
    identical query string.
    """
    query = """
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    SELECT ?c WHERE { ?c skos:broader+ <https://schema.opengrid.org/concept/grid-concept/renewableResource> }
    """
    before = {str(r[0]) for r in g.query(query)}

    extended = Graph()
    extended += g
    novel = URIRef(GC + "osmoticGradient")
    extended.add((novel, SKOS.broader, URIRef(GC + "renewableResource")))
    extended.add(
        (novel, SKOS.prefLabel, __import__("rdflib").Literal("Osmotic gradient", lang="en"))
    )

    after = {str(r[0]) for r in extended.query(query)}
    assert after == before | {str(novel)}


def test_q3_reaches_through_an_intermediate_level(g: Graph) -> None:
    """The '+' in the property path is what makes the query work at unknown
    depth. Solar irradiance components sit two levels down, not one."""
    query = """
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    SELECT ?c WHERE { ?c skos:broader <https://schema.opengrid.org/concept/grid-concept/renewableResource> }
    """
    one_hop = {str(r[0]) for r in g.query(query)}
    assert GC + "globalHorizontalIrradiance" not in one_hop, (
        "GHI is directly under renewableResource; the test no longer proves the "
        "property path is doing anything"
    )
    assert GC + "solarIrradiance" in one_hop


# ---- units ---------------------------------------------------------------


def test_dimensional_leaves_declare_a_unit(g: Graph) -> None:
    """A concept with a physical dimension and no unit cannot participate in the
    'match on normalized name AND unit' stage of concept resolution."""
    exempt_markers = (OG.isCategorical, OG.isJoinKey, OG.isTemporal)
    missing = []
    for c in concepts(g):
        if is_abstract(g, c) or not is_leaf(g, c):
            continue
        if any(g.value(c, m) for m in exempt_markers):
            continue
        if g.value(c, OG.defaultUnit) is None:
            missing.append(str(c).split("/")[-1])
    assert not missing, f"dimensional leaves with no og:defaultUnit: {sorted(missing)}"


def test_units_are_iris_never_strings(g: Graph) -> None:
    """PRD §4.3 C5: QUDT IRI, never free text."""
    for _, unit in g.subject_objects(OG.defaultUnit):
        assert isinstance(unit, URIRef), f"unit {unit!r} is a literal, not an IRI"
        assert str(unit).startswith((QUDT_UNIT, OG_UNIT)), f"unit {unit} is in neither namespace"


def test_abstract_concepts_carry_no_alt_labels(g: Graph) -> None:
    """Alt-labels are resolution targets. An abstract branch node with an
    alt-label invites a field to resolve to 'voltage' or 'plant identifier',
    which is a gap dressed as a hit."""
    for c in concepts(g):
        if is_abstract(g, c):
            labels = [str(x) for x in g.objects(c, SKOS.altLabel)]
            assert not labels, f"abstract concept {c} carries alt-labels {labels}"


def test_abstract_concepts_carry_no_unit(g: Graph) -> None:
    """A branch node with a unit invites a resolver to land a field on it, and a
    field resolved to 'electrical quantity' is a gap dressed as a hit."""
    for c in concepts(g):
        if is_abstract(g, c):
            assert g.value(c, OG.defaultUnit) is None, f"abstract concept {c} declares a unit"


# ---- alt labels are working data ----------------------------------------


def test_real_column_names_are_covered(g: Graph) -> None:
    """These are names that appear in the seed inventory's own datasets. A
    resolver that cannot match them is not doing its job."""
    labels: dict[str, set[str]] = {}
    for c, label in g.subject_objects(SKOS.altLabel):
        labels.setdefault(str(label).lower(), set()).add(str(c))
    for name in (
        "p_nom",
        "pmax",
        "ghi",
        "ssrd",
        "v_nom",
        "base_kv",
        "br_r",
        "br_x",
        "capacity_factor",
        "heat_rate",
        "lmp",
        "wind_speed_100m",
        "plant_id_eia",
        "for",
        "lcoe",
        "wacc",
        "max_hours",
        "rte",
    ):
        assert name in labels, f"no concept carries the alt-label {name!r}"


def test_alt_label_collisions_are_deliberate(g: Graph) -> None:
    """A label on two concepts makes resolution ambiguous. Some collisions are
    unavoidable — 'efficiency' genuinely means several things — so this asserts
    the set of them rather than forbidding them, and a new one must be added
    here consciously."""
    known_ambiguous = {
        # Genuinely ambiguous in the wild. Resolution disambiguates by the
        # dataset's domain and by the unit, not by the name alone.
        "efficiency",
        "capacity",
        "status",
        "rating",
        "price",
        "name",
        "type",
        "angle",
        "direction",
        "resolution",
        "area",
        "region",
        "operational",
        "s_nom",
        "p_nom",
        "duration",
        "frequency",
        # "x" is longitude in a geospatial table and series reactance in a
        # network table. No amount of vocabulary discipline fixes that; the
        # resolver has to look at the dataset's domain.
        "x",
    }
    counts: dict[str, set[str]] = {}
    for c, label in g.subject_objects(SKOS.altLabel):
        counts.setdefault(str(label).lower(), set()).add(str(c))
    unexpected = {
        label: sorted(x.split("/")[-1] for x in cs)
        for label, cs in counts.items()
        if len(cs) > 1 and label not in known_ambiguous
    }
    assert not unexpected, f"undeclared alt-label collisions: {unexpected}"


# ---- bootstrapping provenance -------------------------------------------


def test_bootstrapped_concepts_say_where_they_came_from(g: Graph) -> None:
    """PRD §F7 asks the scheme to be bootstrapped from OEO and Sienna rather
    than authored fresh. Where a concept carries an external match, it must also
    say on what basis."""
    for c, _ in g.subject_objects(SKOS.closeMatch):
        assert g.value(c, SKOS.editorialNote) is not None, (
            f"{c} asserts an external match with no editorial note explaining it"
        )
