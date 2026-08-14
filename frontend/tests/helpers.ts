/**
 * Shared e2e test helpers for bootstrapping an authenticated session.
 */
import type { APIRequestContext, Page } from "@playwright/test";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
export const E2E_ADMIN_USERNAME = "e2e_admin";
export const E2E_ADMIN_PASSWORD = "e2e_admin_password123";

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
