import { expect, test } from "@playwright/test";
import { path } from "./site";

/**
 * Studies, their assumptions and their runs (#82).
 *
 * The registry could hold four kinds of object and the site could show one. A
 * filing's assumption set — the most useful thing the catalog knows about a
 * proposal — was writable, validated, and reachable by nobody.
 *
 * Both builds, no skips. Everything here is server-rendered from data the
 * exporter writes, which is deliberate: the reader most likely to want a
 * filing's assumptions is a regulator opening a published static copy, and a
 * page that needs a session to show them would be useless to exactly that
 * person.
 */

const CASCADE = "cascade-pl-irp-2026";
const COALITION = "coalition-intervention-ue-26-0142";

test("a filing and the intervention contesting it read as one thread", async ({ page }) => {
  /* The failure this prevents: four rows sorted by date, in which the reader
     meets the rebuttal before the thing rebutted and nothing says the two are
     about each other. */
  await page.goto(path("/studies"));

  const thread = page.locator("main li.og-card").filter({ hasText: "UE-26-0142" }).first();
  await expect(thread).toBeVisible();
  await expect(thread).toContainText("2 studies");
  await expect(thread).toContainText("Contests this filing");

  // The filing leads, whatever order the rows arrived in.
  await expect(thread.getByRole("link").first()).toContainText(/Integrated Resource Plan/);
});

test("a study is reached from the list, not through the record page", async ({ page }) => {
  await page.goto(path("/studies"));
  await page.getByRole("link", { name: /Integrated Resource Plan/ }).first().click();
  await expect(page).toHaveURL(new RegExp(`/studies/${CASCADE}`));
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/Integrated Resource Plan/);
});

test("every assumption carries its value, its basis and its source", async ({ page }) => {
  /* Three things, each answering what the others cannot. The value is what was
     assumed; the basis is what kind of claim it is, and 0.098 asserted and
     0.098 measured are different claims; the source is what makes "trace every
     input behind this proposal" a query rather than a reading exercise. */
  await page.goto(path(`/studies/${CASCADE}`));

  const row = page.locator("li").filter({ hasText: "return_on_equity" }).first();
  await expect(row).toContainText("0.098");
  await expect(row).toContainText("Estimated");
  await expect(row).toContainText("cascade-rate-case-exhibits");

  // Cited and not catalogued, said in words. Dropping the citation would lose
  // the trace; linking it would promise a page that 404s.
  await expect(row).toContainText("Not in this catalog");
});

test("the assumptions nest under the model fields they land on", async ({ page }) => {
  await page.goto(path(`/studies/${CASCADE}`));
  const branch = page.locator("details").filter({ hasText: "TechnologyFinancialData" }).first();
  await expect(branch).toBeVisible();
  // Native <details>: the tree browses on a site with no JavaScript at all.
  await expect(branch).toContainText("return_on_equity");
});

test("an intervention says which value it changed and why", async ({ page }) => {
  /* What makes a single-factor intervention checkable. Without the marker a
     reader has to diff two tables by eye to find the one number; without the
     justification they find the difference and not the argument for it. */
  await page.goto(path(`/studies/${COALITION}`));

  const changed = page.locator("li").filter({ hasText: "return_on_equity" }).first();
  await expect(changed).toContainText("0.074");
  await expect(changed).toContainText("Changed");
  await expect(changed).toContainText(/Order 08-441/);

  const inherited = page.locator("li").filter({ hasText: "debt_fraction" }).first();
  await expect(inherited).toContainText("Inherited, unchanged");

  await expect(page.getByRole("link", { name: /Integrated Resource Plan/ })).toBeVisible();
});

test("both studies register a run on the same network", async ({ page }) => {
  for (const id of [CASCADE, COALITION]) {
    await page.goto(path(`/studies/${id}`));
    const runs = page.getByRole("heading", { name: "Runs" });
    await expect(runs).toBeVisible();
    await expect(page.locator("main")).toContainText("PyPSA");
    await expect(page.locator("main")).toContainText("HiGHS 1.7.2");
    // The Hub registers runs; it does not execute them.
    await expect(page.locator("main")).toContainText(/Run by .*fictional/);
  }
});

test("the network says which registered studies stand on it", async ({ page }) => {
  /* Distinct from the citations on the Connections tab, and that is the whole
     point: `usage_evidence` is a string a harvest found in somebody's
     metadata, and every study here is an object in this catalog with an
     assumption set behind it. */
  await page.goto(path("/datasets/gb-osm-reference"));

  const used = page.locator("section").filter({ hasText: "2 registered studies" }).first();
  await expect(used).toBeVisible();
  await expect(used).toContainText("Ran on this network");

  await used.getByRole("link", { name: /Integrated Resource Plan/ }).click();
  await expect(page).toHaveURL(new RegExp(`/studies/${CASCADE}`));
});

test("a study asked for as a dataset says what it is instead of showing empty tabs", async ({
  page,
}) => {
  await page.goto(path(`/datasets/${CASCADE}`));
  await expect(page.getByText("This record is a study")).toBeVisible();
  await page.getByRole("link", { name: /Integrated Resource Plan/ }).click();
  await expect(page).toHaveURL(new RegExp(`/studies/${CASCADE}`));
});
