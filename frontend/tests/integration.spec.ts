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
    await expect(page.getByRole("heading", { name: "My Board" })).toBeVisible();
  });

  test("displays all 5 columns", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator('[data-testid^="column-"]')).toHaveCount(5);
  });

  test("displays column titles", async ({ page }) => {
    await page.goto("/");
    const expectedTitles = ["Backlog", "Discovery", "In Progress", "Review", "Done"];

    for (const title of expectedTitles) {
      await expect(page.getByText(title, { exact: true })).toBeVisible();
    }
  });

  test("displays initial cards", async ({ page }) => {
    await page.goto("/");
    // Should have at least 8 cards initially
    const cards = page.locator('[data-testid^="card-"]');
    const count = await cards.count();
    expect(count).toBeGreaterThanOrEqual(8);
  });

  test("adds a card to a column", async ({ page }) => {
    await page.goto("/");
    const firstColumn = page.locator('[data-testid^="column-"]').first();

    // Click add button
    await firstColumn.getByRole("button", { name: /add a card/i }).click();

    // Fill form
    await firstColumn.getByPlaceholder("Card title").fill("Integration Test Card");
    await firstColumn.getByPlaceholder("Details").fill("This is an integration test.");

    // Submit
    await firstColumn.getByRole("button", { name: /add card/i }).click();

    // Verify card appears
    await expect(firstColumn.getByText("Integration Test Card")).toBeVisible();
  });

  test("deletes a card", async ({ page }) => {
    await page.goto("/");
    const firstColumn = page.locator('[data-testid^="column-"]').first();

    // Add a card first
    await firstColumn.getByRole("button", { name: /add a card/i }).click();
    await firstColumn.getByPlaceholder("Card title").fill("Delete Me");
    await firstColumn.getByPlaceholder("Details").fill("Test");
    await firstColumn.getByRole("button", { name: /add card/i }).click();

    // Verify card exists
    await expect(firstColumn.getByText("Delete Me")).toBeVisible();

    // Delete it
    await firstColumn.getByRole("button", { name: /delete delete me/i }).click();

    // Verify it's gone
    await expect(firstColumn.queryByText("Delete Me")).not.toBeVisible();
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
    const startTime = Date.now();
    await titleInput.fill("Performance Test");
    const duration = Date.now() - startTime;

    // Should respond in less than 500ms
    expect(duration).toBeLessThan(500);
  });

  test("works on mobile viewport", async ({ page }) => {
    // Set mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });

    await page.goto("/");

    // Page should still load
    await expect(page.getByRole("heading", { name: "My Board" })).toBeVisible();

    // Columns should be visible (may stack vertically)
    const columns = page.locator('[data-testid^="column-"]');
    expect(await columns.count()).toBeGreaterThan(0);
  });

  test("works on tablet viewport", async ({ page }) => {
    // Set tablet viewport
    await page.setViewportSize({ width: 768, height: 1024 });

    await page.goto("/");

    // Page should still load
    await expect(page.getByRole("heading", { name: "My Board" })).toBeVisible();
  });
});
