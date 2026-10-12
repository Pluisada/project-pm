/**
 * Shared e2e test helpers for bootstrapping an authenticated session.
 */
import type { APIRequestContext, Locator, Page } from "@playwright/test";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
export const E2E_ADMIN_USERNAME = "e2e_admin";
export const E2E_ADMIN_PASSWORD = "e2e_admin_password123";
export const E2E_MEMBER_USERNAME = "e2e_member";
export const E2E_MEMBER_PASSWORD = "e2e_member_password123";

/**
 * Ensure the e2e admin account exists (running first-run setup if this is a
 * fresh database) and return a valid access token for it.
 */
async function getAdminToken(request: APIRequestContext): Promise<string> {
  const setupResponse = await request.post(`${API_BASE_URL}/api/setup`, {
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
 * Ensure the e2e member account exists (created via the admin API, ignoring
 * a 409 if a previous run already created it) and return a valid access
 * token for it.
 */
async function getMemberToken(request: APIRequestContext): Promise<string> {
  const adminToken = await getAdminToken(request);
  await request.post(`${API_BASE_URL}/api/users`, {
    headers: { Authorization: `Bearer ${adminToken}` },
    data: { username: E2E_MEMBER_USERNAME, password: E2E_MEMBER_PASSWORD },
  });

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
 */
export async function dragCardToColumn(page: Page, card: Locator, targetColumn: Locator): Promise<void> {
  const cardBox = await card.boundingBox();
  const handleBox = await card.getByRole("button", { name: /^drag /i }).boundingBox();
  const columnBox = await targetColumn.boundingBox();
  if (!cardBox || !handleBox || !columnBox) {
    throw new Error("Unable to resolve drag coordinates.");
  }

  const startX = handleBox.x + handleBox.width / 2;
  const startY = handleBox.y + handleBox.height / 2;
  const offsetX = startX - (cardBox.x + cardBox.width / 2);
  const offsetY = startY - (cardBox.y + cardBox.height / 2);

  await page.mouse.move(startX, startY);
  await page.mouse.down();
  await page.mouse.move(
    columnBox.x + columnBox.width / 2 + offsetX,
    columnBox.y + 120 + offsetY,
    { steps: 12 }
  );
  await page.mouse.up();
}
