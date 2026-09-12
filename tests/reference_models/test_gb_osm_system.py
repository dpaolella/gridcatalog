"""The GB model is real where it says it is and estimated where it says it is.

Everything `test_cascade_system.py` asserts holds here too, through the shared
helpers. What is specific to this model is the claim that makes it worth having
at all: its topology is measured and its electrical parameters are not, and the
registry's whole argument is that a reader can tell which is which *per value*
rather than being asked to trust a sentence in a description.

So these tests are mostly about `parameters.json`. An estimate with no
recorded method is a number that looks exactly like a measurement, and the
failure that matters here is not a wrong impedance — it is a right-looking one
that nothing marks as invented.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest
from referencing import Registry

from tests.reference_models import sienna

MODEL = "gb-osm"
FIXTURE = "gb-osm-reference-model"


@pytest.fixture(scope="module")
def registry() -> Registry:
    return sienna.schema_registry()


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return sienna.load_system(MODEL)


@pytest.fixture(scope="module")
def provenance() -> dict[str, Any]:
    return json.loads((sienna.MODELS / MODEL / "parameters.json").read_text())


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

    Over lines alone this extract falls into 48 pieces, because OSM models a
    substation as one bus per voltage level and the *transformers* are what tie
    400 kV to 275 kV at the same site. A subset cut on lines alone would be a
    pile of fragments that still looked like a map of Britain, and every flow
    result computed on it would be wrong with nothing to say so.
    """
    sienna.assert_connected(document)


def test_referential_integrity(document: dict[str, Any]) -> None:
    sienna.assert_referential_integrity(document)


def test_the_transformers_are_what_connect_the_voltage_levels(
    document: dict[str, Any],
) -> None:
    """Not incidental: without them there is no connected GB network.

    Asserted rather than assumed, because a future change that drops
    transformers would leave a document that still validates, still has 412
    buses, and is silently four dozen separate networks. This pins the
    mechanism, not just the outcome.
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

    45 of these circuits are underground and all of them get an overhead line
    type, because that is what the standard tables provide. A cable's
    capacitance is an order of magnitude higher and its reactance far lower, so
    these values are wrong in a known direction — which is a different and much
    more useful thing to tell a reader than "uncertain".
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
    document: dict[str, Any],
) -> None:
    record = sienna.load_record(FIXTURE)
    actual = sienna.network_element_count(document)
    assert record["networkElementCount"] == actual, (
        f"the record claims {record['networkElementCount']} network elements; "
        f"the document holds {actual}. Regenerate one or correct the other."
    )


def test_the_distribution_states_the_size_of_the_file_it_points_at() -> None:
    distribution = sienna.load_distribution(FIXTURE, "sienna")
    assert distribution["byteSize"] == sienna.system_path(MODEL).stat().st_size


def test_the_record_declares_the_share_alike_obligation() -> None:
    """On the record, where a reader meets it before downloading anything."""
    record = sienna.load_record(FIXTURE)
    assert record["shareAlike"] is True
