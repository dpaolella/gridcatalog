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
