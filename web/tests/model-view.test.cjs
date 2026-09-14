const { test } = require("node:test");
const assert = require("node:assert/strict");
const load = require("./load-ts.cjs");
const {
  columnsByKind,
  componentRows,
  filterRows,
  hitTargets,
  inNaturalUnits,
  nearestElement,
  readElements,
} = load("model-view.ts");

/** A two-bus system in both unit conventions, small enough to read. */
function system({ units = "NATURAL_UNITS", base = 100 } = {}) {
  return {
    name: "Test",
    components: {
      ACBus: [
        { id: 1, name: "B-1", base_voltage: 400 },
        { id: 2, name: "B-2", base_voltage: 275 },
      ],
      Arc: [{ id: 10, from_id: 1, to_id: 2 }],
      Line: [
        { id: 20, name: "LN-1", arc: 10, r: 0.017794207, x: 2.6163, rating: 35.75,
          power_units: units, base_power: base },
      ],
      TransformerCircuit: [{ id: 30, arc: 10, x: 27.73, power_units: units, base_power: base }],
      TwoWindingTransformer: [{ id: 31, name: "TR-1", circuit: 30 }],
      ThermalStandard: [
        { id: 40, name: "GEN-1", bus: 1, fuel: "NATURAL_GAS", status: "OFFLINE",
          rating: 7.25, active_power_limits: { min: 3.77, max: 7.25 },
          power_units: units, base_power: base, operation_cost: { vom_cost: 1 } },
      ],
      PowerLoad: [
        { id: 50, name: "LOAD-1", bus: 2, max_active_power: 0.624,
          power_units: units, base_power: base },
      ],
    },
    supplemental_attributes: [
      { id: 60, geo_json: { type: "Point", coordinates: [0, 0] } },
      { id: 61, geo_json: { type: "Point", coordinates: [10, 0] } },
      { id: 62, geo_json: { type: "LineString", coordinates: [[0, 0], [10, 0]] } },
    ],
    supplemental_attribute_associations: [
      { component_id: 1, attribute_id: 60, attribute_type: "GeographicInfo", component_type: "ACBus" },
      { component_id: 2, attribute_id: 61, attribute_type: "GeographicInfo", component_type: "ACBus" },
      { component_id: 20, attribute_id: 62, attribute_type: "GeographicInfo", component_type: "Line" },
    ],
  };
}

test("a per-unit quantity is converted and a natural one is left alone", () => {
  // Floating point, so the assertion is about the quantity and not about the
  // bits: 9.04 × 100 is 903.9999999999999 in IEEE 754, and the display layer
  // rounds. A test that pinned the exact double would be testing the format.
  assert.ok(Math.abs(inNaturalUnits(9.04, "COMPONENT_BASE", 100) - 904) < 1e-9);
  assert.equal(inNaturalUnits(3575, "NATURAL_UNITS", 100), 3575);
  // The base is what makes the conversion possible. Without it the number is
  // per unit of something unstated, and printing it beside "MVA" would assert a
  // unit nobody declared.
  assert.equal(inNaturalUnits(9.04, "COMPONENT_BASE", undefined), null);
  // And a convention this reader does not know is not guessed at. KPG's lines
  // are `rating: 9.04` and GB's are `rating: 3575.0`; a reader that printed
  // whichever number it found would say one network has 9 MVA circuits.
  assert.equal(inNaturalUnits(9.04, "SOMETHING_ELSE", 100), null);
  assert.equal(inNaturalUnits("nine", "NATURAL_UNITS", 100), null);
});

test("the same document in two unit conventions reads the same facts", () => {
  const fact = (elements, name, key) =>
    elements.find((e) => e.name === name).facts.find((f) => f.key === key)?.value;

  const natural = readElements(system({ units: "NATURAL_UNITS", base: 1 }));
  const perUnit = readElements(system({ units: "COMPONENT_BASE", base: 1 }));
  assert.equal(fact(natural, "LN-1", "rating"), "36 MVA");
  assert.equal(fact(perUnit, "LN-1", "rating"), "36 MVA");

  const hundred = readElements(system({ units: "COMPONENT_BASE", base: 100 }));
  assert.equal(fact(hundred, "LN-1", "rating"), "3,575 MVA");
  assert.equal(fact(hundred, "GEN-1", "capacity"), "725 MW");
  assert.equal(fact(hundred, "LOAD-1", "demand"), "62 MW");
});

test("an element is identified by its name and never by its internal index", () => {
  const elements = readElements(system());
  const line = elements.find((e) => e.kind === "line");
  assert.equal(line.name, "LN-1");
  // The integer id is kept for joining components to each other and is not an
  // identity: it is an index the generator assigned and it moves on a rebuild.
  assert.equal(line.id, 20);
  assert.equal(
    line.facts.find((f) => f.key === "between").value,
    "B-1 → B-2",
  );
  assert.equal(line.facts.find((f) => f.key === "voltage").value, "400 kV");
});

test("a commitment state travels with the unit that carries it", () => {
  const gen = readElements(system()).find((e) => e.kind === "thermal");
  assert.equal(gen.facts.find((f) => f.key === "status").value, "OFFLINE");
  assert.equal(gen.facts.find((f) => f.key === "fuel").value, "NATURAL_GAS");
});

test("pointing at a node names the node, and pointing along a corridor names the corridor", () => {
  const elements = readElements(system());
  const identity = (p) => p;
  const targets = hitTargets(elements, identity);

  // At a node, where the circuit also passes. Every circuit meeting a
  // substation runs through the substation's own coordinate, so the bus and the
  // line are both at distance zero and without a preference the winner is
  // floating-point noise — measured in a browser, that named a circuit four
  // times in five when a bus marker was clicked squarely.
  assert.equal(nearestElement(targets, 0, 0, 1)?.kind, "thermal");
  assert.equal(nearestElement(targets, 10, 0, 1)?.kind, "load");
  // Which of the coincident point elements wins is the draw order: the
  // generator and the load are drawn over the bus they sit on, and are what a
  // reader sees at that coordinate.
  const buses = hitTargets(elements.filter((e) => e.kind !== "thermal" && e.kind !== "load"), identity);
  assert.equal(nearestElement(buses, 0, 0, 1)?.name, "B-1");

  // Halfway along the corridor, far from either terminal.
  assert.equal(nearestElement(targets, 5, 0, 1)?.name, "LN-1");
  // And nothing is invented outside the radius.
  assert.equal(nearestElement(targets, 5, 40, 1), null);
});

test("an element with no geography is a component, not a drawable", () => {
  const document = system();
  document.supplemental_attribute_associations = [];
  const elements = readElements(document);
  assert.equal(elements.filter((e) => e.kind === "bus").length, 2, "buses are still components");
  assert.equal(hitTargets(elements, (p) => p).length, 0, "nothing is drawable");
});

const FIELDS = [
  { field_id: "components.Line[].x", local_name: "x", label: "Line reactance",
    value_basis: "modeled", unit_label: "Ω", definition: "Series reactance." },
  { field_id: "components.TransformerCircuit[].x", local_name: "x", label: "Transformer reactance",
    value_basis: "estimated", unit_label: "Ω", completeness_caveats: "A class value." },
  { field_id: "supplemental_attributes[].geo_json", local_name: "geo_json",
    value_basis: "measured" },
];

test("columns join on the field's address, not on its name", () => {
  /* The GB model declares `x` twice, on `components.Line[]` and on
     `components.TransformerCircuit[]`, and the two have *different* bases —
     modeled and estimated. Joined by local name, one of them is labelled with
     the other's basis and nothing says so, which is the exact failure a value
     basis exists to prevent. */
  const columns = columnsByKind(FIELDS);
  assert.equal(columns.get("Line")[0].basis, "modeled");
  assert.equal(columns.get("TransformerCircuit")[0].basis, "estimated");
  // A declaration that does not address a component array is a real statement
  // about real values and is not a column of any of these tables.
  assert.equal(columns.has("supplemental_attributes"), false);
});

test("a component table carries both identities and the rule behind an estimate", () => {
  const parameters = {
    lines: [{
      component: "LN-1", source_id: "100861731", basis: "estimated",
      method: "pypsa-standard-line-type", line_type: "Al/St 240/40",
      inputs: { voltage_kv: 400, circuits: 2 },
      approximations: ["400 kV has no standard type."],
    }],
  };
  const columns = columnsByKind(FIELDS);
  const [line] = componentRows(system(), "Line", columns.get("Line"), parameters);
  assert.equal(line.name, "LN-1");
  assert.equal(line.sourceId, "100861731");
  assert.equal(line.derivation.method, "pypsa-standard-line-type");
  assert.deepEqual(line.derivation.inputs, [["voltage_kv", 400], ["circuits", 2]]);
  assert.equal(line.derivation.approximations.length, 1);

  // A component the provenance file says nothing about says so, rather than
  // borrowing the rule from whichever row it sat next to.
  const [transformer] = componentRows(
    system(), "TransformerCircuit", columns.get("TransformerCircuit"), parameters);
  assert.equal(transformer.sourceId, null);
  assert.equal(transformer.derivation, null);
  // Sienna splits a transformer into a named wrapper and the circuit that
  // carries its impedance; the record's field addresses the circuit, so a table
  // built from it would otherwise be 98 unnamed rows nothing can join to.
  assert.equal(transformer.name, "TR-1");
});

test("a value the document does not carry is an absence, not a zero", () => {
  const columns = columnsByKind([
    { field_id: "components.ACBus[].base_voltage", local_name: "base_voltage" },
    { field_id: "components.ACBus[].magnitude", local_name: "magnitude" },
  ]);
  const [bus] = componentRows(system(), "ACBus", columns.get("ACBus"), null);
  assert.equal(bus.values[0].text, "400");
  assert.equal(bus.values[1].text, null, "a missing value has no text");
  assert.equal(bus.values[1].structured, false);
});

test("an impedance keeps the digits a comparison needs", () => {
  const columns = columnsByKind([{ field_id: "components.Line[].r", local_name: "r" }]);
  const [line] = componentRows(system(), "Line", columns.get("Line"), null);
  // 0.017794207 through a three-decimal formatter is "0.018", which is a
  // different circuit. Significant digits, not decimal places.
  assert.equal(line.values[0].text, "0.01779");
});

test("a structured value is offered whole rather than flattened into a cell", () => {
  const columns = columnsByKind([
    { field_id: "components.ThermalStandard[].operation_cost", local_name: "operation_cost" },
  ]);
  const [gen] = componentRows(system(), "ThermalStandard", columns.get("ThermalStandard"), null);
  assert.equal(gen.values[0].text, null);
  assert.equal(gen.values[0].structured, true);
  assert.deepEqual(gen.values[0].raw, { vom_cost: 1 });
});

test("the filter answers on either identity", () => {
  const rows = [
    { name: "LN-1", sourceId: "100861731" },
    { name: "LN-2", sourceId: null },
  ];
  // A reader arriving from the map has the component name; one arriving from
  // OpenStreetMap has the way id. A filter that answered only one of them would
  // work for whichever visitor the author happened to be.
  assert.equal(filterRows(rows, "LN-2").length, 1);
  assert.equal(filterRows(rows, "1008617").length, 1);
  assert.equal(filterRows(rows, "").length, 2);
  assert.equal(filterRows(rows, "nothing").length, 0);
});
