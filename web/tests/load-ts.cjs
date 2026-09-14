const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

/** Execute the shipped TypeScript, with explicit boundaries only where a
 * server request needs a cookie or a fetch. No second implementation.
 *
 * `runInThisContext` rather than `runInNewContext`, and the difference is not
 * cosmetic. A new context is a new realm with its own intrinsics, so an array
 * built inside it is not an `Array` out here: `assert.deepEqual` reports
 * "values have same structure but are not reference-equal" on two identical
 * lists of strings, and the only way past it is to compare `join()`ed strings
 * and lose the assertion's diff. Running in this realm makes the module's
 * values ordinary values.
 *
 * Isolation is still real: the module gets a fresh `exports`, and anything it
 * reaches for — `require`, `fetch`, `process` — is a parameter. What it does
 * not get is a private copy of `Array`, which nothing was relying on. */
module.exports = function load(file, globals = {}) {
  const source = fs.readFileSync(path.join(__dirname, "..", "src", "lib", file), "utf8");
  const compiled = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;

  // A module under `src/lib` may import a sibling, and a test that stubs
  // `require` is stubbing the *boundary* — `next/headers`, `fetch` — not the
  // library's own internals. So a relative specifier is resolved here, by
  // loading that TypeScript the same way, and everything else falls through to
  // whatever the caller provided. Without this, `api.ts` importing the
  // comparator out of `catalog-search.ts` made every stubbed-require test fail
  // on an import that has nothing to do with what it is testing.
  const outer = globals.require ?? require;
  const resolve = (name) =>
    name.startsWith(".") ? load(`${path.basename(name)}.ts`) : outer(name);

  const scope = { process: { env: {} }, ...globals, require: resolve };
  const names = ["exports", "module", ...Object.keys(scope)];
  const values = [{}, { exports: {} }, ...Object.values(scope)];
  values[1].exports = values[0];

  vm.runInThisContext(`(function (${names.join(", ")}) {\n${compiled}\n})`, { filename: file })(
    ...values,
  );
  return values[1].exports;
};
