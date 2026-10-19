/**
 * Session expiry in the browser: an authenticated request answered with 401
 * clears the stored session and shows the login screen with a notice, without
 * redirect loops or repeated authenticated calls. Runs against the isolated
 * E2E backend (temp SQLite, PM_E2E=1) with a real token whose signature is
 * corrupted in the browser; no production code is touched.
 */
import { expect, test } from "@playwright/test";
import { API_BASE_URL, E2E_ADMIN_PASSWORD, E2E_ADMIN_USERNAME } from "./helpers";

const TOKEN_KEY = "pm_auth_token";

test("a 401 on an authenticated request ends the session once", async ({ page, request }) => {
  // Make sure the e2e admin exists (first run of a fresh database); a 4xx here means it already does.
  await request.post(`${API_BASE_URL}/api/setup`, {
    data: { username: E2E_ADMIN_USERNAME, password: E2E_ADMIN_PASSWORD },
  });

  // 1. Valid login through the UI, protected area visible.
  await page.goto("/");
  await page.getByLabel("Username").fill(E2E_ADMIN_USERNAME);
  await page.getByLabel("Password").fill(E2E_ADMIN_PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  await expect(page.getByText(/welcome/i)).toContainText(E2E_ADMIN_USERNAME);
  await expect.poll(() => page.evaluate((k) => localStorage.getItem(k), TOKEN_KEY)).toBeTruthy();

  // 2. Invalidate the token: same payload, broken signature. The backend rejects it for real.
  await page.evaluate((k) => {
    const token = localStorage.getItem(k)!;
    localStorage.setItem(k, token.slice(0, -4) + (token.endsWith("AAAA") ? "BBBB" : "AAAA"));
  }, TOKEN_KEY);

  // Watch the API from here on: authenticated calls, their statuses, and page navigations.
  const authenticated: number[] = [];
  const authenticatedUrls: string[] = [];
  page.on("response", (response) => {
    const req = response.request();
    if (req.url().startsWith(API_BASE_URL) && req.headers()["authorization"]) {
      authenticated.push(response.status());
      authenticatedUrls.push(req.url());
    }
  });
  let navigations = 0;
  page.on("request", (req) => {
    if (req.isNavigationRequest() && req.frame() === page.mainFrame()) navigations++;
  });

  // 3. The next load makes an authenticated request, which gets 401.
  const unauthorized = page.waitForResponse(
    (r) => r.url().startsWith(API_BASE_URL) && r.request().headers()["authorization"] !== undefined && r.status() === 401
  );
  await page.reload();
  await unauthorized;

  // 4-6. Token gone, login screen, expiry notice.
  await expect(page.getByText("Your session expired. Sign in again.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Sign In" })).toBeVisible();
  expect(await page.evaluate((k) => localStorage.getItem(k), TOKEN_KEY)).toBeNull();

  // 7. Nothing else happens: no further authenticated calls, no navigation loop.
  await page.waitForLoadState("networkidle");
  const callsAfterExpiry = authenticated.length;
  const navigationsAfterExpiry = navigations;
  expect(authenticated.every((s) => s === 401), authenticatedUrls.join(", ")).toBe(true);
  expect(callsAfterExpiry).toBeLessThanOrEqual(2);
  await expect(page.getByRole("heading", { name: "Sign In" })).toBeVisible();
  await page.waitForLoadState("networkidle");
  expect(authenticated.length).toBe(callsAfterExpiry);
  expect(navigations).toBe(navigationsAfterExpiry);
  expect(navigations).toBe(1); // only the reload itself
  expect(await page.evaluate((k) => localStorage.getItem(k), TOKEN_KEY)).toBeNull();
});
