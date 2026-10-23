/**
 * Browser E2E of the Batch 2 changes in the CENTRAL UI: Reports and KPIs navigation, contact fields, Admin promotion
 * (and what the backend refuses), stage colours, ordering by Deal Value and the Undo notice. Same disposable central
 * environment as the other specs here (playwright.central.config.ts + seed_central.py): test users and temporary
 * databases only. The cards it creates have their own titles so the other specs are not disturbed.
 */
import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";

const API = process.env.PM_E2E_CENTRAL_API_URL;
if (!API) throw new Error("PM_E2E_CENTRAL_API_URL is not set - run through playwright.central.config.ts");
const PASSWORD = "Str0ng!Passw0rd"; // the disposable seed's test password
const ADMIN = "e2e-admin@example.com"; // global administrator (Super Admin)
const COMPANY_ADMIN = "e2e-other@example.com";
const MEMBER = "e2e-member@example.com"; // Sales Member
const PROMOTE = "e2e-promote@example.com"; // Sales Member used only by the promotion tests
const LITY = `${API}/api/companies/lity`;
const BOARD = `${LITY}/boards/1`;

test.describe.configure({ mode: "serial" });

// The cards this spec creates are removed at the end, so the other specs (which expect an untouched seed) are not disturbed.
test.afterAll(async ({ request }) => {
  const headers = await auth(request, ADMIN);
  for (const card of await cards(request)) {
    if (card.title.startsWith("B2 ")) await request.delete(`${BOARD}/cards/${card.id}?expected_version=${card.version}`, { headers });
  }
});

async function token(request: APIRequestContext, email: string): Promise<string> {
  const res = await request.post(`${API}/api/central/login`, { data: { email, password: PASSWORD } });
  expect(res.ok()).toBeTruthy();
  return (await res.json()).access_token;
}
const auth = async (request: APIRequestContext, email: string) => ({ Authorization: `Bearer ${await token(request, email)}` });

type ApiCard = { id: number; title: string; column_id: number; version: number; deal_value: number | null; contact_email?: string | null };
async function cards(request: APIRequestContext): Promise<ApiCard[]> {
  const res = await request.get(BOARD, { headers: await auth(request, ADMIN) });
  expect(res.ok()).toBeTruthy();
  return (await res.json()).columns.flatMap((c: { cards: ApiCard[] }) => c.cards);
}
async function memberRole(request: APIRequestContext, email: string): Promise<string> {
  const res = await request.get(`${API}/api/central/companies/lity/members`, { headers: await auth(request, ADMIN) });
  expect(res.ok()).toBeTruthy();
  return (await res.json()).find((m: { email: string }) => m.email === email).role;
}
async function createCard(request: APIRequestContext, title: string, column_id: number, deal_value?: number): Promise<ApiCard> {
  // A Super Admin must name an eligible salesperson: the seeded Sales Member.
  const members = await (await request.get(`${API}/api/central/companies/lity/members`, { headers: await auth(request, ADMIN) })).json();
  const salesperson_id = members.find((m: { email: string }) => m.email === MEMBER).user_id;
  const res = await request.post(`${BOARD}/cards`, { headers: await auth(request, ADMIN), data: { title, column_id, salesperson_id, ...(deal_value === undefined ? {} : { deal_value }) } });
  expect(res.status(), await res.text()).toBe(201);
  return res.json();
}

async function openBoard(page: Page, email = ADMIN) {
  await page.goto("/");
  await page.getByRole("button", { name: "LOGIN" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign In" }).click();
  await page.getByRole("button", { name: /Lity/ }).first().click({ timeout: 3000 }).catch(() => {});
  await expect(page.getByTestId("central-board")).toBeVisible();
}

const sections = (page: Page) => page.getByRole("navigation", { name: "Sections" });
const cardEl = (page: Page, title: string): Locator => page.locator(`[data-testid^="central-card-"][data-title="${title}"]`);
const openDetails = async (page: Page, title: string) => {
  await cardEl(page, title).getByRole("button", { name: `Open details for ${title}` }).click();
  return page.getByRole("dialog");
};

async function dragTo(page: Page, handle: Locator, target: Locator) {
  const from = (await handle.boundingBox())!;
  const to = (await target.boundingBox())!;
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(from.x + from.width / 2 + 12, from.y + from.height / 2 + 12, { steps: 4 });
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 12 });
  await page.mouse.up();
}

// --- Reports and KPIs ---------------------------------------------------------------------------------------------

for (const [role, email, others] of [
  ["Super Admin", ADMIN, ["Users", "Products", "Database"]],
  ["company Admin", COMPANY_ADMIN, ["Database"]],
  ["Sales Member", MEMBER, []],
] as const) {
  test(`${role}: Reports and KPIs are offered and open their screens from the company endpoints`, async ({ page }) => {
    await openBoard(page, email);
    await expect(sections(page).getByRole("button")).toHaveText(["Board", "Reports", "KPIs", ...others]);
    const report = page.waitForRequest((r) => r.url() === `${BOARD}/report`);
    await sections(page).getByRole("button", { name: "Reports" }).click();
    await report;
    await expect(page.getByRole("heading", { name: "Reports", exact: true })).toBeVisible();
    await expect(page.getByTestId("central-board")).toHaveCount(0);
    const kpis = page.waitForRequest((r) => r.url().startsWith(`${BOARD}/kpis/summary`));
    await sections(page).getByRole("button", { name: "KPIs" }).click();
    await kpis;
    await expect(page.getByRole("heading", { name: "Executive Dashboard" })).toBeVisible();
    await expect(page.getByRole("button", { name: "KPIs" })).toHaveAttribute("aria-current", "page");
    await sections(page).getByRole("button", { name: "Board" }).click();
    await expect(page.getByTestId("central-board")).toBeVisible();
  });
}

test("Reports and KPIs never use a legacy endpoint", async ({ page }) => {
  const legacy: string[] = [];
  page.on("request", (r) => { if (/\/api\/boards\//.test(r.url())) legacy.push(r.url()); });
  await openBoard(page);
  await sections(page).getByRole("button", { name: "Reports" }).click();
  await expect(page.getByRole("heading", { name: "Reports", exact: true })).toBeVisible();
  await sections(page).getByRole("button", { name: "KPIs" }).click();
  await expect(page.getByRole("heading", { name: "Executive Dashboard" })).toBeVisible();
  expect(legacy).toEqual([]);
});

test("KPIs show a loading state, then data; a failing endpoint shows an error instead of data", async ({ page }) => {
  await openBoard(page);
  await page.route(`${BOARD}/kpis/summary*`, async (route) => { await new Promise((r) => setTimeout(r, 1500)); await route.continue(); });
  await sections(page).getByRole("button", { name: "KPIs" }).click();
  await expect(page.getByText("Loading KPIs...")).toBeVisible();
  await expect(page.getByText("Loading KPIs...")).toHaveCount(0);
  await page.unroute(`${BOARD}/kpis/summary*`);
  await sections(page).getByRole("button", { name: "Board" }).click();
  await page.route(`${BOARD}/kpis/summary*`, (route) => route.fulfill({ status: 500, json: { detail: "boom" } }));
  await sections(page).getByRole("button", { name: "KPIs" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "boom" })).toBeVisible();
});

test("Reports show an error when the report cannot be loaded, and an empty-valued board still renders", async ({ page }) => {
  await openBoard(page);
  await sections(page).getByRole("button", { name: "Reports" }).click();
  await expect(page.getByRole("heading", { name: "Reports", exact: true })).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toHaveCount(0); // the seeded cards have no values: an empty report is not an error
  await sections(page).getByRole("button", { name: "Board" }).click();
  await page.route(`${BOARD}/report`, (route) => route.fulfill({ status: 500, json: { detail: "boom" } }));
  await sections(page).getByRole("button", { name: "Reports" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "boom" })).toBeVisible();
});

// --- Contact email and telephone ----------------------------------------------------------------------------------

test("contact email and telephone are saved, shown after reopening, and existing cards without them still open", async ({ page, request }) => {
  const card = await createCard(request, "B2 Contact Card", 1);
  await openBoard(page);
  let dialog = await openDetails(page, "B2 Contact Card");
  await expect(dialog.getByText("Contact email")).toHaveCount(0); // an existing record without contact values opens fine
  await dialog.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel("Contact email").fill("buyer@example.com");
  await page.getByLabel("Contact telephone").fill("+55 11 91234-5678");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  dialog = await openDetails(page, "B2 Contact Card");
  await expect(dialog.getByText("buyer@example.com")).toBeVisible();
  await expect(dialog.getByText("+55 11 91234-5678")).toBeVisible();
  await expect(cardEl(page, "B2 Contact Card")).not.toContainText("buyer@example.com"); // not on the compact card
  const saved = (await cards(request)).find((c) => c.id === card.id)!;
  expect(saved.contact_email).toBe("buyer@example.com");
  await page.reload();
  await expect(page.getByTestId("central-board")).toBeVisible();
  const again = await openDetails(page, "B2 Contact Card");
  await expect(again.getByText("buyer@example.com")).toBeVisible();
});

test("an invalid contact email is refused with a message and nothing is saved", async ({ page, request }) => {
  await openBoard(page);
  const dialog = await openDetails(page, "B2 Contact Card");
  await dialog.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel("Contact email").fill("not-an-address");
  await page.getByLabel("Contact telephone").fill("call me");
  const response = page.waitForResponse((r) => r.request().method() === "PUT" && r.url().includes("/cards/"));
  await page.getByRole("button", { name: "Save", exact: true }).click();
  // The browser's own email check may stop the form before the request; either way the card must be unchanged.
  await Promise.race([response, page.waitForTimeout(1500)]);
  const card = (await cards(request)).find((c) => c.title === "B2 Contact Card")!;
  expect(card.contact_email).toBe("buyer@example.com");
  await page.keyboard.press("Escape");
  const direct = await request.put(`${BOARD}/cards/${card.id}`, { headers: await auth(request, ADMIN), data: { contact_email: "nope", expected_version: card.version } });
  expect(direct.status()).toBe(422);
  const phone = await request.put(`${BOARD}/cards/${card.id}`, { headers: await auth(request, ADMIN), data: { contact_phone: "call me", expected_version: card.version } });
  expect(phone.status()).toBe(422);
});

// --- Admin promotion ----------------------------------------------------------------------------------------------

test("the Super Admin promotes a joined member to Admin only after confirming; declining changes nothing", async ({ page, request }) => {
  await openBoard(page);
  await sections(page).getByRole("button", { name: "Users" }).click();
  const select = page.getByRole("combobox", { name: `Role of ${PROMOTE}` });
  await expect(select).toHaveValue("sales_member");
  await expect(select.locator("option")).toHaveText(["Sales Member", "Visitor", "Admin"]);
  page.once("dialog", (d) => d.dismiss());
  await select.selectOption("admin");
  expect(await memberRole(request, PROMOTE)).toBe("sales_member");
  page.once("dialog", (d) => d.accept());
  await select.selectOption("admin");
  await expect(page.getByTestId("members-notice")).toContainText("changed");
  expect(await memberRole(request, PROMOTE)).toBe("admin");
  // Back to Sales Member so the rest of the run keeps a plain member.
  await select.selectOption("sales_member");
  await expect(page.getByTestId("members-notice")).toContainText("changed");
  expect(await memberRole(request, PROMOTE)).toBe("sales_member");
});

test("the backend refuses role changes and Admin/Super Admin invitations that bypass the UI", async ({ request }) => {
  const members = await (await request.get(`${API}/api/central/companies/lity/members`, { headers: await auth(request, ADMIN) })).json();
  const target = members.find((m: { email: string }) => m.email === PROMOTE).user_id;
  const role = `${API}/api/central/companies/lity/members/${target}/role`;
  for (const who of [MEMBER, COMPANY_ADMIN]) {
    const res = await request.put(role, { headers: await auth(request, who), data: { role: "admin" } });
    expect(res.status(), who).toBe(403);
  }
  expect(await memberRole(request, PROMOTE)).toBe("sales_member");
  // A member cannot promote themself either.
  const self = members.find((m: { email: string }) => m.email === MEMBER).user_id;
  const selfRes = await request.put(`${API}/api/central/companies/lity/members/${self}/role`, { headers: await auth(request, MEMBER), data: { role: "admin" } });
  expect(selfRes.status()).toBe(403);
  expect(await memberRole(request, MEMBER)).toBe("sales_member");
  // Not even the Super Admin can invite as Admin or Super Admin; a non-Super Admin cannot invite at all.
  for (const bad of ["admin", "super_admin", "global_admin", "owner"]) {
    const res = await request.post(`${API}/api/central/companies/lity/invitations`, { headers: await auth(request, ADMIN), data: { email: `x-${bad}@example.com`, role: bad } });
    expect(res.status(), bad).toBeGreaterThanOrEqual(400);
    expect(res.status(), bad).toBeLessThan(500);
  }
  for (const who of [MEMBER, COMPANY_ADMIN]) {
    const res = await request.post(`${API}/api/central/companies/lity/invitations`, { headers: await auth(request, who), data: { email: "y@example.com", role: "sales_member" } });
    expect(res.status(), who).toBe(403);
  }
  const pending = await (await request.get(`${API}/api/central/companies/lity/invitations`, { headers: await auth(request, ADMIN) })).json();
  expect(pending.some((p: { role: string }) => p.role === "admin" || p.role === "super_admin")).toBe(false);
});

// --- Stage colours -----------------------------------------------------------------------------------------------

test("stage colours are distinct: open stages use neither the won green nor the lost red", async ({ page }) => {
  await openBoard(page);
  const colours = await page.locator('[data-testid^="central-column-"]').evaluateAll((els) =>
    els.map((el) => ({ title: el.getAttribute("aria-label"), top: getComputedStyle(el).borderTopColor })));
  expect(colours.map((c) => c.title)).toEqual(["1st Contact", "F2F Meeting", "RFI", "Proposal", "Award", "Lost"]);
  const byTitle = Object.fromEntries(colours.map((c) => [c.title, c.top]));
  expect(new Set(Object.values(byTitle)).size).toBe(6); // all six stages differ, RFI and Award included
  expect(byTitle["RFI"]).not.toBe(byTitle["Award"]);
  expect(byTitle["Award"]).toBe("rgb(63, 167, 90)"); // won: green
  expect(byTitle["Lost"]).toBe("rgb(217, 83, 79)"); // lost: red
});

// --- Ordering by Deal Value ---------------------------------------------------------------------------------------

test("a column lists cards by Deal Value (highest first, unvalued last) and a move to another column still works", async ({ page, request }) => {
  const titles = { none: "B2 Order none", small: "B2 Order small", big: "B2 Order big" };
  await createCard(request, titles.none, 3);
  await createCard(request, titles.small, 3, 900);
  const big = await createCard(request, titles.big, 3, 85000);
  await openBoard(page);
  const order = () => page.getByTestId("central-list-3").locator("li").evaluateAll((els) => els.map((e) => e.getAttribute("data-title")));
  await expect.poll(order).toEqual([titles.big, titles.small, titles.none]);
  await page.reload();
  await expect(page.getByTestId("central-board")).toBeVisible();
  await expect.poll(order).toEqual([titles.big, titles.small, titles.none]); // same after a reload
  // Editing a value re-sorts: the small card becomes the largest.
  const small = (await cards(request)).find((c) => c.title === titles.small)!;
  const edit = await request.put(`${BOARD}/cards/${small.id}`, { headers: await auth(request, ADMIN), data: { deal_value: 99000, expected_version: small.version } });
  expect(edit.ok()).toBeTruthy();
  await page.reload();
  await expect.poll(order).toEqual([titles.small, titles.big, titles.none]);
  // Moving the largest card to the next column works and it lands by value there.
  const response = page.waitForResponse((r) => r.request().method() === "PUT" && r.url().endsWith(`/cards/${big.id}/position`));
  await dragTo(page, page.getByTestId(`central-handle-${big.id}`), page.getByTestId("central-list-4"));
  expect((await response).status()).toBe(200);
  await expect.poll(order).toEqual([titles.small, titles.none]);
  await expect(page.getByTestId("central-list-4").locator(`[data-title="${titles.big}"]`)).toBeVisible();
});

// --- Undo notice --------------------------------------------------------------------------------------------------

test("a successful Undo shows no 'Undone' banner but restores the board; failures stay visible", async ({ page, request }) => {
  await openBoard(page);
  const dialog = await openDetails(page, "B2 Order none");
  await dialog.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByLabel("Title").fill("B2 Order none renamed");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(cardEl(page, "B2 Order none renamed")).toBeVisible();
  await page.getByTestId("undo-button").click();
  await expect(cardEl(page, "B2 Order none")).toBeVisible();
  await expect(page.getByText(/^Undone/)).toHaveCount(0);
  await expect(page.getByTestId("undo-notice")).toHaveCount(0);
  // The stale-version / Undo conflict messages are asserted in central-versions.spec.ts ("an Undo conflict is reported").
  void request;
});
