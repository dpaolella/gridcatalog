import { expect, test } from "@playwright/test";

/**
 * PRD §F3 wants federated sign-in. The API had it in full and the UI referenced
 * none of it: no route, no control in the header, and `/admin/review` telling a
 * steward to sign in with nowhere to do it. A shipped feature nobody could
 * reach.
 *
 * These run against a deployment with no identity provider configured, which is
 * the state CI is in and a useful one to pin: the page has to say so rather than
 * offer a button that leads to an error blaming the person who pressed it.
 */

test("the header offers a way to sign in", async ({ page }) => {
  await page.goto("/");
  const signIn = page.getByRole("link", { name: "Sign in" });
  await expect(signIn).toBeVisible();
  await signIn.click();
  await expect(page).toHaveURL(/\/signin/);
});

test("the steward queue sends an anonymous reader somewhere, not nowhere", async ({ page }) => {
  await page.goto("/admin/review");

  const signIn = page.getByRole("link", { name: "Sign in" }).last();
  await expect(signIn).toBeVisible();
  await signIn.click();

  // With `next`, so signing in returns to the queue rather than the home page.
  await expect(page).toHaveURL(/\/signin\?next=%2Fadmin%2Freview/);
});

test("a deployment with no provider configured says so", async ({ page }) => {
  await page.goto("/signin");

  await expect(page.getByText(/no sign-in provider is configured/i)).toBeVisible();
  // And never a dead button: an unconfigured provider must not be offered.
  await expect(page.getByRole("link", { name: /^Continue with/ })).toHaveCount(0);
});

test("browsing is not gated behind it", async ({ page }) => {
  // The sign-in page says this in words; this asserts it in behaviour, because
  // a catalog that asks who you are before it will show you anything is a
  // catalog people stop using (PRD §F10).
  await page.goto("/datasets/ecmwf-era5");
  await expect(page.getByRole("heading", { name: /ERA5/i })).toBeVisible();
  await expect(page).not.toHaveURL(/\/signin/);
});
