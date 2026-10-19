/**
 * E2E tests for member-vs-admin card permissions: delete, move direction,
 * and the member-edit history badge/modal.
 *
 * Card/column ids are DB-assigned and not stable across environments, so
 * these tests locate elements by role/text/DOM order rather than hardcoded
 * testids.
 */
import { expect, type Locator, type Page, test } from "@playwright/test";
import { dragCardToColumn, loginAsAdmin, loginAsMember, openCardEditor } from "./helpers";

const columns = (page: Page) => page.locator('[data-testid^="column-"]');

const cardWithTitle = (page: Page, title: string) =>
  page.locator(`[data-testid^="card-"][data-title="${title}"]`);

const addCard = async (column: Locator, title: string) => {
  await column.getByRole("button", { name: /add a card/i }).click();
  await column.getByPlaceholder("Card title").fill(title);
  await column.getByRole("button", { name: /add card/i }).click();
};

test.describe("Member card permissions", () => {
  test.beforeEach(async ({ page, request }) => {
    await loginAsMember(page, request);
    await page.goto("/");
    await expect(columns(page).first()).toBeVisible();
  });

  test("member has no delete button on a card they created", async ({ page }) => {
    const title = `Member card ${Date.now()}`;
    await addCard(columns(page).first(), title);

    const card = cardWithTitle(page, title);
    await expect(card).toBeVisible();
    await expect(card.getByRole("button", { name: /delete/i })).toHaveCount(0);
  });

  test("member can move a card forward but not backward", async ({ page }) => {
    const title = `Forward-only card ${Date.now()}`;
    const firstColumn = columns(page).first();
    const secondColumn = columns(page).nth(1);
    await addCard(firstColumn, title);

    const card = cardWithTitle(page, title);
    await dragCardToColumn(page, card, secondColumn);
    await expect(secondColumn.locator(`[data-testid^="card-"][data-title="${title}"]`)).toBeVisible();

    // Now try to drag it back to the first (earlier) column.
    await dragCardToColumn(page, card, firstColumn);

    await expect(page.getByText(/members can only move cards forward/i)).toBeVisible();
    await expect(secondColumn.locator(`[data-testid^="card-"][data-title="${title}"]`)).toBeVisible();
  });

  test("editing a card's content shows the M badge and its history", async ({ page }) => {
    const originalTitle = `Original title ${Date.now()}`;
    const newTitle = `Edited title ${Date.now()}`;
    await addCard(columns(page).first(), originalTitle);

    const dialog = await openCardEditor(page, originalTitle);
    await dialog.getByLabel("Opportunity name").fill(newTitle);
    await dialog.getByRole("button", { name: "Save lead" }).click();
    await expect(dialog).toBeHidden();

    const editedCard = cardWithTitle(page, newTitle);
    const historyButton = editedCard.getByRole("button", { name: /view edit history/i });
    await expect(historyButton).toBeVisible();

    await historyButton.click();
    const historyDialog = page.getByRole("dialog");
    await expect(historyDialog).toBeVisible();
    await expect(historyDialog.getByRole("heading", { name: newTitle })).toBeVisible();
    // The before -> after diff line contains both the old and new title.
    await expect(historyDialog.locator("dd")).toContainText(originalTitle);
    await expect(historyDialog.locator("dd")).toContainText(newTitle);

    await historyDialog.getByRole("button", { name: /close/i }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });
});

test("a member sees no Edit action on a card that is not assigned to them", async ({ browser, page, request }) => {
  const title = `Unassigned card ${Date.now()}`;
  // An admin-created card is unassigned, so only admins may edit it.
  const adminPage = await browser.newPage();
  await loginAsAdmin(adminPage, request);
  await adminPage.goto("/");
  await addCard(columns(adminPage).first(), title);
  await expect(cardWithTitle(adminPage, title)).toBeVisible();
  await adminPage.close();

  await loginAsMember(page, request);
  await page.goto("/");
  await page.getByRole("button", { name: `Open details for ${title}` }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: title })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

test.describe("Admin card permissions", () => {
  test.beforeEach(async ({ page, request }) => {
    await loginAsAdmin(page, request);
    await page.goto("/");
    await expect(columns(page).first()).toBeVisible();
  });

  test("admin can delete a card", async ({ page }) => {
    const title = `Admin-deletable card ${Date.now()}`;
    await addCard(columns(page).first(), title);

    const card = cardWithTitle(page, title);
    await card.getByRole("button", { name: /delete/i }).click();
    await expect(cardWithTitle(page, title)).toHaveCount(0);
  });

  test("admin editing a card does not show the M badge", async ({ page }) => {
    const originalTitle = `Admin original ${Date.now()}`;
    const newTitle = `Admin edited ${Date.now()}`;
    await addCard(columns(page).first(), originalTitle);

    const dialog = await openCardEditor(page, originalTitle);
    await dialog.getByLabel("Opportunity name").fill(newTitle);
    await dialog.getByRole("button", { name: "Save lead" }).click();
    await expect(dialog).toBeHidden();

    const editedCard = cardWithTitle(page, newTitle);
    await expect(editedCard).toBeVisible();
    await expect(editedCard.getByRole("button", { name: /view edit history/i })).toHaveCount(0);
  });

  test("admin can move a card backward", async ({ page }) => {
    const title = `Admin backward card ${Date.now()}`;
    const firstColumn = columns(page).first();
    const secondColumn = columns(page).nth(1);
    await addCard(firstColumn, title);

    const card = cardWithTitle(page, title);
    await dragCardToColumn(page, card, secondColumn);
    await expect(secondColumn.locator(`[data-testid^="card-"][data-title="${title}"]`)).toBeVisible();

    await dragCardToColumn(page, cardWithTitle(page, title), firstColumn);
    await expect(firstColumn.locator(`[data-testid^="card-"][data-title="${title}"]`)).toBeVisible();
  });
});
