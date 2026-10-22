/**
 * Documents how Undo and Import Bids interact today (no import undo exists):
 * an import records no Undo entry and leaves imported cards in place, and a
 * manual edit followed by an import of the same field makes Undo answer 409.
 *
 * Runs against the isolated E2E backend (temporary SQLite, PM_E2E=1) with the
 * fictitious workbooks from fixtures/make_bid_fixtures.py. The file name sorts
 * after bid-import.spec.ts on purpose: that spec asserts absolute table counts.
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { deleteBidSpecCards } from "./bid-cleanup";
import { API_BASE_URL, E2E_ADMIN_PASSWORD, E2E_ADMIN_USERNAME, loginAsAdmin } from "./helpers";

test.describe.configure({ mode: "serial" });

const dbPath = process.env.PM_E2E_DB_PATH;
if (!dbPath) throw new Error("PM_E2E_DB_PATH is not set - run through playwright.config.ts");

let fixtures: string;
const fixture = (name: string) => path.join(fixtures, name);

test.beforeAll(() => {
  fixtures = mkdtempSync(path.join(tmpdir(), "pm-e2e-undo-fixtures-"));
  const backend = path.resolve(__dirname, "../../backend");
  execFileSync("uv", ["run", "python", path.resolve(__dirname, "fixtures/make_bid_fixtures.py"), fixtures], {
    cwd: backend,
    env: { ...process.env, PYTHONPATH: backend },
    stdio: "pipe",
  });
});

// Hand-made cards still on the board when a test ends (Undo removes them on the happy path).
const createdCardIds: number[] = [];

test.afterAll(async () => {
  if (fixtures) rmSync(fixtures, { recursive: true, force: true });
  await deleteBidSpecCards(dbPath!, createdCardIds);
});

// --- helpers -----------------------------------------------------------------

function count(table: string): number {
  const db = new DatabaseSync(dbPath!, { readOnly: true });
  try {
    return (db.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get() as { n: number }).n;
  } finally {
    db.close();
  }
}

async function adminSession(request: APIRequestContext) {
  const login = await request.post(`${API_BASE_URL}/api/login`, {
    data: { username: E2E_ADMIN_USERNAME, password: E2E_ADMIN_PASSWORD },
  });
  const headers = { Authorization: `Bearer ${(await login.json()).access_token}` };
  const boards = await (await request.get(`${API_BASE_URL}/api/boards`, { headers })).json();
  return { headers, boardUrl: `${API_BASE_URL}/api/boards/${boards[0].id}`, boardId: boards[0].id as number };
}

async function createCard(request: APIRequestContext, title: string) {
  const { headers, boardUrl } = await adminSession(request);
  const board = await (await request.get(boardUrl, { headers })).json();
  const response = await request.post(`${boardUrl}/cards`, { headers, data: { column_id: board.columns[0].id, title } });
  expect(response.ok()).toBeTruthy();
  const id = (await response.json()).id as number;
  createdCardIds.push(id);
  return id;
}

async function importFile(page: Page, name: string, confirmUpdate = false) {
  await page.goto("/");
  await page.getByRole("button", { name: "Import Bids", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Import Bids" })).toBeVisible();
  await page.getByLabel("Bids report (.xlsx)").setInputFiles(fixture(name));
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  await expect(page.getByRole("heading", { name: "2. Summary" })).toBeVisible();
  if (confirmUpdate) {
    await page.locator('tr[data-row="2"]').getByLabel(/Update card #/).check();
    await page.getByRole("button", { name: "Update preview" }).click();
    await expect(page.getByText("Your decisions changed since this preview")).toHaveCount(0);
  }
  await page.getByLabel("I reviewed this preview and want to apply it to the board.").check();
  await page.getByRole("button", { name: "Confirm import" }).click();
}

async function backToBoard(page: Page) {
  await page.getByRole("button", { name: "Board", exact: true }).click();
  await expect(page.locator('[data-testid^="column-"]').first()).toBeVisible();
}

const cardByTitle = (page: Page, title: string) => page.locator(`[data-testid^="card-"][data-title="${title}"]`);
const undoButton = (page: Page) => page.getByRole("button", { name: "Undo", exact: true });

// --- 1. import and history -----------------------------------------------------------

test("a synthetic workbook imports and the batch appears in the history", async ({ page, request }) => {
  await loginAsAdmin(page, request);
  const batches = count("import_batches");

  await importFile(page, "bids-undo-new.xlsx");
  await expect(page.getByRole("status").filter({ hasText: "completed" })).toContainText("1 created, 0 updated (0 moved), 0 skipped");
  expect(count("import_batches")).toBe(batches + 1);

  await page.getByRole("tab", { name: "History" }).click();
  const batchRow = page.locator("tbody tr").filter({ hasText: "bids-undo-new.xlsx" }).first();
  await expect(batchRow).toContainText("completed");
  await expect(batchRow).toContainText("e2e_admin");

  await backToBoard(page);
  await expect(cardByTitle(page, "E2E Golf")).toBeVisible();
});

// --- 2. an import is not undoable ------------------------------------------------------

test("an import records no Undo entry and Undo leaves the imported card in place", async ({ page, request }) => {
  await loginAsAdmin(page, request);
  const { headers, boardUrl } = await adminSession(request);

  // A manual change gives the admin a known Undo entry; the import must not replace it.
  const title = `Undo baseline ${Date.now()}`;
  await createCard(request, title);
  const manualEntry = `Remove new card "${title}"`;
  const entriesBefore = count("undo_entries");
  expect((await (await request.get(`${boardUrl}/undo`, { headers })).json()).description).toBe(manualEntry);

  await importFile(page, "bids-undo-update.xlsx");
  // The update needs confirmation, so it is left out: the import itself still completes.
  await expect(page.getByRole("status").filter({ hasText: "completed" })).toContainText("0 updated");
  expect(count("undo_entries")).toBe(entriesBefore);
  expect((await (await request.get(`${boardUrl}/undo`, { headers })).json()).description).toBe(manualEntry);

  // Back on the board, Undo still offers the manual change, not the import.
  await backToBoard(page);
  // The entry is shown as the Undo button's tooltip (its title), not as page text.
  await expect(undoButton(page)).toHaveAttribute("title", manualEntry);
  await undoButton(page).click();
  await expect(page.getByText(`Undone: ${manualEntry}`)).toBeVisible();
  await expect(cardByTitle(page, title)).toHaveCount(0);
  await expect(cardByTitle(page, "E2E Golf")).toBeVisible();
  await expect(undoButton(page)).toBeDisabled();
});

// --- 3. manual edit, then an import of the same field -----------------------------------

test("Undo is refused with 409 after an import changed the field that was edited by hand", async ({ page, request }) => {
  await loginAsAdmin(page, request);
  const { headers, boardUrl } = await adminSession(request);
  const board = await (await request.get(boardUrl, { headers })).json();
  const card = board.columns.flatMap((c: { cards: { id: number; title: string }[] }) => c.cards).find((c: { title: string }) => c.title === "E2E Golf");

  const edit = await request.put(`${boardUrl}/cards/${card.id}`, { headers, data: { title: "E2E Golf By Hand" } });
  expect(edit.ok()).toBeTruthy();
  expect((await (await request.get(`${boardUrl}/undo`, { headers })).json()).description).toBe('Undo edit to "E2E Golf"');

  // The import (newer report, same bid) overwrites the title the admin edited.
  await importFile(page, "bids-undo-update.xlsx", true);
  await expect(page.getByRole("status").filter({ hasText: "completed" })).toContainText("1 updated");
  await backToBoard(page);
  await expect(cardByTitle(page, "E2E Golf From Import")).toBeVisible();

  // Through the UI: the HTTP answer is 409, the backend message is shown and the later (import) change survives.
  const undoResponse = page.waitForResponse((r) => r.request().method() === "POST" && r.url() === `${boardUrl}/undo`);
  await undoButton(page).click();
  expect((await undoResponse).status()).toBe(409);
  await expect(page.getByText(/was changed since \(by another user, the AI or an import\)/)).toBeVisible();
  await expect(cardByTitle(page, "E2E Golf From Import")).toBeVisible();
  await expect(cardByTitle(page, "E2E Golf By Hand")).toHaveCount(0);
  await expect(undoButton(page)).toBeDisabled(); // the refused entry is used up

  // And at the API: the exact status and detail the backend defines.
  const again = await request.post(`${boardUrl}/undo`, { headers });
  expect(again.status()).toBe(404);
  expect((await again.json()).detail).toBe("Nothing to undo");
});
