const { test } = require("node:test");
const assert = require("node:assert/strict");
const load = require("./load-ts.cjs");
const { SORT_OPTIONS, compareBySort, isSortable } = load("catalog-search.ts");

const rows = [
  { id: "old", title: "Beta", modified: "2020-01-01T00:00:00Z", completeness_level: 1, temporal: { start: "1990-01-01" } },
  { id: "new", title: "Alpha", modified: "2026-09-01T00:00:00Z", completeness_level: 3, temporal: { start: "2015-01-01" } },
  { id: "unknown", title: "Gamma", completeness_level: 2, temporal: null },
];

const order = (sort) => [...rows].sort(compareBySort(sort)).map((r) => r.id);

test("every sort the control offers is one the comparator reads", () => {
  /* The option list and the comparator used to sit next to each other in
     `SortSelect.tsx`, on the reasoning that proximity keeps them together. It
     stopped being possible when the server needed the comparator too — a
     "use client" module's exports reach a server component as client
     references — so the property is asserted instead of arranged.

     What it catches: an option nobody taught the comparator about is a control
     that silently does nothing, which on a catalog already in roughly that
     order is indistinguishable from working. */
  for (const option of SORT_OPTIONS) {
    if (option.value === "") continue; // Relevance is the absence of a sort.
    assert.ok(isSortable(option.value), `${option.value} orders nothing`);
  }
});

test("relevance and unknown fields are declined rather than faked", () => {
  for (const sort of ["", "downloads", "-popularity"]) assert.equal(isSortable(sort), false);
});

test("recency is descending and a record with no date is never the newest", () => {
  assert.deepEqual(order("-modified"), ["new", "old", "unknown"]);
  // Ascending too: "unknown" sorts last both ways, because it is not a value
  // at either end of the range — it is the absence of one.
  assert.deepEqual(order("modified"), ["old", "new", "unknown"]);
});

test("title, coverage start and completeness each order on their own field", () => {
  assert.deepEqual(order("title"), ["new", "old", "unknown"]);
  assert.deepEqual(order("-temporal_start"), ["new", "old", "unknown"]);
  assert.deepEqual(order("completeness_level"), ["old", "unknown", "new"]);
});
