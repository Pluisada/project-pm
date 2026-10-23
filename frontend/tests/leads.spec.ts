/**
 * E2E tests for sales lead fields and the Reports screen, against the real
 * backend (the board must already be the sales pipeline - fresh setup seeds
 * it, or run backend/convert_to_sales_board.py).
 */

import { expect, test } from "@playwright/test";
import { loginAsAdmin, openCardEditor } from "./helpers";

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
  const dialog = await openCardEditor(page, title);
  await dialog.getByLabel("Company").fill("E2E Electronics");
  await dialog.getByLabel("Contact").fill("Test Buyer");
  await dialog.getByLabel(/Deal value - current year/).fill("75000");
  await expect(dialog.getByLabel("Currency")).toHaveCount(0);   // Deal Value is always USD
  await expect(dialog.getByLabel(/close date/i)).toHaveCount(0);   // the Closing Date field no longer exists
  await dialog.getByLabel("Lead source").selectOption("Trade show");
  await dialog.getByLabel("Product line").selectOption("Chargers");
  await dialog.getByLabel("Assigned salesperson").selectOption({ label: "e2e_admin" });
  await dialog.getByRole("button", { name: "Save lead" }).click();

  await expect(dialog).toBeHidden();
  await expect(card.getByText("E2E Electronics")).toBeVisible();
  await expect(card.getByText(/75,000/)).toBeVisible();
  await expect(card.getByText("e2e_admin")).toBeVisible();

  // Contact, source and product line live in the details view.
  await card.getByRole("button", { name: `Open details for ${title}` }).click();
  const details = page.getByRole("dialog");
  await expect(details.getByText("Test Buyer")).toBeVisible();
  await expect(details.getByText("Trade show")).toBeVisible();
  await expect(details.getByText("Chargers")).toBeVisible();
  await expect(details.getByText(/close date/i)).toHaveCount(0);
});

test("a deal value needs no currency: there is no currency selector and it saves in USD", async ({ page }) => {
  await page.goto("/");
  const first = page.locator('[data-testid^="card-"]').first();
  // The column is ordered by Deal Value, so the card may move once it has one: follow it by title.
  const title = (await first.getAttribute("data-title")) ?? "";
  const card = page.locator(`[data-testid^="card-"][data-title="${title}"]`);
  await card.getByRole("button", { name: /^open details for/i }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Edit", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("Currency")).toHaveCount(0);
  await dialog.getByLabel(/Deal value - current year/).fill("100");
  await dialog.getByRole("button", { name: "Save lead" }).click();
  await expect(dialog).toBeHidden();
  await expect(card.getByText("$100", { exact: true })).toBeVisible();
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

  await page.getByRole("button", { name: "Board", exact: true }).click();
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
  // The description now lives in the nav button's tooltip, not in a text bar.
  await expect(undo).toHaveAttribute("title", `Remove new card "${title}"`);

  await undo.click();
  // A successful undo shows no banner under the title; the card simply disappears.
  await expect(page.getByText(/^Undone/)).toHaveCount(0);
  await expect(card).toHaveCount(0);
  await expect(undo).toBeDisabled();
});

test("a column lists its cards by Deal Value, highest first, and a card without a value last", async ({ page }) => {
  await page.goto("/");
  const firstColumn = page.locator('[data-testid^="column-"]').first();
  const stamp = Date.now();
  const titles = { none: `Order none ${stamp}`, small: `Order small ${stamp}`, big: `Order big ${stamp}` };
  for (const title of [titles.none, titles.small, titles.big]) {
    await firstColumn.getByRole("button", { name: /add a card/i }).click();
    await firstColumn.getByPlaceholder("Card title").fill(title);
    await firstColumn.getByRole("button", { name: /add card/i }).click();
    await expect(firstColumn.locator(`[data-testid^="card-"][data-title="${title}"]`)).toBeVisible();
  }
  for (const [title, value] of [[titles.small, "900"], [titles.big, "85000"]]) {
    const dialog = await openCardEditor(page, title);
    await dialog.getByLabel(/Deal value - current year/).fill(value);
    await dialog.getByRole("button", { name: "Save lead" }).click();
    await expect(dialog).toBeHidden();
  }
  const order = async () => (await firstColumn.locator('[data-testid^="card-"]').evaluateAll((els) => els.map((e) => e.getAttribute("data-title"))))
    .filter((t) => t?.endsWith(String(stamp)));
  const expected = [titles.big, titles.small, titles.none];
  await expect.poll(order).toEqual(expected);
  await page.reload();
  await expect.poll(order).toEqual(expected); // persisted values give the same order after a reload
});
