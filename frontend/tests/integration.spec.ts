/**
 * Integration tests for Kanban board with backend
 * These tests verify the full frontend + backend integration works correctly
 * Run with: npm run test:e2e
 * Or with Docker backend: npx playwright test --config=playwright.docker.config.ts
 */

import { expect, test } from "@playwright/test";
import { loginAsAdmin } from "./helpers";

test.describe("Kanban Board Integration", () => {
  test.beforeEach(async ({ page, request }) => {
    await loginAsAdmin(page, request);
  });

  test("loads the kanban board at root", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Salcomp Sales Pipeline" })).toBeVisible();
  });

  test("displays all 6 stages", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator('[data-testid^="column-"]')).toHaveCount(6);
  });

  test("displays column titles", async ({ page }) => {
    await page.goto("/");
    const titleInputs = page.locator('input[aria-label="Column title"]');
    // evaluateAll doesn't auto-wait like `expect(locator)` matchers do, so
    // it can read [] while the board is still loading.
    await expect(titleInputs.first()).toBeVisible();
    // Column titles live in an editable <input>'s value, not text content,
    // so getByText can't see them.
    const titles = await titleInputs.evaluateAll((inputs) =>
      inputs.map((el) => (el as HTMLInputElement).value)
    );
    for (const title of ["1st Contact", "F2F Meeting", "RFI", "Proposal", "Award", "Lost"]) {
      expect(titles).toContain(title);
    }
  });

  test("displays initial cards", async ({ page }) => {
    await page.goto("/");
    const cards = page.locator('[data-testid^="card-"]');
    // .count() doesn't auto-wait like `expect(locator)` matchers do, so it
    // can read 0 while the board is still loading - wait for the first card
    // to actually render before counting.
    await expect(cards.first()).toBeVisible();
    // Should have at least 8 cards initially
    expect(await cards.count()).toBeGreaterThanOrEqual(8);
  });

  test("adds a card to a column", async ({ page }) => {
    await page.goto("/");
    const firstColumn = page.locator('[data-testid^="column-"]').first();

    // Click add button
    await firstColumn.getByRole("button", { name: /add a card/i }).click();

    // Unique per run: cards persist in the shared database between runs.
    const title = `Integration Test Card ${Date.now()}`;
    await firstColumn.getByPlaceholder("Card title").fill(title);
    await firstColumn.getByPlaceholder("Details").fill("This is an integration test.");

    // Submit
    await firstColumn.getByRole("button", { name: /add card/i }).click();

    // Verify card appears - title lives in an <input>'s value, not text
    // content, so this is matched via the card's data-title attribute.
    await expect(
      firstColumn.locator(`[data-testid^="card-"][data-title="${title}"]`)
    ).toBeVisible();
  });

  test("deletes a card", async ({ page }) => {
    await page.goto("/");
    const firstColumn = page.locator('[data-testid^="column-"]').first();

    // Unique per run - this DB persists across test runs, and a fixed title
    // could otherwise collide with a leftover card from an earlier run.
    const title = `Delete Me ${Date.now()}`;

    // Add a card first
    await firstColumn.getByRole("button", { name: /add a card/i }).click();
    await firstColumn.getByPlaceholder("Card title").fill(title);
    await firstColumn.getByPlaceholder("Details").fill("Test");
    await firstColumn.getByRole("button", { name: /add card/i }).click();

    const card = firstColumn.locator(`[data-testid^="card-"][data-title="${title}"]`);
    await expect(card).toBeVisible();

    // Delete it. Anchored so it doesn't also match the drag handle, whose
    // accessible name ("Drag Delete Me ...") contains the card's own title.
    await card.getByRole("button", { name: /^delete/i }).click();

    // Verify it's gone
    await expect(card).toHaveCount(0);
  });

  test("renames a column", async ({ page }) => {
    await page.goto("/");
    const firstColumn = page.locator('[data-testid^="column-"]').first();
    const titleInput = firstColumn.locator('input[aria-label="Column title"]');

    // Get original value
    const originalValue = await titleInput.inputValue();

    // Change it
    await titleInput.fill("Test Column");

    // Verify change persists
    expect(await titleInput.inputValue()).toBe("Test Column");

    // Revert for other tests
    await titleInput.fill(originalValue);
  });

  test("displays no JavaScript errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        errors.push(msg.text());
      }
    });

    await page.goto("/");
    await page.waitForTimeout(1000);

    // Should have no critical errors
    expect(errors.filter((e) => !e.includes("Deprecation"))).toHaveLength(0);
  });

  test("responds quickly to user interactions", async ({ page }) => {
    await page.goto("/");

    const firstColumn = page.locator('[data-testid^="column-"]').first();
    const titleInput = firstColumn.locator('input[aria-label="Column title"]');

    // Measure interaction speed
    const originalValue = await titleInput.inputValue();
    const startTime = Date.now();
    await titleInput.fill("Performance Test");
    const duration = Date.now() - startTime;

    // Revert - this DB persists across test runs, and other tests
    // (e.g. "displays column titles") expect the seeded column names.
    await titleInput.fill(originalValue);

    // Should respond in less than 500ms
    expect(duration).toBeLessThan(500);
  });

  test("works on mobile viewport", async ({ page }) => {
    // Set mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });

    await page.goto("/");

    // Page should still load
    await expect(page.getByRole("heading", { name: "Salcomp Sales Pipeline" })).toBeVisible();

    // Columns should be visible (may stack vertically)
    const columns = page.locator('[data-testid^="column-"]');
    expect(await columns.count()).toBeGreaterThan(0);
  });

  test("works on tablet viewport", async ({ page }) => {
    // Set tablet viewport
    await page.setViewportSize({ width: 768, height: 1024 });

    await page.goto("/");

    // Page should still load
    await expect(page.getByRole("heading", { name: "Salcomp Sales Pipeline" })).toBeVisible();
  });
});
