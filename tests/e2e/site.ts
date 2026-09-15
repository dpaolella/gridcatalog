import { execFileSync } from "node:child_process";

/**
 * Where the suite is pointed, and what that deployment can do.
 *
 * One copy, because there were two and a half. `hub.spec.ts` grew a `path()`
 * helper when the static pass was added and the other two files never did, so
 * every test in `catalog.spec.ts` and `signin.spec.ts` navigated to an
 * unprefixed URL — eighteen 404s against a site published under a base path,
 * which read as eighteen broken features rather than as one missing helper.
 * That is why the static pass sat red and unwired: it was too noisy to act on.
 *
 * The same reasoning applies to `isStatic`. Whether a test can run at all is a
 * property of the deployment, not of the file it happens to be written in.
 */

/** A site built by `next build` with `output: export`, served as files.
 *
 * It has no API behind it: no session, no write endpoints, no per-request
 * rendering. A test that needs one of those is not failing on this deployment,
 * it is inapplicable to it, and the difference has to be visible in the report.
 */
export const isStatic = process.env.E2E_MODE === "static";

/** The base path the site is published under — `/gridcatalog` on Pages, empty
 *  on a local server. Next prepends it to every link it renders, so a test that
 *  navigates by hand has to prepend it too. */
export const prefix = process.env.E2E_BASE_PATH ?? "";

/** An application route as a URL this deployment will actually serve. */
export const path = (route: string) => `${prefix}${route}`;

/** Read the seeded store directly, for facts the browser cannot be asked for —
 *  what a write actually persisted, what session a fixture user has. */
export const support = (...args: string[]) =>
  JSON.parse(
    execFileSync(process.env.E2E_PYTHON ?? ".venv/bin/python", ["tests/e2e/support.py", ...args], {
      encoding: "utf8",
    }),
  );
