/** Top navigation: sections, active indicator, mobile menu, and header height. */
import { expect, test } from "@playwright/test";
import { loginAsAdmin } from "./helpers";

test.beforeEach(async ({ page, request }) => {
  await loginAsAdmin(page, request);
});

test("desktop: nav switches sections and marks the active one", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("button", { name: "Board", exact: true })).toHaveAttribute("aria-current", "page");
  for (const name of ["Dashboard", "Reports", "Manage Users", "Import Bids"]) {
    await nav.getByRole("button", { name, exact: true }).click();
    await expect(nav.getByRole("button", { name, exact: true })).toHaveAttribute("aria-current", "page");
    await expect(nav.getByRole("button", { name: "Board", exact: true })).not.toHaveAttribute("aria-current", "page");
    await page.screenshot({ path: `test-results/nav-1440-${name.replace(" ", "-")}.png` });
  }
  await nav.getByRole("button", { name: "Board", exact: true }).click();
  await expect(page.locator('[data-testid^="column-"]').first()).toBeVisible();
  const box = await page.locator("header").first().boundingBox();
  expect(box!.height).toBeLessThanOrEqual(56);
});

test("tablet: the inline nav fits on one row without overlapping the board", async ({ page }) => {
  await page.setViewportSize({ width: 768, height: 1024 });
  await page.goto("/");
  await expect(page.locator('[data-testid^="column-"]').first()).toBeVisible();
  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("button", { name: "Import Bids", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open menu" })).toBeHidden();
  const header = await page.locator("header").first().boundingBox();
  expect(header!.height).toBeLessThanOrEqual(56);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/nav-768.png" });
});

for (const [name, width, height] of [["mobile", 375, 812]] as const) {
  test(`${name}: menu toggles, reaches every section and does not overlap the board`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    await expect(page.locator('[data-testid^="column-"]').first()).toBeVisible();
    const toggle = page.getByRole("button", { name: "Open menu" });
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await toggle.click();
    const menu = page.locator("#mobile-nav");
    await expect(menu.getByRole("button", { name: "Board", exact: true })).toHaveAttribute("aria-current", "page");
    for (const label of ["Dashboard", "Reports", "Manage Users", "Import Bids"]) {
      await expect(menu.getByRole("button", { name: label, exact: true })).toBeVisible();
    }
    await page.screenshot({ path: `test-results/nav-${width}-menu.png` });
    await page.keyboard.press("Escape");
    await expect(menu).toBeHidden();
    await expect(toggle).toBeFocused();

    await toggle.click();
    await menu.getByRole("button", { name: "Reports", exact: true }).click();
    await expect(menu).toBeHidden();
    await expect(page.getByRole("heading", { name: "Reports", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Open menu" }).click();
    await expect(page.locator("#mobile-nav").getByRole("button", { name: "Reports", exact: true })).toHaveAttribute("aria-current", "page");
    const noHScroll = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
    expect(noHScroll).toBe(true);
  });
}
