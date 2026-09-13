const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

/** Execute the shipped TypeScript, with explicit boundaries only where a
 * server request needs a cookie or a fetch. No second implementation. */
module.exports = function load(file, globals = {}) {
  const source = fs.readFileSync(path.join(__dirname, "..", "src", "lib", file), "utf8");
  const context = {
    exports: {}, require, URL, URLSearchParams, FormData, process: { env: {} }, ...globals,
  };
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, context);
  return context.exports;
};
