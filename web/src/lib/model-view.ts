import { formatNumber, formatSignificant } from "./format";

/**
 * Reading a registered Sienna model document.
 *
 * One reading, shared by the map that draws it and the page that tabulates it.
 * The two used to be one file with one reader, and the moment there were two
 * views of the same document there were two chances to read `rating` without
 * asking what units it is in — which is not a rounding error. KPG's lines carry
 * `rating: 9.04` on a `COMPONENT_BASE` of 100 MVA and GB's carry
 * `rating: 3575.0` in `NATURAL_UNITS`, so a viewer that prints the number it
 * finds tells a reader one network has 9 MVA circuits and the other 3,575 MVA
 * circuits. Both numbers are right and the claim is nonsense.
 *
 * So the conversion lives here, once, and everything that shows a number goes
 * through it.
 */

export type Pt = [number, number];

export type GeoJson = { type: string; coordinates: number[] | number[][] };

export type Component = Record<string, unknown>;

export type SystemDocument = {
  name: string;
  description?: string;
  components: Record<string, Component[]>;
  supplemental_attributes?: { id: number; geo_json: GeoJson }[];
  supplemental_attribute_associations?: {
    component_id: number;
    component_type: string;
    attribute_id: number;
    attribute_type: string;
  }[];
};

export type BaseMap = {
  bbox: [number, number, number, number];
  source: string;
  source_url: string;
  layers: { land: Pt[][]; lakes: Pt[][]; coastline: Pt[][]; borders: Pt[][] };
};

/**
 * A quantity the document states per unit, in MW or MVA.
 *
 * Sienna's `power_units` says which base a component's power quantities are on:
 * `NATURAL_UNITS` means the number is already MW or MVA, `COMPONENT_BASE` means
 * it is per unit on that component's own `base_power`. The two appear in the
 * same catalog — the OSM models are in natural units and KPG is on a 100 MVA
 * base throughout — so neither can be assumed.
 *
 * An unrecognised `power_units` returns null rather than the raw number. A
 * number whose unit this function did not understand is not a value; printing
 * it beside "MVA" would be asserting a unit nobody stated.
 */
export function inNaturalUnits(
  value: unknown,
  powerUnits: unknown,
  basePower: unknown,
): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  if (powerUnits === "NATURAL_UNITS") return value;
  if (powerUnits === "COMPONENT_BASE") {
    return typeof basePower === "number" && Number.isFinite(basePower) ? value * basePower : null;
  }
  return null;
}

/** Bus points and line corridors, from the flat attribute array.
 *
 * `supplemental_attributes` is a flat, untyped list: the schema is explicit
 * that nothing buckets it by type and that `attribute_type` on the association
 * row is the only discriminator a consumer gets. */
export function readGeography(system: SystemDocument | null): {
  buses: Map<number, Pt>;
  corridors: Map<number, Pt[]>;
} {
  const buses = new Map<number, Pt>();
  const corridors = new Map<number, Pt[]>();
  if (!system) return { buses, corridors };

  const attributes = new Map<number, GeoJson>();
  for (const g of system.supplemental_attributes ?? []) attributes.set(g.id, g.geo_json);

  for (const a of system.supplemental_attribute_associations ?? []) {
    if (a.attribute_type !== "GeographicInfo") continue;
    const g = attributes.get(a.attribute_id);
    if (g?.type === "Point") buses.set(a.component_id, g.coordinates as Pt);
    else if (g?.type === "LineString") corridors.set(a.component_id, g.coordinates as Pt[]);
  }
  return { buses, corridors };
}

/** The map's layer switches, and what each one draws. */
export const LAYER_KEYS = ["lines", "buses", "generation", "load"] as const;
export type LayerKey = (typeof LAYER_KEYS)[number];

export type ElementKind = "bus" | "line" | "hvdc" | "thermal" | "renewable" | "load";

/** One labelled fact about an element, already in stated units.
 *
 * `key` names a message rather than carrying English, and `value` is formatted
 * here because the unit symbol travels with the number: "400 kV" split into a
 * number and a separate unit column is how a reader ends up reading megawatts
 * as megavolt-amperes. `formatBytes` sets the same precedent. */
export interface ElementFact {
  key: "voltage" | "rating" | "capacity" | "demand" | "fuel" | "status" | "between";
  value: string;
}

export interface ModelElement {
  /** Stable across renders and unique within a document. */
  key: string;
  kind: ElementKind;
  layer: LayerKey;
  /** The Sienna component name. **This is the identity**, and it is the one
   *  that joins back to the model document and to `parameters.json`, which
   *  keys its per-branch provenance on exactly this string. */
  name: string;
  /** The document's internal integer id. Deliberately never shown as an
   *  identifier: it is an index the generator assigned, it changes when the
   *  model is rebuilt, and displayed beside a name it would look exactly as
   *  durable. Kept only to join components to each other inside one read. */
  id: number;
  /** Where it is drawn. A point element has `at`; a branch has `route`, which
   *  is the surveyed corridor where the model carries one and the chord
   *  between its terminals where it does not. */
  at?: Pt;
  route?: Pt[];
  /** The nominal voltage, as a number, for drawing.
   *
   *  Separate from the formatted fact beside it and not a duplicate of it: a
   *  transmission map that strokes 132 kV and 400 kV identically has thrown
   *  away the one attribute that tells a reader what they are looking at, and
   *  a stroke width cannot be computed from the string "400 kV". */
  voltageKv?: number | null;
  facts: ElementFact[];
}

const KIND_LAYER: Record<ElementKind, LayerKey> = {
  bus: "buses",
  line: "lines",
  hvdc: "lines",
  thermal: "generation",
  renewable: "generation",
  load: "load",
};

type Named = { id: number; name?: string };

function kv(value: unknown): string | null {
  return typeof value === "number" && Number.isFinite(value)
    ? `${formatNumber(value)} kV`
    : null;
}

function mva(value: number | null, unit: "MVA" | "MW"): string | null {
  return value === null ? null : `${formatNumber(Math.round(value))} ${unit}`;
}

function facts(entries: [ElementFact["key"], string | null][]): ElementFact[] {
  return entries
    .filter((entry): entry is [ElementFact["key"], string] => entry[1] !== null)
    .map(([key, value]) => ({ key, value }));
}

/**
 * Every element of the model, in the order a reader steps through them.
 *
 * Buses first, then branches, then what is attached to them — largest structure
 * to smallest, which is also the order the layer switches are in. The order is
 * the keyboard traversal order, so it has to be stable and it has to be the
 * same one the eye would take.
 *
 * An element with no geography is still returned. It is not drawable and the
 * map skips it, but it is a component of the model and the page lists it;
 * dropping it here would make "the model has 412 buses" and "the page lists
 * 400" disagree with no explanation.
 */
export function readElements(system: SystemDocument | null): ModelElement[] {
  if (!system) return [];
  const { buses: points, corridors } = readGeography(system);
  const at = (id: number) => points.get(id);

  const component = <T>(kind: string) => (system.components[kind] ?? []) as (T & Named)[];

  const busRows = component<{ base_voltage?: number }>("ACBus");
  const voltageOf = new Map(busRows.map((b) => [b.id, b.base_voltage ?? 0]));
  const busName = new Map(busRows.map((b) => [b.id, b.name ?? ""]));
  const arcs = new Map(
    component<{ from_id: number; to_id: number }>("Arc").map((a) => [a.id, a]),
  );

  const out: ModelElement[] = [];

  for (const bus of busRows) {
    out.push({
      key: `bus-${bus.id}`,
      kind: "bus",
      layer: "buses",
      name: bus.name ?? "",
      id: bus.id,
      at: at(bus.id),
      voltageKv: bus.base_voltage ?? null,
      facts: facts([["voltage", kv(bus.base_voltage)]]),
    });
  }

  const branch = (
    kind: ElementKind,
    row: Named & {
      arc?: number;
      rating?: unknown;
      power_units?: unknown;
      base_power?: unknown;
      active_power_limits_from?: { max?: unknown };
    },
  ): ModelElement | null => {
    const arc = typeof row.arc === "number" ? arcs.get(row.arc) : undefined;
    if (!arc) return null;
    const ends = [at(arc.from_id), at(arc.to_id)];
    const corridor = corridors.get(row.id);
    const route =
      corridor && corridor.length > 1
        ? corridor
        : ends[0] && ends[1]
          ? ([ends[0], ends[1]] as Pt[])
          : undefined;
    const rating = inNaturalUnits(
      kind === "hvdc" ? row.active_power_limits_from?.max : row.rating,
      row.power_units,
      row.base_power,
    );
    const terminals = Math.max(voltageOf.get(arc.from_id) ?? 0, voltageOf.get(arc.to_id) ?? 0);
    return {
      key: `${kind}-${row.id}`,
      kind,
      layer: "lines",
      name: row.name ?? "",
      id: row.id,
      route,
      voltageKv: kind === "hvdc" ? null : terminals || null,
      facts: facts([
        ["voltage", kind === "hvdc" ? null : kv(terminals || null)],
        ["rating", mva(rating, kind === "hvdc" ? "MW" : "MVA")],
        [
          "between",
          busName.get(arc.from_id) && busName.get(arc.to_id)
            ? `${busName.get(arc.from_id)} → ${busName.get(arc.to_id)}`
            : null,
        ],
      ]),
    };
  };

  for (const row of component<{ arc: number }>("Line")) {
    const element = branch("line", row);
    if (element) out.push(element);
  }
  for (const row of component<{ arc: number }>("TwoTerminalGenericHVDCLine")) {
    const element = branch("hvdc", row);
    if (element) out.push(element);
  }

  const injection = (
    kind: ElementKind,
    source: string,
    read: (row: Component) => ElementFact[],
  ) => {
    for (const row of component<Component>(source)) {
      const bus = typeof row.bus === "number" ? row.bus : undefined;
      out.push({
        key: `${kind}-${row.id}`,
        kind,
        layer: KIND_LAYER[kind],
        name: row.name ?? "",
        id: row.id,
        at: bus === undefined ? undefined : at(bus),
        facts: read(row),
      });
    }
  };

  const capacity = (row: Component) =>
    inNaturalUnits(
      (row.active_power_limits as { max?: unknown } | undefined)?.max ?? row.rating,
      row.power_units,
      row.base_power,
    );

  injection("thermal", "ThermalStandard", (row) =>
    facts([
      ["capacity", mva(capacity(row), "MW")],
      ["fuel", typeof row.fuel === "string" ? row.fuel : null],
      // On the row because KPG's fleet is committed against one solved hour:
      // 101 of its 201 units are OFFLINE, and a reader who does not see that
      // reads the map as a fleet with twice the running capacity it has.
      ["status", typeof row.status === "string" ? row.status : null],
    ]),
  );
  injection("renewable", "RenewableDispatch", (row) =>
    facts([["capacity", mva(capacity(row), "MW")]]),
  );
  injection("load", "PowerLoad", (row) =>
    facts([
      ["demand", mva(inNaturalUnits(row.max_active_power, row.power_units, row.base_power), "MW")],
    ]),
  );

  return out;
}

/**
 * An element with its geometry already projected, ready to be hit-tested.
 *
 * Separate from `ModelElement`, whose `at` and `route` are longitude and
 * latitude, and the separation is not bookkeeping. Distance has to be measured
 * in the space the reader is pointing at: a degree of longitude at 55°N is a
 * little over half a degree of latitude on screen, so a radius applied to
 * lon/lat is an ellipse, and "the nearest circuit" computed in that space is
 * not the nearest circuit. It is also how this was wrong first time round — the
 * pointer arrived in projected units and was compared against lon/lat, so
 * nothing was ever within range of anything and the map identified nothing at
 * all.
 */
export interface HitTarget {
  element: ModelElement;
  at?: Pt;
  route?: Pt[];
}

/** Project every drawable element once, for the hit test to read many times.
 *
 * **Branches first, points last, which is the order they are drawn in.** Every
 * circuit meeting a substation passes through the substation's own coordinate,
 * so pointing at a bus is very nearly pointing at eight lines, and the winner
 * of a near-tie is decided by this order alone. Pointing at a substation has to
 * name the substation — that is what the marker is for, it is the smaller
 * target, and it is what the reader sees on top. Ordered the other way round
 * this identified a circuit every time a bus was clicked squarely, which looks
 * like the marker not being clickable at all. */
export function hitTargets(
  elements: readonly ModelElement[],
  to: (p: Pt) => Pt,
): HitTarget[] {
  const branches: HitTarget[] = [];
  const points: HitTarget[] = [];
  for (const element of elements) {
    if (element.at) points.push({ element, at: to(element.at) });
    else if (element.route) branches.push({ element, route: element.route.map(to) });
  }
  return [...branches, ...points];
}

/**
 * The element nearest a point, within `radius`, or null.
 *
 * Hit-testing in script rather than with SVG pointer events, and the reason is
 * the lines. A 400 kV circuit is drawn two screen pixels wide; a reader has to
 * land the cursor inside two pixels to hover it, which in practice means the
 * feature does not work for the very elements it is most needed on. Nearest
 * wins instead, so pointing near a circuit identifies it — and the same
 * function answers for a bus, so a dense substation with lines radiating from
 * it resolves to whichever is actually closest rather than to whichever
 * happens to be later in the DOM.
 *
 * Distances are in projected units; the caller converts a screen radius.
 */
export function nearestElement(
  targets: readonly HitTarget[],
  x: number,
  y: number,
  radius: number,
): ModelElement | null {
  let best: ModelElement | null = null;
  let bestDistance = radius;
  for (const target of targets) {
    const distance = target.at
      ? // A point wins a near-tie, and this allowance is what "near" means.
        //
        // Every circuit meeting a substation runs through the substation's own
        // coordinate, so at a node the bus and eight lines are all at distance
        // zero and the winner is decided by sub-pixel noise — measured, pointing
        // squarely at a bus marker named a circuit four times in five. The
        // marker is the affordance and the smaller target, and a circuit is
        // still identifiable anywhere along its length away from a node, so the
        // point takes the node and the line keeps the corridor.
        Math.max(0, Math.hypot(target.at[0] - x, target.at[1] - y) - radius * POINT_BIAS)
      : target.route
        ? distanceToRoute(target.route, x, y)
        : Infinity;
    // `<=`, so that an equally near candidate later in the list wins — and the
    // list runs branches first, points last. At a node the bus and every
    // circuit meeting it are all at zero, and this is what decides between them.
    if (distance <= bestDistance) {
      best = target.element;
      bestDistance = distance;
    }
  }
  return best;
}

/** How much of the hit radius a point element may be behind a branch and still
 *  win. A third: enough to cover the sub-pixel disagreement at a node, small
 *  enough that a corridor is never shadowed by a substation it passes. */
const POINT_BIAS = 1 / 3;

function distanceToRoute(route: readonly Pt[], x: number, y: number): number {
  let best = Infinity;
  for (let i = 1; i < route.length; i += 1) {
    best = Math.min(best, distanceToSegment(route[i - 1], route[i], x, y));
    if (best === 0) break;
  }
  return best;
}

function distanceToSegment(a: Pt, b: Pt, x: number, y: number): number {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const lengthSquared = dx * dx + dy * dy;
  // A degenerate segment is a point, and the projection below would divide by
  // zero. Both terminals of a zero-length branch are the same place, so the
  // distance to it is the distance to that place.
  if (lengthSquared === 0) return Math.hypot(x - a[0], y - a[1]);
  const t = Math.max(0, Math.min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / lengthSquared));
  return Math.hypot(x - (a[0] + t * dx), y - (a[1] + t * dy));
}

// ---------------------------------------------------------------------------
// The model as a table: what each component records, and on what basis.
// ---------------------------------------------------------------------------

/** The per-branch provenance the builders write beside each model.
 *
 * `parameters.json` is where an estimated value stops being a number and starts
 * being an argument: it names the rule, the inputs the rule was given, and the
 * approximations the author knows it makes. The OSM models carry one row per
 * line and per transformer; KPG carries the same statement at model level
 * because its impedances were computed upstream and it has no per-circuit rule
 * of its own to report. Both shapes are read here, and neither is invented for
 * the other. */
export interface BranchParameters {
  component: string;
  source_id?: string;
  basis?: string;
  method?: string;
  line_type?: string;
  inputs?: Record<string, number>;
  approximations?: string[];
}

export interface ModelParameters {
  model?: string;
  source?: { doi?: string; repository?: string; version?: string; commit?: string; licence?: string };
  measured?: string[];
  estimated?: string[];
  conventions?: string[];
  excluded?: Record<string, string>;
  lines?: BranchParameters[];
  transformers?: BranchParameters[];
}

/** A column of one component table, and the record's claim about it. */
export interface ValueColumn {
  /** `components.Line[].x`, verbatim from the record. */
  fieldId: string;
  /** The Sienna component type the field addresses: `Line`. */
  kind: string;
  /** The key inside the component object: `x`. */
  path: string;
  label: string;
  /** `measured`, `estimated` or `modeled`, exactly as `og:valueBasis` has it.
   *  Null where the record declares the field and not its basis, which is a
   *  gap in the record and is shown as one. */
  basis: string | null;
  unit: string | null;
  definition: string | null;
  caveat: string | null;
}

/** A field declaration, as much of it as this file reads. Structural rather
 *  than importing `FieldDetail`, so the model reader does not depend on the
 *  REST client's shape. */
export interface FieldLike {
  field_id?: string | null;
  local_name?: string;
  label?: string | null;
  value_basis?: string | null;
  unit_label?: string | null;
  definition?: string | null;
  completeness_caveats?: string | null;
}

const COMPONENT_FIELD = /^components\.([A-Za-z0-9_]+)\[\]\.(.+)$/;

/**
 * The record's field declarations, grouped by the component type they address.
 *
 * Joined on `og:fieldId` and never on the field's local name. The GB model
 * declares `x` twice — `components.Line[].x` is *modeled* and
 * `components.TransformerCircuit[].x` is *estimated* — so a join by name would
 * label one of them with the other's basis and say nothing about having done
 * so. A field whose `field_id` does not address a component array is skipped
 * rather than guessed at: `supplemental_attributes[].geo_json` is a real
 * declaration about real values and it is not a column of any of these tables.
 */
export function columnsByKind(fields: readonly FieldLike[]): Map<string, ValueColumn[]> {
  const out = new Map<string, ValueColumn[]>();
  for (const field of fields) {
    const match = COMPONENT_FIELD.exec(String(field.field_id ?? ""));
    if (!match) continue;
    const [, kind, path] = match;
    const column: ValueColumn = {
      fieldId: String(field.field_id),
      kind,
      path,
      label: field.label ?? field.local_name ?? path,
      basis: field.value_basis ?? null,
      unit: field.unit_label ?? null,
      definition: field.definition ?? null,
      caveat: field.completeness_caveats ?? null,
    };
    out.set(kind, [...(out.get(kind) ?? []), column]);
  }
  return out;
}

export interface ComponentValue {
  column: ValueColumn;
  /** Formatted for a cell, or null where the component does not carry the
   *  field at all — which is a fact about that component, not a zero. */
  text: string | null;
  /** The value as the document states it, for a reader who wants to see a
   *  structured one rather than be told it is structured. */
  raw: unknown;
  structured: boolean;
}

export interface ComponentRow {
  key: string;
  kind: string;
  /** The Sienna component name: the identity, and the join key. */
  name: string;
  /** The upstream identifier the build recorded — an OSM way id, a PyPSA
   *  transformer id. Null where the model records none, and the page says so
   *  in those words rather than falling back to the internal index. */
  sourceId: string | null;
  values: ComponentValue[];
  derivation: {
    basis: string | null;
    method: string | null;
    lineType: string | null;
    inputs: [string, number][];
    approximations: string[];
  } | null;
}

/** Component types whose identity lives on another component.
 *
 * Sienna splits a transformer into the named `TwoWindingTransformer` and the
 * `TransformerCircuit` that carries its impedance, and the record's field
 * declarations address the circuit. A table of 98 unnamed circuits would be a
 * table nobody can join to anything, so the wrapper's name is carried across. */
const NAMED_BY: Record<string, { wrapper: string; ref: string }> = {
  TransformerCircuit: { wrapper: "TwoWindingTransformer", ref: "circuit" },
};

function formatValue(value: unknown): { text: string | null; structured: boolean } {
  if (value === null || value === undefined) return { text: null, structured: false };
  if (typeof value === "number") {
    // Significant digits, not decimal places. A per-unit reactance of 0.017794
    // through `formatNumber` is "0.018", and through this it is "0.01779" —
    // and a reader comparing two circuits by reactance is comparing exactly the
    // digits the first one throws away.
    return { text: formatSignificant(value), structured: false };
  }
  if (typeof value === "boolean") return { text: String(value), structured: false };
  if (typeof value === "string") return { text: value, structured: false };
  return { text: null, structured: true };
}

/**
 * One component table: every component of a type, with the declared values it
 * carries and the provenance recorded for it.
 *
 * Whole, and paged by the caller. Returning a page from here would put the
 * paging inside the reader, which is where a "showing 20 of 455" that is
 * actually 20 of whatever survived a filter comes from.
 */
export function componentRows(
  system: SystemDocument | null,
  kind: string,
  columns: readonly ValueColumn[],
  parameters: ModelParameters | null,
): ComponentRow[] {
  if (!system) return [];
  const rows = (system.components[kind] ?? []) as Component[];

  const named = NAMED_BY[kind];
  const names = new Map<number, string>();
  if (named) {
    for (const wrapper of (system.components[named.wrapper] ?? []) as Component[]) {
      const ref = wrapper[named.ref];
      if (typeof ref === "number" && typeof wrapper.name === "string") names.set(ref, wrapper.name);
    }
  }

  // Both provenance tables in one index. They key on the component name, which
  // is the same identity the map shows and the same one a reader pastes.
  const provenance = new Map<string, BranchParameters>();
  for (const row of [...(parameters?.lines ?? []), ...(parameters?.transformers ?? [])]) {
    if (row.component) provenance.set(row.component, row);
  }

  return rows.map((row) => {
    const id = typeof row.id === "number" ? row.id : -1;
    const name = (named ? names.get(id) : (row.name as string | undefined)) ?? "";
    const recorded = provenance.get(name);
    return {
      key: `${kind}-${id}`,
      kind,
      name,
      sourceId: recorded?.source_id ?? null,
      values: columns.map((column) => {
        const raw = row[column.path];
        const { text, structured } = formatValue(raw);
        return { column, text, raw, structured };
      }),
      derivation: recorded?.method
        ? {
            basis: recorded.basis ?? null,
            method: recorded.method,
            lineType: recorded.line_type ?? null,
            inputs: Object.entries(recorded.inputs ?? {}),
            approximations: recorded.approximations ?? [],
          }
        : null,
    };
  });
}

/** Rows whose name or upstream id contains `query`, case-insensitively.
 *
 * Both identities, because a reader arriving from the map has the Sienna name
 * and a reader arriving from OpenStreetMap has the way id, and a search that
 * answered only one of them would be a search that works for whichever visitor
 * the author happened to be. */
export function filterRows(rows: readonly ComponentRow[], query: string): ComponentRow[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...rows];
  return rows.filter(
    (row) =>
      row.name.toLowerCase().includes(needle) ||
      (row.sourceId ?? "").toLowerCase().includes(needle),
  );
}
