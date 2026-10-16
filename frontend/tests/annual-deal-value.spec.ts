/**
 * Annual Deal Value end to end, against the isolated E2E backend: both
 * amounts load and save, the card shows their sum, the details view lists the
 * years, and Reports and the Dashboard show total + both years in USD.
 *
 * Deal Value is always USD (no currency selector). The seeded leads have no
 * next-year amount, so the next-year figures below are only this test's card;
 * current-year totals include the seeded leads and are not asserted exactly.
 * The spec is order-dependent on purpose (one card, built up step by step) and
 * runs serially.
 */
import { expect, test, type Page } from "@playwright/test";
import { loginAsAdmin, openCardEditor } from "./helpers";

test.describe.configure({ mode: "serial" });

const YEAR = new Date().getFullYear();
const title = `Annual ${Date.now()}`;
const card = (page: Page) => page.locator(`[data-testid^="card-"][data-title="${title}"]`);

test.beforeEach(async ({ page, request }) => {
  await loginAsAdmin(page, request);
  await page.goto("/");
  await expect(page.locator('[data-testid^="column-"]').first()).toBeVisible();
});

async function setAmounts(page: Page, current: string, next: string) {
  const form = await openCardEditor(page, title);
  await form.getByLabel(/Deal value - current year/).fill(current);
  await form.getByLabel(/Deal value - next year/).fill(next);
  await form.getByLabel("Assigned salesperson").selectOption({ label: "e2e_admin" });
  await form.getByRole("button", { name: "Save lead" }).click();
  await expect(form).toBeHidden();
}

async function openSection(page: Page, name: string, heading: string) {
  await page.getByRole("button", { name, exact: true }).click();
  await expect(page.getByRole("heading", { name: heading })).toBeVisible();
}

test("both annual values save, load back, and the card shows their sum", async ({ page }) => {
  const column = page.locator('[data-testid^="column-"]').first();
  await column.getByRole("button", { name: /add a card/i }).click();
  await column.getByPlaceholder("Card title").fill(title);
  await column.getByRole("button", { name: /add card/i }).click();
  await expect(card(page)).toBeVisible();

  await setAmounts(page, "100000", "50000");
  await expect(card(page).getByText("$150,000")).toBeVisible();
  await expect(card(page).getByText("$100,000")).toHaveCount(0);  // never the two amounts separately

  // Persisted: reload, and the form loads both values with year labels.
  await page.reload();
  await expect(card(page).getByText("$150,000")).toBeVisible();
  const form = await openCardEditor(page, title);
  await expect(form.getByLabel("Currency")).toHaveCount(0);        // no currency choice: always USD
  await expect(form.getByLabel(`Deal value - current year (${YEAR})`)).toHaveValue("100000");
  await expect(form.getByLabel(`Deal value - next year (${YEAR + 1})`)).toHaveValue("50000");
  await form.getByRole("button", { name: "Cancel" }).click();

  // Details view lists each year apart.
  await page.getByRole("button", { name: `Open details for ${title}` }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText(`Deal value ${YEAR}`, { exact: true })).toBeVisible();
  await expect(dialog.getByText("$100,000")).toBeVisible();
  await expect(dialog.getByText(`Deal value ${YEAR + 1}`, { exact: true })).toBeVisible();
  await expect(dialog.getByText("$50,000")).toBeVisible();
  await page.keyboard.press("Escape");
});

test("changing one annual value updates the sum; zero next year shows the current year alone", async ({ page }) => {
  await setAmounts(page, "100000", "25000");
  await expect(card(page).getByText("$125,000")).toBeVisible();
  await setAmounts(page, "100000", "0");
  await expect(card(page).getByText("$100,000")).toBeVisible();
  await setAmounts(page, "100000", "25000");
  await expect(card(page).getByText("$125,000")).toBeVisible();
});

test("Reports show total and both years in USD, and keep the filters", async ({ page }) => {
  await openSection(page, "Reports", "Reports");
  await expect(page.getByTestId("annual-legend")).toContainText(`${YEAR}+${YEAR + 1}`);

  const tiles = page.getByTestId("report-kpis");
  const pipeline = tiles.locator("div", { hasText: /^Open pipeline/ }).first();
  await expect(pipeline).toContainText(new RegExp(`${YEAR} \\$[\\d.,]+[KM]? · ${YEAR + 1} \\$25K`));
  await expect(pipeline.locator("p").filter({ hasText: /[€₹¥₫]|R\$|CN/ })).toHaveCount(0);   // one currency, no per-currency lines

  // Salesperson breakdown: the row carries the same measures.
  const section = page.getByRole("heading", { name: "By salesperson" }).locator("xpath=ancestor::section");
  const row = section.getByRole("row").filter({ hasText: "e2e_admin" });
  await expect(row).toContainText(new RegExp(`${YEAR} \\$[\\d.,]+[KM]? · ${YEAR + 1} \\$25K`));

  // Filter: the test card has no product line, so a product filter removes its next-year $25K.
  await page.getByLabel("Filter by product line").selectOption("Power supplies");
  await expect(pipeline).not.toContainText(`${YEAR + 1} $25K`);
  await page.getByLabel("Filter by product line").selectOption({ label: "All" });
  await expect(pipeline).toContainText(`${YEAR + 1} $25K`);
  // Filter by salesperson (all seeded + test cards belong to e2e_admin).
  await page.getByLabel("Filter by salesperson").selectOption({ label: "e2e_admin" });
  await expect(pipeline).toContainText(`${YEAR + 1} $25K`);
});

test("Dashboard pipeline by stage shows total and both years", async ({ page }) => {
  await openSection(page, "Dashboard", "Executive Dashboard");
  const section = page.getByRole("heading", { name: "Open pipeline by stage (now)" }).locator("xpath=ancestor::section");
  const stage = section.getByRole("listitem").filter({ hasText: "1st Contact" });
  // 1st Contact holds two seeded leads (195,000 + 420,000) plus this test's 100,000 + 25,000.
  await expect(stage).toContainText("$740K");
  await expect(stage).toContainText(`${YEAR} $715K · ${YEAR + 1} $25K`);
  await expect(page.getByTestId("annual-legend")).toContainText(`${YEAR}+${YEAR + 1}`);
  // Aging: the value today column names the two years and shows the sum.
  const aging = page.getByTestId("aging");
  await expect(aging.getByRole("columnheader", { name: new RegExp(`${YEAR}\\+${YEAR + 1}`) })).toBeVisible();
  await expect(aging.getByRole("row").filter({ hasText: title })).toContainText("$125,000");
});

for (const [name, width, height] of [["tablet", 768, 1024], ["mobile", 375, 812]] as const) {
  test(`${name}: Reports and Dashboard fit the screen with the annual measures`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    const open = async (label: string, heading: string) => {
      if (width < 640) {
        await page.getByRole("button", { name: "Open menu" }).click();
        await page.locator("#mobile-nav").getByRole("button", { name: label, exact: true }).click();
      } else {
        await page.getByRole("navigation", { name: "Main" }).getByRole("button", { name: label, exact: true }).click();
      }
      await expect(page.getByRole("heading", { name: heading })).toBeVisible();
    };
    await open("Reports", "Reports");
    await expect(page.getByTestId("report-kpis")).toContainText(`${YEAR + 1} $25K`);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await open("Dashboard", "Executive Dashboard");
    await expect(page.getByTestId("annual-legend")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
