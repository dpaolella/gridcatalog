const { test } = require("node:test");
const assert = require("node:assert/strict");
const load = require("./load-ts.cjs");
const { threads, studyDate, tail } = load("studies.ts");

const iri = (id) => `https://catalog.opengrid.org/study/${id}`;

const filing = {
  id: "cascade-irp",
  title: "Cascade IRP",
  study_kind: "filing",
  docket: "UE-26-0142",
  jurisdiction: "Cascadia",
  frozen_at: "2026-04-02T00:00:00Z",
};
const intervention = {
  id: "coalition",
  title: "Coalition intervention",
  study_kind: "intervention",
  docket: "UE-26-0142",
  jurisdiction: "Cascadia",
  parent_study: iri("cascade-irp"),
  frozen_at: "2026-05-19T00:00:00Z",
};
const paper = {
  id: "some-paper",
  title: "A working paper",
  study_kind: "paper",
  frozen_at: "2026-01-05T00:00:00Z",
};

test("a filing and what contests it are one thread", () => {
  const [thread] = threads([intervention, filing]);
  assert.equal(thread.size, 2);
  assert.equal(thread.docket, "UE-26-0142");
  // The filing leads, whichever order the rows arrived in. Listed flat, the
  // intervention sorts first under "most recent" and the reader meets the
  // rebuttal before the thing rebutted.
  assert.equal(thread.lead.id, "cascade-irp");
  assert.deepEqual(thread.replies.map((s) => s.id), ["coalition"]);
});

test("a paper in no docket is its own thread, not lumped with the others", () => {
  const all = threads([filing, intervention, paper]);
  assert.equal(all.length, 2);
  const single = all.find((t) => t.key === "study:some-paper");
  assert.equal(single.size, 1);
  assert.equal(single.docket, null);
  assert.deepEqual(single.replies, []);
});

test("threads sort on their newest study, not on the filing", () => {
  /* A docket whose filing is old and whose latest intervention landed last
     week is the live one. Sorting on the lead buries it under dockets nobody
     has touched in a year. */
  const stale = { id: "stale", title: "Stale", docket: "UE-20-0001", frozen_at: "2026-02-01T00:00:00Z" };
  const old = { ...filing, frozen_at: "2025-01-01T00:00:00Z" };
  const fresh = { ...intervention, frozen_at: "2026-09-01T00:00:00Z" };

  assert.deepEqual(
    threads([stale, old, fresh]).map((t) => t.key),
    ["docket:UE-26-0142", "docket:UE-20-0001"],
  );
});

test("an intervention whose filing is not on this page still leads its thread", () => {
  /* Otherwise a docket paged away from its filing renders as a headless list of
     rebuttals with nothing to say what is being rebutted. */
  const [thread] = threads([intervention]);
  assert.equal(thread.lead.id, "coalition");
  assert.deepEqual(thread.replies, []);
});

test("two studies naming each other group rather than hang", () => {
  /* `parent_study` is a declaration in a record, and a malformed pair should
     cost an odd grouping, not a render that never returns. */
  const a = { id: "a", title: "A", parent_study: iri("b"), frozen_at: "2026-01-01T00:00:00Z" };
  const b = { id: "b", title: "B", parent_study: iri("a"), frozen_at: "2026-02-01T00:00:00Z" };
  const all = threads([a, b]);
  assert.ok(all.length >= 1);
  assert.equal(all.reduce((n, t) => n + t.size, 0), 2);
});

test("a fork of a paper follows the paper even with no docket between them", () => {
  const fork = {
    id: "fork",
    title: "Fork",
    parent_study: iri("some-paper"),
    frozen_at: "2026-03-01T00:00:00Z",
  };
  const [thread] = threads([paper, fork]);
  assert.equal(thread.size, 2);
  assert.equal(thread.lead.id, "some-paper");
});

test("a study's own frozen date wins over the date its record was touched", () => {
  // `frozen_at` is a statement about the study; `modified` is a statement about
  // the record of it, and a re-catalogued filing has not moved.
  assert.equal(studyDate({ id: "x", title: "X", frozen_at: "2026-04-02", modified: "2026-09-01" }), "2026-04-02");
  assert.equal(studyDate({ id: "x", title: "X", modified: "2026-09-01" }), "2026-09-01");
  assert.equal(studyDate({ id: "x", title: "X" }), "");
});

test("a parent is matched by slug, because the record carries an IRI", () => {
  assert.equal(tail(iri("cascade-irp")), "cascade-irp");
  assert.equal(tail("https://catalog.opengrid.org/study/cascade-irp/"), "cascade-irp");
  assert.equal(tail(null), null);
  assert.equal(tail(""), null);
});

const { splitPath, assumptionTree } = load("studies.ts");

test("a Sienna path splits into the component it addresses and the parameter", () => {
  assert.deepEqual(splitPath("Investments/Financials/TechnologyFinancialData#return_on_equity"), {
    segments: ["Investments", "Financials", "TechnologyFinancialData"],
    parameter: "return_on_equity",
  });
  // No `#`: the whole thing is the parameter. Nothing invented from a shape
  // the record did not use.
  assert.deepEqual(splitPath("discount_rate"), { segments: [], parameter: "discount_rate" });
  assert.deepEqual(splitPath(null), { segments: [], parameter: "" });
});

test("assumptions nest under the component path they name", () => {
  const rows = [
    { id: "roe", path: "Investments/Financials/TechnologyFinancialData#return_on_equity" },
    { id: "debt", path: "Investments/Financials/TechnologyFinancialData#debt_fraction" },
    { id: "cap", path: "Investments/Requirements/CarbonCaps#max_mtons" },
  ];
  const root = assumptionTree(rows);

  assert.deepEqual(root.children.map((c) => c.segment), ["Investments"]);
  const investments = root.children[0];
  assert.deepEqual(investments.children.map((c) => c.segment), ["Financials", "Requirements"]);
  assert.deepEqual(
    investments.children[0].children[0].leaves.map((r) => r.id),
    ["roe", "debt"],
  );
  assert.equal(investments.children[1].children[0].path, "Investments/Requirements/CarbonCaps");
});

test("a value with no path is kept, not dropped", () => {
  /* It is a number the study stands on. Hiding it because the catalog cannot
     file it would lose the one thing the page exists to show. */
  const root = assumptionTree([{ id: "loose" }, { id: "placed", path: "A#b" }]);
  assert.deepEqual(root.leaves.map((r) => r.id), ["loose"]);
  assert.deepEqual(root.children.map((c) => c.segment), ["A"]);
});
