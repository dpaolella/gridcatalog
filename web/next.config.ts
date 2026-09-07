import createNextIntlPlugin from "next-intl/plugin";
import type { NextConfig } from "next";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

/**
 * Two builds from one source.
 *
 * **Server build** (default) — pages render per request against a live API.
 * **Static build** (`DATAHUB_SNAPSHOT` set) — every page is pre-rendered from a
 * snapshot on disk and emitted as files, for a host with no process. GitHub
 * Pages is that host.
 *
 * `basePath` matters only for the second: a project page lives at
 * `<user>.github.io/<repo>/`, and every absolute URL the app emits has to carry
 * that prefix. Next rewrites `<Link>`, the router and imported assets. A raw
 * `fetch` to a path of our own is the case it cannot rewrite, so those read
 * `NEXT_PUBLIC_BASE_PATH` themselves — see `AccountMenu`.
 */
const isStatic = Boolean(process.env.DATAHUB_SNAPSHOT);
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

const nextConfig: NextConfig = {
  ...(isStatic
    ? {
        output: "export" as const,
        // GitHub Pages serves `/thing/index.html` for `/thing/`; without this
        // every internal link 404s on a refresh.
        trailingSlash: true,
        // No image optimiser without a server. The brand art is SVG, so this
        // costs nothing.
        images: { unoptimized: true },
      }
    : {}),
  ...(basePath ? { basePath, assetPrefix: basePath } : {}),

  /**
   * Which files in `app/` are routes.
   *
   * The three route handlers under `app/api/` — `reports`, `submissions` and
   * `session` — are named `route.dynamic.ts` and so only become routes when
   * `dynamic.ts` is in this list. `output: "export"` cannot emit a POST handler
   * — it emits files — and a build that fails on one is right to: there is no
   * server to receive the post. Dropping the extension is how the static build
   * says "these are not part of this deployment" without a second copy of the
   * tree or a delete step in CI.
   *
   * Two serve forms, and those are not silently broken in the static build;
   * they are replaced by `StaticNotice`, which says what a static copy cannot
   * do. `session` serves the header's account control, which renders nothing
   * when the route is absent — there is nobody to be signed in as on a file
   * host, and a "Sign in" link to a flow that cannot exist is worse than none.
   */
  pageExtensions: isStatic
    ? ["tsx", "ts", "jsx", "js"]
    : ["dynamic.ts", "tsx", "ts", "jsx", "js"],

  // Deliberately no `env` block. Next inlines `env` values into the bundle at
  // *build* time, so `DATAHUB_API_URL` would be frozen to whatever the image
  // was built with — and the symptom is a production deployment quietly
  // calling localhost. The API URL is read from the process environment at
  // request time instead, in `src/lib/api.ts`.
  poweredByHeader: false,
};

export default withNextIntl(nextConfig);
