const { test } = require("node:test");
const assert = require("node:assert/strict");
const load = require("./load-ts.cjs");
const { domainCoverage, unplacedGaps, splitCoverageKey, coverageKey } = load("coverage.ts");

const DD = (n) => `https://schema.opengrid.org/concept/data-domain/DD${n}`;
const facets = {
  data_domain: [
    { value: DD(5), count: 4, label: "Weather & Climate" },
    { value: DD(1), count: 2, label: "Network" },
  ],
  domain_coverage: [
    { value: `${DD(5)}|3`, count: 3 },
    { value: `${DD(5)}|1`, count: 1 },
    { value: `${DD(1)}|2`, count: 2 },
  ],
};
const gap = (id, domain) => ({
  id, domain, title: id, category: "", reason: "", observed_by: "Levi", observed: "2025",
});

test("a cell key round-trips, and a malformed one is dropped rather than guessed", () => {
  assert.equal(coverageKey(DD(5), 3), `${DD(5)}|3`);
  assert.deepEqual(splitCoverageKey(`${DD(5)}|3`), { iri: DD(5), level: 3 });
  for (const bad of ["", "|", "no-pipe", `${DD(5)}|`, `${DD(5)}|x`, null]) {
    assert.equal(splitCoverageKey(bad), null, String(bad));
  }
});

test("the crossing becomes one row per domain, in DD order, with labels", () => {
  const rows = domainCoverage(facets, []);
  assert.deepEqual(rows.map((r) => r.code), ["DD1", "DD5"]);
  assert.deepEqual(rows.map((r) => r.label), ["Network", "Weather & Climate"]);
  assert.deepEqual(rows[1].counts, { 1: 1, 3: 3 });
  assert.equal(rows[1].total, 4);
  assert.equal(rows[0].counts[1], undefined, "an empty cell is absent, not zero");
});

test("a domain with no label still gets a row, under its notation", () => {
  const rows = domainCoverage({ domain_coverage: [{ value: `${DD(9)}|1`, count: 1 }] }, []);
  assert.deepEqual(rows.map((r) => [r.code, r.label]), [["DD9", "DD9"]]);
});

test("register entries land on the domain they name", () => {
  const rows = domainCoverage(facets, [gap("a", "DD5"), gap("b", "DD5"), gap("c", "DD1")]);
  assert.deepEqual(rows.find((r) => r.code === "DD5").gaps.map((g) => g.id), ["a", "b"]);
  assert.deepEqual(rows.find((r) => r.code === "DD1").gaps.map((g) => g.id), ["c"]);
});

test("an entry in a domain the catalog holds nothing for is not lost", () => {
  // The register's strongest finding is about a domain with no records at all,
  // and it is exactly the one a table of what the catalog has cannot show.
  const gaps = [gap("a", "DD5"), gap("orphan", "DD8")];
  const rows = domainCoverage(facets, gaps);
  assert.deepEqual(unplacedGaps(rows, gaps).map((g) => g.id), ["orphan"]);
});

test("no register at all is not an empty register", () => {
  // `null` means the call failed. Rendering it as "no entry recorded" would be
  // a claim the register never made.
  const rows = domainCoverage(facets, null);
  assert.ok(rows.every((r) => r.gaps.length === 0));
  assert.deepEqual(unplacedGaps(rows, null), []);
});

test("no crossing means no table, rather than a table of nothing", () => {
  assert.deepEqual(domainCoverage({}, []), []);
});
