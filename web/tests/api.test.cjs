const { test } = require("node:test");
const assert = require("node:assert/strict");
const load = require("./load-ts.cjs");

test("catalog reads forward the session privately and retain anonymous data caching", async () => {
  let signedIn = true;
  const calls = [];
  const api = load("api.ts", {
    require: (name) => {
      assert.equal(name, "next/headers");
      return { cookies: async () => ({ get: () => signedIn ? { value: "fixture-session" } : undefined }) };
    },
    fetch: async (url, options) => {
      calls.push({ url, options });
      return { status: 200, ok: true, json: async () => ({}) };
    },
  });
  for (const authenticated of [true, false]) {
    signedIn = authenticated;
    calls.length = 0;
    await api.search({ q: "model" });
    for (const fn of ["getDataset", "getSchema", "getQuality", "getDistributions", "getLinks"]) await api[fn]("model");
    assert.equal(calls.length, 6);
    for (const { options } of calls) {
      if (authenticated) {
        assert.equal(options.headers.Cookie, "og_session=fixture-session");
        assert.equal(options.cache, "no-store");
        assert.equal(options.next, undefined);
      } else {
        assert.equal(options.headers.Cookie, undefined);
        assert.notEqual(options.cache, "no-store");
        assert.ok(options.next.revalidate > 0);
      }
    }
  }
});

test("the snapshot answers sort, limit and offset, and leaves the rest to the browser", async () => {
  /* Both builds have to answer one question the same way for the front page to
     be one page: "the most recently updated N". The live arm asks the API; this
     arm sorts the exported index and slices it. Everything else — `q`, the
     filters — is still `catalog-search`'s job in the browser, and asserting
     that here is what keeps this from growing into a second search. */
  const results = [
    { id: "old", title: "Old", modified: "2020-01-01T00:00:00Z", data_domains: [], quality: [], completeness_level: 1 },
    { id: "new", title: "New", modified: "2026-09-01T00:00:00Z", data_domains: [], quality: [], completeness_level: 3 },
    { id: "undated", title: "Undated", data_domains: [], quality: [], completeness_level: 2 },
  ];
  const files = {
    "index.json": { total: results.length, results },
    "facets.json": { record_type: [{ value: "dataset", count: 3 }] },
  };
  const api = load("api.ts", {
    process: { env: { DATAHUB_SNAPSHOT: "/snapshot" } },
    require: (name) => {
      if (name === "node:fs/promises") {
        return { readFile: async (file) => JSON.stringify(files[file.split("/").pop()]) };
      }
      if (name === "node:path") return { join: (...parts) => parts.join("/") };
      throw new Error(`unexpected import ${name}`);
    },
    fetch: async () => { throw new Error("a snapshot must not reach the network"); },
  });

  const recent = await api.search({ sort: "-modified", limit: "2" });
  assert.deepEqual(recent.results.map((r) => r.id), ["new", "old"]);
  // The catalog's size, not the page's: this is what "showing 1–2 of 3" counts,
  // and reporting the slice would say the catalog is as big as what fits.
  assert.equal(recent.total, 3);
  assert.equal(recent.limit, 2);

  const second = await api.search({ sort: "-modified", limit: "2", offset: "2" });
  assert.deepEqual(second.results.map((r) => r.id), ["undated"]);
  assert.equal(second.offset, 2);

  // No sort means no order invented: the server ranks by relevance and a file
  // cannot, so the export's own sequence is what comes back.
  const unranked = await api.search({});
  assert.deepEqual(unranked.results.map((r) => r.id), ["old", "new", "undated"]);
  assert.equal(unranked.facets.record_type[0].count, 3);

  // And a filter is not silently applied or silently dropped into a 200 that
  // looks filtered: the whole index comes back for the browser to filter.
  const filtered = await api.search({ q: "New", data_domain: "DD5" });
  assert.equal(filtered.results.length, 3);
});
