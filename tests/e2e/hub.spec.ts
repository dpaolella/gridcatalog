import { expect, test } from "@playwright/test";
import { isStatic, path, support } from "./site";

const hidden = "utility-load-shapes-allowlisted";

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

test("the catalog crosses domain with completeness, and every cell counts its own list", async ({
  page,
}) => {
  await page.goto(path("/datasets"));
  const coverage = page.locator("section[aria-labelledby=coverage-heading]");
  await expect(coverage).toBeVisible();

  // The register is no longer a section of its own.
  await expect(page.locator("header nav").getByRole("link", { name: "Gaps" })).toHaveCount(0);

  // Every row's total is its three level cells, whatever the corpus holds.
  // Asserted as the invariant rather than as a number: a literal here is a
  // fact about a corpus this test never opened, and it went stale the first
  // time a record was added — which is the failure `533e59d` is about.
  for (const row of await coverage.locator("tbody tr").all()) {
    // The domain name is a `th scope="row"`, so the cells are the three
    // levels, then the total, then the register.
    const cells = row.getByRole("cell");
    const value = async (n: number) => Number((await cells.nth(n).innerText()).replace("—", "0"));
    const [l1, l2, l3, total] = await Promise.all([0, 1, 2, 3].map(value));
    expect(l1 + l2 + l3, await row.innerText()).toBe(total);
  }

  // A domain the catalog serves thinly, with entries saying so, is the whole
  // reason the two facts are crossed rather than listed apart.
  const thin = coverage.locator("tbody tr").filter({ hasText: "DD2" });
  const register = thin.locator("details summary");
  await expect(register).toHaveText(/entries/);
  await register.click();
  // Attribution is on the entry, not on a tooltip: a gap is a far stronger
  // claim than a record and a reader has to be able to weigh who made it.
  await expect(thin.locator("details li").first()).toContainText("Observed by");

  // A cell counts exactly the list it links to. The counts come from the same
  // aggregation as the results, so this cannot drift — which is what #87, #89
  // and the fourteen-facet commit were each an instance of it doing.
  const cell = coverage.locator("tbody tr").filter({ hasText: "DD5" }).locator("td a").last();
  const counted = Number(await cell.innerText());
  expect(counted).toBeGreaterThan(0);
  await cell.click();
  await expect(page).toHaveURL(/domain_coverage=/);
  await expect(page.locator("main li.og-card")).toHaveCount(counted);
});

test("the reference inventory is entered by geography, and a stale place widens it", async ({ page }) => {
  const germany = page.locator("#model-de-osm-reference");
  const britain = page.locator("#model-gb-osm-reference");

  await page.goto(path("/reference-models"));
  const picker = page.getByRole("group", { name: "Geography" });
  await expect(picker.getByRole("button", { name: "Germany", exact: true })).toBeVisible();
  await expect(picker.getByRole("button", { name: "Great Britain", exact: true })).toBeVisible();
  // Both models are in the HTML whatever is selected — the server renders
  // every geography and the picker hides the rest, so that a build with no
  // request to read the selection from still publishes the inventory. Hence
  // visibility rather than count: a count assertion would pass on a page that
  // shipped nothing and filtered nothing.
  await expect(germany).toBeVisible();
  await expect(britain).toBeVisible();

  await picker.getByRole("button", { name: "Germany", exact: true }).click();
  await expect(page).toHaveURL(/place=germany/);
  await expect(germany).toBeVisible();
  await expect(britain).toBeHidden();
  await expect(picker.getByRole("button", { name: "Germany", exact: true })).toHaveAttribute(
    "aria-pressed", "true");

  // A link somebody pasted lands where it says it does.
  await page.goto(path("/reference-models?place=greatBritain"));
  await expect(britain).toBeVisible();
  await expect(germany).toBeHidden();

  // And a stale one widens the view rather than rendering an empty shelf,
  // which would read as "there is no network for this place".
  await page.goto(path("/reference-models?place=atlantis"));
  // The picker's own notice, which is the unnamed one: each map also carries a
  // named live region for the component it has identified (#98).
  await expect(page.getByRole("status").filter({ hasText: "atlantis" })).toBeVisible();
  await expect(germany).toBeVisible();
  await expect(britain).toBeVisible();
});

test("filters narrow results, including concept links and combined facets", async ({ page }) => {
  const rows = page.locator("main li.og-card");

  await page.goto(path("/datasets?record_type=reference_model"));
  await expect(page.getByRole("link", { name: "OpenGrid Reference: Great Britain (OSM)", exact: true })).toBeVisible();
  const models = await rows.count();
  expect(models).toBeGreaterThan(1);

  // Narrowing asserted as a relation, not as two literals. The counts move
  // whenever the corpus does — the same two numbers have now gone stale twice
  // — and what this test is actually about is that adding a filter removes
  // records, which a filter that is never evaluated cannot do.
  await page.goto(path("/datasets?record_type=reference_model&fidelity_class=indicative"));
  const indicative = await rows.count();
  expect(indicative).toBeGreaterThan(0);
  expect(indicative).toBeLessThan(models);

  // And a class nothing carries returns nothing rather than everything.
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
  // Wait for a row before counting. The static build cannot filter until
  // `catalog.json` lands and shows no rows at all until it does, so counting
  // straight after navigation counted the moment before the answer existed —
  // which is the state #93 is about, and reading it as the answer is the
  // mistake a reader would make too.
  await expect(page.locator("main li.og-card").first()).toBeVisible();
  // Read what the filter returns rather than asserting how many models the
  // corpus happens to hold: the claim is that coming back lands on the same
  // search, not that the search has a particular size.
  const models = await page.locator("main li.og-card").count();
  expect(models).toBeGreaterThan(1);
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
  await expect(page.locator("main li.og-card").first()).toBeVisible();
  await expect(page.locator("main li.og-card")).toHaveCount(models);
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

test("the front page is the catalog: real facets, real rows, and every count lands", async ({
  page,
}) => {
  /* #100. The front page was three cards and two paragraphs — a table of
     contents for a registry whose whole claim is that its records are
     *described*, which is the one thing a table of contents cannot show.

     What this guards is the property that made the old shape tempting and the
     new shape risky: there is one search behind both columns, so a count in
     the panel and the list it links to are the same aggregation. A second
     implementation here would drift, quietly, in the direction of flattering
     numbers. */
  await page.goto(path("/"));

  const panel = page.getByRole("complementary", { name: "Browse the registry" });
  await expect(panel).toBeVisible();

  // Server-rendered in both builds. `Facets` reads `useSearchParams` and would
  // prerender as its fallback, publishing a front page with no facets on it —
  // the trap `GeographyPicker` documents, which is why this panel is anchors.
  const rows = page.locator("main li.og-card");
  expect(await rows.count()).toBeGreaterThan(0);

  // The four registry kinds are peers here, including the one with nothing in
  // it: a first screen that silently drops the empty section misdescribes what
  // the Hub holds.
  await expect(panel.getByRole("link", { name: /Studies & Assumptions/ })).toContainText("none yet");
  await expect(panel.getByRole("link", { name: /Reference Models/ })).toBeVisible();

  // A row carries what a modeller rejects on without opening it — domain,
  // provenance, completeness, licence — and the last of the five, which the
  // list could not show at all until `DatasetSummary` declared `modified`.
  await expect(rows.first()).toContainText(/Updated \w+ \d+, \d{4}/);
  await expect(rows.first()).toContainText(/Completeness level \d/);

  // And the count beside a facet is the count of what arrives. Read off the
  // page rather than written down: a literal here is a fact about a corpus
  // this test never opened, and it goes stale the next time a record lands.
  const facet = panel.locator('a[href*="data_domain="]').first();
  const counted = Number((await facet.innerText()).trim().split(/\s+/).pop());
  expect(counted).toBeGreaterThan(0);
  await facet.click();
  await expect(page).toHaveURL(/data_domain=/);
  await expect(rows).toHaveCount(counted);
});

test("the map names what you point at, and the name leads to the model", async ({ page }) => {
  /* #98. The viewer drew 412 buses and 455 circuits and identified none of
     them, so the picture could say "this is shaped like Britain" and could not
     say "this is the substation you meant". Only the second makes a reference
     model usable rather than decorative.

     The identification is a live region rather than a `title` tooltip, which is
     what makes the same answer reachable by pointer, by key and by tap. All
     three are exercised: a tooltip would pass a hover test and fail every
     reader on a phone or a keyboard. */
  await page.goto(path("/reference-models?place=greatBritain"));
  const model = page.locator("#model-gb-osm-reference");
  const svg = model.locator("figure svg");
  const status = model.getByRole("status", { name: /Identified component/ });
  await expect(svg).toBeVisible();
  await expect(status).toContainText("Point at a bus or a circuit");

  // Keyboard: the camera keys stay on the bare arrows (#92), so traversal takes
  // the modifier, and each step names what it landed on.
  await svg.focus();
  await page.keyboard.press("Shift+ArrowRight");
  await expect(status).toContainText(/^Bus/);
  const first = await status.innerText();
  await page.keyboard.press("PageDown");
  expect(await status.innerText()).not.toBe(first);

  // The identifier is the one that joins back to the document, and the link
  // carries it. A display index would look exactly as durable and join to
  // nothing.
  const link = status.getByRole("link").first();
  const href = await link.getAttribute("href");
  // Trailing slash optional: the static export writes directory-style URLs,
  // and a base path is prepended when the site is published under one.
  expect(href).toMatch(/\/reference-models\/gb-osm-reference\/?\?component=/);

  // Pointer: the elements are hit-tested by distance rather than by SVG pointer
  // events, because a 400 kV circuit is two pixels wide and a reader cannot be
  // asked to land inside two pixels.
  const spot = await svg.evaluate((el: SVGSVGElement) => {
    const group = el.querySelector("g")!;
    const matrix = group.getScreenCTM()!;
    const marker = el.querySelector("circle")!;
    const point = new DOMPoint(
      Number(marker.getAttribute("cx")),
      Number(marker.getAttribute("cy")),
    ).matrixTransform(matrix);
    return { x: point.x, y: point.y };
  });
  await page.mouse.move(spot.x, spot.y);
  await expect(status).toContainText(/(Bus|Circuit)/);

  await link.click();
  await expect(page).toHaveURL(/\/reference-models\/gb-osm-reference/);
});

test("the model page renders the document's values and says how each was set", async ({ page }) => {
  /* The values were reachable only as a 2.9 MB file on the Downloads tab, so
     the per-field `og:valueBasis` work was invisible at the moment it would
     have changed a decision. */
  await page.goto(path("/reference-models/gb-osm-reference?component=LN-100861731"));
  const table = page.locator("table");
  await expect(table).toBeVisible();

  // Both identities, named as what they are. Where a component carries no
  // upstream id the cell says so rather than falling back to the internal
  // index, which would look exactly like a stable identity.
  const row = table.locator("tbody tr").first();
  await expect(row).toContainText("LN-100861731");
  await expect(row).toContainText("100861731");

  // The basis is the record's own word, on the column it belongs to.
  await expect(table.locator("thead")).toContainText(/modeled/i);

  // And an estimated value shows the rule and the inputs behind it, which is
  // the thing `parameters.json` carries and a file download buries.
  await expect(page.getByText("pypsa-standard-line-type")).toBeVisible();
  await expect(page.getByText("400 kV has no standard type")).toBeVisible();

  // The definitions are readable, not only hoverable: two of these columns
  // carry no unit at all, and the definition is the only thing that says the
  // values are per unit on a 100 MVA base.
  await page.getByText("What these columns mean").click();
  await expect(page.locator("details dl")).toContainText("components.Line[].x");

  // A page that grows with the model is the mistake `StaticSearch` already made
  // once (#34), so the table is paged.
  await page.getByRole("searchbox").fill("");
  await expect(page.getByRole("button", { name: "Next" })).toBeVisible();
  // Counted by row header rather than by `tr`: an expanded derivation adds a
  // row of its own, so `tr` counts the disclosure as a component.
  await expect(table.locator("tbody th[scope=row]")).toHaveCount(25);
});
