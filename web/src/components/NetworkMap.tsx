"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";

/**
 * A reference model on real geography.
 *
 * Two decisions shape this file.
 *
 * **Real coastlines, not invented polygons.** A reader deciding whether to
 * start from a reference model has to see the place it claims to be. The base
 * map is Natural Earth 1:50m—1:10m, clipped to the model's extent by
 * `ops/reference-models/build_basemap.py` and shipped as ~23 KB of lon/lat
 * paths beside the model itself.
 *
 * **No mapping library and no tile server.** Inline SVG, hand-rolled pan and
 * zoom. A tile basemap would look marginally more familiar and would make the
 * published site depend on somebody else's uptime and usage policy to render
 * its own reference models — on a static export with no server of its own,
 * that is a dependency with nothing behind it. The technique is the one
 * `WorldOutline` already uses for coverage thumbnails, at higher resolution.
 *
 * ## Projection
 *
 * Equirectangular with a standard parallel at the extent's centre latitude, so
 * `x` is scaled by `cos(lat0)`. At country scale that is visually correct —
 * shapes hold, nothing shears — and unlike Mercator it needs no inverse
 * transform to turn a click back into a coordinate. `WorldOutline` is
 * deliberately *un*corrected equirectangular for the opposite reason: it draws
 * axis-aligned bounding boxes, and any projection that curved one would draw a
 * shape the data does not have. Different jobs, different projections.
 */

type Pt = [number, number];

export type BaseMap = {
  bbox: [number, number, number, number];
  source: string;
  source_url: string;
  layers: { land: Pt[][]; lakes: Pt[][]; coastline: Pt[][]; borders: Pt[][] };
};

type Component = Record<string, unknown>;

export type SystemDocument = {
  name: string;
  components: Record<string, Component[]>;
  supplemental_attributes?: { GeographicInfo?: { id: number; geo_json: GeoJson }[] };
  supplemental_attribute_associations?: {
    component_id: number;
    component_type: string;
    attribute_id: number;
    attribute_type: string;
  }[];
};

type GeoJson = { type: string; coordinates: number[] | number[][] };

/** Technology colours, as CSS custom properties so both themes are one source.
 *
 *  Not glassbox's palette, which is stated to be "chosen for dark backgrounds".
 *  These are defined in `globals.css` against both themes and checked for
 *  contrast there. */
const TECH: Record<string, string> = {
  thermal: "var(--tech-thermal)",
  renewable: "var(--tech-renewable)",
  load: "var(--tech-load)",
};

/** Line weight by voltage. A transmission map that draws 115 kV and 500 kV
 *  with the same stroke has thrown away the one attribute that tells a reader
 *  what they are looking at. */
function strokeFor(voltage: number): number {
  if (voltage >= 400) return 2.2;
  if (voltage >= 220) return 1.4;
  return 0.8;
}

const LAYER_KEYS = ["lines", "buses", "generation", "load"] as const;
type LayerKey = (typeof LAYER_KEYS)[number];

export function NetworkMap({
  basemapUrl,
  systemUrl,
  synthetic,
}: {
  basemapUrl: string;
  systemUrl: string;
  /** Rendered on the map, not only in the record. A synthetic network drawn on
   *  a real coastline is exactly the picture that travels without its caption. */
  synthetic: boolean;
}) {
  const t = useTranslations("networkMap");
  const [base, setBase] = useState<BaseMap | null>(null);
  const [system, setSystem] = useState<SystemDocument | null>(null);
  const [failed, setFailed] = useState(false);
  const [layers, setLayers] = useState<Record<LayerKey, boolean>>({
    lines: true,
    buses: true,
    generation: true,
    load: false,
  });
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const svgRef = useRef<SVGSVGElement | null>(null);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetch(basemapUrl).then((r) => (r.ok ? r.json() : Promise.reject(r.status))),
      fetch(systemUrl).then((r) => (r.ok ? r.json() : Promise.reject(r.status))),
    ])
      .then(([b, s]) => {
        if (cancelled) return;
        setBase(b as BaseMap);
        setSystem(s as SystemDocument);
      })
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, [basemapUrl, systemUrl]);

  // -- projection ---------------------------------------------------------
  const project = useMemo(() => {
    if (!base) return null;
    const [minLon, minLat, maxLon, maxLat] = base.bbox;
    const lat0 = ((minLat + maxLat) / 2) * (Math.PI / 180);
    const kx = Math.cos(lat0);
    const width = (maxLon - minLon) * kx;
    const height = maxLat - minLat;
    return {
      width,
      height,
      to: ([lon, lat]: Pt): Pt => [(lon - minLon) * kx, maxLat - lat],
    };
  }, [base]);

  // -- bus coordinates, from the association table ------------------------
  const busPoints = useMemo(() => {
    if (!system) return new Map<number, Pt>();
    const geo = new Map<number, GeoJson>();
    for (const g of system.supplemental_attributes?.GeographicInfo ?? []) geo.set(g.id, g.geo_json);
    const out = new Map<number, Pt>();
    for (const a of system.supplemental_attribute_associations ?? []) {
      if (a.attribute_type !== "GeographicInfo") continue;
      const g = geo.get(a.attribute_id);
      if (g?.type === "Point") out.set(a.component_id, g.coordinates as Pt);
    }
    return out;
  }, [system]);

  const onWheel = useCallback((event: WheelEvent) => {
    event.preventDefault();
    setView((v) => {
      const k = Math.min(24, Math.max(1, v.k * (event.deltaY < 0 ? 1.15 : 1 / 1.15)));
      return { ...v, k };
    });
  }, []);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    // Non-passive, because the whole point is to stop the page scrolling. React's
    // onWheel is passive and cannot call preventDefault.
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [onWheel]);

  if (failed) {
    return (
      <div className="og-card px-6 py-8 text-sm text-[color:var(--muted)]">
        <p className="font-medium text-[color:var(--foreground)]">{t("unavailable")}</p>
        <p className="mt-2">{t("unavailableHelp")}</p>
      </div>
    );
  }
  if (!base || !system || !project) {
    return <div className="og-card px-6 py-8 text-sm text-[color:var(--muted)]">{t("loading")}</div>;
  }

  const { width, height, to } = project;
  const path = (pts: Pt[]) => pts.map((p) => to(p).map((n) => n.toFixed(3)).join(",")).join(" ");
  const buses = (system.components.ACBus ?? []) as { id: number; base_voltage?: number }[];
  const arcs = new Map(
    ((system.components.Arc ?? []) as { id: number; from_id: number; to_id: number }[]).map((a) => [
      a.id,
      a,
    ]),
  );
  const lines = (system.components.Line ?? []) as { id: number; arc: number }[];
  const voltageOf = new Map(buses.map((b) => [b.id, b.base_voltage ?? 0]));

  function injections(kind: "ThermalStandard" | "RenewableDispatch" | "PowerLoad") {
    return ((system!.components[kind] ?? []) as { id: number; bus: number }[])
      .map((c) => ({ ...c, at: busPoints.get(c.bus) }))
      .filter((c): c is typeof c & { at: Pt } => Boolean(c.at));
  }

  return (
    <figure className="og-card overflow-hidden">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${width} ${height}`}
        className="block w-full cursor-grab touch-none bg-[color:var(--map-water)]"
        style={{ aspectRatio: `${width} / ${height}` }}
        role="img"
        aria-label={t("alt", { name: system.name })}
        onPointerDown={(e) => {
          drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d) return;
          const rect = e.currentTarget.getBoundingClientRect();
          setView((v) => ({
            ...v,
            x: d.vx + ((e.clientX - d.x) / rect.width) * width,
            y: d.vy + ((e.clientY - d.y) / rect.height) * height,
          }));
        }}
        onPointerUp={() => (drag.current = null)}
      >
        <g transform={`translate(${width / 2} ${height / 2}) scale(${view.k}) translate(${-width / 2 + view.x / view.k} ${-height / 2 + view.y / view.k})`}>
          {/* Sea, then land, then lakes, then the coast and the borders, then
              the network. The order is the legibility of the whole thing: a
              coastline drawn over a transmission line reads as the line
              stopping at the shore, and land drawn over the sea is the only
              way a viewer can tell which is which.

              The first version filled the frame with land and drew coastlines
              on top, so the Pacific and Idaho were the same colour and the
              coast was the boundary between two identical things. */}
          <rect x={0} y={0} width={width} height={height} fill="var(--map-water)" />
          {base.layers.land.map((p, i) => (
            <polygon key={`ld${i}`} points={path(p)} fill="var(--map-land)" stroke="none" />
          ))}
          {base.layers.lakes.map((p, i) => (
            <polygon key={`lk${i}`} points={path(p)} fill="var(--map-water)" stroke="none" />
          ))}
          {/* `vectorEffect="non-scaling-stroke"` makes `strokeWidth` a screen
              unit, so these are pixels and not projection degrees. The first
              version asked for 0.012 — twelve thousandths of a pixel, which is
              a coastline nobody can see. */}
          {base.layers.coastline.map((p, i) => (
            <polyline
              key={`c${i}`}
              points={path(p)}
              fill="none"
              stroke="var(--map-coast)"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          ))}
          {base.layers.borders.map((p, i) => (
            <polyline
              key={`b${i}`}
              points={path(p)}
              fill="none"
              stroke="var(--map-border)"
              strokeWidth={1}
              strokeDasharray="4 3"
              vectorEffect="non-scaling-stroke"
            />
          ))}

          {layers.lines &&
            lines.map((line) => {
              const arc = arcs.get(line.arc);
              if (!arc) return null;
              const a = busPoints.get(arc.from_id);
              const b = busPoints.get(arc.to_id);
              if (!a || !b) return null;
              const kv = Math.max(voltageOf.get(arc.from_id) ?? 0, voltageOf.get(arc.to_id) ?? 0);
              return (
                <line
                  key={line.id}
                  x1={to(a)[0]}
                  y1={to(a)[1]}
                  x2={to(b)[0]}
                  y2={to(b)[1]}
                  stroke="var(--tech-line)"
                  strokeWidth={strokeFor(kv)}
                  strokeOpacity={0.75}
                  vectorEffect="non-scaling-stroke"
                />
              );
            })}

          {layers.buses &&
            buses.map((bus) => {
              const at = busPoints.get(bus.id);
              if (!at) return null;
              const [x, y] = to(at);
              return (
                <circle
                  key={bus.id}
                  cx={x}
                  cy={y}
                  r={0.014}
                  fill="var(--tech-bus)"
                />
              );
            })}

          {layers.generation &&
            (["ThermalStandard", "RenewableDispatch"] as const).map((kind) =>
              injections(kind).map((c) => {
                const [x, y] = to(c.at);
                const fill = kind === "ThermalStandard" ? TECH.thermal : TECH.renewable;
                return (
                  <circle
                    key={`${kind}${c.id}`}
                    cx={x}
                    cy={y}
                    r={0.026}
                    fill={fill}
                    fillOpacity={0.9}
                  />
                );
              }),
            )}

          {layers.load &&
            injections("PowerLoad").map((c) => {
              const [x, y] = to(c.at);
              return (
                <rect
                  key={`ld${c.id}`}
                  x={x - 0.025}
                  y={y - 0.025}
                  width={0.05}
                  height={0.05}
                  fill={TECH.load}
                />
              );
            })}
        </g>
      </svg>

      <figcaption className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-t px-4 py-3 text-xs" style={{ borderColor: "var(--border)" }}>
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          {LAYER_KEYS.map((key) => (
            <label key={key} className="inline-flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={layers[key]}
                onChange={() => setLayers((l) => ({ ...l, [key]: !l[key] }))}
              />
              {t(`layer.${key}`)}
            </label>
          ))}
        </div>
        <p className="text-[color:var(--muted)]">
          {synthetic ? `${t("synthetic")} · ` : ""}
          {base.source}
        </p>
      </figcaption>
    </figure>
  );
}
