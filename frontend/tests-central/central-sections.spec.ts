/**
 * Browser E2E of the central company screens: the board page holds only the board; Users and Database are their own
 * screens in the top navigation, offered by role; and the board scrolls across the whole window without clipping.
 * Same disposable central environment as central-versions.spec.ts (playwright.central.config.ts).
 */
import { expect, test, type Page } from "@playwright/test";

const PASSWORD = "Str0ng!Passw0rd"; // the disposable seed's test password

async function open(page: Page, email: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "LOGIN" }).click();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign In" }).click();
  await page.getByRole("button", { name: /Lity/ }).first().click({ timeout: 3000 }).catch(() => {});
  await expect(page.getByTestId("central-board")).toBeVisible();
}

const sections = (page: Page) => page.getByRole("navigation", { name: "Sections" });

test("the board page holds only the board for the administrator", async ({ page }) => {
  await open(page, "e2e-admin@example.com");
  await expect(page.getByText("Users of Lity")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Export Current Database" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Download Import Template" })).toHaveCount(0);
  await expect(sections(page).getByRole("button")).toHaveText(["Board", "Reports", "KPIs", "Users", "Products", "Database"]);
  await expect(page.getByRole("button", { name: "Switch company" })).toBeVisible();
});

test("Users opens the invitation and member management screen, and Board returns to the board", async ({ page }) => {
  await open(page, "e2e-admin@example.com");
  await sections(page).getByRole("button", { name: "Users" }).click();
  await expect(page.getByRole("heading", { name: "Users of Lity" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Users" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByTestId("central-board")).toHaveCount(0);
  await sections(page).getByRole("button", { name: "Board" }).click();
  await expect(page.getByTestId("central-board")).toBeVisible();
  await expect(page.getByText("Users of Lity")).toHaveCount(0);
});

test("Database offers both existing downloads and they still download a workbook", async ({ page }) => {
  await open(page, "e2e-admin@example.com");
  await sections(page).getByRole("button", { name: "Database" }).click();
  const screen = page.getByTestId("central-database-screen");
  for (const name of ["Export Current Database", "Download Import Template"]) {
    const download = page.waitForEvent("download");
    await screen.getByRole("button", { name }).click();
    expect((await download).suggestedFilename()).toMatch(/\.xlsx$/);
    await expect(screen.getByRole("status")).toContainText("Downloaded");
  }
});

test("a company Admin gets Database but not Users, and a Sales Member gets neither", async ({ page }) => {
  await open(page, "e2e-other@example.com");
  await expect(sections(page).getByRole("button")).toHaveText(["Board", "Reports", "KPIs", "Database"]);
  await page.getByRole("button", { name: "Sign out" }).click();
  await open(page, "e2e-member@example.com");
  await expect(sections(page).getByRole("button")).toHaveText(["Board", "Reports", "KPIs"]); // reads only
  await expect(page.getByRole("button", { name: /^(Users|Database)$/ })).toHaveCount(0);
  await expect(page.getByText("Users of Lity")).toHaveCount(0);
});

for (const width of [1100, 1280, 1440, 1920]) {
  test(`the board is not clipped at ${width}px: first and last columns are reachable and the page does not scroll sideways`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await open(page, "e2e-admin@example.com");
    const area = page.getByTestId("central-columns");
    const measure = () => area.evaluate((el) => {
      const rect = (e: Element) => { const r = e.getBoundingClientRect(); return { left: r.left, right: r.right }; };
      return { vw: window.innerWidth, doc: document.documentElement.scrollWidth, area: rect(el), first: rect(el.firstElementChild!),
               last: rect(el.lastElementChild!), scrollW: el.scrollWidth, clientW: el.clientWidth };
    });
    await area.evaluate((el) => { el.scrollLeft = 0; });
    const start = await measure();
    expect(start.doc).toBeLessThanOrEqual(start.vw); // no sideways scrolling of the page itself
    expect(start.area.left).toBe(0); // the scroll area is the whole window, not an inset strip
    expect(start.area.right).toBe(start.vw);
    expect(start.first.left).toBeGreaterThanOrEqual(0); // the first column rests inside the window
    await area.evaluate((el) => { el.scrollLeft = el.scrollWidth; });
    const end = await measure();
    expect(end.last.right).toBeLessThanOrEqual(end.vw); // the last column can be scrolled fully into view
    if (start.scrollW > start.clientW) expect(end.last.right).toBeGreaterThan(end.vw - 60);
    else expect(start.last.right).toBeLessThanOrEqual(start.vw); // wide enough: every column is already visible
  });
}
