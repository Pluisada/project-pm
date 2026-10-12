import { expect, test } from "@playwright/test";
import { dragCardToColumn, loginAsAdmin } from "./helpers";

test.beforeEach(async ({ page, request }) => {
  await loginAsAdmin(page, request);
});

test("loads the kanban board", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "My Board" })).toBeVisible();
  await expect(page.locator('[data-testid^="column-"]')).toHaveCount(5);
});

test("adds a card to a column", async ({ page }) => {
  await page.goto("/");
  const firstColumn = page.locator('[data-testid^="column-"]').first();
  const title = `Playwright card ${Date.now()}`;
  await firstColumn.getByRole("button", { name: /add a card/i }).click();
  await firstColumn.getByPlaceholder("Card title").fill(title);
  await firstColumn.getByPlaceholder("Details").fill("Added via e2e.");
  await firstColumn.getByRole("button", { name: /add card/i }).click();
  await expect(firstColumn.locator(`[data-testid^="card-"][data-title="${title}"]`)).toBeVisible();
});

test("moves a card between columns", async ({ page }) => {
  await page.goto("/");
  // Column/card ids are DB-assigned and not stable across environments, so
  // this locates the target by DOM order and adds a fresh card to drag
  // rather than relying on the seeded sample data's ids.
  const columns = page.locator('[data-testid^="column-"]');
  const firstColumn = columns.first();
  const targetColumn = columns.nth(3); // "Review"

  const title = `Drag me ${Date.now()}`;
  await firstColumn.getByRole("button", { name: /add a card/i }).click();
  await firstColumn.getByPlaceholder("Card title").fill(title);
  await firstColumn.getByRole("button", { name: /add card/i }).click();

  const card = firstColumn.locator(`[data-testid^="card-"][data-title="${title}"]`);
  await dragCardToColumn(page, card, targetColumn);
  await expect(targetColumn.locator(`[data-testid^="card-"][data-title="${title}"]`)).toBeVisible();
});
