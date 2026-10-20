/**
 * Creating a card with both annual Deal Values, against the isolated E2E backend:
 * the values persist, the compact card shows their sum, the details view lists
 * each year, and editing one value updates the sum.
 */
import { expect, test } from "@playwright/test";
import { loginAsAdmin, openCardEditor } from "./helpers";

const YEAR = new Date().getFullYear();

test("a card created with both annual values persists, shows the sum, and can be edited", async ({ page, request }) => {
  const title = `Create annual ${Date.now()}`;
  const card = page.locator(`[data-testid^="card-"][data-title="${title}"]`);
  await loginAsAdmin(page, request);
  await page.goto("/");
  const column = page.locator('[data-testid^="column-"]').first();
  await expect(column).toBeVisible();

  await column.getByRole("button", { name: /add a card/i }).click();
  await column.getByPlaceholder("Card title").fill(title);
  await column.getByLabel("Current year (USD)").fill("60000");
  await column.getByLabel("Next year (USD)").fill("15000");
  await column.getByRole("button", { name: /add card/i }).click();
  await expect(card).toBeVisible();
  await expect(card.getByText("$75,000")).toBeVisible();

  // Persisted server-side: still there after a reload.
  await page.reload();
  await expect(card.getByText("$75,000")).toBeVisible();

  await page.getByRole("button", { name: `Open details for ${title}` }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText(`Deal value ${YEAR}`, { exact: true })).toBeVisible();
  await expect(dialog.getByText("$60,000")).toBeVisible();
  await expect(dialog.getByText(`Deal value ${YEAR + 1}`, { exact: true })).toBeVisible();
  await expect(dialog.getByText("$15,000")).toBeVisible();
  await page.keyboard.press("Escape");

  const form = await openCardEditor(page, title);
  await expect(form.getByLabel(/Deal value - current year/)).toHaveValue("60000");
  await expect(form.getByLabel(/Deal value - next year/)).toHaveValue("15000");
  await form.getByLabel(/Deal value - next year/).fill("40000");
  await form.getByLabel("Assigned salesperson").selectOption({ label: "e2e_admin" });
  await form.getByRole("button", { name: "Save lead" }).click();
  await expect(form).toBeHidden();
  await expect(card.getByText("$100,000")).toBeVisible();
});
