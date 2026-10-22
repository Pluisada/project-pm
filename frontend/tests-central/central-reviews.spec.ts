/**
 * Browser E2E of the central review markers and the top-navigation controls, against the real central backend with the
 * disposable seed (playwright.central.config.ts). A Sales Member's edit and an Admin's backward move create real review
 * obligations; the UI shows one small marker per obligation on the card, opens it in a dialog and clears only that one.
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const API = process.env.PM_E2E_CENTRAL_API_URL;
if (!API) throw new Error("PM_E2E_CENTRAL_API_URL is not set - run through playwright.central.config.ts");
const PASSWORD = "Str0ng!Passw0rd"; // the disposable seed's test password
const BOARD = `${API}/api/companies/lity/boards/1`;

test.describe.configure({ mode: "serial" });

type ApiCard = { id: number; title: string; column_id: number; version: number };

async function token(request: APIRequestContext, email: string) {
  const res = await request.post(`${API}/api/central/login`, { data: { email, password: PASSWORD } });
  expect(res.ok()).toBeTruthy();
  return { Authorization: `Bearer ${(await res.json()).access_token}` };
}
async function card(request: APIRequestContext, title: string): Promise<ApiCard> {
  const res = await request.get(BOARD, { headers: await token(request, "e2e-admin@example.com") });
  return (await res.json()).columns.flatMap((c: { cards: ApiCard[] }) => c.cards).find((c: ApiCard) => c.title === title);
}
async function put(request: APIRequestContext, email: string, c: ApiCard, path: string, data: object) {
  const res = await request.put(`${BOARD}/cards/${c.id}${path}`, { headers: await token(request, email), data: { ...data, expected_version: c.version } });
  expect(res.ok(), await res.text()).toBeTruthy();
}
async function open(page: Page, email: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "LOGIN" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign In" }).click();
  await page.getByRole("button", { name: /Lity/ }).first().click({ timeout: 3000 }).catch(() => {});
  await expect(page.getByTestId("central-board")).toBeVisible();
}
const cardEl = (page: Page, title: string) => page.locator(`[data-testid^="central-card-"][data-title="${title}"]`);
const markers = (page: Page, title: string) => cardEl(page, title).getByRole("button", { name: /review required/i });

test("the company picker shows the Sales Tracker horses", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "LOGIN" }).click();
  await page.getByLabel("Email").fill("e2e-admin@example.com");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Choose a company" })).toBeVisible();
  const horses = page.getByRole("img", { name: /three racehorses/ });
  await expect(horses).toBeVisible();
  expect(await horses.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true); // really loaded
  await page.getByRole("button", { name: /Lity/ }).first().click();
  await expect(page.getByTestId("central-board")).toBeVisible();
  await page.getByRole("button", { name: "Switch company" }).click(); // and again after switching
  await expect(page.getByRole("img", { name: /three racehorses/ })).toBeVisible();
});

test("a Sales Member's edit shows one marker per reviewer, opens it in a dialog and clears only that obligation", async ({ page, request }) => {
  const target = await card(request, "E2E Review Card");
  await put(request, "e2e-member@example.com", target, "", { details: "changed by the Sales Member", deal_value: 1234 });

  await open(page, "e2e-admin@example.com"); // the Super Admin
  await expect(page.getByRole("region", { name: "Pending reviews" })).toHaveCount(0); // no big section on the board
  const marker = markers(page, "E2E Review Card");
  await expect(marker).toHaveCount(1);
  await expect(marker).toHaveAccessibleName(/Super Admin review required \(Sales Member edit\)/);
  await expect(markers(page, "E2E Edit saved")).toHaveCount(0); // other cards carry none
  await marker.click();
  const dialog = page.getByRole("dialog", { name: /Super Admin review/ });
  await expect(dialog.getByTestId("review-status")).toHaveText("Pending");
  await expect(dialog.getByTestId("review-trigger")).toContainText("By user #");
  await expect(dialog.getByRole("row", { name: /Deal value \(current year\)/ })).toContainText("1234");
  await expect(dialog.getByRole("row", { name: /Details/ })).toContainText("changed by the Sales Member");
  await dialog.getByRole("button", { name: "Clear" }).click();
  await expect(page.getByTestId("review-notice")).toContainText("was cleared");
  await expect(markers(page, "E2E Review Card")).toHaveCount(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // The company Admin's own obligation for the same change is still pending and is cleared separately.
  await page.getByRole("button", { name: "Sign out" }).click();
  await open(page, "e2e-other@example.com");
  const adminMarker = markers(page, "E2E Review Card");
  await expect(adminMarker).toHaveCount(1);
  await expect(adminMarker).toHaveAccessibleName(/^Admin review required/);
  await adminMarker.click();
  await page.getByRole("dialog", { name: /Admin review/ }).getByRole("button", { name: "Clear" }).click();
  await expect(markers(page, "E2E Review Card")).toHaveCount(0);
});

test("a Sales Member gets no review markers or controls", async ({ page, request }) => {
  const target = await card(request, "E2E Review Card");
  await put(request, "e2e-member@example.com", target, "", { details: "second change" }); // pending again for the reviewers
  await open(page, "e2e-member@example.com");
  await expect(cardEl(page, "E2E Review Card")).toBeVisible();
  await expect(page.getByRole("button", { name: /review required/i })).toHaveCount(0);
});

test("an Admin's backward move is a Super Admin review; clearing with a stale version is refused and the marker stays", async ({ page, request }) => {
  let target = await card(request, "E2E Review Back");
  await put(request, "e2e-other@example.com", target, "/position", { column_id: 2, position: 0 });
  target = await card(request, "E2E Review Back");
  await put(request, "e2e-other@example.com", target, "/position", { column_id: 1, position: 0 }); // back: needs the Super Admin's review

  await open(page, "e2e-admin@example.com");
  const marker = markers(page, "E2E Review Back");
  await expect(marker).toHaveAccessibleName(/Super Admin review required \(Move to an earlier stage\)/);
  await marker.click();
  const dialog = page.getByRole("dialog", { name: /Super Admin review/ });
  await expect(dialog.getByTestId("review-trigger")).toContainText("Moved back from");
  // Somebody changes the card while the dialog is open: the version the dialog holds is now stale.
  const fresh = await card(request, "E2E Review Back");
  await put(request, "e2e-other@example.com", fresh, "", { details: "changed meanwhile" });
  await dialog.getByRole("button", { name: "Clear" }).click();
  await expect(dialog.getByTestId("review-error")).toContainText("changed after the review was opened");
  await expect(page.getByTestId("review-notice")).toHaveCount(0); // never claims it was cleared
  await expect(markers(page, "E2E Review Back")).toHaveCount(1);
  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(markers(page, "E2E Review Back")).toHaveCount(1);
});

test("Undo, Manage boards and columns and the AI Assistant are in the top navigation", async ({ page }) => {
  await open(page, "e2e-admin@example.com");
  const header = page.getByTestId("central-header");
  await expect(header.getByRole("button", { name: "Manage boards and columns" })).toBeVisible();
  await expect(page.getByTestId("central-board").getByRole("button", { name: /Manage boards/ })).toHaveCount(0);
  await expect(header.getByRole("button", { name: "AI Assistant" })).toBeVisible();

  // An edit made here makes Undo available, and it appears in the navigation, not above the columns.
  await page.getByRole("button", { name: "Edit E2E Review Back" }).click();
  await page.getByLabel("Details").fill("edited from the browser");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(header.getByTestId("undo-button")).toBeVisible();
  await expect(page.getByTestId("central-board").getByTestId("undo-button")).toHaveCount(0);

  await header.getByRole("button", { name: "AI Assistant" }).click();
  const panel = page.getByRole("complementary", { name: "AI Assistant" });
  await expect(panel).toBeVisible();
  await expect(header.getByRole("button", { name: "Sign out" })).toBeVisible(); // the navigation stays reachable
  await panel.getByPlaceholder(/Ask me to organize/).fill("hello");
  const asked = page.waitForResponse((r) => r.request().method() === "POST" && r.url().endsWith("/boards/1/ai"));
  await panel.getByRole("button", { name: "Send" }).click();
  await asked; // the company endpoint (no model is configured here, so an error is the honest answer)
  await expect(panel.getByText(/Error|AI/).first()).toBeVisible();
  await panel.getByRole("button", { name: "Close AI Assistant" }).click();
  await expect(panel).toHaveCount(0);
  await expect(page.getByTestId("central-board")).toBeVisible();
});

test("Import Cards from Excel is on the Database screen, not the Board", async ({ page }) => {
  await open(page, "e2e-admin@example.com");
  await expect(page.getByRole("heading", { name: "Import Cards from Excel" })).toHaveCount(0);
  await page.getByRole("navigation", { name: "Sections" }).getByRole("button", { name: "Database" }).click();
  const screen = page.getByTestId("central-database-screen");
  await expect(screen.getByRole("heading", { name: "Import Cards from Excel" })).toBeVisible();
  await expect(screen.getByRole("button", { name: "Export Current Database" })).toBeVisible();
  await expect(screen.getByRole("button", { name: "Download Import Template" })).toBeVisible();
});
