"""The Cascade Interconnect validates against the real Sienna schemas.

Not against a summary of them, and not against a hand-written copy of the
fields we happened to remember. `vendor/siennaschemas` is pinned at a commit
and this walks it: every component row is checked against the schema for its
own type, with `$ref`s resolved across the vendored tree the way upstream's
relative paths expect.

That distinction earned its keep on the first run. Two invented shapes passed
review and failed here: solar's prime mover spelled `PV` where the closed
vocabulary says `PVe`, and a `{start_up_type, value}` wrapper around a
start-up cost that `oneOf [number, StartUpStages]` accepts in neither form.
Both would have shipped a document that says "conforms to sienna-schemas
3325d27" and does not.

It also missed one for weeks, which is why the checks now live in `sienna.py`
and run against every model rather than this one. The document was never
validated against `SystemDocument` itself — only its rows against their own
schemas — so `supplemental_attributes` shipped as a map keyed by type where the
schema says a flat, untyped array. Building a second model found it in the
first minute.
"""

from __future__ import annotations

from typing import Any

import pytest
from referencing import Registry

from tests.reference_models import sienna

MODEL = "cascade-interconnect"
FIXTURE = "cascade-reference-model"


@pytest.fixture(scope="module")
def registry() -> Registry:
    return sienna.schema_registry()


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return sienna.load_system(MODEL)


def test_the_document_and_every_row_in_it_validate(
    document: dict[str, Any], registry: Registry
) -> None:
    sienna.assert_document_validates(document, registry)


def test_the_document_carries_every_required_container(document: dict[str, Any]) -> None:
    """A `SystemDocument` is eight containers, and an absent one is not an empty
    one: a consumer that indexes into `plant_associations` gets a KeyError
    rather than an empty result."""
    sienna.assert_required_containers(document)


def test_components_are_emitted_in_sorted_order(document: dict[str, Any]) -> None:
    """`SystemDocument` says keys are emitted in sorted order so output is
    deterministic. A document that churns key order rewrites itself on every
    build and makes every diff unreadable."""
    keys = list(document["components"])
    assert keys == sorted(keys)


def test_the_pin_is_the_one_on_disk() -> None:
    assert sienna.PINNED in (sienna.VENDOR / "PINNED.md").read_text()


def test_every_bus_has_a_location(document: dict[str, Any]) -> None:
    """Without coordinates the model cannot be drawn, and a reference model
    nobody can look at is one nobody will check."""
    sienna.assert_every_bus_is_located(document)


def test_the_network_is_connected(document: dict[str, Any]) -> None:
    sienna.assert_connected(document)


def test_every_line_refers_to_a_real_arc_and_every_arc_to_real_buses(
    document: dict[str, Any],
) -> None:
    sienna.assert_referential_integrity(document)


def test_the_record_counts_the_elements_the_document_actually_has(
    document: dict[str, Any],
) -> None:
    """The catalog record and the bytes it describes, checked against each other.

    This is the assertion that was missing, and its absence cost a red build.
    The number lived in three places — the document, the record, and a literal
    in the projector's test — with nothing connecting any pair of them. The
    generator regenerated the network, the record was updated to match, and the
    third copy sat at a figure two generations stale, asserting a fact about a
    file it never opened.

    Reading the record is what makes this a check rather than a tautology: the
    projector's tests can now assert against the record and be honest, because
    something has established that the record agrees with the data.
    """
    record = sienna.load_record(FIXTURE)
    actual = sienna.network_element_count(document)
    assert record["networkElementCount"] == actual, (
        f"the record claims {record['networkElementCount']} network elements; "
        f"the document holds {actual}. Regenerate one or correct the other."
    )


def test_the_distribution_states_the_size_of_the_file_it_points_at() -> None:
    """`byteSize` is a promise about a specific file, and it is checkable here.

    A stale one is worse than an absent one: a client sizing a download or a
    reader judging whether a model is tractable is given a number that looks
    authoritative and describes a file that no longer exists.
    """
    distribution = sienna.load_distribution(FIXTURE, "sienna")
    assert distribution["byteSize"] == sienna.system_path(MODEL).stat().st_size
