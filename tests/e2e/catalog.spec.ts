import { expect, test } from "@playwright/test";

/**
 * The M9 done-criterion, as two tests:
 *
 * > A modeler goes from landing page to a correct access plan for a DD5 dataset
 * > in under 60 seconds, and an unauthenticated evaluator can read all three
 * > quality grades.
 *
 * Everything else in this file guards a rule that is invisible until it breaks:
 * that a restricted record is indistinguishable from an absent one, that a
 * correlated pair is flagged rather than hidden, and that an empty state
 * explains itself.
 */

test("a modeller reaches a DD5 access path from the landing page", async ({ page }) => {
  const started = Date.now();

  await page.goto("/");
  await page.getByLabel("Search the catalog").fill("wind");
  // The search is debounced and pushed into the URL. Waiting for the URL is
  // what makes this deterministic: asserting on the results before the
  // navigation lands passes or fails depending on machine load, and a flaky
  // E2E test is one people learn to re-run rather than read.
  await page.waitForURL(/[?&]q=wind/);

  const result = page.getByRole("link", { name: /Global Wind Atlas/i }).first();
  await expect(result).toBeVisible();
  await result.click();

  await expect(page.getByRole("heading", { name: /Global Wind Atlas/i })).toBeVisible();
  await page.getByRole("tab", { name: "Downloads" }).click();

  // "A correct access plan" means a path the modeller can act on: a format and
  // a link to the source. The Hub never serves the bytes, so what the page owes
  // them is where to get them.
  await expect(page.getByRole("link", { name: /Open at source/i }).first()).toBeVisible();

  expect(Date.now() - started).toBeLessThan(60_000);
});

test("an unauthenticated evaluator reads all three quality grades", async ({ page }) => {
  await page.goto("/datasets/ecmwf-era5");
  await page.getByRole("tab", { name: "Data quality" }).click();

  const panel = page.getByRole("tabpanel", { name: "Data quality" });
  for (const facet of ["Provenance", "Documentation", "Currency & maintenance"]) {
    await expect(panel.getByText(facet, { exact: true })).toBeVisible();
  }

  // And no composite anywhere on the page (ADR-0007).
  await expect(page.getByText(/overall score|composite|total quality/i)).toHaveCount(0);
});

test("a correlated pair is flagged and still shown", async ({ page }) => {
  await page.goto("/datasets/global-wind-atlas");
  await page.getByRole("tab", { name: "Connections" }).click();

  const flag = page.getByRole("button", { name: /Not independent/i }).first();
  await expect(flag).toBeVisible();

  await flag.click();
  await expect(page.getByText(/agreeing with itself/i)).toBeVisible();
  await expect(page.getByText(/ERA5/).first()).toBeVisible();
});

test("a restricted record answers exactly as an absent one does", async ({ page }) => {
  const restricted = await page.goto("/datasets/utility-load-shapes-allowlisted");
  const restrictedBody = await page.locator("body").innerText();

  const absent = await page.goto("/datasets/there-is-no-such-dataset");
  const absentBody = await page.locator("body").innerText();

  expect(restricted?.status()).toBe(404);
  expect(absent?.status()).toBe(404);
  expect(restrictedBody).toBe(absentBody);
});

test("an empty search explains itself", async ({ page }) => {
  await page.goto("/?q=zzzznothingmatchesthis");

  await expect(page.getByText(/No datasets match this search/i)).toBeVisible();
  await expect(page.getByRole("link", { name: /Clear all filters/i })).toBeVisible();
});

test("a level 1 record says why its schema tab is empty", async ({ page }) => {
  await page.goto("/datasets/eia-natural-gas-prices");
  await page.getByRole("tab", { name: "Schema" }).click();

  await expect(page.getByText(/completeness level/i).first()).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
});

test("a field the catalog could not map says why", async ({ page }) => {
  await page.goto("/datasets/global-transmission-database");
  await page.getByRole("tab", { name: "Schema" }).click();

  const gap = page.getByTitle(/No concept in the .* scheme covers|pollute a shared vocabulary/i);
  await expect(gap.first()).toBeVisible();
});

test("the connections graph is capped with a way to see more", async ({ page }) => {
  await page.goto("/datasets/pypsa-eur-grid");
  await page.getByRole("tab", { name: "Connections" }).click();

  // By test id, not by role: each connection row contains its own list of
  // reasons, so counting every listitem in the panel counts the reasons too
  // and reports 33 where there are 10.
  const rows = page.getByTestId("connection");
  expect(await rows.count()).toBeLessThanOrEqual(12);
  await expect(page.getByTestId("connection-list")).toBeVisible();
});
