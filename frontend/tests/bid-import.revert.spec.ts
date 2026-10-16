/**
 * Revert of Bid imports (Part 23) against the isolated E2E backend and its
 * temporary SQLite file. The workbooks are fictitious (E2E907-E2E915 codes,
 * fixtures/make_bid_fixtures.py), never real reports.
 *
 * Each test owns its bids, so batches only overlap where a test makes them. The
 * file runs before bid-import.spec.ts (alphabetical order), so it removes every
 * card it leaves behind through the admin DELETE API (bid-cleanup.ts); import
 * batches have no delete API and stay as audit history, which is why the other
 * Import Bids specs count batches relative to a baseline.
 */
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import { deleteBidSpecCards } from "./bid-cleanup";
import { API_BASE_URL, E2E_ADMIN_PASSWORD, E2E_ADMIN_USERNAME, loginAsAdmin, loginAsMember } from "./helpers";

const XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

test.describe.configure({ mode: "serial" });

const dbPath = process.env.PM_E2E_DB_PATH;
if (!dbPath) throw new Error("PM_E2E_DB_PATH is not set - run through playwright.config.ts");

let fixtures: string;
const fixture = (name: string) => path.join(fixtures, name);

test.beforeAll(() => {
  fixtures = mkdtempSync(path.join(tmpdir(), "pm-e2e-revert-fixtures-"));
  const backend = path.resolve(__dirname, "../../backend");
  execFileSync("uv", ["run", "python", path.resolve(__dirname, "fixtures/make_bid_fixtures.py"), fixtures], {
    cwd: backend,
    env: { ...process.env, PYTHONPATH: backend },
    stdio: "pipe",
  });
});

test.afterAll(async () => {
  if (fixtures) rmSync(fixtures, { recursive: true, force: true });
  await deleteBidSpecCards(dbPath!);
});

// --- helpers -----------------------------------------------------------------

function query<T>(sql: string, ...params: (string | number)[]): T[] {
  const db = new DatabaseSync(dbPath!, { readOnly: true });
  try {
    return db.prepare(sql).all(...params) as T[];
  } finally {
    db.close();
  }
}

const count = (table: string) => query<{ n: number }>(`SELECT COUNT(*) AS n FROM ${table}`)[0].n;
const lastBatchId = () => query<{ n: number }>("SELECT MAX(id) AS n FROM import_batches")[0].n;
const cardExists = (id: number) => query<{ n: number }>("SELECT COUNT(*) AS n FROM cards WHERE id = ?", id)[0].n === 1;
const cardTitle = (id: number) => query<{ title: string }>("SELECT title FROM cards WHERE id = ?", id)[0]?.title;
const ledger = (batchId: number) =>
  query<{ reverted_at: string | null; revert_outcome: string | null }>(
    "SELECT reverted_at, revert_outcome FROM import_batch_ledgers WHERE import_batch_id = ?",
    batchId
  )[0];
const isReverted = (batchId: number) => ledger(batchId).reverted_at !== null;
const cardIdByCode = (prefix: string) =>
  query<{ card_id: number }>("SELECT card_id FROM card_source_refs WHERE source_code LIKE ?", `${prefix}%`)[0]?.card_id;

/** Every row of every table a revert could touch, to prove a step wrote nothing. */
function snapshot(): string {
  const tables = [
    "cards", "card_bid_details", "card_source_refs", "card_stage_events", "card_import_changes", "card_edit_history",
    "import_batches", "import_batch_ledgers", "import_batch_items", "undo_entries",
  ];
  return JSON.stringify(tables.map((t) => query(`SELECT * FROM ${t} ORDER BY rowid`)));
}

async function adminSession(request: APIRequestContext) {
  const login = await request.post(`${API_BASE_URL}/api/login`, {
    data: { username: E2E_ADMIN_USERNAME, password: E2E_ADMIN_PASSWORD },
  });
  const headers = { Authorization: `Bearer ${(await login.json()).access_token}` };
  const boards = await (await request.get(`${API_BASE_URL}/api/boards`, { headers })).json();
  return { headers, boardUrl: `${API_BASE_URL}/api/boards/${boards[0].id}` };
}

/** Preview and confirm a workbook through the API (setup only; the UI import is covered by bid-import.spec.ts). */
async function importViaApi(request: APIRequestContext, name: string, decisions: object[] = []) {
  const { headers, boardUrl } = await adminSession(request);
  const form = () => ({
    file: { name, mimeType: XLSX, buffer: readFileSync(fixture(name)) },
    decisions: JSON.stringify(decisions),
  });
  const preview = await request.post(`${boardUrl}/imports/preview`, { headers, multipart: form() });
  expect(preview.ok()).toBeTruthy();
  const { preview_token } = await preview.json();
  const confirmed = await request.post(`${boardUrl}/imports`, { headers, multipart: { ...form(), preview_token, confirm: "true" } });
  expect(confirmed.status()).toBe(201);
  const body = await confirmed.json();
  return { batchId: body.batch.id as number, created: body.created_card_ids as number[] };
}

async function editTitle(request: APIRequestContext, cardId: number, title: string) {
  const { headers, boardUrl } = await adminSession(request);
  const response = await request.put(`${boardUrl}/cards/${cardId}`, { headers, data: { title } });
  expect(response.ok()).toBeTruthy();
}

async function openHistory(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Import Bids", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Import Bids" })).toBeVisible();
  await page.getByRole("tab", { name: "History" }).click();
  await expect(page.locator("tbody tr").first()).toBeVisible();
}

const historyRow = (page: Page, batchId: number) =>
  page.locator("tbody tr").filter({ has: page.locator("td:first-child").filter({ hasText: new RegExp(`^${batchId}$`) }) });

const dialog = (page: Page) => page.getByRole("dialog");
const stat = (page: Page, label: string): Locator => dialog(page).locator("dt", { hasText: label }).locator("xpath=following-sibling::dd");
const confirmRevert = (page: Page) => dialog(page).getByRole("button", { name: "Revert import", exact: true });
const dialogAlert = (page: Page) => dialog(page).locator('[role="alert"]').filter({ hasText: /\S/ });

/** Open the revert dialog of a batch and wait for its preview to load. */
async function openRevert(page: Page, batchId: number) {
  await historyRow(page, batchId).getByRole("button", { name: `Revert import #${batchId}` }).click();
  await expect(dialog(page)).toBeVisible();
  await expect(dialog(page).getByText("Loading the revert plan...")).toHaveCount(0);
}

async function closeDialog(page: Page) {
  await dialog(page).getByRole("button", { name: "Cancel" }).click();
  await expect(dialog(page)).toHaveCount(0);
}

const revertPreviewResponse = (page: Page, batchId: number) =>
  page.waitForResponse((r) => r.url().endsWith(`/api/imports/${batchId}/revert-preview`) && r.ok());

// --- 1. import, revert, history ------------------------------------------------------------

test("a synthetic import is reverted: the cards are removed and the history shows who, what and how many", async ({ page, request }) => {
  await loginAsAdmin(page, request);
  const undoEntries = count("undo_entries");

  await page.goto("/");
  await page.getByRole("button", { name: "Import Bids", exact: true }).click();
  await page.getByLabel("Bids report (.xlsx)").setInputFiles(fixture("revert-new.xlsx"));
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  await expect(page.getByRole("heading", { name: "2. Summary" })).toBeVisible();
  await page.getByLabel("I reviewed this preview and want to apply it to the board.").check();
  await page.getByRole("button", { name: "Confirm import" }).click();
  await expect(page.getByRole("status").filter({ hasText: "completed" })).toContainText("2 created");
  const batchId = lastBatchId();
  const cardIds = [cardIdByCode("E2E907"), cardIdByCode("E2E908")];
  expect(cardIds.every((id) => id !== undefined && cardExists(id))).toBe(true);

  await page.getByRole("tab", { name: "History" }).click();
  await openRevert(page, batchId);
  await expect(dialog(page)).toContainText("Reverting cannot be undone with the Kanban Undo");
  await expect(stat(page, "Cards to delete")).toHaveText("2");
  await expect(stat(page, "Cards to restore")).toHaveText("0");
  await confirmRevert(page).click();

  const done = dialog(page).getByRole("status");
  await expect(done).toContainText("Import reverted.");
  await expect(done).toContainText("2 deleted, 0 restored, 0 moves undone, 0 already deleted.");
  await dialog(page).getByRole("button", { name: "Done" }).click();

  // The cards are gone from the database and the ledger records the outcome.
  expect(cardIds.some((id) => cardExists(id!))).toBe(false);
  expect(ledger(batchId)).toMatchObject({ revert_outcome: "reverted" });
  expect(isReverted(batchId)).toBe(true);
  expect(count("undo_entries")).toBe(undoEntries); // a revert is not a Kanban Undo step

  // History: badge, who, and no way to revert twice.
  const row = historyRow(page, batchId);
  await expect(row.getByText("Reverted", { exact: true })).toBeVisible();
  await expect(row).toContainText("by e2e_admin");
  await expect(row.getByRole("button", { name: /Revert import/ })).toHaveCount(0);
  await row.getByRole("button", { name: "Details" }).click();
  const detail = page.getByLabel("Import details");
  await expect(detail).toContainText("Reverted by e2e_admin on");
  await expect(detail).toContainText("2 cards deleted, 0 restored, 0 moves undone, 0 already deleted.");

  // And the board no longer shows them.
  await page.getByRole("button", { name: "Board", exact: true }).click();
  await expect(page.locator('[data-testid^="column-"]').first()).toBeVisible();
  await expect(page.locator('[data-testid^="card-"][data-title="E2E Hotel"]')).toHaveCount(0);
  await expect(page.locator('[data-testid^="card-"][data-title="E2E India"]')).toHaveCount(0);
});

// --- 2. a manual edit after the import ------------------------------------------------------

test("a card edited after the import is a conflict: nothing changes and the batch stays unreverted", async ({ page, request }) => {
  await loginAsAdmin(page, request);
  const { batchId, created } = await importViaApi(request, "revert-edit.xlsx");
  const cardId = created[0];
  await editTitle(request, cardId, "E2E Juliet By Hand");

  await openHistory(page);
  const before = snapshot();
  await openRevert(page, batchId);
  const conflicts = dialog(page).getByRole("table", { name: "Conflicts" });
  await expect(dialog(page)).toContainText(/\d+ conflicts? blocks? this revert/);
  await expect(conflicts).toContainText(`Card #${cardId}`);
  await expect(conflicts).toContainText("E2E Juliet");
  await expect(conflicts).toContainText("E2E Juliet By Hand");
  await expect(conflicts).toContainText("The field was changed after the import.");
  await expect(confirmRevert(page)).toBeDisabled();
  await closeDialog(page);

  // Previewing is read-only: not a single row changed, the card keeps the manual title.
  expect(snapshot()).toBe(before);
  expect(isReverted(batchId)).toBe(false);
  expect(cardTitle(cardId)).toBe("E2E Juliet By Hand");
  await expect(historyRow(page, batchId).getByRole("button", { name: `Revert import #${batchId}` })).toBeVisible();

  // Resolving the conflict by hand makes the same batch revertible.
  await editTitle(request, cardId, "E2E Juliet");
  await openRevert(page, batchId);
  await expect(dialog(page).getByRole("table", { name: "Conflicts" })).toHaveCount(0);
  await expect(confirmRevert(page)).toBeEnabled();
  await closeDialog(page);
});

// --- 3. a later batch must be reverted first ------------------------------------------------

test("a later batch on the same card must be reverted first, then the earlier one can be", async ({ page, request }) => {
  await loginAsAdmin(page, request);
  const a = await importViaApi(request, "revert-dep-a.xlsx");
  const cardId = a.created[0];
  const b = await importViaApi(request, "revert-dep-b.xlsx", [{ row_number: 2, confirm_update: true }]);
  expect(b.batchId).toBeGreaterThan(a.batchId);
  expect(cardTitle(cardId)).toBe("E2E Kilo Renamed");

  await openHistory(page);
  await openRevert(page, a.batchId);
  await expect(dialog(page).getByRole("note")).toContainText(`Revert import #${b.batchId} first`);
  await expect(dialog(page).getByRole("table", { name: "Conflicts" })).toContainText("A later import made a decision about this card.");
  await expect(confirmRevert(page)).toBeDisabled();
  await closeDialog(page);
  expect(isReverted(a.batchId)).toBe(false);

  // Reverting the later batch restores the title the earlier one wrote.
  await openRevert(page, b.batchId);
  await expect(stat(page, "Cards to restore")).toHaveText("1");
  await confirmRevert(page).click();
  await expect(dialog(page).getByRole("status")).toContainText("Import reverted.");
  await dialog(page).getByRole("button", { name: "Done" }).click();
  expect(cardTitle(cardId)).toBe("E2E Kilo");
  expect(isReverted(b.batchId)).toBe(true);

  // Now the earlier batch is free.
  await openRevert(page, a.batchId);
  await expect(dialog(page).getByRole("note")).toHaveCount(0);
  await expect(stat(page, "Cards to delete")).toHaveText("1");
  await confirmRevert(page).click();
  await expect(dialog(page).getByRole("status")).toContainText("Import reverted.");
  await dialog(page).getByRole("button", { name: "Done" }).click();
  expect(cardExists(cardId)).toBe(false);
  expect(isReverted(a.batchId)).toBe(true);
  expect(ledger(a.batchId).reverted_at! >= ledger(b.batchId).reverted_at!).toBe(true);
});

// --- 4. a batch that only observed cards ------------------------------------------------------

test("a batch with only observed items reverts as no_commercial_changes and unlocks the earlier batch", async ({ page, request }) => {
  await loginAsAdmin(page, request);
  const x = await importViaApi(request, "revert-observed.xlsx");
  const cardId = x.created[0];
  // A newer report whose update is not confirmed: the batch only observes the card. (Repeating the
  // identical file would be a no-op that records no batch at all.)
  const y = await importViaApi(request, "revert-observed-update.xlsx");
  expect(y.created).toEqual([]);

  await openHistory(page);
  await openRevert(page, x.batchId);
  await expect(dialog(page).getByRole("note")).toContainText(`Revert import #${y.batchId} first`);
  await expect(confirmRevert(page)).toBeDisabled();
  await closeDialog(page);

  const before = cardTitle(cardId);
  await openRevert(page, y.batchId);
  await expect(dialog(page)).toContainText("This import made no commercial changes. Confirming only marks it as reverted.");
  await expect(stat(page, "Cards to delete")).toHaveText("0");
  await confirmRevert(page).click();
  await expect(dialog(page).getByRole("status")).toContainText("Import marked as reverted. It had no commercial changes to undo.");
  await dialog(page).getByRole("button", { name: "Done" }).click();

  expect(ledger(y.batchId)).toMatchObject({ revert_outcome: "no_commercial_changes" });
  expect(cardExists(cardId)).toBe(true);
  expect(cardTitle(cardId)).toBe(before);
  await expect(historyRow(page, y.batchId).getByText("Reverted", { exact: true })).toBeVisible();
  await historyRow(page, y.batchId).getByRole("button", { name: "Details" }).click();
  await expect(page.getByLabel("Import details")).toContainText("Reverted (no commercial changes)");

  // The earlier batch is revertible again.
  await openRevert(page, x.batchId);
  await expect(dialog(page).getByRole("note")).toHaveCount(0);
  await expect(confirmRevert(page)).toBeEnabled();
  await closeDialog(page);
  expect(isReverted(x.batchId)).toBe(false);
});

// --- 5. double click and second confirmation -----------------------------------------------------

test("a double click sends one revert, and a replayed confirmation is refused with already_reverted", async ({ page, request }) => {
  await loginAsAdmin(page, request);
  const { batchId, created } = await importViaApi(request, "revert-double.xlsx");
  await openHistory(page);
  const preview = revertPreviewResponse(page, batchId);
  await openRevert(page, batchId);
  const fingerprint = (await (await preview).json()).plan_fingerprint as string;

  // Slow the answer down so the second click lands while the first request is in flight.
  let posts = 0;
  await page.route("**/api/imports/*/revert", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    posts += 1;
    await new Promise((resolve) => setTimeout(resolve, 700));
    await route.continue();
  });
  await confirmRevert(page).dblclick();
  await expect(dialog(page).getByRole("status")).toContainText("Import reverted.");
  expect(posts).toBe(1);
  await page.unroute("**/api/imports/*/revert");
  await dialog(page).getByRole("button", { name: "Done" }).click();
  expect(cardExists(created[0])).toBe(false);

  // Replaying the same confirmation (another tab, a retry) changes nothing.
  const { headers } = await adminSession(request);
  const before = snapshot();
  const replay = await request.post(`${API_BASE_URL}/api/imports/${batchId}/revert`, { headers, data: { plan_fingerprint: fingerprint } });
  expect(replay.status()).toBe(409);
  expect((await replay.json()).detail.code).toBe("already_reverted");
  expect(snapshot()).toBe(before);
});

// --- 6. members ---------------------------------------------------------------------------------

test("a member sees no revert action and the API refuses the member", async ({ page, request }) => {
  const { batchId, created } = await importViaApi(request, "revert-new.xlsx");
  await loginAsMember(page, request);
  await page.goto("/");
  await expect(page.locator('[data-testid^="column-"]').first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Import Bids", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Revert import/ })).toHaveCount(0);

  const token = await page.evaluate(() => window.localStorage.getItem("pm_auth_token"));
  const member = { Authorization: `Bearer ${token}` };
  const preview = `${API_BASE_URL}/api/imports/${batchId}/revert-preview`;
  const revert = `${API_BASE_URL}/api/imports/${batchId}/revert`;
  const body = { plan_fingerprint: "0".repeat(64) };
  const before = snapshot();
  expect((await request.get(preview, { headers: member })).status()).toBe(403);
  expect((await request.post(revert, { headers: member, data: body })).status()).toBe(403);
  expect((await request.get(preview)).status()).toBe(401);
  expect((await request.post(revert, { data: body })).status()).toBe(401);
  expect(snapshot()).toBe(before);
  expect(isReverted(batchId)).toBe(false);
  expect(created.every(cardExists)).toBe(true);
});

// --- 7. plan_changed ---------------------------------------------------------------------------

test("a change between preview and confirmation gives plan_changed and a refreshed preview", async ({ page, request }) => {
  await loginAsAdmin(page, request);
  const { batchId, created } = await importViaApi(request, "revert-stale.xlsx");
  const cardId = created[0];
  await openHistory(page);
  await openRevert(page, batchId);
  await expect(confirmRevert(page)).toBeEnabled();

  // Meanwhile somebody edits the card the dialog is about to delete.
  await editTitle(request, cardId, "E2E November Edited");
  const refused = page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith(`/api/imports/${batchId}/revert`));
  await confirmRevert(page).click();
  const response = await refused;
  expect(response.status()).toBe(409);
  expect((await response.json()).detail.code).toBe("plan_changed");

  await expect(dialogAlert(page).first()).toContainText("The data changed since this preview. Nothing was reverted.");
  // The preview was reloaded and now shows the conflict; nothing was deleted.
  await expect(dialog(page).getByRole("table", { name: "Conflicts" })).toContainText("E2E November Edited");
  await expect(confirmRevert(page)).toBeDisabled();
  expect(cardExists(cardId)).toBe(true);
  expect(isReverted(batchId)).toBe(false);

  // After the edit is undone by hand, a fresh review lets the admin confirm.
  await editTitle(request, cardId, "E2E November");
  await dialog(page).getByRole("button", { name: "Review again" }).click();
  await expect(dialog(page).getByRole("table", { name: "Conflicts" })).toHaveCount(0);
  await expect(confirmRevert(page)).toBeEnabled();
  await confirmRevert(page).click();
  await expect(dialog(page).getByRole("status")).toContainText("Import reverted.");
  expect(cardExists(cardId)).toBe(false);
});

// --- 8. network failures ------------------------------------------------------------------------

test("a request that never reached the server is not reported as success and the revert can be redone", async ({ page, request }) => {
  await loginAsAdmin(page, request);
  const { batchId, created } = await importViaApi(request, "revert-net-lost.xlsx");
  await openHistory(page);
  await openRevert(page, batchId);

  await page.route("**/api/imports/*/revert", (route) => (route.request().method() === "POST" ? route.abort("failed") : route.continue()));
  const before = snapshot();
  await confirmRevert(page).click();
  await expect(dialogAlert(page)).toContainText("Could not reach the server. The revert may or may not have been applied.");
  await expect(dialog(page).getByRole("status")).toHaveCount(0);
  await expect(confirmRevert(page)).toBeDisabled(); // no blind second attempt while the outcome is unknown
  expect(snapshot()).toBe(before);

  // The way out is the history, which asks the server: here nothing was applied.
  await page.unroute("**/api/imports/*/revert");
  await dialog(page).getByRole("button", { name: "Check import history" }).click();
  await expect(dialog(page)).toHaveCount(0);
  expect(isReverted(batchId)).toBe(false);
  expect(cardExists(created[0])).toBe(true);
  await expect(historyRow(page, batchId).getByRole("button", { name: `Revert import #${batchId}` })).toBeVisible();

  await openRevert(page, batchId);
  await confirmRevert(page).click();
  await expect(dialog(page).getByRole("status")).toContainText("Import reverted.");
  expect(cardExists(created[0])).toBe(false);
});

test("a revert applied but never acknowledged is not shown as success, and the history tells the truth", async ({ page, request }) => {
  await loginAsAdmin(page, request);
  const { batchId, created } = await importViaApi(request, "revert-net-ambiguous.xlsx");
  await openHistory(page);
  await openRevert(page, batchId);

  // The server performs the revert, then the answer is lost on the way back.
  await page.route("**/api/imports/*/revert", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await route.fetch();
    await route.abort("failed");
  });
  await confirmRevert(page).click();
  await expect(dialogAlert(page)).toContainText("The revert may or may not have been applied.");
  await expect(dialog(page).getByRole("status")).toHaveCount(0);
  await expect(dialog(page)).not.toContainText("Import reverted.");
  await expect(confirmRevert(page)).toBeDisabled();
  await page.unroute("**/api/imports/*/revert");

  await dialog(page).getByRole("button", { name: "Check import history" }).click();
  await expect(dialog(page)).toHaveCount(0);
  const row = historyRow(page, batchId);
  await expect(row.getByText("Reverted", { exact: true })).toBeVisible();
  await expect(row.getByRole("button", { name: /Revert import/ })).toHaveCount(0);
  expect(isReverted(batchId)).toBe(true);
  expect(cardExists(created[0])).toBe(false);
});
