/**
 * Serve `out/` the way GitHub Pages serves it.
 *
 * Not a convenience — a correctness check. The usual static servers are wrong
 * in exactly the way that hides the bugs this build can have: `serve -s` falls
 * back to `index.html` for every miss, so a page that was never exported still
 * renders (the home page, wearing the wrong URL), and a broken link looks fine
 * until it is deployed. Pages does no such fallback: it serves the file, or
 * `<path>/index.html`, or `404.html` with a 404 status.
 *
 * `--prefix /repo` reproduces a project site, where everything lives under the
 * repository name. That is the setting that breaks absolute URLs, so it is the
 * one worth being able to test before pushing.
 *
 *   node scripts/serve-export.mjs [dir] [--port 4321] [--prefix /repo]
 */
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { join, normalize, extname } from "node:path";

const args = process.argv.slice(2);
const flags = new Map();
const positional = [];
for (let i = 0; i < args.length; i += 1) {
  if (args[i].startsWith("--")) {
    flags.set(args[i], args[i + 1]);
    i += 1;
  } else {
    positional.push(args[i]);
  }
}
const root = positional[0] ?? "out";
const port = Number(flags.get("--port") ?? 4321);
const prefix = (flags.get("--prefix") ?? "").replace(/\/$/, "");

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".txt": "text/plain; charset=utf-8",
  ".woff2": "font/woff2",
  ".ico": "image/x-icon",
  ".png": "image/png",
};

async function isFile(path) {
  try {
    return (await stat(path)).isFile();
  } catch {
    return false;
  }
}

/** Pages' resolution order, and nothing else. */
async function resolve(urlPath) {
  if (prefix) {
    if (urlPath !== prefix && !urlPath.startsWith(`${prefix}/`)) return null;
    urlPath = urlPath.slice(prefix.length) || "/";
  }
  const rel = normalize(decodeURIComponent(urlPath)).replace(/^(\.\.[/\\])+/, "").replace(/^\/+/, "");
  const base = join(root, rel);
  for (const candidate of [base, `${base}.html`, join(base, "index.html")]) {
    if (await isFile(candidate)) return candidate;
  }
  return null;
}

createServer(async (req, res) => {
  const path = new URL(req.url, "http://localhost").pathname;
  const file = await resolve(path);

  if (file === null) {
    const notFound = join(root, "404.html");
    const body = (await isFile(notFound)) ? await readFile(notFound) : Buffer.from("404");
    res.writeHead(404, { "Content-Type": "text/html; charset=utf-8" });
    res.end(body);
    return;
  }

  res.writeHead(200, { "Content-Type": TYPES[extname(file)] ?? "application/octet-stream" });
  res.end(await readFile(file));
}).listen(port, () => {
  console.log(`serving ${root} at http://localhost:${port}${prefix}/ (as GitHub Pages would)`);
});
