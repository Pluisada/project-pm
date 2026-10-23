/**
 * E2E tests for the compact card and its read-first details view.
 *
 * Bid fields only exist on imported cards, and importing needs a workbook, so
 * these tests add an ordinary card through the UI and then enrich the board
 * response in the browser (route interception) with synthetic Bid data. The
 * isolated E2E backend is never given the data.
 */
import { expect, type Page, test } from "@playwright/test";
import { loginAsAdmin } from "./helpers";

const BID = {
  plant: "Manaus",
  customer_type: "OEM",
  business_type: "New business",
  product_category: "Chargers",
  product: "65W GaN",
  solution_type: "Turnkey",
  volume_current_year: 12000,
  volume_next_year: 480000,
  production_start_date: "2027-03-15",
  revenue_current_year_cents: 123456,
  revenue_next_year_cents: 98765432,
  revenue_currency: "USD",
  status_note: "Waiting for sample approval",
  project_lead: "J. Source",
  cpm: "C. Source",
  next_step: "Send quotation",
  next_step_due: "2020-01-01",
  close_reason: null,
  close_reason_note: null,
};

const columns = (page: Page) => page.locator('[data-testid^="column-"]');

async function addCard(page: Page, title: string) {
  const column = columns(page).first();
  await column.getByRole("button", { name: /add a card/i }).click();
  await column.getByPlaceholder("Card title").fill(title);
  await column.getByRole("button", { name: /add card/i }).click();
  await expect(page.locator(`[data-testid^="card-"][data-title="${title}"]`)).toBeVisible();
}

/** Reload with the named card enriched; `patch` is merged over the card in each board response. */
async function reloadWithCardPatch(page: Page, title: string, patch: Record<string, unknown>) {
  await page.route(/\/api\/boards\/\d+$/, async (route) => {
    const response = await route.fetch();
    const board = await response.json();
    for (const column of board.columns) {
      column.cards = column.cards?.map((card: { title: string }) =>
        card.title === title ? { ...card, ...patch } : card
      );
    }
    await route.fulfill({ response, json: board });
  });
  await page.reload();
  await expect(columns(page).first()).toBeVisible();
}

test.beforeEach(async ({ page, request }) => {
  await loginAsAdmin(page, request);
  await page.goto("/");
  await expect(columns(page).first()).toBeVisible();
});

test("the card is a compact read-only summary with no editable fields", async ({ page }) => {
  const title = `Compact ${Date.now()}`;
  await addCard(page, title);
  await reloadWithCardPatch(page, title, {
    company: "Acme Mobile",
    deal_value: 75000,
    currency: "EUR",
    expected_close_date: "2099-01-31",
    salesperson_name: "e2e_admin",
    bid_details: BID,
    source_ref: { source_code: "OPP-0042" },
  });

  const card = page.locator(`[data-testid^="card-"][data-title="${title}"]`);
  await expect(card.locator("input, textarea")).toHaveCount(0);
  await expect(card.getByText("Acme Mobile")).toBeVisible();
  await expect(card.getByText("e2e_admin")).toBeVisible();
  await expect(card.getByText(/75,000/)).toBeVisible();
  await expect(card.getByText("Send quotation")).toBeVisible();
  await expect(card.getByText("Next action overdue")).toBeVisible();
  await expect(card.getByText("OPP-0042")).toBeVisible();
  // Bid-only fields stay off the card.
  await expect(card.getByText("Manaus")).toHaveCount(0);
  await expect(card.getByText("Waiting for sample approval")).toHaveCount(0);
});

test("the details view groups general, Bid and source data and keeps their meanings apart", async ({ page }) => {
  const title = `Details ${Date.now()}`;
  await addCard(page, title);
  await reloadWithCardPatch(page, title, {
    company: "Acme Mobile",
    deal_value: 75000,
    currency: "EUR",
    expected_close_date: "2099-01-31",
    confidence_level: "HIGH",
    bid_details: BID,
    source_ref: { source_code: "OPP-0042" },
  });

  await page.getByRole("button", { name: `Open details for ${title}` }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: title })).toBeVisible();

  // Deal value shows (a legacy "EUR" card reads as USD); Bid revenue is no longer shown.
  await expect(dialog.getByText(/\$75,000/)).toBeVisible();
  await expect(dialog.getByText("$1,234.56")).toHaveCount(0);
  await expect(dialog.getByText("$987,654.32")).toHaveCount(0);
  await expect(dialog.getByText(/Revenue/)).toHaveCount(0);

  // Next action date and production start; the stored closing date is no longer shown.
  await expect(dialog.getByText("Jan 31, 2099")).toHaveCount(0);
  await expect(dialog.getByText("Jan 1, 2020")).toBeVisible();
  await expect(dialog.getByText("Mar 15, 2027")).toBeVisible();

  // The obsolete Bid confidence rows no longer exist.
  await expect(dialog.getByText("Confidence", { exact: true })).toHaveCount(0);
  await expect(dialog.getByText("Source confidence")).toHaveCount(0);
  await expect(dialog.getByText("10%", { exact: true })).toHaveCount(0);
  await expect(dialog.getByText("Project lead (source)")).toBeVisible();
  await expect(dialog.getByText("CPM (source)")).toBeVisible();
  // Null Bid fields are not shown.
  await expect(dialog.getByText("Close reason")).toHaveCount(0);

  // Probability level is the card's own field; the removed Bid percentage and source label are gone.
  await expect(dialog.getByText("Probability level")).toBeVisible();
  await expect(dialog.getByTestId("confidence-badge")).toHaveText("HIGH");
  // The source code stays; the old confidence label is gone.
  await expect(dialog.getByText("OPP-0042")).toBeVisible();
  await expect(dialog.getByText("LOW (10%)")).toHaveCount(0);
});

test("a card without Bid data shows a neutral note and no invented values", async ({ page }) => {
  const title = `Plain ${Date.now()}`;
  await addCard(page, title);
  await page.getByRole("button", { name: `Open details for ${title}` }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("No Bid report data for this opportunity.")).toBeVisible();
  await expect(dialog.getByText("Revenue")).toHaveCount(0);
  await expect(dialog.getByText("Source reference")).toHaveCount(0);
  await expect(dialog.getByText("Deal value")).toHaveCount(0);
});

test("the details view opens by keyboard, traps focus, closes with Escape and restores focus", async ({ page }) => {
  const title = `Keyboard ${Date.now()}`;
  await addCard(page, title);
  const opener = page.getByRole("button", { name: `Open details for ${title}` });
  await opener.focus();
  await page.keyboard.press("Enter");

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Close" })).toBeFocused();

  for (let i = 0; i < 4; i++) {
    await page.keyboard.press("Tab");
    expect(await dialog.evaluate((el) => el.contains(document.activeElement))).toBe(true);
  }

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
});

test("editing from the details view persists and returns to the board", async ({ page }) => {
  const title = `Editable ${Date.now()}`;
  const renamed = `${title} renamed`;
  await addCard(page, title);
  await page.getByRole("button", { name: `Open details for ${title}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Edit", exact: true }).click();

  const form = page.getByRole("dialog", { name: `Edit lead ${title}` });
  await form.getByLabel("Opportunity name").fill(renamed);
  await form.getByLabel("Details").fill("Saved through the edit form");
  await form.getByRole("button", { name: "Save lead" }).click();
  await expect(form).toBeHidden();

  await page.reload();
  await page.getByRole("button", { name: `Open details for ${renamed}` }).click();
  await expect(page.getByRole("dialog").getByText("Saved through the edit form")).toBeVisible();
});
