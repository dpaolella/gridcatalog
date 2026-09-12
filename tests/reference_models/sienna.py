"""Validating a Sienna `SystemDocument` against the vendored schemas.

Extracted from `test_cascade_system.py`, which validated every component row
against its own schema and never validated the *document* against
`SystemDocument`. `supplemental_attributes` shipped as a map keyed by type
where the schema says a flat, untyped array, and it shipped for weeks under a
test whose docstring says the model "validates against the real Sienna
schemas".

A module rather than a file of one model's tests is how that stops recurring: a
check written once applies to every reference model, and the next one inherits
the whole set by existing rather than by someone remembering to copy it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "vendor" / "siennaschemas"
MODELS = ROOT / "data" / "reference-models"
REGISTRY_FIXTURES = ROOT / "tests" / "fixtures" / "registry"

#: The commit `vendor/siennaschemas/PINNED.md` records. Asserted because a
#: record claiming to conform to a specific schema version is only meaningful
#: if something checks that the version is the one on disk.
PINNED = "3325d27c8c626a375c7492f1341af139d5686e53"

#: The component groups a reference model's `networkElementCount` counts.
#:
#: Not every row in the document. An `Arc` is a branch's topology rather than a
#: second element, and `Area` and `LoadZone` are aggregations *of* elements, so
#: counting them would inflate the figure by the same buses twice. What is left
#: is the set a modeller would recognise as "things in the network".
NETWORK_ELEMENT_GROUPS = (
    "ACBus",
    "Line",
    "PowerLoad",
    "RenewableDispatch",
    "ThermalStandard",
    "TwoWindingTransformer",
)


def schema_registry() -> Registry:
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


def validator_for(type_name: str, registry: Registry) -> Draft202012Validator:
    matches = list(VENDOR.rglob(f"{type_name}.json"))
    assert matches, f"no vendored schema for type {type_name!r}"
    schema = json.loads(matches[0].read_text())
    relative = matches[0].relative_to(VENDOR)
    schema["$id"] = "file:///" + str(relative).replace("\\", "/")
    return Draft202012Validator(schema, registry=registry)


def system_path(model: str) -> Path:
    return MODELS / model / "system.json"


def load_system(model: str) -> dict[str, Any]:
    return json.loads(system_path(model).read_text())


def load_record(fixture: str) -> dict[str, Any]:
    """The `ReferenceModel` node of a registry fixture."""
    graph = json.loads((REGISTRY_FIXTURES / f"{fixture}.jsonld").read_text())["@graph"]
    return next(node for node in graph if "ReferenceModel" in node.get("type", []))


def load_distribution(fixture: str, identifier: str) -> dict[str, Any]:
    graph = json.loads((REGISTRY_FIXTURES / f"{fixture}.jsonld").read_text())["@graph"]
    return next(
        node
        for node in graph
        if node.get("type") == "Distribution" and node["id"].endswith(identifier)
    )


def assert_document_validates(document: dict[str, Any], registry: Registry) -> None:
    """The document, every component row, and every supplemental attribute.

    All three, because the omission that made this function necessary was one
    of them being checked and the others not.
    """
    errors = list(validator_for("SystemDocument", registry).iter_errors(document))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors[:5])

    for type_name, rows in document["components"].items():
        validator = validator_for(type_name, registry)
        for row in rows:
            found = list(validator.iter_errors(row))
            assert not found, f"{type_name} {row.get('name', row['id'])}: {found[0].message}"

    by_id = {a["id"]: a for a in document["supplemental_attributes"]}
    assert len(by_id) == len(document["supplemental_attributes"]), (
        "two supplemental attributes share an id"
    )
    for association in document["supplemental_attribute_associations"]:
        attribute = by_id.get(association["attribute_id"])
        assert attribute is not None, (
            f"association names attribute {association['attribute_id']}, which is absent"
        )
        validator = validator_for(association["attribute_type"], registry)
        found = list(validator.iter_errors(attribute))
        assert not found, f"{association['attribute_type']} {attribute['id']}: {found[0].message}"


def assert_required_containers(document: dict[str, Any]) -> None:
    """The eight containers `SystemDocument` requires.

    An absent container is not an empty one: a consumer that indexes into
    `plant_associations` gets a KeyError rather than an empty result.
    """
    required = (
        "components",
        "supplemental_attributes",
        "supplemental_attribute_associations",
        "plant_associations",
        "combined_cycle_associations",
        "service_associations",
        "time_series_associations",
        "time_series_storage_file",
    )
    for container in required:
        assert container in document, f"{container} is missing"


def assert_every_bus_is_located(document: dict[str, Any]) -> None:
    """Without coordinates the model cannot be drawn, and a reference model
    nobody can look at is one nobody will check."""
    buses = {row["id"] for row in document["components"]["ACBus"]}
    located = {
        a["component_id"]
        for a in document["supplemental_attribute_associations"]
        if a["component_type"] == "ACBus" and a["attribute_type"] == "GeographicInfo"
    }
    assert buses == located, f"{len(buses - located)} buses have no location"


def assert_connected(document: dict[str, Any]) -> None:
    """One network, reachable from any bus.

    A disconnected reference model is not a reference model with a flaw; it is
    several networks in one file, and every flow result computed on it is wrong
    in a way no solver reports.
    """
    buses = {row["id"] for row in document["components"]["ACBus"]}
    neighbours: dict[int, set[int]] = {bus: set() for bus in buses}
    for arc in document["components"]["Arc"]:
        neighbours[arc["from_id"]].add(arc["to_id"])
        neighbours[arc["to_id"]].add(arc["from_id"])

    start = next(iter(buses))
    seen = {start}
    stack = [start]
    while stack:
        for nxt in neighbours[stack.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    assert seen == buses, f"{len(buses - seen)} buses are unreachable from the rest"


def assert_referential_integrity(document: dict[str, Any]) -> None:
    """The constraints a relational store would enforce and a JSON document
    will not."""
    components = document["components"]
    buses = {row["id"] for row in components["ACBus"]}
    arcs = {row["id"] for row in components["Arc"]}

    for arc in components["Arc"]:
        assert arc["from_id"] in buses and arc["to_id"] in buses, f"arc {arc['id']} dangles"
        assert arc["from_id"] != arc["to_id"], f"arc {arc['id']} is a self-loop"

    for branch_type in ("Line", "TransformerCircuit"):
        for row in components.get(branch_type, []):
            assert row["arc"] in arcs, (
                f"{branch_type} {row['id']} names arc {row['arc']}, which does not exist"
            )

    circuits = {row["id"] for row in components.get("TransformerCircuit", [])}
    for row in components.get("TwoWindingTransformer", []):
        assert row["circuit"] in circuits, (
            f"transformer {row['name']} names circuit {row['circuit']}, which does not exist"
        )

    for group in ("ThermalStandard", "RenewableDispatch", "PowerLoad"):
        for row in components.get(group, []):
            assert row["bus"] in buses, f"{group} {row['id']} sits on a bus that does not exist"


def network_element_count(document: dict[str, Any]) -> int:
    return sum(len(document["components"].get(group, [])) for group in NETWORK_ELEMENT_GROUPS)
