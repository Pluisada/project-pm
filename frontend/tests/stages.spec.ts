/**
 * E2E tests for stage identity (stage_kind) and for the E2E isolation itself.
 */
import { expect, test } from "@playwright/test";
import { API_BASE_URL, loginAsAdmin, loginAsMember } from "./helpers";

const columnByTitle = (page: import("@playwright/test").Page, title: string) =>
  page.locator('[data-testid^="column-"]').filter({
    has: page.locator(`input[aria-label="Column title"][value="${title}"]`),
  });

test("runs against the isolated test backend", async ({ request }) => {
  const health = await (await request.get(`${API_BASE_URL}/api/health`)).json();
  expect(health.test_mode).toBe(true);
  expect(API_BASE_URL).not.toContain(":8000");
});

test("member sees stage titles read-only", async ({ page, request }) => {
  await loginAsMember(page, request);
  await page.goto("/");
  await expect(page.locator('[data-testid^="column-"]').first()).toBeVisible();
  await expect(page.locator('input[aria-label="Column title"]')).toHaveCount(0);
  await expect(page.getByTestId("stage-title").first()).toBeVisible();
});

test("renaming the won stage doesn't change the won count", async ({ page, request }) => {
  await loginAsAdmin(page, request);
  await page.goto("/");

  const readWon = async () => {
    await page.getByRole("button", { name: "Reports" }).click();
    const tile = page.getByTestId("report-kpis").locator("div", { hasText: /^Won/ }).first();
    const value = await tile.locator("p").nth(1).textContent();
    await page.getByRole("button", { name: "Back to Board" }).click();
    return value;
  };

  const wonBefore = await readWon();
  // Hold on to the column by its stable testid: its title is about to change.
  const testId = await columnByTitle(page, "Award").getAttribute("data-testid");
  const awardColumn = page.getByTestId(testId!);
  await expect(awardColumn.getByText("won", { exact: true })).toBeVisible();

  const input = awardColumn.locator('input[aria-label="Column title"]');
  await input.fill("Closed - Won");
  await input.press("Enter");
  await page.reload();
  await expect(input).toHaveValue("Closed - Won");
  await expect(awardColumn.getByText("won", { exact: true })).toBeVisible();

  expect(await readWon()).toBe(wonBefore);

  // Put the title back for the other tests.
  await input.fill("Award");
  await input.press("Enter");
  await page.reload();
  await expect(input).toHaveValue("Award");
});
