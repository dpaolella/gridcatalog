#!/usr/bin/env python3
"""The Great Britain transmission network, from real OSM topology.

The Cascade Interconnect is invented end to end, which is the right way to show
the registry's shape and the wrong way to show that a reference model is
something a modeller would actually start from. This one is the other half of
that argument: the topology is real — real substations at real coordinates,
real circuits following real corridors — and the electrical parameters are
estimated, because the source has none.

That gap is the interesting part, and it is the reason this model is in the
demo at all. A reference model whose every number is measured needs no
registry: you would read its README. A model that is real in one dimension and
estimated in another needs somewhere to say *which is which, per value*, which
is the whole claim the Hub makes. So every estimated number here is written
down in `parameters.json` beside the document, with the rule that produced it
and the inputs it read — not a footnote in a description that a loader will
never see.

## Source

*Prebuilt Electricity Network for PyPSA-Eur based on OpenStreetMap Data*,
DOI 10.5281/zenodo.13358976, version 0.3, published 2024-08-22, **ODbL**.

Fetch it yourself; this script does not download:

    mkdir -p var/osm
    for f in buses lines transformers; do
      curl -L -o var/osm/$f.csv \\
        "https://zenodo.org/api/records/13358976/files/$f.csv/content"
    done
    python ops/reference-models/build_gb_osm.py --source var/osm

The digests of the three files it read are recorded in `parameters.json`, so a
document's provenance can be checked against the archive rather than trusted.

**ODbL is share-alike at the database level.** That is a real constraint on
anyone who joins this to their own data, it is an open question in the Data Hub
writeup, and the catalog record says so where a reader will see it before they
build on it.

## Reading the CSVs

Not with `csv.DictReader`. The `geometry` column is unquoted and full of commas
— a LINESTRING is nothing but commas — so the reader splits each row across the
overflow key and reports about a twentieth of the real content. `geometry` is
last, so `split(",", len(header) - 1)` is exact and needs no quoting rules.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "reference-models" / "gb-osm" / "system.json"
PROVENANCE = OUT.parent / "parameters.json"
VIEW = OUT.parent / "system.view.json"

COUNTRY = "GB"
FREQUENCY = 50.0
BASE_POWER = 100.0
UNITS = "NATURAL_UNITS"

#: The Zenodo record this is derived from, stated where the numbers are made.
SOURCE_DOI = "10.5281/zenodo.13358976"
SOURCE_VERSION = "0.3"
SOURCE_LICENCE = "ODbL 1.0"

#: Overhead line parameters by nominal voltage, per circuit.
#:
#: These are PyPSA's standard line types — `pypsa.Network().line_types`, which
#: takes them from pandapower, which cites Oeding and Oswald, *Elektrische
#: Kraftwerke und Netze*, 2011, Chapter 9. Read out of PyPSA 1.3.0 rather than
#: recalled: every one of these numbers is checkable against a published table,
#: which is the difference between an estimate and a guess.
#:
#: GB's 275 kV has no standard type of its own and takes the 300 kV one, which
#: is what PyPSA-Eur does with the same data. That is an approximation and it is
#: recorded as one on every 275 kV line.
LINE_TYPES: dict[float, dict[str, Any]] = {
    220.0: {
        "type": "Al/St 240/40 2-bundle 220.0",
        "r_per_km": 0.06,
        "x_per_km": 0.301,
        "c_nf_per_km": 12.5,
        "i_nom_ka": 1.29,
        "nominal_kv": 220.0,
    },
    275.0: {
        "type": "Al/St 240/40 3-bundle 300.0",
        "r_per_km": 0.04,
        "x_per_km": 0.265,
        "c_nf_per_km": 13.2,
        "i_nom_ka": 1.935,
        "nominal_kv": 300.0,
    },
    400.0: {
        "type": "Al/St 240/40 4-bundle 380.0",
        "r_per_km": 0.03,
        "x_per_km": 0.246,
        "c_nf_per_km": 13.8,
        "i_nom_ka": 2.58,
        "nominal_kv": 380.0,
    },
}

#: Transformer ratings and impedance by voltage pair.
#:
#: Weaker evidence than the line types, and labelled accordingly. These are
#: typical values for a GB supergrid autotransformer — 750 MVA at 400/275,
#: 240 MVA at the 220 kV pairs — with 13% reactance on rating and an X/R of 50.
#: No public per-unit register of GB transformer impedances exists to check
#: them against, so they are a plausible class value and nothing more.
TRANSFORMER_RATING_MVA = {(275.0, 400.0): 750.0, (220.0, 400.0): 500.0}
TRANSFORMER_DEFAULT_MVA = 240.0
TRANSFORMER_X_PU = 0.13
TRANSFORMER_X_OVER_R = 50.0

#: Douglas-Peucker tolerance for the viewer's copy of the line corridors, in
#: degrees.
#:
#: The whole island is about ten degrees of longitude and a page renders it
#: around a thousand pixels wide, so a pixel is roughly 0.01 degrees. At 0.003
#: the error is a third of a pixel un-zoomed and still sub-pixel at three times
#: zoom, and it keeps 4,191 of 33,090 vertices — an eighth of the data for a
#: difference nobody can see until they are looking at one valley.
#:
#: `system.json` keeps every vertex. The viewer's copy is a *rendering*
#: decision and the download is the model; conflating the two would mean
#: shipping a simplified corridor to somebody running a study on it.
VIEW_TOLERANCE = 0.003


def read_rows(path: Path) -> list[dict[str, str]]:
    """Rows from a CSV whose last column is unquoted and full of commas."""
    lines = path.read_text().splitlines()
    header = lines[0].split(",")
    width = len(header)
    return [
        dict(zip(header, line.split(",", width - 1), strict=True))
        for line in lines[1:]
        if line.strip()
    ]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_linestring(raw: str) -> list[list[float]]:
    """The vertices of a `LINESTRING (...)`, as `[lon, lat]` pairs."""
    inner = raw.strip().strip("'\"")
    inner = inner[inner.index("(") + 1 : inner.rindex(")")]
    out = []
    for pair in inner.split(","):
        lon, lat = pair.split()
        out.append([round(float(lon), 5), round(float(lat), 5)])
    return out


def simplify(points: list[list[float]], tolerance: float) -> list[list[float]]:
    """Douglas-Peucker, iteratively — a 900-vertex corridor recurses deep enough
    to matter and there is no reason to spend stack on it."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        (x1, y1), (x2, y2) = points[start], points[end]
        dx, dy = x2 - x1, y2 - y1
        span = math.hypot(dx, dy)
        worst, index = -1.0, start
        for i in range(start + 1, end):
            px, py = points[i]
            if span == 0:
                d = math.hypot(px - x1, py - y1)
            else:
                d = abs(dy * px - dx * py + x2 * y1 - y2 * x1) / span
            if d > worst:
                worst, index = d, i
        if worst > tolerance:
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))
    return [p for p, k in zip(points, keep, strict=True) if k]


def line_parameters(voltage: float, circuits: float, length_km: float) -> dict[str, Any]:
    """Series impedance, shunt susceptance and thermal rating for one line.

    Returned with the inputs and the rule alongside the numbers, because the
    numbers on their own are indistinguishable from measurements.
    """
    spec = LINE_TYPES[voltage]
    n = max(int(circuits), 1)
    r = spec["r_per_km"] * length_km / n
    x = spec["x_per_km"] * length_km / n
    # Total line charging: capacitance is per circuit and circuits are in
    # parallel, so it *adds* where the series impedance divides. Split half to
    # each end, which is what the pi model means by `b.from` and `b.to`.
    b_total = 2 * math.pi * FREQUENCY * (spec["c_nf_per_km"] * 1e-9) * length_km * n
    # The per-end half is the stored quantity and the total is derived from it,
    # not the other way round. Rounding the total and then halving it gives two
    # ends that do not sum back to the total the provenance states — by 1e-8,
    # which is nothing electrically and is still a sidecar describing a document
    # it does not match. Ten places rather than eight because these are ~5e-5 S:
    # eight decimals is three significant figures on a line's charging.
    b_half = round(b_total / 2, 10)
    rating = math.sqrt(3) * voltage * spec["i_nom_ka"] * n
    return {
        "r_ohm": round(r, 4),
        "x_ohm": round(x, 4),
        "b_half_siemens": b_half,
        "b_siemens": round(b_half * 2, 10),
        "rating_mva": round(rating, 1),
        "basis": "estimated",
        "method": "pypsa-standard-line-type",
        "line_type": spec["type"],
        "inputs": {
            "voltage_kv": voltage,
            "circuits": n,
            "length_km": round(length_km, 3),
            "r_per_km_ohm": spec["r_per_km"],
            "x_per_km_ohm": spec["x_per_km"],
            "c_per_km_nf": spec["c_nf_per_km"],
            "i_nom_ka": spec["i_nom_ka"],
        },
        "approximations": [
            note
            for note in (
                (
                    f"{voltage:.0f} kV has no standard type; the "
                    f"{spec['nominal_kv']:.0f} kV type is used, as PyPSA-Eur does."
                )
                if voltage != spec["nominal_kv"]
                else None,
            )
            if note
        ],
    }


def transformer_parameters(v_low: float, v_high: float) -> dict[str, Any]:
    rating = TRANSFORMER_RATING_MVA.get((v_low, v_high), TRANSFORMER_DEFAULT_MVA)
    x = TRANSFORMER_X_PU * v_high**2 / rating
    r = x / TRANSFORMER_X_OVER_R
    return {
        "r_ohm": round(r, 4),
        "x_ohm": round(x, 4),
        "rating_mva": rating,
        "basis": "estimated",
        "method": "typical-supergrid-autotransformer",
        "inputs": {
            "voltage_primary_kv": v_high,
            "voltage_secondary_kv": v_low,
            "x_pu_on_rating": TRANSFORMER_X_PU,
            "x_over_r": TRANSFORMER_X_OVER_R,
        },
        "approximations": [
            "A class value, not a nameplate. No public register of GB "
            "transformer impedances exists to check it against, so this is the "
            "weakest estimate in the model."
        ],
    }


def largest_component(bus_ids: set[str], edges: list[tuple[str, str]]) -> set[str]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for a, b in edges:
        adjacency[a].add(b)
        adjacency[b].add(a)
    seen: set[str] = set()
    best: set[str] = set()
    for start in bus_ids:
        if start in seen:
            continue
        stack, component = [start], set()
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            seen.add(node)
            stack.extend(adjacency[node] - component)
        if len(component) > len(best):
            best = component
    return best


def build(source: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    buses_csv, lines_csv, trafos_csv = (
        source / "buses.csv",
        source / "lines.csv",
        source / "transformers.csv",
    )
    buses_all = {row["bus_id"]: row for row in read_rows(buses_csv) if row["country"] == COUNTRY}
    lines_all = [
        row for row in read_rows(lines_csv) if row["bus0"] in buses_all and row["bus1"] in buses_all
    ]
    trafos_all = [
        row
        for row in read_rows(trafos_csv)
        if row["bus0"] in buses_all and row["bus1"] in buses_all
    ]

    # One connected network, or it is not a network.
    #
    # Over lines alone the GB extract falls into 48 pieces, because OSM models a
    # substation as one bus per voltage level and it is the *transformers* that
    # tie 400 kV to 275 kV at the same site. Adding them takes it to 412 buses in
    # one piece. That is worth knowing about the source and is why this is not a
    # detail of the build: a subset cut on lines alone would be a pile of
    # fragments that still looked like a map.
    #
    # What is left out is out for a reason, not for tidiness: Northern Ireland is
    # a separate 13-bus island under the same country code and is not in Great
    # Britain, and the rest are HVDC converter stations whose links are not
    # modelled here.
    edges = [(row["bus0"], row["bus1"]) for row in lines_all + trafos_all]
    keep = largest_component(set(buses_all), edges)

    buses = {k: v for k, v in buses_all.items() if k in keep}
    lines = [row for row in lines_all if row["bus0"] in keep and row["bus1"] in keep]
    trafos = [row for row in trafos_all if row["bus0"] in keep and row["bus1"] in keep]

    next_id = iter(range(1, 1_000_000))

    def nid() -> int:
        return next(next_id)

    geo: list[dict[str, Any]] = []
    assoc: list[dict[str, Any]] = []
    provenance_lines: list[dict[str, Any]] = []
    provenance_trafos: list[dict[str, Any]] = []

    area_id = nid()
    area = {
        "id": area_id,
        "name": "Great Britain",
        "base_power": BASE_POWER,
        "power_units": UNITS,
    }

    # Buses, in source order so the document is stable across runs.
    sienna_buses = []
    bus_key: dict[str, int] = {}
    for osm_id in sorted(buses, key=int):
        row = buses[osm_id]
        bus_id = nid()
        bus_key[osm_id] = bus_id
        sienna_buses.append(
            {
                "id": bus_id,
                "number": bus_id,
                "name": f"GB-{osm_id}",
                "available": True,
                "bustype": "PQ",
                "angle": 0.0,
                "magnitude": 1.0,
                "voltage_limits": {"min": 0.95, "max": 1.05},
                "base_voltage": float(row["voltage"]),
                "area": area_id,
            }
        )
        geo_id = nid()
        geo.append(
            {
                "id": geo_id,
                "geo_json": {
                    "type": "Point",
                    "coordinates": [round(float(row["x"]), 5), round(float(row["y"]), 5)],
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

    # The slack bus is the highest-voltage bus with the most connections — a
    # convention, and recorded as one. Nothing in the source says where GB's
    # reference angle sits.
    degree: dict[str, int] = defaultdict(int)
    for row in lines:
        degree[row["bus0"]] += 1
        degree[row["bus1"]] += 1
    slack_osm = max(buses, key=lambda k: (float(buses[k]["voltage"]), degree[k], -int(k)))
    for bus in sienna_buses:
        if bus["id"] == bus_key[slack_osm]:
            bus["bustype"] = "REF"

    arcs: list[dict[str, Any]] = []
    sienna_lines: list[dict[str, Any]] = []
    for row in sorted(lines, key=lambda r: r["line_id"]):
        voltage = float(row["voltage"])
        circuits = float(row["circuits"])
        length_km = float(row["length"]) / 1000.0
        estimate = line_parameters(voltage, circuits, length_km)

        arc_id = nid()
        arcs.append({"id": arc_id, "from_id": bus_key[row["bus0"]], "to_id": bus_key[row["bus1"]]})
        line_id = nid()
        sienna_lines.append(
            {
                "id": line_id,
                "name": f"LN-{row['line_id']}",
                "available": True,
                "active_power_flow": 0.0,
                "reactive_power_flow": 0.0,
                "arc": arc_id,
                "r": estimate["r_ohm"],
                "x": estimate["x_ohm"],
                "b": {
                    "from": estimate["b_half_siemens"],
                    "to": estimate["b_half_siemens"],
                },
                "rating": estimate["rating_mva"],
                "angle_limits": {"min": -0.6, "max": 0.6},
                "base_power": BASE_POWER,
                "power_units": UNITS,
                "parameter_units": UNITS,
            }
        )

        vertices = parse_linestring(row["geometry"])
        geo_id = nid()
        geo.append({"id": geo_id, "geo_json": {"type": "LineString", "coordinates": vertices}})
        assoc.append(
            {
                "component_id": line_id,
                "component_type": "Line",
                "attribute_id": geo_id,
                "attribute_type": "GeographicInfo",
            }
        )
        view = simplify(vertices, VIEW_TOLERANCE)

        record = {"component": f"LN-{row['line_id']}", "source_id": row["line_id"], **estimate}
        if row["underground"] == "t":
            record["approximations"] = [
                *record["approximations"],
                "The source marks this circuit underground. An overhead type is "
                "applied anyway: a cable's capacitance is an order of magnitude "
                "higher and its reactance far lower, so r, x and b are wrong "
                "here in a known direction, not merely uncertain.",
            ]
        provenance_lines.append(record)

    sienna_trafos: list[dict[str, Any]] = []
    circuits_out: list[dict[str, Any]] = []
    for row in sorted(trafos, key=lambda r: r["transformer_id"]):
        v0, v1 = float(row["voltage_bus0"]), float(row["voltage_bus1"])
        low, high = min(v0, v1), max(v0, v1)
        estimate = transformer_parameters(low, high)

        arc_id = nid()
        arcs.append({"id": arc_id, "from_id": bus_key[row["bus0"]], "to_id": bus_key[row["bus1"]]})
        circuit_id = nid()
        circuits_out.append(
            {
                "id": circuit_id,
                "available": True,
                "arc": arc_id,
                "r": estimate["r_ohm"],
                "x": estimate["x_ohm"],
                "rating": estimate["rating_mva"],
                "active_power_flow": 0.0,
                "reactive_power_flow": 0.0,
                "base_power": BASE_POWER,
                "power_units": UNITS,
                "parameter_units": UNITS,
                "base_voltage_primary": high,
                "base_voltage_secondary": low,
            }
        )
        sienna_trafos.append(
            {
                "id": nid(),
                "name": f"TR-{row['transformer_id']}",
                "circuit": circuit_id,
            }
        )
        provenance_trafos.append(
            {
                "component": f"TR-{row['transformer_id']}",
                "source_id": row["transformer_id"],
                **estimate,
            }
        )

    components = {
        "ACBus": sienna_buses,
        "Arc": arcs,
        "Area": [area],
        "Line": sienna_lines,
        "TransformerCircuit": circuits_out,
        "TwoWindingTransformer": sienna_trafos,
    }

    document = {
        "name": "OpenGrid Reference: Great Britain (OSM)",
        "description": (
            "The Great Britain transmission network as mapped in OpenStreetMap, "
            "via the PyPSA-Eur prebuilt extract (Zenodo 10.5281/zenodo.13358976 "
            "v0.3, ODbL). Topology, coordinates, voltages, circuit counts and "
            "line routes are from the source. Every electrical parameter — r, x, "
            "b and thermal rating — is ESTIMATED from voltage class and length; "
            "the source contains none. See parameters.json for the rule and "
            "inputs behind each value. No demand and no generation: this is a "
            "network, not a system, and it cannot be dispatched as it stands."
        ),
        "frequency": FREQUENCY,
        "components": {k: components[k] for k in sorted(components)},
        # A flat, untyped array — the schema is explicit that it is not
        # bucketed by type, and that `attribute_type` on the association row
        # is the only per-row discriminator a consumer gets.
        "supplemental_attributes": geo,
        "supplemental_attribute_associations": assoc,
        "plant_associations": [],
        "combined_cycle_associations": [],
        "service_associations": [],
        "trading_hub_associations": [],
        "time_series_associations": [],
        "time_series_storage_file": "gb-osm-timeseries.h5",
        "ext": {
            "opengrid": {
                "parameter_basis": "estimated",
                "parameter_provenance": "parameters.json",
                "source_doi": SOURCE_DOI,
                "source_version": SOURCE_VERSION,
                "source_licence": SOURCE_LICENCE,
                "share_alike": True,
            }
        },
    }

    provenance = {
        "model": "gb-osm",
        "source": {
            "doi": SOURCE_DOI,
            "version": SOURCE_VERSION,
            "licence": SOURCE_LICENCE,
            "share_alike": True,
            "files": {
                path.name: {"sha256": digest(path), "bytes": path.stat().st_size}
                for path in (buses_csv, lines_csv, trafos_csv)
            },
        },
        "measured": [
            "bus coordinates",
            "bus nominal voltage",
            "line terminals and route geometry",
            "line circuit count",
            "line route length",
            "transformer terminals and voltage pair",
        ],
        "estimated": [
            "line r",
            "line x",
            "line b",
            "line rating",
            "transformer r",
            "transformer x",
            "transformer rating",
        ],
        "conventions": [
            f"Slack bus: GB-{slack_osm}, the highest-voltage bus with the most "
            "line connections. The source says nothing about where the reference "
            "angle sits; this is a convention, not a fact about the network.",
            "Voltage limits of 0.95-1.05 pu on every bus, and angle limits of "
            "+/-0.6 rad on every line, are placeholders of the same kind.",
        ],
        "excluded": {
            "northern_ireland": "A separate 13-bus AC island under the same "
            "country code, and not part of Great Britain.",
            "hvdc": "Converter stations and their links are not modelled. The "
            "GB interconnectors and the internal HVDC bootstraps are absent, so "
            "north-south transfer capability is understated.",
        },
        "lines": provenance_lines,
        "transformers": provenance_trafos,
    }

    # The viewer's copy: the same document, with the corridors simplified.
    #
    # A second *format* was the first attempt and it was wrong. The viewer then
    # has two code paths — one for a Sienna document, one for a bespoke
    # `{v, c, p}` blob — and any future model has to be taught to emit both. The
    # same shape at a lower resolution keeps one reader and makes the
    # difference between the files what it actually is: resolution, not kind.
    view = json.loads(json.dumps(document))
    view["description"] = (
        "Rendering copy. Line corridors are simplified to "
        f"{VIEW_TOLERANCE} degrees for display; system.json holds every vertex "
        "and is the model. Do not run a study on this file."
    )
    for attribute in view["supplemental_attributes"]:
        geo = attribute["geo_json"]
        if geo["type"] == "LineString":
            geo["coordinates"] = simplify(geo["coordinates"], VIEW_TOLERANCE)

    return document, provenance, view


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "var" / "osm",
        help="directory holding buses.csv, lines.csv and transformers.csv",
    )
    args = parser.parse_args()

    document, provenance, view = build(args.source)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(document, indent=2) + "\n")
    PROVENANCE.write_text(json.dumps(provenance, indent=2) + "\n")
    VIEW.write_text(json.dumps(view, separators=(",", ":")) + "\n")

    counts = {k: len(v) for k, v in document["components"].items()}
    print(f"wrote {OUT.relative_to(ROOT)}")
    for key, value in counts.items():
        print(f"  {key:24} {value:4d}")
    print(f"  {'TOTAL':24} {sum(counts.values()):4d}")
    for path in (OUT, PROVENANCE, VIEW):
        print(f"  {path.name:24} {path.stat().st_size:>9,} bytes")


if __name__ == "__main__":
    main()
