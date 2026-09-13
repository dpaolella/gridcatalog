import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";

const isStatic = process.env.E2E_MODE === "static";
const prefix = process.env.E2E_BASE_PATH ?? "";
const path = (route: string) => `${prefix}${route}`;
const hidden = "utility-load-shapes-allowlisted";
const support = (...args: string[]) => JSON.parse(execFileSync(
  process.env.E2E_PYTHON ?? ".venv/bin/python",
  ["tests/e2e/support.py", ...args], { encoding: "utf8" },
));

test("reference-model detail retains fields, downloads and suitability", async ({ page }) => {
  await page.goto(path("/datasets/gb-osm-reference"));
  await expect(page.getByRole("region", { name: "Model suitability" })).toContainText("Indicative");
  await expect(page.getByRole("region", { name: "Model suitability" })).toContainText("Fragile");
  await expect(page.getByRole("region", { name: "Model suitability" })).toContainText("Share-alike");
  await page.getByRole("tab", { name: "Schema", exact: true }).click();
  await expect(page.locator("#panel-schema tbody tr")).toHaveCount(6);
  await page.getByRole("tab", { name: "Downloads", exact: true }).click();
  await expect(page.locator("#panel-downloads").getByRole("link", { name: /Download file/ })).toHaveCount(2);
  await page.getByRole("link", { name: "View network map" }).click();
  await expect(page).toHaveURL(/reference-models.*#model-gb-osm-reference/);
  await expect(page.locator("#model-gb-osm-reference")).toBeVisible();
});

test("filters narrow results, including concept links and combined facets", async ({ page }) => {
  await page.goto(path("/datasets?record_type=reference_model"));
  await expect(page.getByRole("link", { name: "OpenGrid Reference: Great Britain (OSM)", exact: true })).toBeVisible();
  await expect(page.locator("main li.og-card")).toHaveCount(2);
  // Both published models are built the same way and so declare the same
  // fidelity, which means "indicative returns one of two" is no longer
  // available as proof the filter runs. Narrowing is shown the other way
  // round: a class nothing carries must return nothing, where a filter that
  // is never evaluated returns everything.
  await page.goto(path("/datasets?record_type=reference_model&fidelity_class=indicative"));
  await expect(page.locator("main li.og-card")).toHaveCount(2);
  await page.goto(path("/datasets?record_type=reference_model&fidelity_class=authoritative"));
  await expect(page.getByText("No datasets match this search.", { exact: true })).toBeVisible();
  await page.goto(path("/datasets?concept=nonexistent-review-concept"));
  await expect(page.getByText("No datasets match this search.", { exact: true })).toBeVisible();
  await page.goto(path("/datasets/ecmwf-era5#schema"));
  const concept = page.locator("#panel-schema a[href*='concept=']").first();
  await expect(concept).toBeVisible();
  await concept.click();
  await expect(page).toHaveURL(/concept=/);
  await expect(page.getByRole("link", { name: /ERA5/i }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "OpenGrid Reference: Germany (OSM)", exact: true })).toHaveCount(0);
});

test("evidence links survive refresh and history and return to the filtered search", async ({ page }) => {
  await page.goto(path("/datasets?record_type=reference_model&sort=title"));
  await page.getByRole("link", { name: "OpenGrid Reference: Great Britain (OSM)", exact: true }).click();
  await page.getByRole("tab", { name: "Schema", exact: true }).click();
  await expect(page).toHaveURL(/#schema$/);
  const link = page.getByRole("link", { name: "Link to Schema", exact: true });
  const href = await link.evaluate((a: HTMLAnchorElement) => a.href);
  await page.reload();
  await expect(page.getByRole("tab", { name: "Schema", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("tab", { name: "Downloads", exact: true }).click();
  await page.goBack();
  await expect(page.getByRole("tab", { name: "Schema", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.goto(href);
  await page.getByRole("link", { name: /Back to the data catalog/ }).click();
  await expect(page).toHaveURL(/datasets\/?\?record_type=reference_model&sort=title$/);
  await expect(page.locator("main li.og-card")).toHaveCount(2);
});

test("map controls work after delayed data, with wheel, keyboard and reset", async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/reference-models/gb-osm/system.view.json", async (route) => {
    await gate;
    await route.continue();
  });
  await page.goto(path("/reference-models"));
  const model = page.locator("#model-gb-osm-reference");
  await expect(model.getByText("Loading the network…", { exact: true })).toBeVisible();
  release();
  const svg = model.locator("figure svg");
  await expect(svg).toBeVisible();
  await svg.hover();
  await page.mouse.wheel(0, -100);
  await expect(model.getByText("115% zoom", { exact: true })).toBeVisible();
  await svg.focus();
  await page.keyboard.press("ArrowRight");
  const transformed = await svg.locator(":scope > g").getAttribute("transform");
  await page.keyboard.press("+");
  await expect(model.getByText("150% zoom", { exact: true })).toBeVisible();
  await model.getByRole("button", { name: "Reset view" }).click();
  await expect(model.getByText("100% zoom", { exact: true })).toBeVisible();
  expect(await svg.locator(":scope > g").getAttribute("transform")).not.toEqual(transformed);
  await model.getByRole("button", { name: "Zoom in", exact: true }).tap({ force: true }).catch(async () => {
    await model.getByRole("button", { name: "Zoom in", exact: true }).click();
  });
  await expect(model.getByText("130% zoom", { exact: true })).toBeVisible();
});

test.describe("live writes and identity", () => {
  test.skip(isStatic, "A static export has no session or write endpoints");

  for (const kind of ["incorrect-metadata", "broken-link", "license-question", "duplicate-record", "other"]) {
    test(`report ${kind} reaches the store with its exact target`, async ({ page }) => {
      await page.goto(path("/datasets/ecmwf-era5#schema"));
      await page.locator("#panel-schema").getByRole("button", { name: "Report", exact: true }).first().click();
      const form = page.locator("#panel-schema form");
      await form.locator("select").selectOption(kind);
      await form.locator("textarea").fill("E2E report verification");
      if (kind === "incorrect-metadata") await form.locator("input[type=email]").fill("reporter@example.org");
      const responsePromise = page.waitForResponse((r) => r.url().endsWith("/api/reports") && r.request().method() === "POST");
      await form.getByRole("button", { name: "Send report" }).click();
      const response = await responsePromise;
      expect(response.ok()).toBeTruthy();
      const payload = response.request().postDataJSON();
      const receipt = await response.json();
      const persisted = support("report", receipt.id);
      expect(persisted).toEqual({
        dataset_id: "ecmwf-era5", target_kind: "field", target_id: payload.target_id,
        issue_type: kind, reporter_contact: kind === "incorrect-metadata" ? "reporter@example.org" : null,
        comment: "E2E report verification",
      });
    });
  }

  test("an entitled browser sees restricted data and loses it after logout", async ({ page, context, baseURL }) => {
    const fixture = support("session");
    await context.addCookies([{ name: "og_session", value: fixture.session, url: baseURL!, httpOnly: true }]);
    await page.goto(path("/datasets?q=Utility"));
    await expect(page.locator(`a[href*='/datasets/${hidden}']`).first()).toBeVisible();
    await page.goto(path(`/datasets/${hidden}#downloads`));
    await expect(page.locator("#panel-downloads").getByRole("link", { name: /Open at source/ }).first()).toBeVisible();
    await page.getByRole("button", { name: "Sign out", exact: true }).click();
    await expect(page.getByRole("link", { name: "Sign in", exact: true })).toBeVisible();
    const response = await page.goto(path(`/datasets/${hidden}`));
    expect(response?.status()).toBe(404);
    await page.goto(path("/datasets?q=Utility"));
    await expect(page.locator(`a[href*='/datasets/${hidden}']`)).toHaveCount(0);
  });
});

test.describe("static catalog loading", () => {
  test.skip(!isStatic, "The live catalog is rendered by the API");

  test("published schema and downloads have no report forms", async ({ page }) => {
    await page.goto(path("/datasets/ecmwf-era5#schema"));
    await expect(page.getByRole("button", { name: "Report", exact: true })).toHaveCount(0);
    await page.getByRole("tab", { name: "Downloads", exact: true }).click();
    await expect(page.getByRole("button", { name: "Report", exact: true })).toHaveCount(0);
  });

  for (const query of ["", "?q=BeyondFirstPage"]) {
    test(`failed catalog is a preview and can retry without losing ${query || "browsing"}`, async ({ page }) => {
      let attempts = 0;
      await page.route("**/catalog.json", async (route) => {
        if (++attempts === 1) return route.abort();
        const response = await route.fetch();
        const body = await response.json();
        body.results.push({ ...body.results[0], id: "beyond-first", title: "BeyondFirstPage" });
        body.total = body.results.length;
        await route.fulfill({ json: body });
      });
      await page.goto(path(`/datasets${query}`));
      await expect(page.getByRole("status")).toContainText("The full catalog could not be loaded");
      await expect(page.getByText("No datasets match this search.", { exact: true })).toHaveCount(0);
      await page.getByRole("button", { name: "Retry loading catalog" }).click();
      await expect(page.getByRole("status")).toHaveCount(0);
      if (query) await expect(page.getByRole("link", { name: "BeyondFirstPage", exact: true })).toBeVisible();
      await expect(page).toHaveURL(query ? /q=BeyondFirstPage$/ : /datasets\/?$/);
    });
  }

  test("empty catalog is successful; malformed data exposes retry", async ({ page }) => {
    await page.route("**/catalog.json", (route) => route.fulfill({ json: { total: 0, results: [] } }));
    await page.goto(path("/datasets"));
    await expect(page.getByText("No datasets match this search.", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry loading catalog" })).toHaveCount(0);
    await page.unroute("**/catalog.json");
    await page.route("**/catalog.json", (route) => route.fulfill({ json: { total: 1, results: [null] } }));
    await page.reload();
    await expect(page.getByRole("button", { name: "Retry loading catalog" })).toBeVisible();
  });
});
