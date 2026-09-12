"""Clip Natural Earth to a reference model's extent and emit SVG-ready paths.

A reference model a reader is deciding whether to start from has to look like
the place it claims to be, so the viewer draws real coastlines and real
administrative boundaries rather than the invented zone polygons a sandbox can
get away with.

**No tile server.** The alternative to this is leaflet or maplibre against a
hosted tile service, which would make the published site depend on somebody
else's uptime and usage policy to render its own reference models, and would
not work in a static export without network access at view time. Natural Earth
is public domain, ships as a few kilobytes of path data once clipped, and is
the same technique `web/src/components/WorldOutline.tsx` already uses for the
coverage thumbnails.

Coordinates stay in lon/lat. Projecting here would bake in a projection and a
viewport; the component does it, because it is the thing that knows how big it
is and what the reader has zoomed to.

Source: Natural Earth 1:50m physical and cultural vectors, public domain.
https://www.naturalearthdata.com/about/terms-of-use/
"""

from __future__ import annotations

import json
import math
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "reference-models"

BASE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson"
#: 1:10m, not 1:50m. At this extent the difference is Puget Sound: at 50m it is
#: a notch, at 10m it is the shape somebody from Seattle recognises — and
#: recognising the place is the entire reason for drawing a real base map
#: rather than invented polygons. The clip makes the choice nearly free; the
#: source files are ~10x larger and the output is not, because almost all of
#: the extra detail is somewhere else in the world.
#: Polygon layers are filled; line layers are stroked. The distinction is not
#: cosmetic — the first attempt shipped only `coastline`, which is a *line*
#: layer, so the map had a coast drawn on it and no sea: everything inside the
#: frame was land-coloured and the Pacific was indistinguishable from Idaho.
#: Land has to arrive as polygons to be filled as land.
POLYGONS = {"land": "ne_10m_land", "lakes": "ne_10m_lakes"}
LINES = {"coastline": "ne_10m_coastline", "borders": "ne_10m_admin_1_states_provinces_lines"}

#: Douglas-Peucker tolerance in degrees. 0.004° is roughly 300 m of longitude
#: at this latitude — below one screen pixel at the zoom the viewer opens at,
#: and still detailed enough that Puget Sound is Puget Sound.
TOLERANCE = 0.004

#: Decimal places kept. Three is ~100 m, which is finer than the tolerance
#: above, so rounding never becomes the thing that shapes the coastline.
PRECISION = 3


def perpendicular_distance(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    if start == end:
        return math.dist(point, start)
    (x, y), (x1, y1), (x2, y2) = point, start, end
    numerator = abs((y2 - y1) * x - (x2 - x1) * y + x2 * y1 - y2 * x1)
    return numerator / math.dist(start, end)


def simplify(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Douglas-Peucker, iteratively.

    Iterative rather than recursive because a coastline ring can be thousands
    of points long and CPython's recursion limit is not a design constraint
    worth inheriting.
    """
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        worst, index = 0.0, first
        for i in range(first + 1, last):
            d = perpendicular_distance(points[i], points[first], points[last])
            if d > worst:
                worst, index = d, i
        if worst > tolerance:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [p for p, k in zip(points, keep, strict=True) if k]


def clip(points: list[tuple[float, float]], bbox: tuple[float, float, float, float]) -> list[list]:
    """Split a line into the runs that fall inside *bbox*.

    Cheap containment rather than proper Cohen-Sutherland: a segment crossing
    the edge is kept whole if either end is inside, so a coastline runs a
    little past the frame and gets clipped by the SVG viewport instead. The
    alternative is a coastline that stops short of the edge with a visible gap,
    which reads as missing data rather than as a cropped map.
    """
    minx, miny, maxx, maxy = bbox
    runs: list[list] = []
    current: list = []
    for i, (x, y) in enumerate(points):
        inside = minx <= x <= maxx and miny <= y <= maxy
        neighbour_inside = any(
            minx <= points[j][0] <= maxx and miny <= points[j][1] <= maxy
            for j in (i - 1, i + 1)
            if 0 <= j < len(points)
        )
        if inside or neighbour_inside:
            current.append((x, y))
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return [r for r in runs if len(r) > 1]


def rings(geometry: dict[str, Any]) -> list[list[tuple[float, float]]]:
    # Natural Earth's 1:10m admin-1 lines carry features with a null geometry —
    # a boundary that exists as an attribute row and has no drawn line. Skipped
    # rather than crashed on: it is upstream's data being honest about a
    # boundary it does not hold a shape for.
    kind, coords = geometry.get("type"), geometry.get("coordinates")
    if not kind or not coords:
        return []
    if kind == "LineString":
        return [[(x, y) for x, y, *_ in coords]]
    if kind == "MultiLineString":
        return [[(x, y) for x, y, *_ in line] for line in coords]
    if kind == "Polygon":
        return [[(x, y) for x, y, *_ in ring] for ring in coords]
    if kind == "MultiPolygon":
        return [[(x, y) for x, y, *_ in ring] for poly in coords for ring in poly]
    return []


def fetch(source: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{BASE}/{source}.geojson", timeout=180) as response:
        return json.load(response)


def intersects(ring: list[tuple[float, float]], bbox: tuple[float, float, float, float]) -> bool:
    minx, miny, maxx, maxy = bbox
    xs = [x for x, _ in ring]
    ys = [y for _, y in ring]
    return not (max(xs) < minx or min(xs) > maxx or max(ys) < miny or min(ys) > maxy)


def clip_polygon(
    ring: list[tuple[float, float]], bbox: tuple[float, float, float, float]
) -> list[tuple[float, float]]:
    """Sutherland-Hodgman against the frame.

    Keeping whole rings that merely *intersect* the frame was the first attempt
    and it pulled North America in as a single 39,000-point polygon: a 682 KB
    base map for a view 7 degrees wide. A rectangle is convex, so
    Sutherland-Hodgman is exact here, and it closes the ring along the frame
    edge — which is what makes the land fill reach the edge of the map instead
    of stopping short of it.
    """
    minx, miny, maxx, maxy = bbox
    edges = (
        ("x", minx, 1),  # keep x >= minx
        ("x", maxx, -1),  # keep x <= maxx
        ("y", miny, 1),
        ("y", maxy, -1),
    )
    output = list(ring)
    for axis, bound, sign in edges:
        if not output:
            return []
        index = 0 if axis == "x" else 1

        def inside(
            point: tuple[float, float], index: int = index, bound: float = bound, sign: int = sign
        ) -> bool:
            return (point[index] - bound) * sign >= 0

        def cross(
            a: tuple[float, float], b: tuple[float, float], index: int = index, bound: float = bound
        ) -> tuple[float, float]:
            span = b[index] - a[index]
            t = 0.0 if span == 0 else (bound - a[index]) / span
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

        clipped: list[tuple[float, float]] = []
        for i, current in enumerate(output):
            previous = output[i - 1]
            if inside(current):
                if not inside(previous):
                    clipped.append(cross(previous, current))
                clipped.append(current)
            elif inside(previous):
                clipped.append(cross(previous, current))
        output = clipped
    return output


def build(name: str, bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    layers: dict[str, list[list[list[float]]]] = {}

    def emit(ring: list[tuple[float, float]]) -> list[list[float]]:
        return [[round(x, PRECISION), round(y, PRECISION)] for x, y in ring]

    # Polygons are clipped properly, not by dropping outside points: a filled
    # ring cut that way fills across the gap and draws a continent with a bite
    # out of it. Sutherland-Hodgman closes the ring along the frame edge.
    for label, source in POLYGONS.items():
        out = []
        for feature in fetch(source)["features"]:
            for ring in rings(feature.get("geometry") or {}):
                if len(ring) > 2 and intersects(ring, bbox):
                    clipped = clip_polygon(ring, bbox)
                    simplified = simplify(clipped, TOLERANCE) if len(clipped) > 2 else []
                    if len(simplified) > 2:
                        out.append(emit(simplified))
        layers[label] = out
        print(f"  {label:12} {len(out):4d} polygons, {sum(len(p) for p in out):6d} points")

    # Lines are clipped, because a stroked path has no interior to break.
    for label, source in LINES.items():
        out = []
        for feature in fetch(source)["features"]:
            for ring in rings(feature.get("geometry") or {}):
                for run in clip(ring, bbox):
                    simplified = simplify(run, TOLERANCE)
                    if len(simplified) > 1:
                        out.append(emit(simplified))
        layers[label] = out
        print(f"  {label:12} {len(out):4d} paths,    {sum(len(p) for p in out):6d} points")

    return {
        "name": name,
        "bbox": list(bbox),
        "source": "Natural Earth 1:10m, public domain",
        "source_url": "https://www.naturalearthdata.com/about/terms-of-use/",
        "tolerance_degrees": TOLERANCE,
        "layers": layers,
    }


EXTENTS = {
    # A margin around the Cascade Interconnect's own bounds, so the network
    # sits in a place rather than filling the frame edge to edge.
    "cascade-interconnect": (-125.6, 44.4, -118.2, 49.2),
}


def main() -> None:
    for slug, bbox in EXTENTS.items():
        print(f"{slug}:")
        document = build(slug, bbox)
        target = OUT / slug / "basemap.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, separators=(",", ":")) + "\n")
        size = target.stat().st_size
        print(f"  -> {target.relative_to(ROOT)} ({size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
