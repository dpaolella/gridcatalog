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

test("a modeller reaches a DD5 access path from the catalog", async ({
  page,
}) => {
  const started = Date.now();

  // The catalog moved off "/" when the nav became four peers, so the 60-second
  // budget now starts one click later than it did. Measured from the catalog
  // rather than from the Hub landing page because the landing page is a menu:
  // timing a reader's reading speed would make this a test of the copy.
  await page.goto("/datasets");
  await page.getByLabel("Search the catalog").fill("wind");
  // The search is debounced and pushed into the URL. Waiting for the URL is
  // what makes this deterministic: asserting on the results before the
  // navigation lands passes or fails depending on machine load, and a flaky
  // E2E test is one people learn to re-run rather than read.
  await page.waitForURL(/[?&]q=wind/);

  const result = page.getByRole("link", { name: /Global Wind Atlas/i }).first();
  await expect(result).toBeVisible();
  await result.click();

  await expect(
    page.getByRole("heading", { name: /Global Wind Atlas/i }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Downloads" }).click();

  // "A correct access plan" means a path the modeller can act on: a format and
  // a link to the source. The Hub never serves the bytes, so what the page owes
  // them is where to get them.
  await expect(
    page.getByRole("link", { name: /Open at source/i }).first(),
  ).toBeVisible();

  expect(Date.now() - started).toBeLessThan(60_000);
});

test("an unauthenticated evaluator reads all three quality grades", async ({
  page,
}) => {
  await page.goto("/datasets/ecmwf-era5");
  await page.getByRole("tab", { name: "Data quality" }).click();

  const panel = page.getByRole("tabpanel", { name: "Data quality" });
  for (const facet of [
    "Provenance",
    "Documentation",
    "Currency & maintenance",
  ]) {
    await expect(panel.getByText(facet, { exact: true })).toBeVisible();
  }

  // And no composite anywhere on the page (ADR-0007).
  await expect(
    page.getByText(/overall score|composite|total quality/i),
  ).toHaveCount(0);
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

test("a restricted record answers exactly as an absent one does", async ({
  page,
}) => {
  // The header settles asynchronously — `AccountMenu` renders nothing until
  // `/api/session` answers — so reading the whole body straight after
  // navigation compares one page that has the account control against one that
  // does not yet, and reports a difference in the header as a difference in the
  // refusal. It failed exactly that way once the suite got busy enough for the
  // second navigation to find a warm route.
  //
  // Waited for rather than filtered out: the assertion is that these two pages
  // are textually identical, and excluding a region from the comparison is how
  // a leak in that region stops being caught.
  async function settledBody(path: string) {
    const response = await page.goto(path);
    await expect(page.getByRole("link", { name: "Sign in" })).toBeVisible();
    return {
      status: response?.status(),
      text: await page.locator("body").innerText(),
    };
  }

  const restricted = await settledBody(
    "/datasets/utility-load-shapes-allowlisted",
  );
  const absent = await settledBody("/datasets/there-is-no-such-dataset");

  expect(restricted.status).toBe(404);
  expect(absent.status).toBe(404);
  expect(restricted.text).toBe(absent.text);
});

test("an empty search explains itself", async ({ page }) => {
  await page.goto("/datasets?q=zzzznothingmatchesthis");

  await expect(page.getByText(/No datasets match this search/i)).toBeVisible();
  await expect(
    page.getByRole("link", { name: /Clear all filters/i }),
  ).toBeVisible();
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

  const gap = page.getByTitle(
    /No concept in the .* scheme covers|pollute a shared vocabulary/i,
  );
  await expect(gap.first()).toBeVisible();
});

test("the connections list is capped with a way to see more", async ({
  page,
}) => {
  await page.goto("/datasets/pypsa-eur-grid");
  await page.getByRole("tab", { name: "Connections" }).click();

  // By test id, not by role: each connection row contains its own list of
  // reasons, so counting every listitem in the panel counts the reasons too
  // and reports 33 where there are 10.
  const rows = page.getByTestId("connection");
  expect(await rows.count()).toBeLessThanOrEqual(12);
  await expect(page.getByTestId("connection-list")).toBeVisible();
});

test("a concept on a schema row links to every dataset that carries it", async ({
  page,
}) => {
  // PRD §F3: "Each concept links to the semantic layer." The Schema tab used to
  // render concept names as plain text, which is the one thing a modeller wants
  // to pull on — the useful question at that cell is "what else has this
  // quantity", and the answer is a filter the search backend already compiles.
  await page.goto("/datasets/ecmwf-era5");
  await page.getByRole("tab", { name: "Schema" }).click();

  const concept = page.locator("table a[href*='concept=']").first();
  await expect(concept).toBeVisible();
  await concept.click();

  await expect(page).toHaveURL(/concept=/);
  await expect(page.getByRole("link", { name: /ERA5/i }).first()).toBeVisible();
});

test("an issue can be reported against one field and one distribution", async ({
  page,
}) => {
  // §F3 asks for a report on any record, *field* or distribution, with the
  // reference captured automatically. The API has taken `field_id` and
  // `distribution_id` since it was written; the UI passed neither, so a wrong
  // unit on one column and a dead URL on one of several paths both had to be
  // filed against the whole record.
  await page.goto("/datasets/ecmwf-era5");

  await page.getByRole("tab", { name: "Schema" }).click();
  await page
    .locator("table")
    .getByRole("button", { name: "Report" })
    .first()
    .click();
  // The form names what it is about, so a reporter can see the reference was
  // captured rather than having to trust that it was.
  await expect(page.getByText(/About/).first()).toBeVisible();
  await expect(page.getByRole("combobox").first()).toBeVisible();

  await page.getByRole("tab", { name: "Downloads" }).click();
  await expect(
    page.getByRole("button", { name: "Report" }).first(),
  ).toBeVisible();
});

test("field-level provenance reaches the schema table", async ({ page }) => {
  // It was fetched, exported, shipped to the browser and dropped at render
  // time. It is the evidence behind the Provenance grade.
  await page.goto("/datasets/global-wind-atlas");
  await page.getByRole("tab", { name: "Schema" }).click();

  await expect(
    page.getByRole("columnheader", { name: "Provenance" }),
  ).toBeVisible();
});

test("the caveats a steward wrote are on the page", async ({ page }) => {
  // They were held in the graph from M2 and projected nowhere: `og:caveat`
  // hangs off the `og:QualityFlags` node and `build_document` never followed
  // the edge, so 478 of them — including the two hand-written ones on the ERA5
  // golden record — reached no API caller and no page. A caveat is the one
  // thing on a record that comes from somebody having *used* the dataset, so
  // this asserts it is above the fold rather than behind a tab.
  await page.goto("/datasets/ecmwf-era5");

  const caveats = page.getByRole("heading", { name: /Before you use this/i });
  await expect(caveats).toBeVisible();
  await expect(
    page.getByText(/does not resolve individual wind farms/i),
  ).toBeVisible();
});

test("an empty search names the gap when there is one", async ({ page }) => {
  // PRD §5: saying what does not exist is a feature. "No datasets match this
  // search" tells a reader the catalog is small; "nothing open supplies this,
  // here is why, here is who found that and when" tells them something true
  // about the field (#56).
  //
  // `max upward ramp` is the worked case, chosen because it is one of the few
  // gap entries whose wording finds *no* datasets in the seeded catalog. The
  // notice only renders on an empty result set, so a query matching both would
  // test nothing — "nodal demand" reads better and returns three datasets.
  await page.goto("/datasets?q=max+upward+ramp");

  await expect(page.getByText(/No datasets match this search/i)).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /Nothing open supplies this/i }),
  ).toBeVisible();
  await expect(page.getByText(/per-plant or per-unit basis/i)).toBeVisible();
  // The attribution, which is what makes the claim weighable at all.
  await expect(page.getByText(/Open Energy Data Inventory/i)).toBeVisible();
});

test("each section's heading is about that section", async ({ page }) => {
  /* The catalog page kept the landing page's hero when it moved to
     `/datasets`, so two pages shipped with an identical heading and a reader
     clicking "Data Catalog" read "Publish a model. Test an assumption." over a
     list of datasets. Nothing caught it: no assertion anywhere covers whether
     a heading is about the page it is on, and it was found by reading the
     deployed site.

     Asserted as distinctness rather than exact strings. Pinning the copy would
     make every wording change a test change, and the defect was never that a
     particular sentence was wrong — it was that two pages said the same one. */
  const headings = new Map<string, string>();
  for (const path of ["/", "/datasets", "/studies", "/reference-models", "/gaps"]) {
    await page.goto(path);
    headings.set(path, (await page.getByRole("heading", { level: 1 }).first().textContent()) ?? "");
  }

  const seen = new Map<string, string>();
  for (const [path, heading] of headings) {
    expect(heading.trim()).not.toBe("");
    const duplicate = seen.get(heading);
    expect(duplicate, `${path} and ${duplicate} share the heading "${heading}"`).toBeUndefined();
    seen.set(heading, path);
  }
});
