/**
 * Shared e2e test helpers for bootstrapping an authenticated session.
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import type { APIRequestContext, Locator, Page } from "@playwright/test";

// Set by playwright.config.ts for the isolated E2E backend. No fallback: a
// missing value must fail, never quietly hit the development backend.
const configuredApiUrl = process.env.PM_E2E_API_URL;
if (!configuredApiUrl) {
  throw new Error("PM_E2E_API_URL is not set - run E2E tests through playwright.config.ts");
}
export const API_BASE_URL = configuredApiUrl;
export const E2E_ADMIN_USERNAME = "e2e_admin";
export const E2E_ADMIN_PASSWORD = "E2e_admin_password123!";
export const E2E_MEMBER_USERNAME = "e2e_member";
export const E2E_MEMBER_PASSWORD = "E2e_member_password123!";

/**
 * Ensure the e2e admin account exists (running first-run setup if this is a
 * fresh database) and return a valid access token for it.
 */
async function getAdminToken(request: APIRequestContext): Promise<string> {
  const setupResponse = await request.post(`${API_BASE_URL}/api/setup`, {
    headers: { "X-Setup-Token": process.env.PM_E2E_SETUP_TOKEN ?? "" },
    data: { username: E2E_ADMIN_USERNAME, password: E2E_ADMIN_PASSWORD },
  });
  if (setupResponse.ok()) {
    return (await setupResponse.json()).access_token;
  }

  const loginResponse = await request.post(`${API_BASE_URL}/api/login`, {
    data: { username: E2E_ADMIN_USERNAME, password: E2E_ADMIN_PASSWORD },
  });
  if (!loginResponse.ok()) {
    throw new Error(
      "Could not bootstrap or log in as the e2e admin - was setup already " +
        "completed with different credentials on this backend?"
    );
  }
  return (await loginResponse.json()).access_token;
}

/**
 * Seed localStorage with a valid admin session before the page loads, so
 * tests that aren't about the auth flow itself can start already logged in.
 */
export async function loginAsAdmin(page: Page, request: APIRequestContext): Promise<void> {
  const token = await getAdminToken(request);
  await page.addInitScript(
    ({ token, username }) => {
      window.localStorage.setItem("pm_auth_token", token);
      window.localStorage.setItem("pm_auth_user", username);
      window.localStorage.setItem("pm_auth_role", "admin");
    },
    { token, username: E2E_ADMIN_USERNAME }
  );
}

/**
 * Run the operator CLI (backend/admin_cli.py) against the isolated E2E
 * database. The CLI is the supported provisioning path: it takes the password
 * on stdin, applies the password policy, creates the account email-verified
 * and only accepts the reserved e2e_* usernames when PM_E2E=1. That mode makes
 * the backend refuse any database outside the system temp directory, so this
 * cannot reach pm.db or a commercial database. There is no HTTP bypass.
 */
function runAdminCli(args: string[], stdin?: string): { status: number; output: string } {
  const dbPath = process.env.PM_E2E_DB_PATH;
  const secretKey = process.env.PM_E2E_SECRET_KEY;
  if (!dbPath || !secretKey) {
    throw new Error("PM_E2E_DB_PATH / PM_E2E_SECRET_KEY are not set - run E2E tests through playwright.config.ts");
  }
  try {
    const output = execFileSync("uv", ["run", "python", "admin_cli.py", ...args], {
      cwd: path.resolve(__dirname, "../../backend"),
      input: stdin,
      encoding: "utf8",
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, PM_E2E: "1", DATABASE_URL: `sqlite:///${dbPath}`, SECRET_KEY: secretKey },
    });
    return { status: 0, output };
  } catch (err) {
    const e = err as { status?: number; stdout?: string; stderr?: string };
    return { status: e.status ?? 1, output: `${e.stdout ?? ""}${e.stderr ?? ""}` };
  }
}

let memberProvisioned = false;

/**
 * Ensure the e2e member account exists (verified, role member) and return a
 * valid access token for it. The admin API only invites by email, so the
 * account is created through the operator CLI instead (see runAdminCli): the
 * CLI only creates admins, hence create-then-demote.
 */
async function getMemberToken(request: APIRequestContext): Promise<string> {
  await getAdminToken(request); // the database must be set up (an admin exists) first
  if (!memberProvisioned) {
    const created = runAdminCli(
      ["create", "--username", E2E_MEMBER_USERNAME, "--password-stdin"],
      `${E2E_MEMBER_PASSWORD}\n`
    );
    if (created.status === 0) {
      const demoted = runAdminCli(["demote", "--username", E2E_MEMBER_USERNAME, "--yes"]);
      if (demoted.status !== 0) {
        throw new Error(`Could not demote the e2e member: ${demoted.output.trim()}`);
      }
    } else if (!/already exists/.test(created.output)) {
      throw new Error(`Could not provision the e2e member: ${created.output.trim()}`);
    }
    memberProvisioned = true;
  }

  const loginResponse = await request.post(`${API_BASE_URL}/api/login`, {
    data: { username: E2E_MEMBER_USERNAME, password: E2E_MEMBER_PASSWORD },
  });
  if (!loginResponse.ok()) {
    throw new Error("Could not log in as the e2e member");
  }
  return (await loginResponse.json()).access_token;
}

/**
 * Seed localStorage with a valid member session before the page loads, so
 * tests that aren't about the auth flow itself can start already logged in.
 */
export async function loginAsMember(page: Page, request: APIRequestContext): Promise<void> {
  const token = await getMemberToken(request);
  await page.addInitScript(
    ({ token, username }) => {
      window.localStorage.setItem("pm_auth_token", token);
      window.localStorage.setItem("pm_auth_user", username);
      window.localStorage.setItem("pm_auth_role", "member");
    },
    { token, username: E2E_MEMBER_USERNAME }
  );
}

/**
 * Drag a card (by its `data-testid^="card-"` container) into a target
 * column via a real mouse gesture (dnd-kit needs actual pointer events, not
 * a synthetic HTML5 drag-and-drop).
 *
 * Dragging starts from the card's dedicated drag handle (near the card's
 * left edge), not the card's center - title/details are editable fields, so
 * the rest of the card is reserved for clicking into them. dnd-kit tracks
 * the dragged item by the cursor's *delta*, preserving the initial offset
 * between the cursor and the item's own center throughout the drag - so the
 * mouse target has to be shifted by that same offset, or the item's actual
 * (not the cursor's) center overshoots into the next column.
 *
 * Lead cards are tall, so a newly added card can sit below the viewport;
 * it's scrolled to the middle of the view first, and the drag moves horizontally at the
 * card's own height (columns share one grid row, so that height is inside
 * the target column too) rather than to a fixed offset from the column top,
 * which may then be scrolled out of view.
 */
/**
 * dnd-kit keeps the drag overlay on screen while it animates a drop, and
 * ignores a new drag until that finishes - starting the next gesture earlier
 * silently does nothing (seen when a test drags the same card twice).
 */
async function waitForDropAnimation(page: Page): Promise<void> {
  await page.getByTestId("drag-overlay").waitFor({ state: "detached" });
}

export async function dragCardToColumn(page: Page, card: Locator, targetColumn: Locator): Promise<void> {
  // Centered, not just "in view": a card left at the viewport's edge puts the
  // pointer in dnd-kit's auto-scroll zone, which scrolls the board mid-drag.
  await card.evaluate((el) => el.scrollIntoView({ block: "center", inline: "nearest" }));
  await waitForDropAnimation(page);
  const cardBox = await card.boundingBox();
  const handleBox = await card.getByRole("button", { name: /^drag /i }).boundingBox();
  const columnBox = await targetColumn.boundingBox();
  if (!cardBox || !handleBox || !columnBox) {
    throw new Error("Unable to resolve drag coordinates.");
  }

  const startX = handleBox.x + handleBox.width / 2;
  const startY = handleBox.y + handleBox.height / 2;
  const offsetX = startX - (cardBox.x + cardBox.width / 2);

  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(
    columnBox.x + columnBox.width / 2 + offsetX,
    startY,
    { steps: 12 }
  );
  await page.mouse.up();
  await waitForDropAnimation(page);
}

/** Open a card's details view, then its edit form (the only way to edit a card). */
export async function openCardEditor(page: Page, title: string): Promise<Locator> {
  await page.getByRole("button", { name: `Open details for ${title}` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Edit", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: `Edit lead ${title}` });
  await dialog.waitFor();
  return dialog;
}
