/**
 * Browser E2E of importing a workbook exported from one board into a different, newly created, empty board
 * (disposable central environment, playwright.central.config.ts). The exported card_ids do not exist on the new board.
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = process.env.PM_E2E_CENTRAL_API_URL;
if (!API) throw new Error("PM_E2E_CENTRAL_API_URL is not set - run through playwright.central.config.ts");
const PASSWORD = "Str0ng!Passw0rd"; // the disposable seed's test password
const COMPANY = `${API}/api/companies/lity`;

test.describe.configure({ mode: "serial" });

async function admin(request: APIRequestContext) {
  const res = await request.post(`${API}/api/central/login`, { data: { email: "e2e-admin@example.com", password: PASSWORD } });
  return { Authorization: `Bearer ${(await res.json()).access_token}` };
}
const titlesOn = async (request: APIRequestContext, boardId: number) => {
  const board = await (await request.get(`${COMPANY}/boards/${boardId}`, { headers: await admin(request) })).json();
  return board.columns.flatMap((c: { cards: { id: number; title: string }[] }) => c.cards) as { id: number; title: string }[];
};

let targetId = 0;
let sourceIds: number[] = [];
let exported: Buffer;

test("the destination board is new and empty, and the source workbook comes from board 1", async ({ request }) => {
  const headers = await admin(request);
  const created = await request.post(`${COMPANY}/boards`, { headers, data: { title: "E2E Import Target" } });
  expect(created.ok()).toBeTruthy();
  targetId = (await created.json()).id;
  for (const [position, title] of ["1st Contact", "F2F Meeting", "RFI", "Proposal", "Award", "Lost"].entries()) {
    const kind = title === "Award" ? "won" : title === "Lost" ? "lost" : "open";
    const col = await request.post(`${COMPANY}/boards/${targetId}/columns`, { headers, data: { title, position, stage_kind: kind } });
    expect(col.ok(), await col.text()).toBeTruthy();
  }
  expect(await titlesOn(request, targetId)).toEqual([]);
  sourceIds = (await titlesOn(request, 1)).map((c) => c.id);
  const file = await request.get(`${COMPANY}/boards/1/excel/export`, { headers });
  expect(file.ok()).toBeTruthy();
  exported = await file.body();
});

async function importScreen(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "LOGIN" }).click();
  await page.getByLabel("Email").fill("e2e-admin@example.com");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign In" }).click();
  await page.getByRole("button", { name: /Lity/ }).first().click({ timeout: 3000 }).catch(() => {});
  await expect(page.getByTestId("central-board")).toBeVisible();
  await page.locator("#board-select").selectOption({ label: "E2E Import Target" });
  await expect(page.getByRole("heading", { name: "E2E Import Target" })).toBeVisible();
  await page.getByRole("navigation", { name: "Sections" }).getByRole("button", { name: "Database" }).click();
  return page.getByTestId("central-database-screen");
}
const upload = (page: Page) => page.getByLabel("Excel workbook (.xlsx)").setInputFiles({
  name: "exported.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: exported });

test("unmatched card_ids are offered as new cards only after an explicit choice, then previewed again and confirmed", async ({ page, request }) => {
  const screen = await importScreen(page);
  await expect(screen.getByRole("checkbox")).toHaveCount(0); // no hidden checkbox: the choice is offered when it is needed
  await upload(page);
  await screen.getByRole("button", { name: "Preview import" }).click();
  const preview = page.getByRole("region", { name: "Import preview" });
  const prompt = preview.getByTestId("unmatched-prompt");
  await expect(prompt).toContainText("not on this board");
  await expect(prompt).toContainText("old card_id is not reused");
  await expect(preview).toContainText("No card with this card_id exists on this board");
  await expect(preview.getByRole("button", { name: /Confirm import/ })).toBeDisabled(); // no choice, no import
  expect(await titlesOn(request, targetId)).toEqual([]); // a preview writes nothing

  await prompt.getByRole("button", { name: "Create new cards for unmatched IDs" }).click();
  await expect(preview.getByTestId("unmatched-note")).toContainText(`${sourceIds.length} of the new cards come from a card_id that is not on this board`);
  await expect(preview.getByTestId("unmatched-prompt")).toHaveCount(0);
  await expect(preview.getByRole("button", { name: /Confirm import/ })).toBeEnabled();
  expect(await titlesOn(request, targetId)).toEqual([]); // choosing imported nothing; still nothing written
  await preview.getByRole("button", { name: /Confirm import/ }).click();
  await expect(page.getByRole("region", { name: "Import result" })).toContainText(`${sourceIds.length} created, 0 updated`);

  const created = await titlesOn(request, targetId);
  expect(created).toHaveLength(sourceIds.length);
  for (const card of created) expect(sourceIds).not.toContain(card.id); // fresh ids from this database
  expect((await titlesOn(request, 1)).map((c) => c.id)).toEqual(sourceIds); // the source board is untouched
});

test("the same workbook cannot be imported a second time", async ({ page, request }) => {
  const before = (await titlesOn(request, targetId)).length;
  const screen = await importScreen(page);
  await upload(page);
  await screen.getByRole("button", { name: "Preview import" }).click();
  const preview = page.getByRole("region", { name: "Import preview" });
  await expect(preview).toContainText("already imported");
  await expect(preview.getByRole("button", { name: /Confirm import/ })).toBeDisabled();
  // Its cards still exist on this board, so there is nothing to restore and no restore action is offered.
  await expect(preview.getByTestId("restore-unavailable")).toContainText("No card of this workbook is missing from this board");
  await expect(preview.getByRole("button", { name: "Restore missing cards from this workbook" })).toHaveCount(0);
  expect((await titlesOn(request, targetId)).length).toBe(before);
});

test("restoring deleted cards: export the board, delete its cards, import the export and choose to create them", async ({ page, request }) => {
  const headers = await admin(request);
  const board = await (await request.get(`${COMPANY}/boards/${targetId}`, { headers })).json();
  const cards = board.columns.flatMap((c: { cards: { id: number; title: string; version: number }[] }) => c.cards);
  expect(cards.length).toBeGreaterThan(0);
  const file = await request.get(`${COMPANY}/boards/${targetId}/excel/export`, { headers });
  exported = await file.body();
  for (const card of cards) {
    const gone = await request.delete(`${COMPANY}/boards/${targetId}/cards/${card.id}?expected_version=${card.version}`, { headers });
    expect(gone.status()).toBe(204);
  }
  expect(await titlesOn(request, targetId)).toEqual([]);
  const deletedIds = cards.map((c: { id: number }) => c.id);

  const screen = await importScreen(page);
  await upload(page);
  await screen.getByRole("button", { name: "Preview import" }).click();
  const preview = page.getByRole("region", { name: "Import preview" });
  await expect(preview.getByTestId("unmatched-prompt")).toContainText(`${cards.length} rows have a card_id that is not on this board`);
  await preview.getByRole("button", { name: "Create new cards for unmatched IDs" }).click();
  await expect(preview.getByTestId("unmatched-note")).toBeVisible();
  await preview.getByRole("button", { name: /Confirm import/ }).click();
  await expect(page.getByRole("region", { name: "Import result" })).toContainText(`${cards.length} created, 0 updated`);
  const restored = await titlesOn(request, targetId);
  expect(restored.map((c) => c.title).sort()).toEqual(cards.map((c: { title: string }) => c.title).sort());
  for (const card of restored) expect(deletedIds).not.toContain(card.id); // fresh ids, never the deleted ones
});

test("the same workbook is refused by the ledger, and the missing cards can be restored with an explicit action", async ({ page, request }) => {
  const headers = await admin(request);
  // The workbook imported in the previous test now has its cards; delete them again, as the user did.
  const board = await (await request.get(`${COMPANY}/boards/${targetId}`, { headers })).json();
  const cards = board.columns.flatMap((c: { cards: { id: number; title: string; version: number }[] }) => c.cards);
  expect(cards.length).toBeGreaterThan(0);
  for (const card of cards) {
    expect((await request.delete(`${COMPANY}/boards/${targetId}/cards/${card.id}?expected_version=${card.version}`, { headers })).status()).toBe(204);
  }
  const deletedIds = cards.map((c: { id: number }) => c.id);

  const screen = await importScreen(page);
  await upload(page);
  await screen.getByRole("button", { name: "Preview import" }).click();
  const preview = page.getByRole("region", { name: "Import preview" });
  const prompt = preview.getByTestId("restore-prompt");
  await expect(prompt).toContainText("already imported, so the ordinary import is blocked");
  await expect(prompt).toContainText(`${cards.length} cards from it are missing from this board`);
  await expect(preview.getByRole("button", { name: /Confirm import/ })).toBeDisabled(); // the ledger still blocks the ordinary import
  expect(await titlesOn(request, targetId)).toEqual([]);

  await prompt.getByRole("button", { name: "Restore missing cards from this workbook" }).click();
  const mode = preview.getByTestId("restore-mode");
  await expect(mode).toContainText(`${cards.length} cards will be created, each with a new id`);
  await expect(mode).toContainText("No card is updated");
  expect(await titlesOn(request, targetId)).toEqual([]); // choosing restore imported nothing
  await preview.getByRole("button", { name: /Confirm import/ }).click();
  await expect(page.getByRole("region", { name: "Import result" })).toContainText(`${cards.length} created, 0 updated`);
  const restored = await titlesOn(request, targetId);
  expect(restored.map((c) => c.title).sort()).toEqual(cards.map((c: { title: string }) => c.title).sort());
  for (const card of restored) expect(deletedIds).not.toContain(card.id); // fresh ids, never the deleted ones

  // Done: the restored cards exist, so the same workbook offers nothing more.
  await upload(page);
  await screen.getByRole("button", { name: "Preview import" }).click();
  await expect(page.getByRole("region", { name: "Import preview" }).getByTestId("restore-unavailable")).toContainText("Restoring is not possible now");
});
