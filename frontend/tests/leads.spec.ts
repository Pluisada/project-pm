/**
 * E2E tests for sales lead fields and the Reports screen, against the real
 * backend (the board must already be the sales pipeline - fresh setup seeds
 * it, or run backend/convert_to_sales_board.py).
 */

import { expect, test } from "@playwright/test";
import { loginAsAdmin } from "./helpers";

test.beforeEach(async ({ page, request }) => {
  await loginAsAdmin(page, request);
});

test("edits a lead's fields and shows them on the card", async ({ page }) => {
  await page.goto("/");
  const firstColumn = page.locator('[data-testid^="column-"]').first();
  const title = `Lead ${Date.now()}`;
  await firstColumn.getByRole("button", { name: /add a card/i }).click();
  await firstColumn.getByPlaceholder("Card title").fill(title);
  await firstColumn.getByRole("button", { name: /add card/i }).click();

  const card = firstColumn.locator(`[data-testid^="card-"][data-title="${title}"]`);
  await card.getByRole("button", { name: `Edit lead ${title}` }).click();

  const dialog = page.getByRole("dialog", { name: `Edit lead ${title}` });
  await dialog.getByLabel("Company").fill("E2E Electronics");
  await dialog.getByLabel("Contact").fill("Test Buyer");
  await dialog.getByLabel("Deal value").fill("75000");
  await dialog.getByLabel("Currency").selectOption("EUR");
  await dialog.getByLabel("Expected close date").fill("2027-02-28");
  await dialog.getByLabel("Lead source").selectOption("Trade show");
  await dialog.getByLabel("Product line").selectOption("Chargers");
  await dialog.getByLabel("Assigned salesperson").selectOption({ label: "e2e_admin" });
  await dialog.getByRole("button", { name: "Save lead" }).click();

  await expect(dialog).toBeHidden();
  await expect(card.getByText("E2E Electronics - Test Buyer")).toBeVisible();
  await expect(card.getByText(/75,000/)).toBeVisible();
  await expect(card.getByText("Chargers")).toBeVisible();
  await expect(card.getByText("e2e_admin")).toBeVisible();
});

test("requires a currency when a deal value is set", async ({ page }) => {
  await page.goto("/");
  const card = page.locator('[data-testid^="card-"]').first();
  await card.getByRole("button", { name: /^edit lead/i }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Currency").selectOption("");
  await dialog.getByLabel("Deal value").fill("100");
  await dialog.getByRole("button", { name: "Save lead" }).click();
  await expect(dialog.getByText("Choose a currency for the deal value")).toBeVisible();
});

test("shows the report and filters it", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Reports" }).click();
  await expect(page.getByRole("heading", { name: "Reports" })).toBeVisible();

  const kpis = page.getByTestId("report-kpis");
  await expect(kpis.getByText("Open leads")).toBeVisible();
  await expect(kpis.getByText("Win rate")).toBeVisible();
  for (const stage of ["1st Contact", "F2F Meeting", "RFI", "Proposal", "Award", "Lost"]) {
    await expect(page.getByRole("listitem").filter({ hasText: stage }).first()).toBeVisible();
  }

  const openLeads = kpis.locator("div", { hasText: "Open leads" }).locator("p").nth(1);
  const before = await openLeads.textContent();
  await page.getByLabel("Filter by product line").selectOption("Power supplies");
  await expect(openLeads).not.toHaveText(before ?? "");

  await page.getByRole("button", { name: "Back to Board" }).click();
  await expect(page.getByRole("heading", { name: "Salcomp Sales Pipeline" })).toBeVisible();
});

test("undoes the last move from the top bar", async ({ page }) => {
  await page.goto("/");
  const columns = page.locator('[data-testid^="column-"]');
  const title = `Undo me ${Date.now()}`;
  await columns.first().getByRole("button", { name: /add a card/i }).click();
  await columns.first().getByPlaceholder("Card title").fill(title);
  await columns.first().getByRole("button", { name: /add card/i }).click();
  const card = page.locator(`[data-testid^="card-"][data-title="${title}"]`);
  await expect(card).toBeVisible();

  const undo = page.getByRole("button", { name: "Undo", exact: true });
  await expect(undo).toBeEnabled();
  await expect(page.getByText(`Remove new card "${title}"`)).toBeVisible();

  await undo.click();
  await expect(page.getByText(`Undone: Remove new card "${title}"`)).toBeVisible();
  await expect(card).toHaveCount(0);
  await expect(undo).toBeDisabled();
});
