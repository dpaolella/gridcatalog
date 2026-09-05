import { defineConfig, devices } from "@playwright/test";

/**
 * The E2E suite (PRD §11), over the M9 done-criterion flows.
 *
 * It drives a **real** stack: the FastAPI app on one port, the built Next app
 * on another, and the fixture corpus behind both. Mocking the API here would
 * test the components against a fiction of the API, which is precisely the
 * class of bug this suite exists to catch — and did: the distribution's
 * `link_health` is an object where the UI's type said string, and no unit test
 * on either side could have noticed.
 *
 * Run it with `make e2e`, which seeds a store, starts both servers and tears
 * them down. The config and the dependency live at the repo root rather than
 * under `web/`, because this suite belongs to neither half — and because two
 * installs of Playwright resolve to two copies of the same module, which fails
 * with "No tests found" and no hint as to why.
 */
export default defineConfig({
  testDir: "tests/e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  // Bounded: the suite drives one API process and one Next process, and eight
  // workers against them turns a real timing bug and mere contention into the
  // same red.
  workers: process.env.CI ? 2 : 4,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:3210",
    trace: "retain-on-failure",
    // Point at a browser that is already on the machine when one is, rather
    // than downloading a second copy. Playwright pins a browser revision per
    // release, so an image that ships revision N and a package that wants N+1
    // fails at launch with an instruction to download — which is the wrong
    // answer in a sandbox with no egress and a perfectly good Chromium in it.
    launchOptions: process.env.E2E_CHROMIUM
      ? { executablePath: process.env.E2E_CHROMIUM }
      : undefined,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
