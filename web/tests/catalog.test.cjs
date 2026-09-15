const { test } = require("node:test");
const assert = require("node:assert/strict");
const load = require("./load-ts.cjs");
const { filterCatalog, matchGaps, selectedFilters, unsupportedFilters, isCatalog, STATIC_FILTERS } = load("catalog-search.ts");
const { catalogReturn, pageOffset } = load("navigation.ts");
const rows = [
  { id: "model", title: "Network", record_type: "reference_model", fidelity_class: "indicative", field_count_bucket: "1-9", concepts: [{ iri: "voltage" }], data_domains: [{ iri: "DD1" }], completeness_level: 2, quality: [] },
  { id: "weather", title: "Weather", record_type: "dataset", field_count_bucket: "50+", concepts: [{ iri: "wind" }], data_domains: [{ iri: "DD5" }], completeness_level: 3, quality: [] },
];

test("model, fidelity, field-count and concept filters exclude nonmatches", () => {
  for (const selected of [
    { record_type: ["reference_model"] }, { fidelity_class: ["indicative"] },
    { field_count_bucket: ["1-9"] }, { concept: ["voltage"] },
  ]) assert.equal(filterCatalog(rows, "", selected).map((r) => r.id).join(), "model");
  assert.equal(filterCatalog(rows, "", { concept: ["absent"] }).length, 0);
  assert.equal(filterCatalog(rows, "", { concept: ["voltage", "wind"] }).length, 2);
  assert.equal(filterCatalog(rows, "", { concept: ["voltage", "wind"], data_domain: ["DD5"] })[0].id, "weather");
});

test("every advertised snapshot facet has a client matcher", () => {
  const fs = require("node:fs");
  const source = fs.readFileSync(require("node:path").join(__dirname, "../../services/snapshot.py"), "utf8");
  const facets = source.match(/FACETS = \(([\s\S]*?)\)/)[1].match(/"[a-z_]+"/g).map((v) => JSON.parse(v));
  for (const field of facets) assert.ok(STATIC_FILTERS.includes(field), field);
});

test("sort and paging are controls; unknown filters do not silently match", () => {
  const selected = selectedFilters(new URLSearchParams("q=wind&sort=title&offset=20&unsupported=value"));
  assert.equal(Object.keys(selected).join(), "unsupported");
  assert.equal(unsupportedFilters(selected).join(), "unsupported");
  assert.equal(filterCatalog(rows, "", selected).length, 0);
});

test("empty success is valid, but malformed or incomplete catalog data is not", () => {
  assert.ok(isCatalog({ total: 0, results: [] }));
  assert.ok(isCatalog({ total: 2, results: rows }));
  for (const body of [null, {}, { total: 2, results: [] }, { total: 1, results: [null] }, { total: 1, results: [{ id: "bad" }] }]) assert.equal(isCatalog(body), false);
});

test("return links preserve catalog state and reject other destinations", () => {
  assert.equal(catalogReturn("/datasets/?q=wind&sort=title&offset=20"), "/datasets?q=wind&sort=title&offset=20");
  for (const value of ["https://example.org/datasets", "//example.org/datasets", "/signin", "/datasets/../account", "/\\evil.org/datasets", null]) assert.equal(catalogReturn(value), "/datasets");
  for (const value of ["-1", "1.5", "Infinity", "bad"]) assert.equal(pageOffset(value), 0);
});

const GAPS = [
  { id: "g1", title: "Max upward ramp", category: "Generator fleet",
    reason: "Not published on a per-plant or per-unit basis." },
  { id: "g2", title: "Line flow limits", category: "Network topology",
    reason: "Ratings are commercially sensitive." },
];

test("the gap register answers the same query on both builds", () => {
  /* The live catalog called `searchGaps` on an empty result and the published
     site did not, so the strongest thing the Hub has to say — that nothing open
     supplies this, with who found that and when — was missing from exactly the
     deployment most people read. One rule now, and this is it. */
  assert.deepEqual(matchGaps(GAPS, "max upward ramp").map((g) => g.id), ["g1"]);
  // Every token has to appear, across title, category and reason together.
  assert.deepEqual(matchGaps(GAPS, "ramp generator").map((g) => g.id), ["g1"]);
  assert.equal(matchGaps(GAPS, "ramp nuclear").length, 0);
  // A blank query is not a match-everything: the notice belongs to a reader who
  // asked for something and got nothing, not to an empty catalog page.
  assert.equal(matchGaps(GAPS, "   ").length, 0);
  // And a register that could not be read is an absence, not an empty register.
  assert.equal(matchGaps(null, "ramp").length, 0);
  assert.equal(matchGaps(GAPS, "limits", 0).length, 0);
});
