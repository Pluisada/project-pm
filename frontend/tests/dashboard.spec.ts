/**
 * Executive Dashboard (Part 21), against the isolated E2E backend: a real win
 * recorded through the board, its closing value kept after the card is edited,
 * coverage, and period filters.
 */
import { expect, test, type Page } from "@playwright/test";
import { dragCardToColumn, loginAsAdmin, openCardEditor } from "./helpers";

const columns = (page: Page) => page.locator('[data-testid^="column-"]');

async function openDashboard(page: Page) {
  await page.getByRole("button", { name: "Dashboard", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Executive Dashboard" })).toBeVisible();
  await expect(page.getByTestId("dashboard-kpis")).toBeVisible();
}

async function wonCount(page: Page): Promise<number> {
  const tile = page.getByTestId("dashboard-kpis").locator("div", { hasText: /^Won/ }).first();
  return Number(await tile.locator("p").nth(1).textContent());
}

async function setLeadValue(page: Page, title: string, value: string) {
  const dialog = await openCardEditor(page, title);
  await dialog.getByLabel(/Deal value - current year/).fill(value);
  await dialog.getByRole("button", { name: "Save lead" }).click();
  await expect(dialog).toBeHidden();
}

test.beforeEach(async ({ page, request }) => {
  await loginAsAdmin(page, request);
  await page.goto("/");
});

test("records a win at its closing value and keeps it after the card is edited", async ({ page }) => {
  const title = `Dashboard deal ${Date.now()}`;
  await columns(page).first().getByRole("button", { name: /add a card/i }).click();
  await columns(page).first().getByPlaceholder("Card title").fill(title);
  await columns(page).first().getByRole("button", { name: /add card/i }).click();
  await setLeadValue(page, title, "12345");

  await openDashboard(page);
  const before = await wonCount(page);
  await page.getByRole("button", { name: "Board", exact: true }).click();

  const awardColumn = columns(page).nth(4);
  await dragCardToColumn(page, page.locator(`[data-testid^="card-"][data-title="${title}"]`), awardColumn);
  await expect(awardColumn.locator(`[data-testid^="card-"][data-title="${title}"]`)).toBeVisible();

  await openDashboard(page);
  expect(await wonCount(page)).toBe(before + 1);
  const wonRow = page.getByTestId("closing-won");
  await expect(wonRow).toContainText("12,345");

  // Edit the value after closing: history keeps 12,345; today's value is separate.
  await page.getByRole("button", { name: "Board", exact: true }).click();
  await setLeadValue(page, title, "99999");
  await openDashboard(page);
  const cells = page.getByTestId("closing-won").locator("td");
  await expect(cells.nth(2)).toContainText("12,345");
  await expect(cells.nth(2)).not.toContainText("99,999");
  await expect(cells.nth(5)).toContainText("99,999");
});

test("shows coverage and data quality, and re-queries when the period changes", async ({ page }) => {
  await openDashboard(page);
  // The isolated database starts tracking at setup time, after the last 30 days began.
  await expect(page.getByTestId("coverage")).toContainText("Partial history");
  await expect(page.getByTestId("data-quality")).toContainText("Stage tracking started");
  await expect(page.getByTestId("aging")).toBeVisible();
  // The previous 30 days ended before tracking started: no data, not zero.
  await expect(page.getByTestId("dashboard-kpis")).toContainText("Previous period: not tracked");
  await expect(page.getByTestId("closing-won").locator("td").last()).toHaveText("Not tracked");

  await page.getByLabel("Period", { exact: true }).selectOption("custom");
  await expect(page.getByText("Choose a start and an end date")).toBeVisible();
  await expect(page.getByTestId("dashboard-kpis")).toHaveCount(0); // no stale numbers

  await page.getByLabel("Start date").fill("2020-01-01");
  await page.getByLabel("End date").fill("2020-01-31");
  await expect(page.getByTestId("dashboard-kpis")).toBeVisible();
  expect(await wonCount(page)).toBe(0);
  await expect(page.getByTestId("closing-won").locator("td").nth(2)).toHaveText("-"); // absent, not "0"
  await expect(page.getByTestId("dashboard-kpis")).toContainText("no opportunity was won or lost in the period");
});

test("shows the API's message for an invalid range", async ({ page }) => {
  await openDashboard(page);
  await page.getByLabel("Period", { exact: true }).selectOption("custom");
  await page.getByLabel("Start date").fill("2026-02-10");
  await page.getByLabel("End date").fill("2026-02-01");
  // (Next.js renders its own empty role="alert" route announcer too.)
  await expect(page.getByRole("alert").filter({ hasText: "start must not be after end" })).toBeVisible();
  await expect(page.getByTestId("dashboard-kpis")).toHaveCount(0);
});
