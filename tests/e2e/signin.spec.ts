import { expect, test } from "@playwright/test";
import { isStatic, path } from "./site";

/**
 * PRD §F3 wants federated sign-in. The API had it in full and the UI referenced
 * none of it: no route, no control in the header, and `/admin/review` telling a
 * steward to sign in with nowhere to do it. A shipped feature nobody could
 * reach.
 *
 * These run against a deployment with no identity provider configured, which is
 * the state CI is in and a useful one to pin: the page has to say so rather than
 * offer a button that leads to an error blaming the person who pressed it.
 *
 * The static export is a third state again, and the distinction matters. It has
 * no service behind it at all — no session endpoint, no provider list, no
 * account control in the header — so these are not features failing there, they
 * are features that deployment does not have. Skipped rather than red, with the
 * thing it does instead asserted below.
 */

test.describe("against a service", () => {
  test.skip(isStatic, "A site made of files has no session endpoint to sign in against");

  test("the header offers a way to sign in", async ({ page }) => {
    await page.goto(path("/"));
    const signIn = page.getByRole("link", { name: "Sign in" });
    await expect(signIn).toBeVisible();
    await signIn.click();
    await expect(page).toHaveURL(/\/signin/);
  });

  test("the steward queue sends an anonymous reader somewhere, not nowhere", async ({ page }) => {
    await page.goto(path("/admin/review"));

    const signIn = page.getByRole("link", { name: "Sign in" }).last();
    await expect(signIn).toBeVisible();
    await signIn.click();

    // With `next`, so signing in returns to the queue rather than the home page.
    await expect(page).toHaveURL(/\/signin\?next=%2Fadmin%2Freview/);
  });

  test("a deployment with no provider configured says so", async ({ page }) => {
    await page.goto(path("/signin"));

    await expect(page.getByText(/no sign-in provider is configured/i)).toBeVisible();
    // And never a dead button: an unconfigured provider must not be offered.
    await expect(page.getByRole("link", { name: /^Continue with/ })).toHaveCount(0);
  });
});

test.describe("against a static export", () => {
  test.skip(!isStatic, "The live build has a service to sign in against");

  test("a page that needs identity says the build has no service, not that you may not", async ({
    page,
  }) => {
    /* The same obligation the provider test pins for a live deployment, one
       state further out: a page whose whole job needs a service has to say the
       service is absent. Silence here reads as a refusal — a steward who opens
       the review queue and finds it empty concludes there is nothing to review,
       which is a claim about the catalog rather than about the build. */
    for (const route of ["/signin", "/admin/review"]) {
      await page.goto(path(route));
      await expect(page.getByText(/static copy of the catalog/i)).toBeVisible();
      // And no control that cannot work: a sign-in button on a site with no
      // session endpoint is the dead button the live test also refuses.
      await expect(page.getByRole("link", { name: /^Continue with/ })).toHaveCount(0);
    }
  });
});

test("browsing is not gated behind it", async ({ page }) => {
  // The sign-in page says this in words; this asserts it in behaviour, because
  // a catalog that asks who you are before it will show you anything is a
  // catalog people stop using (PRD §F10). True of both builds, which is why it
  // is outside both blocks.
  await page.goto(path("/datasets/ecmwf-era5"));
  await expect(page.getByRole("heading", { name: /ERA5/i })).toBeVisible();
  await expect(page).not.toHaveURL(/\/signin/);
});
