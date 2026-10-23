/**
 * Browser E2E of optimistic concurrency in the CENTRAL UI, against a real central-mode backend with disposable
 * identity and company databases (playwright.central.config.ts + seed_central.py). "Someone else" is a second company
 * Admin who changes a card through the API after the page loaded, so the page keeps showing the version it had.
 */
import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

/** Edit and Delete live in the card's details dialog: open it, then use the action. */
async function cardAction(page: Page, title: string, action: "Edit" | "Delete") {
  await page.locator(`[data-testid^="central-card-"][data-title="${title}"]`).getByRole("button", { name: `Open details for ${title}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: action, exact: true }).click();
}

const API = process.env.PM_E2E_CENTRAL_API_URL;
if (!API) throw new Error("PM_E2E_CENTRAL_API_URL is not set - run through playwright.central.config.ts");
const PASSWORD = "Str0ng!Passw0rd"; // the disposable seed's test password
const BOARD = `${API}/api/companies/lity/boards/1`;

test.describe.configure({ mode: "serial" });

type ApiCard = { id: number; title: string; column_id: number; version: number };

async function token(request: APIRequestContext, email: string): Promise<string> {
  const res = await request.post(`${API}/api/central/login`, { data: { email, password: PASSWORD } });
  expect(res.ok()).toBeTruthy();
  return (await res.json()).access_token;
}

async function cards(request: APIRequestContext): Promise<ApiCard[]> {
  const res = await request.get(BOARD, { headers: { Authorization: `Bearer ${await token(request, "e2e-admin@example.com")}` } });
  expect(res.ok()).toBeTruthy();
  return (await res.json()).columns.flatMap((c: { cards: ApiCard[] }) => c.cards);
}

const cardNamed = async (request: APIRequestContext, title: string) => (await cards(request)).find((c) => c.title === title)!;

/** A change by another person, made with the current version (so it succeeds) while the page still shows the old one. */
async function someoneElseRetitles(request: APIRequestContext, from: string, to: string) {
  const card = await cardNamed(request, from);
  const res = await request.put(`${BOARD}/cards/${card.id}`, {
    headers: { Authorization: `Bearer ${await token(request, "e2e-other@example.com")}` },
    data: { title: to, expected_version: card.version },
  });
  expect(res.ok()).toBeTruthy();
}

async function openBoard(page: Page, email = "e2e-admin@example.com") {
  await page.goto("/");
  await page.getByRole("button", { name: "LOGIN" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign In" }).click();
  const lity = page.getByRole("button", { name: /Lity/ }).first(); // several companies: pick Lity; a member of one only is taken straight in
  await lity.click({ timeout: 3000 }).catch(() => {});
  await expect(page.getByTestId("central-board")).toBeVisible();
}

const cardEl = (page: Page, title: string): Locator => page.locator(`[data-testid^="central-card-"][data-title="${title}"]`);

/** A real pointer drag (dnd-kit needs movement past its activation distance, then a drop over the target). */
async function dragTo(page: Page, handle: Locator, target: Locator) {
  const from = (await handle.boundingBox())!;
  const to = (await target.boundingBox())!;
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(from.x + from.width / 2 + 12, from.y + from.height / 2 + 12, { steps: 4 });
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 12 });
  await page.mouse.up();
}

test("edits a card and saves it with the version it was opened on", async ({ page, request }) => {
  await openBoard(page);
  const before = await cardNamed(request, "E2E Edit");
  await cardAction(page, "E2E Edit", "Edit");
  await page.getByLabel("Title").fill("E2E Edit saved");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(cardEl(page, "E2E Edit saved")).toBeVisible();
  const after = await cardNamed(request, "E2E Edit saved");
  expect(after.id).toBe(before.id);
  expect(after.version).toBe(before.version + 1);
});

test("a stale edit is refused with the conflict message and keeps what was typed", async ({ page, request }) => {
  await openBoard(page);
  await expect(cardEl(page, "E2E Stale Edit")).toBeVisible();
  await someoneElseRetitles(request, "E2E Stale Edit", "E2E Stale Edit by someone else");
  const conflict = page.waitForResponse((r) => r.request().method() === "PUT" && r.url().includes("/cards/"));
  await cardAction(page, "E2E Stale Edit", "Edit");
  await page.getByLabel("Title").fill("E2E Stale Edit mine");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const response = await conflict;
  expect(response.status()).toBe(409);
  expect((await response.json()).detail.code).toBe("card_version_conflict");
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText("changed by someone else");
  await expect(page.getByLabel("Title")).toHaveValue("E2E Stale Edit mine"); // nothing typed is lost
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
  const server = await cardNamed(request, "E2E Stale Edit by someone else");
  expect(server.title).toBe("E2E Stale Edit by someone else"); // the other person's change was not overwritten
  expect((await cards(request)).some((c) => c.title === "E2E Stale Edit mine")).toBe(false);
});

test("a stale move puts the card back where the server has it", async ({ page, request }) => {
  await openBoard(page);
  const card = await cardNamed(request, "E2E Stale Move");
  await expect(cardEl(page, "E2E Stale Move")).toBeVisible();
  await someoneElseRetitles(request, "E2E Stale Move", "E2E Stale Move by someone else");
  const conflict = page.waitForResponse((r) => r.request().method() === "PUT" && r.url().endsWith(`/cards/${card.id}/position`));
  await dragTo(page, page.getByTestId(`central-handle-${card.id}`), page.getByTestId("central-list-2"));
  expect((await conflict).status()).toBe(409);
  await expect(page.getByTestId("move-error")).toContainText("changed by someone else");
  // After the re-read the card is in its first column, now with the other person's title; nothing moved on the server.
  await expect(page.getByTestId("central-list-1").locator(`[data-title="E2E Stale Move by someone else"]`)).toBeVisible();
  await expect(page.getByTestId("central-list-2").locator("li")).toHaveCount(0);
  expect((await cardNamed(request, "E2E Stale Move by someone else")).column_id).toBe(card.column_id);
});

test("a stale delete keeps the card on the board and on the server", async ({ page, request }) => {
  await openBoard(page);
  await expect(cardEl(page, "E2E Stale Delete")).toBeVisible();
  await someoneElseRetitles(request, "E2E Stale Delete", "E2E Stale Delete by someone else");
  const conflict = page.waitForResponse((r) => r.request().method() === "DELETE");
  await cardAction(page, "E2E Stale Delete", "Delete");
  await page.getByRole("button", { name: "Confirm delete" }).click();
  expect((await conflict).status()).toBe(409);
  await expect(page.getByTestId("delete-notice")).toContainText("was not deleted");
  await expect(cardEl(page, "E2E Stale Delete by someone else")).toBeVisible();
  expect((await cards(request)).some((c) => c.title === "E2E Stale Delete by someone else")).toBe(true);
});

test("an Undo conflict is reported and does not claim success", async ({ page, request }) => {
  await openBoard(page);
  await cardAction(page, "E2E Undo Conflict", "Edit");
  await page.getByLabel("Title").fill("E2E Undo Conflict edited");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(cardEl(page, "E2E Undo Conflict edited")).toBeVisible();
  await expect(page.getByTestId("undo-button")).toBeVisible();
  // Another person changes the same card after the edit: the admin's Undo entry is now out of date.
  await someoneElseRetitles(request, "E2E Undo Conflict edited", "E2E Undo Conflict by someone else");
  const undo = page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/undo"));
  await page.getByTestId("undo-button").click();
  const response = await undo;
  expect(response.status()).toBe(409);
  expect((await response.json()).detail.code).toBe("card_version_conflict");
  await expect(page.getByTestId("undo-notice")).toContainText("nothing was undone");
  await expect(page.getByText(/^Undone/)).toHaveCount(0);
  await expect(cardEl(page, "E2E Undo Conflict by someone else")).toBeVisible(); // the board shows the server's state
  expect((await cardNamed(request, "E2E Undo Conflict by someone else")).title).toBe("E2E Undo Conflict by someone else");
});

test("the server refuses card writes that carry no version (428) and changes nothing", async ({ request }) => {
  const headers = { Authorization: `Bearer ${await token(request, "e2e-admin@example.com")}` };
  const card = await cardNamed(request, "E2E Edit saved");
  const update = await request.put(`${BOARD}/cards/${card.id}`, { headers, data: { title: "no version" } });
  const move = await request.put(`${BOARD}/cards/${card.id}/position`, { headers, data: { column_id: 2, position: 0 } });
  const remove = await request.delete(`${BOARD}/cards/${card.id}`, { headers });
  for (const response of [update, move, remove]) {
    expect(response.status()).toBe(428);
    expect((await response.json()).detail.code).toBe("card_version_required");
  }
  const after = await cardNamed(request, "E2E Edit saved");
  expect(after).toMatchObject({ id: card.id, column_id: card.column_id, version: card.version });
});
