#!/usr/bin/env python3
"""KPG 193, the Korean grid test system, as a Sienna SystemDocument.

The first registered model that can be *run*. The GB and Germany models are
networks with no demand and no generation, and both records say so in as many
words: "this is a network, not a system, and it cannot be dispatched as it
stands." This one carries 193 buses of demand, a committed fleet of 201
thermal units with cost curves, and a solved AC operating point — which is the
thing a modeller actually wants a reference system for.

## Source

*KPG 193: A Synthetic Korean Power Grid Test System for Decarbonization
Studies*, Geonho Song and Jip Kim, AGM Center, Korea Institute of Energy
Technology. arXiv:2411.14756; repository `agm-center/kpg-testgrid`, **ODbL 1.0**
— the same licence as the two OSM models, so the share-alike notice applies
unchanged.

Pinned to the **v2.0.0 tag**, not a branch. Fetch it yourself; this script does
not download:

    SHA=5ab3e25d6d1a84e27d7de6d11169cb6f9a7dd29b
    mkdir -p var/kpg
    base="https://raw.githubusercontent.com/agm-center/kpg-testgrid/$SHA/kpg193_v2_0"
    curl -L -o var/kpg/KPG193_ver2_0.m "$base/network/m/KPG193_ver2_0.m"
    curl -L -o var/kpg/bus_metadata_2025.csv \\
      "$base/network/metadata/bus_metadata/bus_metadata_2025.csv"
    curl -L -o var/kpg/generator_metadata_2025.csv \\
      "$base/network/metadata/generator_metadata/generator_metadata_2025.csv"
    python ops/reference-models/build_kpg.py --source var/kpg

The SHA-256 of each file read is recorded in `parameters.json`, as for the
other models.

## What is measured, and what a clustered model means

The paper is explicit that the circuits come from survey data: *"Two types of
line data were extracted from the OSM database using the Overpass API: power
line for overhead lines and power cable for underground cables."* So the
topology shares its upstream with the GB and Germany models.

The **buses do not**. Clustering is applied *"based on the locations of
regional offices of the Korea Electric Power Corporation (KEPCO), the sole
transmission and distribution utility in Korea"*, so a KPG bus is a region and
not a site. That is why the paper's own title says synthetic, and it is a
materially different claim from "this coordinate is a substation" — which is
what the other two models say. The record class has to carry that difference
rather than leaving one word to mean both.

## Per unit, not ohms, and that is not a shortcut

184 of the 385 branches join buses at different nominal voltages — 154/345 and
345/765 kV couplings — and **every branch has `ratio = 0`**. KPG does not model
those couplings as transformers at all; the impedances are per-unit on the
100 MVA system base, which is exactly what makes a ratio-free branch between
two voltage levels meaningful.

Converting to ohms would mean choosing a base voltage for each of those 184
branches, and there is no correct choice. So the document rides
`COMPONENT_BASE` with `base_power` on every component, which the schema
describes as the intended form for values per-unitised against a shared base.
The GB and Germany documents are `NATURAL_UNITS` because their impedances were
computed in ohms from a per-kilometre table; different models, different
honest answers, and each says which it is.

## A solved hour, not a generic case

The case file names itself `KPG193_ver2_0_2025_12_31_24` and its header says
"Generated from KPG193_ver1_6 ACOPF hourly result". Bus voltages and angles,
the unit commitment (100 of 201 units online) and the dispatch are one hour's
solution, not a neutral starting point. 66,967 MW dispatched against 66,135 MW
of demand.

That is worth having and worth saying. A reader who takes it for a generic
case will wonder why half the fleet is off.

## What is not here

The 8,760-hour demand and renewable profiles, the renewable capacity per bus,
and the pumped-storage metadata are in the upstream repository and not in this
document: Sienna carries time series in a separate HDF5 file, which this
builder does not write. So the fleet here is the **conventional** one — 115
LNG, 60 coal, 26 nuclear — and the renewables are upstream. The record says so;
a system that quietly had no wind in it would be the worse failure.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "reference-models" / "kpg-193" / "system.json"
PROVENANCE = OUT.parent / "parameters.json"
VIEW = OUT.parent / "system.view.json"

FREQUENCY = 60.0
BASE_POWER = 100.0
#: Per-unit against the base each component records, which is the schema's own
#: prescription for values per-unitised on a shared base. See the module
#: docstring for why ohms are not available here.
UNITS = "COMPONENT_BASE"

SOURCE_REPO = "https://github.com/agm-center/kpg-testgrid"
SOURCE_COMMIT = "5ab3e25d6d1a84e27d7de6d11169cb6f9a7dd29b"
SOURCE_VERSION = "v2.0.0"
SOURCE_LICENCE = "ODbL 1.0"
SOURCE_PAPER = "arXiv:2411.14756"

#: `gen_type` in the generator metadata to the EIA-923 code set Sienna uses.
#:
#: Korea's fleet in this case is three fuels. The prime mover is the part that
#: needs care: an LNG plant named `복합` is a combined cycle and a simple-cycle
#: peaker is not, and the metadata does not distinguish them, so every LNG unit
#: takes `CC`. That is right for the great majority of Korean LNG capacity and
#: wrong for some of it, which is recorded rather than smoothed over.
FUELS = {
    "LNG": ("NATURAL_GAS", "CC"),
    "Coal": ("BITUMINOUS_COAL", "ST"),
    "Nuclear": ("NUCLEAR", "ST"),
}

#: MATPOWER bus types.
BUS_TYPES = {1: "PQ", 2: "PV", 3: "REF"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def matpower_block(text: str, name: str) -> list[list[float]]:
    """The numeric rows of one `mpc.<name> = [ ... ];` block."""
    match = re.search(rf"mpc\.{name}\s*=\s*\[(.*?)\n\];", text, re.S)
    if match is None:
        return []
    rows = []
    for line in match.group(1).splitlines():
        line = line.strip().rstrip(";").strip()
        if not line or line.startswith("%"):
            continue
        rows.append([float(value) for value in line.split()])
    return rows


def read_csv(path: Path) -> list[dict[str, str]]:
    # utf-8-sig: both metadata files start with a BOM, and without this the
    # first column name arrives as "﻿bus_id" and every lookup misses.
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))


def build(source: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    case_path = source / "KPG193_ver2_0.m"
    bus_meta_path = source / "bus_metadata_2025.csv"
    gen_meta_path = source / "generator_metadata_2025.csv"

    text = case_path.read_text()
    buses = matpower_block(text, "bus")
    gens = matpower_block(text, "gen")
    gencost = matpower_block(text, "gencost")
    branches = matpower_block(text, "branch")
    dclines = matpower_block(text, "dcline")

    bus_meta = {row["bus_id"]: row for row in read_csv(bus_meta_path)}
    gen_meta = read_csv(gen_meta_path)

    if len(gens) != len(gencost) or len(gens) != len(gen_meta):
        raise SystemExit(
            f"generator rows disagree: mpc.gen {len(gens)}, mpc.gencost {len(gencost)}, "
            f"metadata {len(gen_meta)}. The three are joined by row order and a "
            "mismatch would give a unit somebody else's name and cost curve."
        )

    next_id = iter(range(1, 1_000_000))

    def nid() -> int:
        return next(next_id)

    shapes: list[dict[str, Any]] = []
    assoc: list[dict[str, Any]] = []

    area_id = nid()
    area = {"id": area_id, "name": "Korea", "base_power": BASE_POWER, "power_units": UNITS}

    sienna_buses: list[dict[str, Any]] = []
    bus_key: dict[int, int] = {}
    bus_kv: dict[int, float] = {}
    unlocated: list[int] = []
    for row in buses:
        number = int(row[0])
        meta = bus_meta.get(str(number))
        bus_id = nid()
        bus_key[number] = bus_id
        bus_kv[number] = row[9]
        name = meta["name_English"].strip() if meta else f"BUS-{number}"
        sienna_buses.append(
            {
                "id": bus_id,
                "number": number,
                "name": f"{name} {number:03d}",
                "available": True,
                "bustype": BUS_TYPES[int(row[1])],
                # The solved hour's voltage state, not a flat start. Carried
                # because it is what the source holds and discarding it would
                # throw away the only operating point anybody has validated.
                "angle": math.radians(row[8]),
                "magnitude": row[7],
                "voltage_limits": {"min": row[12], "max": row[11]},
                "base_voltage": row[9],
                "area": area_id,
            }
        )
        if meta:
            geo_id = nid()
            shapes.append(
                {
                    "id": geo_id,
                    "geo_json": {
                        "type": "Point",
                        "coordinates": [
                            round(float(meta["Longitude"]), 5),
                            round(float(meta["Latitude"]), 5),
                        ],
                    },
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
        else:
            unlocated.append(number)

    arcs: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []
    crossing_levels = 0
    for index, row in enumerate(branches, start=1):
        frm, to = int(row[0]), int(row[1])
        if bus_kv[frm] != bus_kv[to]:
            crossing_levels += 1
        arc_id = nid()
        arcs.append({"id": arc_id, "from_id": bus_key[frm], "to_id": bus_key[to]})
        half = round(row[4] / 2, 10)
        lines.append(
            {
                "id": nid(),
                "name": f"LN-{index:04d}",
                "available": bool(row[10]),
                "active_power_flow": row[13] / BASE_POWER,
                "reactive_power_flow": row[14] / BASE_POWER,
                "arc": arc_id,
                "r": row[2],
                "x": row[3],
                "b": {"from": half, "to": half},
                "rating": row[5] / BASE_POWER,
                "angle_limits": {"min": math.radians(row[11]), "max": math.radians(row[12])},
                "base_power": BASE_POWER,
                "power_units": UNITS,
                "parameter_units": UNITS,
            }
        )

    hvdc: list[dict[str, Any]] = []
    for index, row in enumerate(dclines, start=1):
        arc_id = nid()
        arcs.append({"id": arc_id, "from_id": bus_key[int(row[0])], "to_id": bus_key[int(row[1])]})
        hvdc.append(
            {
                "id": nid(),
                "name": f"HVDC-{index:02d}",
                "available": bool(row[2]),
                "active_power_flow": row[3] / BASE_POWER,
                "arc": arc_id,
                "active_power_limits_from": {"min": row[9] / BASE_POWER, "max": row[10] / BASE_POWER},
                "active_power_limits_to": {"min": row[9] / BASE_POWER, "max": row[10] / BASE_POWER},
                "reactive_power_limits_from": {
                    "min": row[11] / BASE_POWER,
                    "max": row[12] / BASE_POWER,
                },
                "reactive_power_limits_to": {
                    "min": row[13] / BASE_POWER,
                    "max": row[14] / BASE_POWER,
                },
                # MATPOWER's loss0 is a constant MW loss and loss1 a
                # proportional rate, which is exactly a linear input-output
                # curve. Both are zero in this case — the source models the
                # links as lossless — and that is carried rather than replaced
                # with a plausible figure.
                "loss": {
                    "power_units": UNITS,
                    "value_curve": {
                        "curve_type": "INPUT_OUTPUT",
                        "function_data": {
                            "function_type": "LINEAR",
                            "constant_term": row[15] / BASE_POWER,
                            "proportional_term": row[16],
                        },
                    },
                },
                "base_power": BASE_POWER,
                "power_units": UNITS,
            }
        )

    loads: list[dict[str, Any]] = []
    for row in buses:
        if row[2] == 0 and row[3] == 0:
            continue
        number = int(row[0])
        meta = bus_meta.get(str(number))
        name = meta["name_English"].strip() if meta else f"BUS-{number}"
        loads.append(
            {
                "id": nid(),
                "name": f"LOAD-{number:03d}",
                "available": True,
                "bus": bus_key[number],
                "active_power": row[2] / BASE_POWER,
                "reactive_power": row[3] / BASE_POWER,
                # The hour's demand is also the maximum this document knows
                # about: the 8,760-hour profile that would give a real maximum
                # is upstream and not carried here, and inventing a headroom
                # factor would be a number nobody could check.
                "max_active_power": row[2] / BASE_POWER,
                "max_reactive_power": row[3] / BASE_POWER,
                "base_power": BASE_POWER,
                "power_units": UNITS,
                "__place": name,
            }
        )
    for load in loads:
        load.pop("__place")

    thermal: list[dict[str, Any]] = []
    misplaced: list[dict[str, Any]] = []
    for index, (row, cost, meta) in enumerate(zip(gens, gencost, gen_meta, strict=True), start=1):
        number = int(row[0])
        if int(meta["bus_id"]) != number:
            misplaced.append(
                {
                    "unit": f"{meta['plant_name_kor']} {meta['unit_name']}",
                    "case_bus": number,
                    "case_bus_name": (bus_meta.get(str(number)) or {}).get("name_English"),
                    "metadata_bus": int(meta["bus_id"]),
                    "metadata_bus_name": (bus_meta.get(meta["bus_id"]) or {}).get("name_English"),
                }
            )
        fuel, prime_mover = FUELS.get(meta["gen_type"], ("OTHER", "OT"))
        quadratic, linear, constant = cost[4], cost[5], cost[6]
        thermal.append(
            {
                "id": nid(),
                "name": f"GEN-{index:03d}",
                "available": True,
                # `status` is the commitment state, `available` is whether the
                # unit is in service at all. A unit the ACOPF left off is not
                # on outage, and conflating the two would delete half the
                # fleet from anything that re-commits.
                "status": "ONLINE" if int(row[7]) == 1 else "OFFLINE",
                "bus": bus_key[number],
                "active_power": row[1] / BASE_POWER,
                "reactive_power": row[2] / BASE_POWER,
                "rating": row[8] / BASE_POWER,
                "active_power_limits": {"min": row[9] / BASE_POWER, "max": row[8] / BASE_POWER},
                "reactive_power_limits": {"min": row[4] / BASE_POWER, "max": row[3] / BASE_POWER},
                "operation_cost": {
                    "cost_type": "THERMAL",
                    "variable_operation_cost": {
                        "variable_cost_type": "COST",
                        "power_units": "NATURAL_UNITS",
                        "value_curve": {
                            "curve_type": "INPUT_OUTPUT",
                            "function_data": {
                                "function_type": "QUADRATIC",
                                "quadratic_term": quadratic,
                                "proportional_term": linear,
                                "constant_term": constant,
                            },
                        },
                        # Required despite carrying a default in the schema.
                        # Zero rather than absent: the source separates fuel
                        # cost from nothing else, so there is no VOM term to
                        # state, and a made-up one would ride into every
                        # dispatch as real money.
                        "vom_cost": {
                            "curve_type": "INPUT_OUTPUT",
                            "function_data": {
                                "function_type": "LINEAR",
                                "proportional_term": 0.0,
                                "constant_term": 0.0,
                            },
                        },
                    },
                    "fixed": 0.0,
                    "start_up": cost[1],
                    "shut_down": cost[2],
                },
                "fuel": fuel,
                "prime_mover_type": prime_mover,
                "base_power": BASE_POWER,
                "power_units": UNITS,
            }
        )

    components = {
        "ACBus": sienna_buses,
        "Arc": arcs,
        "Area": [area],
        "Line": lines,
        "PowerLoad": loads,
        "ThermalStandard": thermal,
        "TwoTerminalGenericHVDCLine": hvdc,
    }

    demand_mw = sum(row[2] for row in buses)
    dispatch_mw = sum(row[1] for row in gens)
    capacity_mw = sum(row[8] for row in gens)
    online = sum(1 for row in gens if int(row[7]) == 1)

    document = {
        "name": "KPG 193: Korean Power Grid test system",
        "description": (
            "The 193-bus synthetic Korean power grid (KPG 193 v2.0.0) from the AGM "
            "Center at KENTECH, as a Sienna system. Unlike the Hub's other reference "
            "models this one is a SYSTEM and not a bare network: it carries demand at "
            f"{sum(1 for row in buses if row[2] or row[3])} buses, {len(thermal)} thermal "
            "units with quadratic cost curves and commitment state, and two HVDC links. "
            "Transmission-line geometry is from OpenStreetMap via the Overpass API; the "
            "buses are CLUSTERS on KEPCO regional office locations, not individual "
            "substations, which is what the source means by synthetic. Values are per "
            "unit on a 100 MVA base, because 184 of the 385 branches join different "
            "voltage levels with no transformer ratio and have no single base voltage "
            "to convert against. This is one solved hour (2025-12-31, hour 24), not a "
            "generic case: the commitment, dispatch and voltage state are an ACOPF "
            "result. Hourly demand and renewable profiles, and the renewable fleet, are "
            "upstream and not carried here — the generators in this document are the "
            "conventional fleet only."
        ),
        "frequency": FREQUENCY,
        "components": {key: components[key] for key in sorted(components)},
        "supplemental_attributes": shapes,
        "supplemental_attribute_associations": assoc,
        "plant_associations": [],
        "combined_cycle_associations": [],
        "service_associations": [],
        "trading_hub_associations": [],
        "time_series_associations": [],
        "time_series_storage_file": "kpg-193-timeseries.h5",
        "ext": {
            "opengrid": {
                "parameter_basis": "estimated",
                "parameter_provenance": "parameters.json",
                "source_repository": SOURCE_REPO,
                "source_commit": SOURCE_COMMIT,
                "source_version": SOURCE_VERSION,
                "source_licence": SOURCE_LICENCE,
                "source_paper": SOURCE_PAPER,
                "share_alike": True,
            }
        },
    }

    provenance = {
        "model": "kpg-193",
        "source": {
            "repository": SOURCE_REPO,
            "commit": SOURCE_COMMIT,
            "version": SOURCE_VERSION,
            "paper": SOURCE_PAPER,
            "licence": SOURCE_LICENCE,
            "share_alike": True,
            "files": {
                path.name: {"sha256": digest(path), "bytes": path.stat().st_size}
                for path in (case_path, bus_meta_path, gen_meta_path)
            },
        },
        "operating_point": {
            "case": "KPG193_ver2_0_2025_12_31_24",
            "note": (
                "One hour's ACOPF solution, carried through as initial conditions. Bus "
                "voltage magnitudes and angles, unit commitment and dispatch all "
                "describe that hour and are not a neutral starting point."
            ),
            "demand_mw": round(demand_mw, 1),
            "dispatch_mw": round(dispatch_mw, 1),
            "fleet_capacity_mw": round(capacity_mw, 1),
            "units_online": online,
            "units_total": len(gens),
        },
        "measured": [
            "transmission line terminals, from OpenStreetMap via the Overpass API",
            "bus nominal voltage",
            "generator nameplate capacity, fuel and plant identity",
            "generator cost curves, from the source's fuel-price and heat-rate data",
        ],
        "estimated": [
            "line r, x and b — computed upstream from published conductor tables by "
            "voltage class and conductor type, not measured per circuit",
            "nodal reactive demand, fixed upstream at 90% of real power demand",
        ],
        "conventions": [
            "Per unit on a 100 MVA base throughout (COMPONENT_BASE). 184 of 385 "
            "branches join different nominal voltages with ratio 0, so there is no "
            "single base voltage to express them in ohms against.",
            "Bus coordinates are KEPCO regional office locations. A bus is a cluster, "
            "not a substation, and its coordinate is the office's.",
            "MATPOWER generator `status` becomes Sienna `status` (ONLINE/OFFLINE) and "
            "never `available`: a unit the ACOPF left off is not on outage.",
            "Every LNG unit takes prime mover CC. The metadata does not distinguish "
            "combined cycle from simple cycle, and CC is right for most of Korea's LNG "
            "capacity and wrong for some of it.",
        ],
        "excluded": {
            "renewables": "Wind, solar and hydro capacity is published per bus upstream "
            "and is not in the MATPOWER case, so the fleet here is conventional only: "
            "115 LNG, 60 coal, 26 nuclear.",
            "time_series": "The 8,760-hour demand and renewable profiles are upstream. "
            "Sienna carries time series in a separate HDF5 file, which this builder does "
            "not write, so the document holds one hour rather than a year.",
            "compensation_devices": "Shunt capacitors, static synchronous compensators "
            "and tap-changing transformers are in the real Korean grid and are stated by "
            "the authors to be absent from the test system.",
            "hvdc_converter_detail": "The two HVDC links are emitted as generic two "
            "terminal lines. Sienna's LCC type wants bridge counts, delay and extinction "
            "angle limits and commutating reactances, none of which the source publishes; "
            "generic carries the limits and losses it does publish and invents nothing.",
        },
        "upstream_inconsistencies": misplaced,
        "unlocated_buses": unlocated,
    }

    # The viewer's copy. No line geometry to simplify — KPG publishes terminals
    # and not routes — so the display copy differs from the model only in that
    # it drops the cost curves, which are a third of the bytes and nothing the
    # map draws.
    view = json.loads(json.dumps(document))
    view["description"] = (
        "Rendering copy. Generator cost curves are dropped for display; system.json "
        "holds them and is the model. Do not run a study on this file."
    )
    for unit in view["components"]["ThermalStandard"]:
        unit.pop("operation_cost", None)

    return document, provenance, view


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "var" / "kpg",
        help="directory holding the MATPOWER case and the two metadata CSVs",
    )
    args = parser.parse_args()

    document, provenance, view = build(args.source)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    PROVENANCE.write_text(json.dumps(provenance, indent=2, ensure_ascii=False) + "\n")
    VIEW.write_text(json.dumps(view, separators=(",", ":"), ensure_ascii=False) + "\n")

    counts = {key: len(value) for key, value in document["components"].items()}
    print(f"wrote {OUT.relative_to(ROOT)}")
    for key, value in counts.items():
        print(f"  {key:30} {value:4d}")
    print(f"  {'TOTAL':30} {sum(counts.values()):4d}")
    point = provenance["operating_point"]
    print(
        f"  demand {point['demand_mw']:,.0f} MW · dispatch {point['dispatch_mw']:,.0f} MW · "
        f"{point['units_online']}/{point['units_total']} units online"
    )
    if provenance["upstream_inconsistencies"]:
        print(f"  {len(provenance['upstream_inconsistencies'])} upstream inconsistency recorded")
    for path in (OUT, PROVENANCE, VIEW):
        print(f"  {path.name:30} {path.stat().st_size:>9,} bytes")


if __name__ == "__main__":
    main()
