"""KPG 193 is a system, and the checks that matter are the ones that says so.

The Hub's other two reference models are networks: topology, impedance, and an
explicit statement that they cannot be dispatched. This one carries demand, a
committed fleet and a solved operating point, so the question is no longer
"does it validate" but "is it feasible" — and a document that validates while
describing an infeasible system would be exactly the artefact a reader would
act on and should not.

The conversion is also somebody else's model passing through this repository
rather than a build from raw data, which changes what there is to check. There
is no per-branch estimate to recompute here; there are joins between three
files that can silently go wrong, units that can silently be read on the wrong
base, and an operating point that can silently stop balancing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from referencing import Registry

from tests.reference_models import sienna

MODEL = "kpg-193"
FIXTURE = "kpg-193-reference-model"
BASE_MVA = 100.0


@pytest.fixture(scope="module")
def registry() -> Registry:
    return sienna.schema_registry()


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return sienna.load_system(MODEL)


@pytest.fixture(scope="module")
def provenance() -> dict[str, Any]:
    return json.loads((sienna.MODELS / MODEL / "parameters.json").read_text())


# ---- the structural checks every registered model gets ---------------------


def test_the_document_and_every_row_in_it_validate(
    document: dict[str, Any], registry: Registry
) -> None:
    sienna.assert_document_validates(document, registry)


def test_the_document_carries_every_required_container(document: dict[str, Any]) -> None:
    sienna.assert_required_containers(document)


def test_every_bus_has_a_location(document: dict[str, Any]) -> None:
    sienna.assert_every_bus_is_located(document)


def test_the_network_is_connected(document: dict[str, Any]) -> None:
    """One synchronous island, which for Korea is the whole of it.

    Unlike the OSM models this needs no transformers to hold together: KPG
    models its 154/345/765 kV couplings as per-unit branches with no ratio, so
    connectivity comes from the branch list directly.
    """
    sienna.assert_connected(document)


def test_referential_integrity(document: dict[str, Any]) -> None:
    sienna.assert_referential_integrity(document)


# ---- the checks that only apply to a system --------------------------------


def test_the_registered_operating_point_is_feasible(document: dict[str, Any]) -> None:
    """A document that validates while describing an impossible system is worse
    than one that fails to validate.

    The case is an AC OPF solution and its feasibility is the single claim that
    makes this model worth registering as a system. Asserted on the bytes as
    published rather than taken from the paper: a conversion that quietly
    swapped Pmin for Pmax, or read a limit on the wrong base, produces a
    document that still conforms and no longer solves.
    """
    components = document["components"]

    outside_voltage = [
        bus["name"]
        for bus in components["ACBus"]
        if not (
            bus["voltage_limits"]["min"] - 1e-6
            <= bus["magnitude"]
            <= bus["voltage_limits"]["max"] + 1e-6
        )
    ]
    assert not outside_voltage, f"buses outside their voltage limits: {outside_voltage[:5]}"

    overloaded = [
        line["name"]
        for line in components["Line"]
        if (line["active_power_flow"] ** 2 + line["reactive_power_flow"] ** 2) ** 0.5
        > line["rating"] * 1.0001
    ]
    assert not overloaded, f"branches over their rating: {overloaded[:5]}"

    misdispatched = [
        unit["name"]
        for unit in components["ThermalStandard"]
        if unit["status"] == "ONLINE"
        and not (
            unit["active_power_limits"]["min"] - 1e-6
            <= unit["active_power"]
            <= unit["active_power_limits"]["max"] + 1e-6
        )
    ]
    assert not misdispatched, f"online units outside their limits: {misdispatched[:5]}"


def test_generation_covers_demand_with_a_plausible_loss(document: dict[str, Any]) -> None:
    """Balance is the cheapest evidence that the base was read correctly.

    A per-unit field read as MW, or MW read as per unit, moves this by two
    orders of magnitude. Bounded rather than pinned: the losses are what they
    are in the source's solution, and asserting the exact figure would make a
    re-solve upstream look like a conversion bug.
    """
    generation = sum(g["active_power"] for g in document["components"]["ThermalStandard"])
    demand = sum(load["active_power"] for load in document["components"]["PowerLoad"])
    losses = (generation - demand) / demand

    assert demand * BASE_MVA == pytest.approx(66_135, rel=0.01), "demand is not Korea-sized"
    assert 0.0 < losses < 0.05, (
        f"losses are {losses:.1%} of demand, which is not a transmission system: either "
        "the dispatch and the demand are on different bases or one of them is wrong"
    )


def test_an_offline_unit_is_committed_off_and_not_unavailable(document: dict[str, Any]) -> None:
    """The distinction the conversion has to preserve, and could lose in one line.

    MATPOWER has one `status` column and Sienna has two concepts. Half this
    fleet is off in the registered hour because an optimiser chose that, not
    because those plants are broken, and mapping commitment onto `available`
    would delete a hundred units from anything that re-commits.
    """
    units = document["components"]["ThermalStandard"]
    offline = [u for u in units if u["status"] == "OFFLINE"]

    assert offline, "no unit is committed off, which is not the case this model carries"
    assert all(u["available"] for u in units), "commitment state leaked into availability"
    assert all(u["active_power"] == 0 for u in offline), "an offline unit is dispatching"


def test_every_unit_carries_a_cost_curve_with_its_own_coefficients(
    document: dict[str, Any],
) -> None:
    """The join across three files, checked by its consequence.

    `mpc.gen`, `mpc.gencost` and the generator metadata are matched by row
    order. If that slips, every unit still gets *a* curve and the document
    still validates — the failure is silent and it is a plant priced as its
    neighbour. Distinct coefficients are what a slipped join cannot fake.
    """
    curves = [
        unit["operation_cost"]["variable_operation_cost"]["value_curve"]["function_data"]
        for unit in document["components"]["ThermalStandard"]
    ]
    assert all(c["function_type"] == "QUADRATIC" for c in curves)
    assert all(c["quadratic_term"] >= 0 for c in curves), "a non-convex heat rate"

    distinct = {(c["quadratic_term"], c["proportional_term"], c["constant_term"]) for c in curves}
    assert len(distinct) > len(curves) * 0.8, (
        f"only {len(distinct)} distinct cost curves across {len(curves)} units, which "
        "is what a slipped row-order join looks like"
    )


def test_the_source_files_are_identified_by_digest_at_a_pinned_commit(
    provenance: dict[str, Any],
) -> None:
    """A branch is not a citation. The commit is."""
    source = provenance["source"]
    assert len(source["commit"]) == 40, "the source is not pinned to a commit"
    assert source["version"] == "v2.0.0"
    files = source["files"]
    assert set(files) == {
        "KPG193_ver2_0.m",
        "bus_metadata_2025.csv",
        "generator_metadata_2025.csv",
    }
    for name, entry in files.items():
        assert len(entry["sha256"]) == 64, f"{name} has no usable digest"
        assert entry["bytes"] > 0


def test_the_upstream_inconsistency_is_recorded_rather_than_resolved_silently(
    provenance: dict[str, Any],
) -> None:
    """Two units sit at different buses in the case and in the metadata.

    The Samcheok Green coal units are at bus 75 in `mpc.gen` and bus 82 in the
    generator metadata. Bus 75 is Samcheok and bus 82 is Uljin, so the case
    agrees with the plant's own name and the metadata does not — which is why
    the conversion follows the case. That reasoning is worth keeping where a
    reader can check it: a silent choice between two disagreeing sources is
    indistinguishable from not having noticed.
    """
    recorded = provenance["upstream_inconsistencies"]
    assert recorded, "the conversion found no disagreement, which it should have"
    for entry in recorded:
        assert entry["case_bus"] != entry["metadata_bus"]
        assert entry["case_bus_name"] and entry["metadata_bus_name"]


def test_the_record_counts_the_elements_the_document_actually_has(
    document: dict[str, Any],
) -> None:
    record = sienna.load_record(FIXTURE)
    components = document["components"]
    actual = sum(
        len(components[key])
        for key in ("ACBus", "Line", "ThermalStandard", "PowerLoad", "TwoTerminalGenericHVDCLine")
    )
    assert record["networkElementCount"] == actual, (
        f"the record claims {record['networkElementCount']} network elements; the "
        f"document holds {actual}. Regenerate one or correct the other."
    )


def test_the_record_does_not_repeat_the_undispatchable_caveat(document: dict[str, Any]) -> None:
    """The other two models say they cannot be dispatched. This one can.

    Copying a record is how the caveat would arrive, and it would be false — a
    reader who believed it would go looking elsewhere for the thing they were
    already holding.
    """
    record = sienna.load_record(FIXTURE)
    text = f"{record['summary']} {record['description']}".lower()
    assert "cannot be dispatched" not in text
    assert document["components"]["PowerLoad"], "a system with no demand is a network"
    assert document["components"]["ThermalStandard"], "a system with no generation is a network"
