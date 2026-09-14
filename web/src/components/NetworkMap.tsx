"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import {
  type BaseMap,
  LAYER_KEYS,
  type LayerKey,
  type ModelElement,
  type Pt,
  type SystemDocument,
  hitTargets,
  nearestElement,
  readElements,
} from "@/lib/model-view";

/**
 * A reference model on real geography, and what you are pointing at.
 *
 * Three decisions shape this file.
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
 * **The map names what you point at (#98).** It drew 412 buses and 455 circuits
 * and identified none of them, so the picture could say "this is shaped like
 * Britain" and could not say "this is the substation you meant". Those are very
 * different claims and only the second makes a reference model usable rather
 * than decorative.
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

/** How near the pointer has to be, in CSS pixels, to identify an element.
 *
 *  Generous on purpose: a 400 kV circuit is two pixels wide, and a reader who
 *  has to land inside two pixels to hover it has a feature that does not work
 *  where it is most wanted. `nearestElement` resolves ties by distance, so a
 *  large radius over a dense substation still picks the closest thing rather
 *  than the first one it met. */
const HIT_RADIUS_PX = 14;

/** How far `PageUp`/`PageDown` move through the element order. Stepping one at
 *  a time through 965 components with the arrow keys is not traversal. */
const PAGE_STEP = 25;

export function NetworkMap({
  basemapUrl,
  systemUrl,
  synthetic,
  modelHref,
}: {
  basemapUrl: string;
  systemUrl: string;
  /** Rendered on the map, not only in the record. A synthetic network drawn on
   *  a real coastline is exactly the picture that travels without its caption. */
  synthetic: boolean;
  /** The page that renders this model's components and their recorded values.
   *
   *  Optional: a model with no page behind it shows no link rather than one
   *  that 404s. The identity card is still worth having without it — knowing
   *  which circuit you are looking at is the point, and the page is where you
   *  go next. */
  modelHref?: string;
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
  const [svg, setSvg] = useState<SVGSVGElement | null>(null);
  const [identified, setIdentified] = useState<ModelElement | null>(null);
  /** Where keyboard traversal is, as an index into `visible`.
   *
   *  Separate from `identified` because hovering must not move it: a reader who
   *  has stepped to a circuit with the keyboard and then brushes the mouse over
   *  the map should find their place still there. `-1` is "not started". */
  const [cursor, setCursor] = useState(-1);
  const instructionsId = useId();
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean } | null>(null);
  const group = useRef<SVGGElement | null>(null);

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

  // -- the model, read once -----------------------------------------------
  //
  // One reading, in `model-view`, shared with the page that tabulates the same
  // document. What is drawn and what can be identified are the same list by
  // construction, so there is no way for the map to show an element the
  // identity card cannot name.
  const elements = useMemo(() => readElements(system), [system]);

  /** Drawable, in a layer the reader has switched on. The traversal order and
   *  the hit-test set are both this: a switched-off layer is not on the map, so
   *  it must not be under the cursor or in the keyboard order either. */
  const visible = useMemo(
    () => elements.filter((e) => layers[e.layer] && (e.at || e.route)),
    [elements, layers],
  );

  const populated = useMemo(() => {
    const seen = { lines: false, buses: false, generation: false, load: false };
    for (const element of elements) seen[element.layer] = true;
    return seen as Record<LayerKey, boolean>;
  }, [elements]);

  const zoom = useCallback((factor: number) => {
    setView((v) => ({ ...v, k: Math.min(24, Math.max(1, v.k * factor)) }));
  }, []);

  const onWheel = useCallback((event: WheelEvent) => {
    event.preventDefault();
    zoom(event.deltaY < 0 ? 1.15 : 1 / 1.15);
  }, [zoom]);

  useEffect(() => {
    if (!svg) return;
    // Non-passive, because the whole point is to stop the page scrolling. React's
    // onWheel is passive and cannot call preventDefault.
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [svg, onWheel]);

  /** Client coordinates into model coordinates.
   *
   *  Through the drawn group's own screen CTM rather than by inverting the pan
   *  and zoom by hand: the group carries the viewBox scaling *and* the camera
   *  transform, and reproducing that arithmetic here would be a second copy of
   *  it that drifts the first time the transform changes. */
  const toModel = useCallback((clientX: number, clientY: number) => {
    const g = group.current;
    const matrix = g?.getScreenCTM();
    if (!g || !matrix) return null;
    const inverse = matrix.inverse();
    const point = new DOMPoint(clientX, clientY).matrixTransform(inverse);
    // One CSS pixel, in model units, at this zoom. `a` is the horizontal scale
    // of the forward matrix, so its reciprocal converts back.
    const perPixel = matrix.a === 0 ? 0 : 1 / matrix.a;
    return { x: point.x, y: point.y, perPixel };
  }, []);

  /** The same elements, projected once, in the space the pointer arrives in.
   *
   *  Drawn elements are positioned by `project.to` at render time while
   *  `ModelElement` keeps longitude and latitude, so a hit test against the
   *  unprojected geometry compares two different coordinate systems and matches
   *  nothing. It did exactly that until a browser was pointed at it. */
  const targets = useMemo(
    () => (project ? hitTargets(visible, project.to) : []),
    [visible, project],
  );

  const identifyAt = useCallback(
    (clientX: number, clientY: number) => {
      const at = toModel(clientX, clientY);
      if (!at) return;
      setIdentified(nearestElement(targets, at.x, at.y, HIT_RADIUS_PX * at.perPixel));
    },
    [toModel, targets],
  );

  /** Step the keyboard cursor, and bring what it lands on into view.
   *
   *  Centring matters more than it sounds: without it the cursor walks onto
   *  elements that are off-screen at any zoom past 1, so the card names
   *  something the reader cannot see and the map appears not to have responded. */
  const step = useCallback(
    (delta: number) => {
      if (visible.length === 0) return;
      setCursor((previous) => {
        const next =
          previous < 0
            ? delta > 0
              ? 0
              : visible.length - 1
            : Math.min(visible.length - 1, Math.max(0, previous + delta));
        const element = visible[next];
        setIdentified(element);
        const centre = element.at ?? element.route?.[Math.floor(element.route.length / 2)];
        if (centre && project) {
          const [x, y] = project.to(centre);
          setView((v) => ({
            ...v,
            x: (project.width / 2 - x) * v.k,
            y: (project.height / 2 - y) * v.k,
          }));
        }
        return next;
      });
    },
    [visible, project],
  );

  /** Projected points as an SVG `points` string. Stable, so the memo below is. */
  const path = useCallback(
    (pts: readonly Pt[]) =>
      project
        ? pts.map((p) => project.to(p).map((n) => n.toFixed(3)).join(",")).join(" ")
        : "",
    [project],
  );

  /** The network layers, built once per model and per layer switch.
   *
   *  Held in a memo rather than inlined in the render, and that is the whole
   *  performance story of this component: `identified` changes on every pointer
   *  move, and without this React would reconcile up to 965 SVG nodes at
   *  pointer-move frequency to redraw one halo. Identical element references
   *  let it skip the subtree entirely. */
  const drawn = useMemo(() => {
    if (!project) return null;
    return {
      branches: visible
        .filter((element) => element.route)
        .map((element) => <Branch key={element.key} element={element} path={path} />),
      points: visible
        .filter((element) => element.at)
        .map((element) => <Point key={element.key} element={element} to={project.to} />),
    };
  }, [visible, project, path]);

  // A layer switched off can leave the cursor past the end of a shorter list,
  // pointing at whatever now occupies that index — a silent jump to an
  // unrelated component. Reset rather than clamp: the reader's place was in the
  // layer they just hid, and there is no honest place to move it to.
  useEffect(() => {
    setCursor(-1);
    setIdentified(null);
  }, [layers]);

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

  return (
    <figure className="og-card overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b px-4 py-3 text-sm" style={{ borderColor: "var(--border)" }}>
        <button type="button" className="og-tag px-3 py-1.5" onClick={() => zoom(1.3)} disabled={view.k >= 24}>{t("zoomIn")}</button>
        <button type="button" className="og-tag px-3 py-1.5" onClick={() => zoom(1 / 1.3)} disabled={view.k <= 1}>{t("zoomOut")}</button>
        <button type="button" className="og-tag px-3 py-1.5" onClick={() => { setView({ x: 0, y: 0, k: 1 }); setCursor(-1); setIdentified(null); }}>{t("reset")}</button>
        <span className="text-xs text-[color:var(--muted)]">{t("zoomLevel", { level: Math.round(view.k * 100) })}</span>
        <p id={instructionsId} className="w-full text-xs text-[color:var(--muted)]">{t("navigationHelp")}</p>
      </div>
      <svg
        ref={setSvg}
        tabIndex={0}
        viewBox={`0 0 ${width} ${height}`}
        className="block w-full cursor-grab touch-none bg-[color:var(--map-water)]"
        style={{ aspectRatio: `${width} / ${height}` }}
        role="img"
        aria-label={t("alt", { name: system.name })}
        aria-describedby={instructionsId}
        onKeyDown={(e) => {
          const step10 = 0.1;
          const movement: Record<string, [number, number]> = {
            ArrowLeft: [width * step10, 0], ArrowRight: [-width * step10, 0],
            ArrowUp: [0, height * step10], ArrowDown: [0, -height * step10],
          };
          // Shift turns the arrow keys from a camera into a cursor. The camera
          // keys keep the bare arrows because panning is what a reader reaches
          // for first and #92 established them; traversal is the deliberate
          // act, so it takes the modifier. Both are announced in the help text
          // under the toolbar rather than left to be discovered.
          if (e.shiftKey && (e.key === "ArrowRight" || e.key === "ArrowLeft")) {
            e.preventDefault();
            step(e.key === "ArrowRight" ? 1 : -1);
          } else if (e.key === "PageDown" || e.key === "PageUp") {
            e.preventDefault();
            step(e.key === "PageDown" ? PAGE_STEP : -PAGE_STEP);
          } else if (e.key === "Escape") {
            setCursor(-1);
            setIdentified(null);
          } else if (movement[e.key]) {
            e.preventDefault();
            const [x, y] = movement[e.key];
            setView((v) => ({ ...v, x: v.x + x, y: v.y + y }));
          } else if (["+", "=", "-", "Home"].includes(e.key)) {
            e.preventDefault();
            if (e.key === "Home") setView({ x: 0, y: 0, k: 1 });
            else zoom(e.key === "-" ? 1 / 1.3 : 1.3);
          }
        }}
        onPointerDown={(e) => {
          if (!e.isPrimary || e.button !== 0) return;
          drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d) {
            // Hover identifies; it never moves the keyboard cursor.
            if (e.pointerType === "mouse") identifyAt(e.clientX, e.clientY);
            return;
          }
          if (Math.abs(e.clientX - d.x) > 3 || Math.abs(e.clientY - d.y) > 3) d.moved = true;
          const rect = e.currentTarget.getBoundingClientRect();
          setView((v) => ({
            ...v,
            x: d.vx + ((e.clientX - d.x) / rect.width) * width,
            y: d.vy + ((e.clientY - d.y) / rect.height) * height,
          }));
        }}
        onPointerUp={(e) => {
          // A tap is the touch equivalent of a hover, and it has to be told
          // apart from the end of a drag — otherwise panning the map with a
          // finger identifies whatever happened to be under it when you let go.
          if (drag.current && !drag.current.moved) identifyAt(e.clientX, e.clientY);
          drag.current = null;
        }}
        onPointerLeave={(e) => {
          // Mouse only, and the exception is the whole reason this reads the
          // pointer type. A touch pointer stops existing the instant the finger
          // lifts, so `pointerleave` fires immediately after every tap — and
          // clearing here wiped out the identification the tap had just made,
          // which looked exactly like tapping doing nothing at all.
          //
          // Nor does it clear what the keyboard is pointing at: a reader who
          // stepped to a circuit and then moved the mouse off the map has not
          // changed their mind about the circuit.
          if (e.pointerType === "mouse" && cursor < 0) setIdentified(null);
        }}
        onPointerCancel={() => (drag.current = null)}
        onLostPointerCapture={() => (drag.current = null)}
      >
        <g
          ref={group}
          transform={`translate(${width / 2} ${height / 2}) scale(${view.k}) translate(${-width / 2 + view.x / view.k} ${-height / 2 + view.y / view.k})`}
        >
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

          {/* The network, drawn from the same list that answers "what is this".
              Branches first so that buses and injections sit on top of the
              circuits meeting them — the substation is the thing you point at,
              and a line drawn over it would take the hit. */}
          {drawn?.branches}
          {drawn?.points}

          {/* The halo, over everything and outside the layers above, so that
              identifying an element never re-renders 965 siblings. */}
          {identified ? <Halo element={identified} to={to} path={path} /> : null}
        </g>
      </svg>

      {/* Not a `title` element and not a tooltip. A native tooltip is invisible
          to touch, unreachable by keyboard, and gone the moment the pointer
          moves — so the one place a reader could read an identifier would be
          the one place they could not copy it from. A region that stays put
          answers hover, tap and focus with the same words, and `aria-live`
          means a screen reader hears each one as the cursor moves. */}
      <div
        role="status"
        aria-live="polite"
        // Named, so it is distinguishable from the other live regions a page
        // may hold — the reference inventory renders one map per geography and
        // the picker has a notice of its own, so four unnamed status regions
        // were on one page and "the status region" stopped meaning anything.
        aria-label={t("identifiedLabel", { name: system.name })}
        className="border-t px-4 py-3 text-sm"
        style={{ borderColor: "var(--border)" }}
      >
        {identified ? (
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="og-eyebrow">{t(`kind.${identified.kind}`)}</span>
            <span className="font-mono text-sm font-medium">{identified.name}</span>
            {identified.facts.map((fact) => (
              <span key={fact.key} className="text-xs text-[color:var(--muted)]">
                {t(`fact.${fact.key}`)} {fact.value}
              </span>
            ))}
            {modelHref ? (
              <Link
                href={`${modelHref}?component=${encodeURIComponent(identified.name)}`}
                className="text-xs font-medium text-[color:var(--accent-text)] hover:underline"
              >
                {t("openInModel")}
              </Link>
            ) : null}
          </div>
        ) : (
          <p className="text-xs text-[color:var(--muted)]">{t("identifyHelp")}</p>
        )}
      </div>

      <figcaption className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-t px-4 py-3 text-xs" style={{ borderColor: "var(--border)" }}>
        <div className="flex flex-wrap gap-x-4 gap-y-2">
          {/* Only the layers this model actually has.
              A "Generation" checkbox on a network with no generators is a
              claim that generators are there and hidden, which is the opposite
              of what the model says about itself — and the GB model, which
              carries topology and no injections at all, would have offered two
              switches that do nothing. The empty-layer case is the one worth
              being careful about: a control that toggles nothing is read as a
              filter, not as an absence. */}
          {LAYER_KEYS.filter((key) => populated[key]).map((key) => (
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
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          {modelHref ? (
            <Link href={modelHref} className="font-medium text-[color:var(--accent-text)] hover:underline">
              {t("openModel")}
            </Link>
          ) : null}
          <p className="text-[color:var(--muted)]">
            {synthetic ? `${t("synthetic")} · ` : ""}
            {base.source}
          </p>
        </div>
      </figcaption>
    </figure>
  );
}

/** One branch. Memoised on its own props so that moving the pointer across the
 *  map re-renders the halo and nothing else. */
function Branch({
  element,
  path,
}: {
  element: ModelElement;
  path: (pts: readonly Pt[]) => string;
}) {
  const route = element.route!;
  return (
    <polyline
      points={path(route)}
      fill="none"
      strokeLinejoin="round"
      strokeLinecap="round"
      stroke={element.kind === "hvdc" ? "var(--tech-renewable)" : "var(--tech-line)"}
      strokeWidth={strokeFor(element.voltageKv ?? 0)}
      strokeOpacity={0.75}
      strokeDasharray={element.kind === "hvdc" ? "6 4" : undefined}
      vectorEffect="non-scaling-stroke"
    />
  );
}

function Point({ element, to }: { element: ModelElement; to: (p: Pt) => Pt }) {
  const [x, y] = to(element.at!);
  if (element.kind === "load") {
    return <rect x={x - 0.025} y={y - 0.025} width={0.05} height={0.05} fill={TECH.load} />;
  }
  if (element.kind === "bus") {
    return <circle cx={x} cy={y} r={0.014} fill="var(--tech-bus)" />;
  }
  return (
    <circle
      cx={x}
      cy={y}
      r={0.026}
      fill={element.kind === "thermal" ? TECH.thermal : TECH.renewable}
      fillOpacity={0.9}
    />
  );
}

/** The ring, or the traced route, around whatever the card is naming.
 *
 * Drawn rather than restyling the element in place, because restyling means
 * re-rendering the layer it sits in — and that layer is up to 965 nodes that
 * would be reconciled on every pointer move. */
function Halo({
  element,
  to,
  path,
}: {
  element: ModelElement;
  to: (p: Pt) => Pt;
  path: (pts: readonly Pt[]) => string;
}) {
  const stroke = {
    stroke: "var(--accent)",
    fill: "none" as const,
    vectorEffect: "non-scaling-stroke" as const,
    pointerEvents: "none" as const,
  };
  if (element.route) {
    return <polyline points={path(element.route)} strokeWidth={5} strokeOpacity={0.55} strokeLinecap="round" strokeLinejoin="round" {...stroke} />;
  }
  if (!element.at) return null;
  const [x, y] = to(element.at);
  return <circle cx={x} cy={y} r={0.05} strokeWidth={2} {...stroke} />;
}
