"""Generate the Cascade Interconnect reference model as a Sienna SystemDocument.

A synthetic network for an invented service territory, built to be
topologically plausible at system scale and to correspond to no real asset.
That second half is the point: the record declares `og:fidelityClass
"screening"` and rates local congestion *fragile*, and a generator that placed
buses on real substations would make that declaration a lie.

Deterministic — a fixed seed and no clock — so a rebuild produces byte-identical
output and a diff of this file means somebody changed the generator.

Output validates against the vendored SiennaSchemas at the pinned commit, which
is what makes it a Sienna document rather than a JSON file shaped like one.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "reference-models" / "cascade-interconnect" / "system.json"

#: Fixed, and the only source of randomness. See the module docstring.
SEED = 20260312

#: Invented geography. A coastal strip, a valley and an interior plateau —
#: chosen so the three zones have visibly different shapes on a map, because a
#: network whose zones are three identical blobs reads as a diagram rather than
#: a system.
ZONES: list[dict[str, Any]] = [
    {"id": 1, "name": "Cascade Coast", "lon": (-124.4, -122.9), "lat": (45.2, 48.4), "buses": 30},
    {"id": 2, "name": "Cascade Valley", "lon": (-122.9, -121.4), "lat": (45.6, 48.0), "buses": 34},
    {
        "id": 3,
        "name": "Cascade Interior",
        "lon": (-121.4, -118.8),
        "lat": (45.0, 48.6),
        "buses": 26,
    },
]

#: Technology mix. Hydro-heavy with a thermal spine and growing wind and solar,
#: which is what a plausible Pacific-Northwest-shaped system looks like without
#: being any particular one.
THERMAL = [
    ("CCGT", "GT", "NATURAL_GAS", 320.0, 18.4),
    ("CCGT", "GT", "NATURAL_GAS", 280.0, 19.1),
    ("Peaker", "GT", "NATURAL_GAS", 95.0, 42.6),
    ("Cogen", "CT", "NATURAL_GAS", 140.0, 24.8),
]
#: `PVe`, not `PV`. The closed `PrimeMovers` vocabulary spells photovoltaic
#: with the trailing e, and the validator caught the guess — which is the
#: argument for validating against the real schemas rather than a summary
#: of them.
RENEWABLE = [("Wind", "WT", 0.95), ("Solar", "PVe", 1.0), ("Hydro", "HY", 0.98)]

BASE_POWER = 100.0
UNITS = "NATURAL_UNITS"


def minmax(lo: float, hi: float) -> dict[str, float]:
    return {"min": lo, "max": hi}


def linear(proportional: float, constant: float = 0.0) -> dict[str, Any]:
    return {
        "function_type": "LINEAR",
        "proportional_term": proportional,
        "constant_term": constant,
    }


def cost_curve(rate: float) -> dict[str, Any]:
    """A cost curve, the long way round, because Sienna's shape is the point.

    `variable_operation_cost` is not a number: it is a value curve with a
    declared unit basis and a VOM curve of its own. Flattening it to $/MWh here
    would produce a document that validates against nothing.
    """
    return {
        "variable_cost_type": "COST",
        "power_units": UNITS,
        "value_curve": {"curve_type": "INPUT_OUTPUT", "function_data": linear(rate)},
        "vom_cost": {"curve_type": "INPUT_OUTPUT", "function_data": linear(0.0)},
    }


def build() -> dict[str, Any]:
    rng = random.Random(SEED)
    components: dict[str, list[dict[str, Any]]] = {}
    geo: list[dict[str, Any]] = []
    assoc: list[dict[str, Any]] = []
    next_id = iter(range(1, 100_000))

    def nid() -> int:
        return next(next_id)

    # -- areas and zones ---------------------------------------------------
    areas, zones, buses = [], [], []
    zone_of_bus: dict[int, int] = {}
    for spec in ZONES:
        area_id, zone_id = nid(), nid()
        areas.append(
            {
                "id": area_id,
                "name": spec["name"],
                "base_power": BASE_POWER,
                "power_units": UNITS,
            }
        )
        spec["_area"] = area_id
        spec["_zone"] = zone_id

    # peak demand is filled in once the loads exist
    for spec in ZONES:
        for _ in range(spec["buses"]):
            bus_id = nid()
            lon = rng.uniform(*spec["lon"])
            lat = rng.uniform(*spec["lat"])
            buses.append(
                {
                    "id": bus_id,
                    "number": bus_id,
                    "name": f"{spec['name'].split()[-1][:3].upper()}-{bus_id:04d}",
                    "available": True,
                    "bustype": "PQ",
                    "angle": 0.0,
                    "magnitude": 1.0,
                    "voltage_limits": minmax(0.95, 1.05),
                    "base_voltage": rng.choice([115.0, 230.0, 500.0]),
                    "area": spec["_area"],
                    "load_zone": spec["_zone"],
                }
            )
            zone_of_bus[bus_id] = spec["_zone"]
            geo_id = nid()
            geo.append(
                {
                    "id": geo_id,
                    "geo_json": {"type": "Point", "coordinates": [round(lon, 5), round(lat, 5)]},
                }
            )
            assoc.append(
                {
                    "component_id": bus_id,
                    "component_type": "ACBus",
                    "attribute_id": geo_id,
                    "attribute_type": "GeographicInfo",
                }
            )
    buses[0]["bustype"] = "REF"

    # -- branches ----------------------------------------------------------
    # Each bus links to its nearest unconnected neighbour first (a spanning
    # tree, so the network is connected), then a few long ties per zone pair so
    # it is meshed rather than radial. A radial synthetic network would make
    # every contingency trivial and the fragile rating meaningless.
    coords = {b["id"]: geo[i]["geo_json"]["coordinates"] for i, b in enumerate(buses)}

    def distance(a: int, b: int) -> float:
        (x1, y1), (x2, y2) = coords[a], coords[b]
        return math.hypot(x1 - x2, y1 - y2)

    arcs, lines = [], []
    connected = {buses[0]["id"]}
    remaining = [b["id"] for b in buses[1:]]
    while remaining:
        best = min(((c, r) for c in connected for r in remaining), key=lambda pair: distance(*pair))
        source, target = best
        connected.add(target)
        remaining.remove(target)
        arcs.append({"id": nid(), "from_id": source, "to_id": target})

    # Mesh by proximity, not at random.
    #
    # The first version added six ties per zone pair between randomly chosen
    # buses plus twenty-two random pairs anywhere. Drawn on a map that looked
    # like spaghetti: long straight lines crossing the whole territory and each
    # other, which no transmission network does. A reader who knows the domain
    # dismisses the model in one glance, and they are right to.
    #
    # Each bus instead gets ties to its nearest neighbours that the spanning
    # tree did not already connect it to. That produces the local triangles a
    # real meshed network has, keeps line lengths plausible against their
    # ratings, and still leaves the network meshed rather than radial — which
    # the fragile congestion rating depends on, since every contingency on a
    # radial network is trivial.
    existing = {frozenset((a["from_id"], a["to_id"])) for a in arcs}
    ordered = [b["id"] for b in buses]
    for bus in ordered:
        nearest = sorted((b for b in ordered if b != bus), key=lambda other: distance(bus, other))
        added = 0
        for candidate in nearest[:6]:
            if added >= 2:
                break
            pair = frozenset((bus, candidate))
            if pair in existing:
                continue
            existing.add(pair)
            arcs.append({"id": nid(), "from_id": bus, "to_id": candidate})
            added += 1

    for arc in arcs:
        length = max(distance(arc["from_id"], arc["to_id"]), 0.01)
        lines.append(
            {
                "id": nid(),
                "name": f"L-{arc['id']:05d}",
                "available": True,
                "active_power_flow": 0.0,
                "reactive_power_flow": 0.0,
                "arc": arc["id"],
                "r": round(0.0006 * length, 6),
                "x": round(0.0085 * length, 6),
                "b": {"from": 0.0, "to": 0.0},
                "rating": float(rng.choice([120, 250, 400, 800])),
                "angle_limits": minmax(-0.6, 0.6),
                "base_power": BASE_POWER,
                "power_units": UNITS,
            }
        )

    # -- injections --------------------------------------------------------
    thermal, renewable, loads = [], [], []
    bus_ids = [b["id"] for b in buses]
    for i in range(28):
        label, prime, fuel, rating, rate = THERMAL[i % len(THERMAL)]
        thermal.append(
            {
                "id": nid(),
                "name": f"{label}-{i + 1:02d}",
                "available": True,
                "status": "ONLINE",
                "bus": rng.choice(bus_ids),
                "active_power": 0.0,
                "reactive_power": 0.0,
                "rating": rating,
                "active_power_limits": minmax(round(rating * 0.3, 1), rating),
                "prime_mover_type": prime,
                "fuel": fuel,
                "base_power": BASE_POWER,
                "power_units": UNITS,
                "operation_cost": {
                    "cost_type": "THERMAL",
                    "variable_operation_cost": cost_curve(rate),
                    "fixed": round(rating * 12.0, 1),
                    # `oneOf [number, StartUpStages]`. A bare number is the scalar
                    # case; the three-stage hot/warm/cold object is the other. An
                    # invented `{start_up_type, value}` wrapper matches neither,
                    # and did not until the validator said so.
                    "start_up": round(rating * 55.0, 1),
                    "shut_down": 0.0,
                },
            }
        )
    for i in range(34):
        label, prime, pf = RENEWABLE[i % len(RENEWABLE)]
        rating = float(rng.choice([45, 80, 120, 200, 310]))
        renewable.append(
            {
                "id": nid(),
                "name": f"{label}-{i + 1:02d}",
                "available": True,
                "bus": rng.choice(bus_ids),
                "active_power": 0.0,
                "reactive_power": 0.0,
                "rating": rating,
                "prime_mover_type": prime,
                "power_factor": pf,
                "base_power": BASE_POWER,
                "power_units": UNITS,
                "operation_cost": {
                    "cost_type": "RENEWABLE",
                    "variable_operation_cost": cost_curve(0.0),
                },
            }
        )
    zone_peak: dict[int, float] = {z["_zone"]: 0.0 for z in ZONES}
    for i in range(38):
        bus = rng.choice(bus_ids)
        peak = float(rng.choice([40, 75, 110, 180, 240]))
        zone_peak[zone_of_bus[bus]] += peak
        loads.append(
            {
                "id": nid(),
                "name": f"LOAD-{i + 1:02d}",
                "available": True,
                "bus": bus,
                "active_power": 0.0,
                "reactive_power": 0.0,
                "max_active_power": peak,
                "max_reactive_power": round(peak * 0.33, 2),
                "base_power": BASE_POWER,
                "power_units": UNITS,
            }
        )

    for spec in ZONES:
        zones.append(
            {
                "id": spec["_zone"],
                "name": spec["name"],
                "peak_active_power": round(zone_peak[spec["_zone"]], 1),
                "peak_reactive_power": round(zone_peak[spec["_zone"]] * 0.33, 1),
                "base_power": BASE_POWER,
                "power_units": UNITS,
            }
        )

    components = {
        "ACBus": buses,
        "Arc": arcs,
        "Area": areas,
        "Line": lines,
        "LoadZone": zones,
        "PowerLoad": loads,
        "RenewableDispatch": renewable,
        "ThermalStandard": thermal,
    }
    # Sorted keys, because `SystemDocument` says the map is emitted in sorted
    # order so output is deterministic and diffs read like source code.
    return {
        "name": "OpenGrid Reference: Cascade Interconnect",
        "description": (
            "A synthetic network for an invented service territory. Topologically "
            "plausible at system scale; corresponds to no real asset, and no bus, "
            "line or plant here is a real one."
        ),
        "frequency": 60.0,
        "components": {k: components[k] for k in sorted(components)},
        "supplemental_attributes": {"GeographicInfo": geo},
        "supplemental_attribute_associations": assoc,
        "plant_associations": [],
        "combined_cycle_associations": [],
        "service_associations": [],
        "trading_hub_associations": [],
        "time_series_associations": [],
        "time_series_storage_file": "cascade-interconnect-timeseries.h5",
    }


def main() -> None:
    document = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(document, indent=2, sort_keys=False) + "\n")
    counts = {k: len(v) for k, v in document["components"].items()}
    total = sum(counts.values())
    print(f"wrote {OUT.relative_to(ROOT)}")
    for k, v in counts.items():
        print(f"  {k:20} {v:4d}")
    print(f"  {'TOTAL':20} {total:4d}")


if __name__ == "__main__":
    main()
