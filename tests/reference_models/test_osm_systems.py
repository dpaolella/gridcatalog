"""Each OSM model is real where it says it is and estimated where it says it is.

The claim that makes these models worth having: the topology is measured and
the electrical parameters are not, and the registry's whole argument is that a
reader can tell which is which *per value* rather than being asked to trust a
sentence in a description.

So these tests are mostly about `parameters.json`. An estimate with no recorded
method is a number that looks exactly like a measurement, and the failure that
matters here is not a wrong impedance — it is a right-looking one that nothing
marks as invented.

**Every test runs against every model.** One builder now serves several
countries, and the risk that introduces is not a build that fails — that is
loud — but one that succeeds by quietly applying the first country's answers to
the second. A suite written against one model would not see it.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest
from referencing import Registry

from tests.reference_models import sienna

#: Slug to record fixture. Adding a geography adds it to every test below.
MODELS = {
    "gb-osm": "gb-osm-reference-model",
    "de-osm": "de-osm-reference-model",
}

#: KPG 193 is deliberately not in `MODELS`. It is a registered model and it
#: gets the same structural checks, in `test_kpg_system.py` — but it is a
#: conversion of somebody else's published case rather than a build from the
#: PyPSA-Eur extract, and almost everything below is about *this builder's*
#: provenance discipline: `parameters.json` shaped per branch, standard line
#: types, substitution notes. Running those against a document that was never
#: built that way would either fail for the wrong reason or be quietly
#: weakened until it passed, and the second is worse.


@pytest.fixture(scope="module")
def registry() -> Registry:
    return sienna.schema_registry()


@pytest.fixture(scope="module", params=sorted(MODELS))
def model(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture(scope="module")
def document(model: str) -> dict[str, Any]:
    return sienna.load_system(model)


@pytest.fixture(scope="module")
def provenance(model: str) -> dict[str, Any]:
    return json.loads((sienna.MODELS / model / "parameters.json").read_text())


def test_the_document_and_every_row_in_it_validate(
    document: dict[str, Any], registry: Registry
) -> None:
    sienna.assert_document_validates(document, registry)


def test_the_document_carries_every_required_container(document: dict[str, Any]) -> None:
    sienna.assert_required_containers(document)


def test_components_are_emitted_in_sorted_order(document: dict[str, Any]) -> None:
    keys = list(document["components"])
    assert keys == sorted(keys)


def test_every_bus_has_a_location(document: dict[str, Any]) -> None:
    sienna.assert_every_bus_is_located(document)


def test_the_network_is_connected(document: dict[str, Any]) -> None:
    """The assertion that shaped the whole subset.

    Over lines alone every one of these extracts falls into pieces — 48 for
    Great Britain, 37 for Germany — because OSM models a substation as one bus
    per voltage level and the *transformers* are what tie the levels together
    at the same site. A subset cut on lines alone would be a pile of fragments
    that still looked like a map of the country, and every flow result computed
    on it would be wrong with nothing to say so.
    """
    sienna.assert_connected(document)


def test_referential_integrity(document: dict[str, Any]) -> None:
    sienna.assert_referential_integrity(document)


def test_the_transformers_are_what_connect_the_voltage_levels(
    document: dict[str, Any],
) -> None:
    """Not incidental: without them there is no connected network, anywhere.

    Asserted rather than assumed, because a future change that drops
    transformers would leave a document that still validates, still has every
    bus it had, and is silently dozens of separate networks. This pins the
    mechanism, not just the outcome — and pins it per model, since "the
    transformers are load-bearing" is a claim about each country's mapping.
    """
    buses = {row["id"]: row for row in document["components"]["ACBus"]}
    arcs = {row["id"]: row for row in document["components"]["Arc"]}
    circuits = {row["id"]: row for row in document["components"]["TransformerCircuit"]}

    crossing = 0
    for transformer in document["components"]["TwoWindingTransformer"]:
        arc = arcs[circuits[transformer["circuit"]]["arc"]]
        if buses[arc["from_id"]]["base_voltage"] != buses[arc["to_id"]]["base_voltage"]:
            crossing += 1
    assert crossing > 0, "no transformer joins two voltage levels"

    line_only = {
        "components": {
            "ACBus": document["components"]["ACBus"],
            "Arc": [arcs[line["arc"]] for line in document["components"]["Line"]],
        }
    }
    with pytest.raises(AssertionError):
        sienna.assert_connected(line_only)


def test_every_estimated_value_names_the_rule_that_produced_it(
    provenance: dict[str, Any],
) -> None:
    """Per row, not per document. This is the model's entire reason to exist."""
    rows = provenance["lines"] + provenance["transformers"]
    assert rows, "no parameter provenance recorded at all"
    for row in rows:
        assert row["basis"] == "estimated", f"{row['component']} claims a basis it did not earn"
        assert row["method"], f"{row['component']} records no method"
        assert row["inputs"], f"{row['component']} records no inputs"


def test_the_provenance_covers_every_branch_in_the_document(
    document: dict[str, Any], provenance: dict[str, Any]
) -> None:
    """A branch missing from `parameters.json` is a number presented as a fact.

    The direction of this check is the point: it is not "is the provenance
    valid" but "is anything in the document unaccounted for". A file that
    documents nine tenths of the estimates is worse than none, because it
    invites the reader to assume the tenth was measured.
    """
    documented = {row["component"] for row in provenance["lines"]}
    documented |= {row["component"] for row in provenance["transformers"]}
    in_document = {row["name"] for row in document["components"]["Line"]}
    in_document |= {row["name"] for row in document["components"]["TwoWindingTransformer"]}
    assert in_document == documented, (
        f"{len(in_document - documented)} branches carry estimated parameters that "
        f"nothing accounts for"
    )


def test_the_estimates_in_the_document_are_the_ones_the_provenance_records(
    document: dict[str, Any], provenance: dict[str, Any]
) -> None:
    """The provenance describes *these* bytes, or it describes nothing.

    Recomputing from the recorded inputs rather than comparing two copies of an
    answer: this fails if the document was edited by hand, if the estimator
    changed without the sidecar being regenerated, and — the one that matters —
    if `b` is ever split per end wrongly again.
    """
    by_name = {row["component"]: row for row in provenance["lines"]}
    for line in document["components"]["Line"]:
        record = by_name[line["name"]]
        inputs = record["inputs"]
        n = inputs["circuits"]
        expected_r = inputs["r_per_km_ohm"] * inputs["length_km"] / n
        expected_x = inputs["x_per_km_ohm"] * inputs["length_km"] / n
        assert line["r"] == pytest.approx(expected_r, abs=1e-3)
        assert line["x"] == pytest.approx(expected_x, abs=1e-3)
        # Susceptance is halved to each end of the pi model, so the two ends
        # must sum back to the total the sidecar states.
        assert line["b"]["from"] + line["b"]["to"] == pytest.approx(record["b_siemens"], abs=1e-8)
        expected_rating = math.sqrt(3) * inputs["voltage_kv"] * inputs["i_nom_ka"] * n
        assert line["rating"] == pytest.approx(expected_rating, abs=0.1)


def test_an_underground_circuit_says_the_overhead_type_is_wrong_for_it(
    provenance: dict[str, Any],
) -> None:
    """The known error, named rather than averaged away.

    Some circuits in each extract are underground — 45 in Great Britain, nine
    in Germany — and all of them get an overhead line type, because that is
    what the standard tables provide. A cable's capacitance is an order of
    magnitude higher and its reactance far lower, so these values are wrong in
    a known direction, which is a different and much more useful thing to tell
    a reader than "uncertain".
    """
    underground = [
        row
        for row in provenance["lines"]
        if any("underground" in note for note in row["approximations"])
    ]
    assert underground, "no line records the overhead-type approximation"


def test_the_source_files_are_identified_by_digest(provenance: dict[str, Any]) -> None:
    """Provenance that names a DOI and not a digest is a citation, not a chain.

    Zenodo records get new versions; a DOI alone does not say which bytes were
    read. These digests make the claim checkable against the archive.
    """
    files = provenance["source"]["files"]
    assert set(files) == {"buses.csv", "lines.csv", "transformers.csv"}
    for name, entry in files.items():
        assert len(entry["sha256"]) == 64, f"{name} has no usable digest"
        assert entry["bytes"] > 0


def test_the_share_alike_obligation_is_recorded_in_both_places(
    document: dict[str, Any], provenance: dict[str, Any]
) -> None:
    """ODbL share-alike is the open question this model exists to make visible.

    In the document as well as the sidecar, because the document is what a
    modeller downloads and opens, and an obligation recorded only in a file
    beside it is one they will meet after they have already built on it.
    """
    assert provenance["source"]["share_alike"] is True
    assert document["ext"]["opengrid"]["share_alike"] is True
    assert document["ext"]["opengrid"]["parameter_basis"] == "estimated"


def test_the_record_counts_the_elements_the_document_actually_has(
    document: dict[str, Any], model: str
) -> None:
    record = sienna.load_record(MODELS[model])
    actual = sienna.network_element_count(document)
    assert record["networkElementCount"] == actual, (
        f"the record claims {record['networkElementCount']} network elements; "
        f"the document holds {actual}. Regenerate one or correct the other."
    )


def test_the_distribution_states_the_size_of_the_file_it_points_at(model: str) -> None:
    distribution = sienna.load_distribution(MODELS[model], "sienna")
    assert distribution["byteSize"] == sienna.system_path(model).stat().st_size


def test_the_record_declares_the_share_alike_obligation(model: str) -> None:
    """On the record, where a reader meets it before downloading anything."""
    record = sienna.load_record(MODELS[model])
    assert record["shareAlike"] is True


#: Which standard line type each country's circuits are entitled to, and where
#: that is a substitution rather than a match.
#:
#: This is the check that the generalised builder did not quietly carry one
#: country's answers into another. GB has no 380 kV class and its 400 kV
#: circuits borrow the 380 kV type, which every one of them records; Germany's
#: network *is* 380 kV and takes that type natively, so a substitution note on
#: a German 380 kV circuit would mean the wrong table was consulted.
SUBSTITUTIONS = {
    "gb-osm": {220.0: False, 275.0: True, 400.0: True},
    "de-osm": {220.0: False, 380.0: False, 400.0: True},
}


def test_a_borrowed_line_type_says_so_and_a_native_one_does_not(
    provenance: dict[str, Any], model: str
) -> None:
    expected = SUBSTITUTIONS[model]
    seen: dict[float, bool] = {}
    for row in provenance["lines"]:
        voltage = row["inputs"]["voltage_kv"]
        borrowed = any("no standard type" in note for note in row["approximations"])
        if voltage in seen:
            assert seen[voltage] == borrowed, (
                f"{model}: {voltage:.0f} kV circuits disagree about whether their "
                "line type is a substitution"
            )
        seen[voltage] = borrowed

    assert seen == expected, (
        f"{model}: voltage classes and their substitution status are {seen}, "
        f"expected {expected}. A class that silently changed status means the "
        "line-type table moved under a model that had already been published."
    )
