/** The AI sidebar starts closed; it is a drawer below the lg breakpoint and a right-hand column above it. */
import { expect, test } from "@playwright/test";
import { loginAsAdmin } from "./helpers";

const columns = (page: import("@playwright/test").Page) => page.locator('[data-testid^="column-"]');
const sidebar = (page: import("@playwright/test").Page) => page.locator("#ai-sidebar");

// The launcher floats continuously, which Playwright never sees as "stable" enough to click;
// reduced motion is the documented way to switch the animation off.
test.beforeEach(async ({ page, request }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await loginAsAdmin(page, request);
});

test("desktop: the sidebar starts closed, opens as the right-hand column and closes again", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(columns(page).first()).toBeVisible();
  await expect(sidebar(page)).toBeHidden();
  const launcher = page.getByRole("button", { name: "Open AI Assistant" });
  await expect(launcher).toBeVisible();
  await expect(launcher).toHaveCSS("animation-name", "none");
  await page.screenshot({ path: "test-results/responsive-1440-closed.png" });

  await launcher.click();
  await expect(sidebar(page)).toBeVisible();
  await expect(launcher).toBeHidden();
  const box = await sidebar(page).boundingBox();
  expect(box!.width).toBe(320);
  expect(box!.x + box!.width).toBe(1440);
  await page.screenshot({ path: "test-results/responsive-1440-open.png" });

  await sidebar(page).getByRole("button", { name: "Close AI Assistant" }).click();
  await expect(sidebar(page)).toBeHidden();
  await expect(launcher).toBeVisible();
  await expect(launcher).toBeFocused();
});

for (const [name, width, height] of [["tablet", 768, 1024], ["mobile", 375, 812]] as const) {
  test(`${name}: board is unobstructed and the sidebar opens as a drawer`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    await expect(columns(page).first()).toBeVisible();
    await expect(sidebar(page)).toBeHidden();
    await page.screenshot({ path: `test-results/responsive-${width}-closed.png` });

    await page.getByRole("button", { name: "Open AI Assistant" }).click();
    await expect(sidebar(page)).toBeVisible();
    const box = await sidebar(page).boundingBox();
    expect(box!.width).toBeLessThanOrEqual(320);
    await page.screenshot({ path: `test-results/responsive-${width}-open.png` });

    await page.keyboard.press("Escape");
    await expect(sidebar(page)).toBeHidden();
    await expect(page.getByRole("button", { name: "Open AI Assistant" })).toBeFocused();

    await page.getByRole("button", { name: "Open AI Assistant" }).click();
    await sidebar(page).getByRole("button", { name: "Close AI Assistant" }).click();
    await expect(sidebar(page)).toBeHidden();
    await expect(page.getByRole("button", { name: "Open AI Assistant" })).toBeVisible();
  });
}

test("mobile: card details open above the closed drawer and Escape closes only the dialog", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  const title = `Narrow ${Date.now()}`;
  const column = columns(page).first();
  await column.getByRole("button", { name: /add a card/i }).click();
  await column.getByPlaceholder("Card title").fill(title);
  await column.getByRole("button", { name: /add card/i }).click();

  await page.getByRole("button", { name: `Open details for ${title}` }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await page.screenshot({ path: "test-results/responsive-375-details.png" });
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(sidebar(page)).toBeHidden();
});
