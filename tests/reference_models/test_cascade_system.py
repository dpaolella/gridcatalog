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
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "vendor" / "siennaschemas"
SYSTEM = ROOT / "data" / "reference-models" / "cascade-interconnect" / "system.json"

#: The commit `vendor/siennaschemas/PINNED.md` records. Asserted here because a
#: record claiming to conform to a specific schema version is only meaningful
#: if something checks that the version is the one on disk.
PINNED = "3325d27c8c626a375c7492f1341af139d5686e53"


@pytest.fixture(scope="module")
def registry() -> Registry:
    """Every vendored schema, keyed by its path relative to the vendor root.

    Upstream's `$ref`s are relative and load-bearing — its contributor guide
    warns that moving a file breaks every reference to it — so the keys have to
    mirror the directory layout exactly.
    """
    resources = {}
    for path in VENDOR.rglob("*.json"):
        if path.name.startswith("openapi-"):
            continue
        uri = "file:///" + str(path.relative_to(VENDOR)).replace("\\", "/")
        resources[uri] = Resource.from_contents(
            json.loads(path.read_text()), default_specification=DRAFT202012
        )
    return Registry().with_resources(resources.items())


@pytest.fixture(scope="module")
def document() -> dict[str, Any]:
    return json.loads(SYSTEM.read_text())


def validator_for(type_name: str, registry: Registry) -> Draft202012Validator:
    matches = list(VENDOR.rglob(f"{type_name}.json"))
    assert matches, f"no vendored schema for component type {type_name!r}"
    schema = json.loads(matches[0].read_text())
    relative = matches[0].relative_to(VENDOR)
    schema["$id"] = "file:///" + str(relative).replace("\\", "/")
    return Draft202012Validator(schema, registry=registry)


def test_every_component_validates(document: dict[str, Any], registry: Registry) -> None:
    failures: list[str] = []
    for type_name, rows in document["components"].items():
        validator = validator_for(type_name, registry)
        for row in rows:
            for error in validator.iter_errors(row):
                failures.append(f"{type_name}[{row.get('id')}] {list(error.path)}: {error.message}")
    assert not failures, "\n".join(failures[:20])


def test_the_document_carries_every_required_container(document: dict[str, Any]) -> None:
    """`SystemDocument`'s eight required keys, present even when empty.

    An empty list and an absent key are different claims: the first says there
    are no service associations, the second says nobody looked.
    """
    required = json.loads((VENDOR / "Core" / "SystemDocument.json").read_text())["required"]
    missing = [key for key in required if key not in document]
    assert not missing, f"SystemDocument requires {missing}, and they are absent"


def test_components_are_emitted_in_sorted_order(document: dict[str, Any]) -> None:
    """Upstream says the map is emitted in sorted key order so output is
    deterministic and diffs read like source code. A generator that emitted
    insertion order would produce a different file on a Python upgrade."""
    keys = list(document["components"])
    assert keys == sorted(keys)


def test_the_pin_is_the_one_on_disk() -> None:
    pinned_md = (VENDOR / "PINNED.md").read_text()
    assert PINNED in pinned_md


def test_every_bus_has_a_location(document: dict[str, Any]) -> None:
    """A reference model you cannot draw is one nobody will start from.

    GIS rides as a supplemental attribute rather than as fields on the bus,
    which is Sienna's design — coordinates are not part of what makes a bus a
    bus — so the association table is what makes the map possible and this
    checks it covers every one.
    """
    buses = {row["id"] for row in document["components"]["ACBus"]}
    located = {
        a["component_id"]
        for a in document["supplemental_attribute_associations"]
        if a["component_type"] == "ACBus" and a["attribute_type"] == "GeographicInfo"
    }
    assert buses <= located, f"{len(buses - located)} buses have no GeographicInfo"


def test_the_network_is_connected(document: dict[str, Any]) -> None:
    """An island would make a power-flow demo silently wrong rather than
    visibly broken, and the fragile congestion rating assumes a meshed
    network — a radial one makes every contingency trivial."""
    arcs = document["components"]["Arc"]
    buses = {row["id"] for row in document["components"]["ACBus"]}
    neighbours: dict[int, set[int]] = {b: set() for b in buses}
    for arc in arcs:
        neighbours[arc["from_id"]].add(arc["to_id"])
        neighbours[arc["to_id"]].add(arc["from_id"])

    seen = {next(iter(buses))}
    stack = [next(iter(buses))]
    while stack:
        for nxt in neighbours[stack.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    assert seen == buses, f"{len(buses - seen)} buses are unreachable from the rest"


def test_every_line_refers_to_a_real_arc_and_every_arc_to_real_buses(
    document: dict[str, Any],
) -> None:
    """The referential integrity a relational store would enforce for us and a
    JSON document will not."""
    buses = {row["id"] for row in document["components"]["ACBus"]}
    arcs = {row["id"] for row in document["components"]["Arc"]}
    for arc in document["components"]["Arc"]:
        assert arc["from_id"] in buses and arc["to_id"] in buses, f"arc {arc['id']} dangles"
    for line in document["components"]["Line"]:
        assert line["arc"] in arcs, (
            f"line {line['id']} names arc {line['arc']}, which does not exist"
        )
    for group in ("ThermalStandard", "RenewableDispatch", "PowerLoad"):
        for row in document["components"][group]:
            assert row["bus"] in buses, f"{group} {row['id']} sits on a bus that does not exist"
