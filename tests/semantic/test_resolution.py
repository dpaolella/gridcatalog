"""Concept and unit resolution (WP-7.1).

The milestone's first done-criterion: *two differently-named fields for the
same quantity resolve to one concept IRI.* That is `test_two_names_one_concept`
below; everything else in this file guards the ways a resolver can appear to
satisfy it while being wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from rdflib import URIRef

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.semantic.resolve import MARGIN, Part, Resolver
from datahub.semantic.similarity import LexicalSimilarity
from datahub.semantic.vocabulary import normalise

GHI = "https://schema.opengrid.org/concept/grid-concept/globalHorizontalIrradiance"
W_PER_M2 = "http://qudt.org/vocab/unit/W-PER-M2"
MEGAWATT = "http://qudt.org/vocab/unit/MegaW"
KILOWATT = "http://qudt.org/vocab/unit/KiloW"


def field(name: str, **kwargs) -> Part:
    return Part(iri=f"urn:test:{name}", shape="tabular", local_name=name, **kwargs)


# ---- the done-criterion --------------------------------------------------


def test_two_names_one_concept(resolver) -> None:
    """PRD §F4's whole purpose: `ssrd` in ERA5 and `GHI` in NSRDB are the same
    quantity, and a modeler should not have to know both names."""
    era5 = resolver.resolve(field("ssrd", unit=W_PER_M2))
    nsrdb = resolver.resolve(field("GHI", unit=W_PER_M2))

    assert era5.concept == nsrdb.concept == GHI
    assert era5.resolved and nsrdb.resolved


def test_a_third_name_for_the_same_quantity_agrees(resolver) -> None:
    """MERRA-2 calls it `swgdn`. Three sources, three names, one concept."""
    assert resolver.resolve(field("swgdn")).concept == GHI


# ---- the ladder ----------------------------------------------------------


def test_a_source_confirmed_assignment_is_never_overwritten(resolver) -> None:
    """ADR-0005 and PRD §F4.8. A steward's decision outranks anything computed,
    and the two must stay distinguishable."""
    part = field("whatever_the_source_called_it", concept=GHI, inferred=False)

    resolution = resolver.resolve(part)

    assert resolution.concept == GHI
    assert "source-confirmed" in resolution.basis


def test_an_inferred_assignment_is_recomputed(resolver) -> None:
    """The inverse. A previous run's guess is not evidence for the next one,
    or a single early mistake becomes permanent."""
    part = field("ssrd", concept="urn:wrong:concept", inferred=True, unit=W_PER_M2)

    assert resolver.resolve(part).concept == GHI


def test_similarity_resolves_what_no_label_matches(resolver) -> None:
    """The third rung. `p_nom` matches nothing by label; its definition says
    exactly what it is."""
    resolution = resolver.resolve(
        field(
            "p_nom",
            definition="Nominal installed generation capacity of the unit in megawatts",
            unit=MEGAWATT,
        )
    )

    assert resolution.resolved
    assert resolution.rung == "similarity"
    assert "nameplateCapacity" in resolution.concept


def test_an_unmatchable_field_gets_a_gap_with_a_reason(resolver) -> None:
    """Rule X4: never a silent omission. The gap says why, so a steward reading
    it knows whether to add a concept or to fix the field."""
    resolution = resolver.resolve(field("zzz_internal_row_key"))

    assert not resolution.resolved
    assert resolution.rung == "gap"
    assert resolution.gap_reason
    assert "threshold" in resolution.gap_reason


# ---- the ways a resolver goes wrong quietly ------------------------------


def test_a_unit_from_another_quantity_kind_blocks_the_match(resolver) -> None:
    """The most dangerous case in the ladder: the name agrees and the physics
    does not. A column called `ghi` holding megawatts is not irradiance, and
    resolving it on the name alone asserts a claim the data does not support."""
    resolution = resolver.resolve(field("ghi", unit=MEGAWATT))

    assert not resolution.resolved
    assert "quantity kind" in (resolution.gap_reason or "")


def test_a_convertible_unit_does_not_block_the_match(resolver) -> None:
    """A kilowatt column and a megawatt concept are the same quantity. The
    factor is recorded; the assignment stands."""
    resolution = resolver.resolve(
        field(
            "p_nom",
            definition="Nominal installed generation capacity of the unit",
            unit=KILOWATT,
        )
    )

    assert resolution.resolved
    assert resolution.unit_factor == pytest.approx(0.001)
    assert "factor" in resolution.basis


def test_an_ambiguous_label_is_not_resolved_by_picking_one(resolver) -> None:
    """`capacity` is an altLabel of several concepts. A resolver that took the
    first would be right some of the time and confident always."""
    resolution = resolver.resolve(field("capacity"))

    assert not resolution.resolved
    assert len(resolution.alternatives) > 1
    assert "nothing to separate them by" in (resolution.gap_reason or "")


def test_a_definition_can_break_a_label_tie(resolver) -> None:
    """…but evidence the label rung could not see may still settle it."""
    resolution = resolver.resolve(
        field("capacity", definition="Nameplate generating capacity of the unit")
    )

    assert resolution.resolved
    assert "nameplateCapacity" in resolution.concept


def test_a_near_tie_on_similarity_is_a_gap_not_a_coin_flip(resolver) -> None:
    """The margin rule. On a compressed similarity scale this is what does the
    safety work, not the threshold."""

    class AlwaysTied:
        name = "always-tied"
        threshold = 0.0

        def score(self, text: str, concept) -> float:
            return 0.9

    resolution = Resolver(resolver.vocabulary, similarity=AlwaysTied()).resolve(
        field("anything_at_all")
    )

    assert not resolution.resolved
    assert "within" in (resolution.gap_reason or "")


def test_an_abstract_concept_is_never_a_resolution_target(resolver) -> None:
    """`solarIrradiance` holds a subtree together; no dataset measures it.
    Resolving to it would lose the distinction the subtree exists to make."""
    for resolution in (
        resolver.resolve(field(name)) for name in ("ssrd", "GHI", "dni", "wind_speed")
    ):
        if resolution.resolved:
            concept = resolver.vocabulary.get(resolution.concept)
            assert concept is not None and not concept.abstract


def test_resolution_is_deterministic(resolver) -> None:
    """`og:lastComputedAt` is only meaningful if a recompute that changes
    nothing writes nothing, which requires the same answer twice."""
    part = field("p_nom", definition="Nominal installed generation capacity in megawatts")

    first = resolver.resolve(part)
    second = resolver.resolve(part)

    assert (first.concept, first.rung, first.confidence) == (
        second.concept,
        second.rung,
        second.confidence,
    )


# ---- the four data shapes ------------------------------------------------


def test_all_four_shapes_are_read(resolver, records) -> None:
    """PRD §F4.1 names four containers. A resolver that read only `hasField`
    would silently do nothing for every NetCDF in the catalog."""
    from datahub.semantic.resolve import SHAPE_CONTAINERS

    assert set(SHAPE_CONTAINERS) == {
        "hasField",
        "hasVariable",
        "hasLayer",
        "hasNodeType",
        "hasEdgeType",
    }
    assert set(SHAPE_CONTAINERS.values()) == {"tabular", "hierarchical", "geospatial", "graph"}


def test_a_records_parts_are_found(resolver, records) -> None:
    graph = records.get_graph("ecmwf-era5")
    parts = resolver.parts(graph, URIRef("https://catalog.opengrid.org/ds/ecmwf-era5"))

    assert len(parts) == 4
    assert {p.local_name for p in parts} == {"ssrd", "t2m", "u100", "ro"}


# ---- normalisation -------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("wind_speed_100m", "WindSpeed100m"),
        ("wind_speed", "speed_wind"),
        ("Global Horizontal Irradiance", "global-horizontal-irradiance"),
        ("demand_value", "demand"),
    ],
)
def test_names_that_should_normalise_alike(left: str, right: str) -> None:
    assert normalise(left) == normalise(right)


def test_names_that_must_not_collide() -> None:
    assert normalise("wind_speed") != normalise("wind_direction")
    assert normalise("ghi") != normalise("dni")


def test_margin_is_small_enough_to_be_useful_and_large_enough_to_matter() -> None:
    """Guards a change to a constant whose consequences are not local: raise it
    and nothing resolves by similarity, drop it to zero and every near-tie
    becomes a confident guess."""
    assert 0.01 <= MARGIN <= 0.2


def test_the_lexical_backend_declares_its_own_threshold() -> None:
    """One global threshold across backends whose scores are not on the same
    scale makes the resolver either reckless or inert."""
    assert 0 < LexicalSimilarity().threshold < 1
