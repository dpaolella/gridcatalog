"""A reference model projects as one, and a dataset is untouched by the change.

`record_type` is the field that lets one index hold four kinds of thing. The
risk it introduces is not that reference models fail to project — that is loud
— but that the discriminator is read wrongly and every record silently becomes
a dataset, which looks exactly like the state before the field existed.
"""

from __future__ import annotations

import json
from pathlib import Path

from datahub.projector.build import build_document

from tests.fixtures.loader import load_graph

REFERENCE_MODEL = "https://catalog.opengrid.org/ds/cascade-interconnect-reference"
DATASET = "https://catalog.opengrid.org/ds/ecmwf-era5"

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "registry"


def declared(fixture: str, field: str) -> object:
    """The value the record itself states, read straight out of the JSON.

    A literal here is a fourth copy of a number that already lives in the
    generator, the document and the record, and this test had one: it asserted
    312 network elements against a record that said 444, and went red on a
    commit that had regenerated the network and updated the record correctly.

    Reading the record closes that. It is not circular — the projector reaches
    this value through the context, the store's containment walk and a SPARQL
    construct, so "the record says 444 and the projection says 444" is a real
    claim about several hundred lines of code. What it does *not* establish is
    that the record is true of the bytes, and
    `tests/reference_models/test_cascade_system.py` asserts exactly that
    against `system.json`. The pair is what makes each half honest.
    """
    graph = json.loads((FIXTURE / f"{fixture}.jsonld").read_text())["@graph"]
    return next(node[field] for node in graph if field in node)


def test_a_reference_model_carries_its_declared_fidelity() -> None:
    doc = build_document(load_graph("cascade-reference-model"), REFERENCE_MODEL)

    assert doc.record_type == "reference_model"
    assert doc.fidelity_class == "screening"
    assert doc.network_element_count == declared("cascade-reference-model", "networkElementCount")


def test_an_ordinary_dataset_is_unchanged_by_the_discriminator() -> None:
    """The failure mode worth guarding. A discriminator read from the wrong
    predicate makes everything a dataset, which is indistinguishable from the
    state before it existed — every other assertion in this file would still
    pass."""
    doc = build_document(load_graph("ecmwf-era5"), DATASET)

    assert doc.record_type == "dataset"
    assert doc.fidelity_class is None, "fidelity on a dataset means the type was misread"
    assert doc.question_classes == []


def test_the_partition_leads_with_what_the_network_can_do() -> None:
    """Robust, then fragile, then unknown — and alphabetical within each.

    Ordered because it is a reading order, not a set: a partition that led with
    what the network cannot do would be honest and unusable. Sorted because
    rdflib's object order is not stable, so an unsorted projection churns the
    index on every rebuild and makes every diff unreadable.
    """
    doc = build_document(load_graph("cascade-reference-model"), REFERENCE_MODEL)

    assert [q.robustness for q in doc.question_classes] == [
        "robust",
        "robust",
        "fragile",
        "unknown",
    ]
    robust = [q.question_class for q in doc.question_classes if q.robustness == "robust"]
    assert robust == sorted(robust)


def test_a_claimed_strength_carries_its_basis_and_an_admitted_limit_need_not() -> None:
    """The shapes enforce this on the record; the projection must not lose it.

    A partition rendered without its basis is a list of adjectives, and the
    whole argument for declaring fidelity rather than writing it in a README is
    that a reader can see what the claim rests on.
    """
    doc = build_document(load_graph("cascade-reference-model"), REFERENCE_MODEL)
    by_robustness = {q.robustness: q for q in doc.question_classes}

    assert by_robustness["robust"].basis
    assert by_robustness["unknown"].basis is None
