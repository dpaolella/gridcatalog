"""Inter-dataset links (WP-8.1, WP-8.2).

The milestone's done-criterion is `test_the_known_correlated_pair_surfaces_
with_a_warning`: *a known correlated pair from the golden set surfaces with a
warning naming the shared upstream source and stating the modelling consequence
in plain language — and with its strength reduced, not zeroed.*

The rest of this file guards the ways a link ranker goes wrong while looking
right: hiding what it should reduce, ranking without a reason, and leaking the
existence of a record through a suggestion.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datahub.api.search.backend import Entitlement
from datahub.linksvc import compute, describe, load, score, worth_surfacing
from datahub.linksvc.weights import PENALTY_NAME, SIGNAL_NAMES
from datahub.semantic.provenance import LineageIndex

GWA = "global-wind-atlas"
CUTOUTS = "pypsa-eur-weather-cutouts"
ERA5 = "ecmwf-era5"
RESTRICTED = "utility-load-shapes-allowlisted"


# ---- the done-criterion --------------------------------------------------


def test_the_known_correlated_pair_surfaces_with_a_warning(service) -> None:
    """Global Wind Atlas and the PyPSA-Eur cutouts both trace to ERA5 — at
    different depths, through an uncatalogued mesoscale run on one side. A
    modeler pairing them for siting and time series believes they are
    independent, and the study's uncertainty band is narrower than it should
    be."""
    links = service.links_for(GWA)

    pair = next((link for link in links if link.target == CUTOUTS), None)
    assert pair is not None, "the correlated pair is not surfaced at all"
    assert pair.warning
    assert "ERA5" in pair.warning
    assert "uncertainty" in pair.warning
    assert pair.shared_origin and pair.shared_origin.endswith(ERA5)


def test_the_warning_states_the_consequence_not_just_the_fact(service) -> None:
    """ "Correlated" is a word a modeller reads past. What they do not read past
    is being told the agreement they are about to treat as corroboration is
    partly one source agreeing with itself."""
    pair = next(link for link in service.links_for(GWA) if link.target == CUTOUTS)

    assert "agreeing with itself" in pair.warning
    assert "validation" in pair.warning


def test_the_correlated_pair_is_reduced_not_zeroed(service) -> None:
    """PRD §F6.9. Hiding it removes exactly the information the user needs, and
    leaves them believing the two are independent — a stronger and more wrong
    claim than the warning would have made."""
    pair = next(link for link in service.links_for(GWA) if link.target == CUTOUTS)

    assert pair.penalised, "the penalty did not apply"
    assert pair.unpenalised_score > pair.score
    assert pair.score > 0, "the pairing was zeroed"
    assert pair.tier >= 1, "the pairing fell out of the ranking"


def test_the_warning_names_the_depth_on_each_side(service) -> None:
    """Two datasets one hop from a reanalysis are barely independent; two six
    hops away through different products may be independent enough. A warning
    that treated those alike would be ignored within a week."""
    pair = next(link for link in service.links_for(GWA) if link.target == CUTOUTS)

    assert "hop" in pair.warning


# ---- every pairing carries a reason --------------------------------------


def test_no_pairing_is_surfaced_with_only_a_number(service, backend) -> None:
    """PRD §F6: *a bare numeric score is not sufficient and should fail
    review.* So it fails here instead."""
    for dataset_id in ("eia-930", GWA, "pypsa-eur-grid", ERA5):
        for link in service.links_for(dataset_id):
            assert link.descriptor, f"{dataset_id} → {link.target} has no descriptor"
            assert link.reasons or link.warning, f"{dataset_id} → {link.target} has no reason"


def test_a_pairing_with_nothing_to_say_is_dropped_rather_than_ranked() -> None:
    from datahub.linksvc.describe import Description
    from datahub.linksvc.rank import Link

    bare = Link(
        source="a",
        target="b",
        score=0.4,
        tier=3,
        relation="related",
        descriptor="",
        reasons=(),
    )
    assert not worth_surfacing(bare)
    _ = Description


def test_a_correlated_pairing_is_kept_even_with_no_other_reason() -> None:
    """The warning *is* the reason, and it is the pairing a user most needs."""
    from datahub.linksvc.rank import Link

    warned = Link(
        source="a",
        target="b",
        score=0.0,
        tier=1,
        relation="related",
        descriptor="",
        reasons=(),
        warning="both trace back to X",
    )
    assert worth_surfacing(warned)


def test_descriptors_are_specific_where_the_evidence_is(service) -> None:
    """PRD §F6.4's own examples: "Different physics, complementary." "Nodal
    versus zonal, different granularity of the same network." A descriptor that
    said "related" for every pair would satisfy the letter of the requirement
    and none of its purpose."""
    descriptors = {
        link.descriptor for name in ("pypsa-eur-grid", GWA) for link in service.links_for(name)
    }

    assert len(descriptors) > 3, "every pairing got the same sentence"
    assert any("granularity" in d for d in descriptors)


# ---- typed relations -----------------------------------------------------


def test_supersession_is_read_from_the_record_not_inferred(service) -> None:
    """A record that says it is superseded has settled the question. Inferring
    "substitute" from an overlap on top of that would contradict a statement
    the catalog holds."""
    links = service.links_for("wri-global-power-plant-database")

    successor = next(
        (link for link in links if link.target == "gem-global-integrated-power-tracker"), None
    )
    assert successor is not None
    assert successor.relation == "superseded-by"
    assert "Prefer that record" in successor.descriptor


def test_a_lineage_pair_is_typed_as_derived_from(service) -> None:
    """One built from the other is not a correlation warning — it is a
    derivation, and saying "these are correlated" about a dataset and its own
    source is true and useless."""
    era5 = next(link for link in service.links_for(GWA) if link.target == ERA5)

    assert era5.relation == "derived-from"
    assert era5.warning is None


# ---- signals -------------------------------------------------------------


def test_concept_overlap_uses_the_expanded_closure(doc) -> None:
    """Comparing leaf concepts only would call two datasets about the same
    quantity unrelated because one of them is more specific."""
    signals = compute(doc(ERA5), doc("nrel-nsrdb"))

    assert signals.value("concept_overlap") > 0


def test_geographic_overlap_is_iou_not_intersection(doc) -> None:
    """A global dataset intersects everything. Scoring that at full strength
    would put the largest record in the catalog at the top of every list.

    ERA5 is global and PyPSA-Eur is European: they intersect completely from
    PyPSA-Eur's side and barely at all as a fraction of their union, and it is
    the second number that should rank them.
    """
    partial = compute(doc(ERA5), doc("pypsa-eur-grid"))
    identical = compute(doc(ERA5), doc("global-transmission-database"))

    assert 0 < partial.value("geographic_overlap") < 0.2
    assert identical.value("geographic_overlap") == pytest.approx(1.0), (
        "two global datasets do share their whole extent"
    )


def test_uncaptured_coverage_scores_zero_and_says_so(doc) -> None:
    """Absent means not captured. A descriptor must never claim two datasets
    cover different places when nobody recorded where either one is."""
    signals = compute(doc("ember-electricity-review"), doc("lbnl-queued-up"))
    evidence = signals.evidence("temporal_overlap")

    if signals.value("temporal_overlap") == 0:
        assert evidence.get("reason"), "a zero with no explanation"


def test_quality_contribution_never_becomes_a_dataset_attribute(service, backend) -> None:
    """ADR-0007. The number ranks a *pairing*; the moment it is attached to a
    dataset it is the composite score the ADR forbids."""
    document = backend.get(ERA5)

    assert not hasattr(document, "quality_contribution")
    assert not hasattr(document.quality, "overall")
    for link in service.links_for(ERA5):
        assert not hasattr(link, "quality_score")


def test_an_ungraded_pair_is_not_penalised_for_being_ungraded(doc) -> None:
    """Treating "not assessed" as a low score would bury every harvested
    record — the same conflation PRD §F5 forbids on the display side, arriving
    through the ranking instead."""
    signals = compute(doc("lbnl-queued-up"), doc("ember-electricity-review"))
    evidence = signals.evidence("quality_contribution")

    assert "graded_facets" in evidence or "reason" in evidence


# ---- weights -------------------------------------------------------------


def test_the_weights_come_from_config() -> None:
    """PRD §F6: *put the weights in config, not code. They will change.*"""
    weights = load()

    assert set(weights.signals) == set(SIGNAL_NAMES)
    assert weights.shared_origin_penalty < 0


def test_a_weights_file_that_does_not_match_the_ranker_fails_loudly(tmp_path) -> None:
    """A key nobody computes contributes zero to every score, silently."""
    import yaml
    from datahub.linksvc.weights import load as load_weights

    path = tmp_path / "weights.yaml"
    path.write_text(
        yaml.safe_dump({"weights": {"concept_overlap": 1.0, PENALTY_NAME: -0.1}, "tiers": {1: 0.0}})
    )

    with pytest.raises(ValueError, match="missing"):
        load_weights(path)


def test_a_positive_penalty_is_refused(tmp_path) -> None:
    """It would make a shared origin *strengthen* a pairing — the exact
    inversion of PRD §F6.9, expressible as a one-character config edit."""
    import yaml
    from datahub.linksvc.weights import load as load_weights

    path = tmp_path / "weights.yaml"
    path.write_text(
        yaml.safe_dump(
            {"weights": {**dict.fromkeys(SIGNAL_NAMES, 0.1), PENALTY_NAME: 0.15}, "tiers": {1: 0.0}}
        )
    )

    with pytest.raises(ValueError, match="penalty"):
        load_weights(path)


def test_ranking_is_stable_across_runs(service) -> None:
    """Without a total order two runs over unchanged data produce different
    top-12 lists, and a user who refreshes sees the suggestions move for no
    reason."""
    first = [link.target for link in service.links_for("pypsa-eur-grid")]
    second = [link.target for link in service.links_for("pypsa-eur-grid")]

    assert first == second


def test_top_n_is_respected(service) -> None:
    weights = load()
    assert len(service.links_for("pypsa-eur-grid")) <= weights.top_n


# ---- entitlement ---------------------------------------------------------


def test_an_anonymous_caller_is_never_linked_to_a_hidden_record(service) -> None:
    """A link to a record the caller may not see leaks its existence through a
    suggestion list — the same leak the entitlement matrix hunts for on
    /datasets, arriving by a different door."""
    for name in ("eia-930", "pypsa-eur-grid", ERA5):
        targets = [
            link.target for link in service.links_for(name, entitlement=Entitlement.anonymous())
        ]
        assert RESTRICTED not in targets


def test_the_batch_pass_sees_the_whole_catalog(service) -> None:
    """A restricted record that got no links because the batch could not see it
    would have none to show its own custodian either — a leak in the other
    direction."""
    summary = service.run_all()

    assert summary.records == 17


# ---- lineage -------------------------------------------------------------


def test_lineage_depth_is_the_shortest_chain(store) -> None:
    """A dataset reaching an origin both directly and through an intermediate
    is one hop away, not two: the closest path is what determines how much two
    datasets have in common."""
    index = LineageIndex.from_store(store)
    base = "https://catalog.opengrid.org/ds/"

    shared = index.shared_origins(base + GWA, base + CUTOUTS)

    assert shared
    assert shared[0].origin == base + ERA5
    assert (shared[0].depth_a, shared[0].depth_b) == (2, 1)


def test_a_dataset_is_not_its_own_shared_origin(store) -> None:
    """ERA5 and something derived from ERA5 are in a derivation relationship,
    not a correlation one. Reporting it as shared origin would tell a user a
    dataset is correlated with its own source."""
    index = LineageIndex.from_store(store)
    base = "https://catalog.opengrid.org/ds/"

    assert not index.shared_origins(base + ERA5, base + CUTOUTS)


def test_lineage_walking_survives_a_cycle() -> None:
    """A record that wrongly derives from itself should produce a wrong
    lineage, not a hung recompute."""
    index = LineageIndex(parents={"a": {"b"}, "b": {"a"}})

    assert index.ancestors("a") == {"b": 1}


# ---- persistence ---------------------------------------------------------


def test_links_are_written_to_the_computed_graph(service, store) -> None:
    from datahub.graph.graphs import NamedGraph
    from datahub.namespaces import OG

    service.run_all()
    computed = store.get_graph(NamedGraph.COMPUTED)

    assert list(computed.subject_objects(OG.hasLink))
    assert list(computed.objects(None, OG.complementarityDescriptor))


def test_a_stored_warning_names_its_origin_and_consequence(service, store) -> None:
    from datahub.graph.graphs import NamedGraph
    from datahub.namespaces import OG

    service.run_all()
    computed = store.get_graph(NamedGraph.COMPUTED)

    warnings = list(computed.subjects(OG.modelingConsequence, None))
    assert warnings
    for warning in warnings:
        assert computed.value(warning, OG.sharedOrigin) is not None


def test_rewriting_links_does_not_accumulate(service, store) -> None:
    from datahub.graph.graphs import NamedGraph
    from datahub.namespaces import OG

    service.run_all()
    computed = store.get_graph(NamedGraph.COMPUTED)
    first = len(list(computed.subject_objects(OG.hasLink)))

    service.run_all()

    assert len(list(store.get_graph(NamedGraph.COMPUTED).subject_objects(OG.hasLink))) == first


def test_describe_reads_evidence_not_the_score(doc) -> None:
    """A descriptor that varied with the score would be describing the ranking
    rather than the relationship."""
    signals = compute(doc("pypsa-eur-grid"), doc("global-transmission-database"))

    described = describe(doc("pypsa-eur-grid"), doc("global-transmission-database"), signals)
    scored = score(signals, load(), described)

    assert described.descriptor == scored.descriptor
