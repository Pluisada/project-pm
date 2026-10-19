/**
 * E2E tests for the /api/setup key (X-Setup-Token). The backend is started
 * with a per-run key, so these exercise the key checks on the isolated database.
 * The "key not configured" case needs a backend without a key and is covered
 * by pytest (test_security.py). Every refusal below happens whether or not an
 * admin exists yet, because the key is checked before the user check.
 */

import { expect, test } from "@playwright/test";
import { API_BASE_URL, E2E_ADMIN_PASSWORD } from "./helpers";

const key = process.env.PM_E2E_SETUP_TOKEN ?? "";
const attacker = { username: "setup_probe_admin", password: E2E_ADMIN_PASSWORD };

async function expectNoUserCreated(request: import("@playwright/test").APIRequestContext) {
  // An admin may legitimately exist; the probe account must never be the one.
  const login = await request.post(`${API_BASE_URL}/api/login`, { data: attacker });
  expect(login.status()).toBe(401);
}

test.describe("Setup key", () => {
  test("status reports setup as enabled without revealing the key", async ({ request }) => {
    const response = await request.get(`${API_BASE_URL}/api/setup/status`);
    expect(response.ok()).toBeTruthy();
    const text = await response.text();
    expect(JSON.parse(text).setup_enabled).toBe(true);
    expect(text).not.toContain(key);
  });

  test("setup without a key is refused", async ({ request }) => {
    const response = await request.post(`${API_BASE_URL}/api/setup`, { data: attacker });
    expect(response.status()).toBe(403);
    expect(await response.text()).not.toContain(key);
    await expectNoUserCreated(request);
  });

  test("setup with an incorrect key is refused", async ({ request }) => {
    const response = await request.post(`${API_BASE_URL}/api/setup`, {
      headers: { "X-Setup-Token": `${key}x` },
      data: attacker,
    });
    expect(response.status()).toBe(403);
    expect(await response.text()).not.toContain(key);
    await expectNoUserCreated(request);
  });

  test("the key is not accepted in the request body", async ({ request }) => {
    const response = await request.post(`${API_BASE_URL}/api/setup`, {
      data: { ...attacker, setup_token: key, setup_key: key, token: key },
    });
    expect(response.status()).toBe(403);
    await expectNoUserCreated(request);
  });

  test("the key is not accepted in the query string", async ({ request }) => {
    const response = await request.post(
      `${API_BASE_URL}/api/setup?setup_token=${key}&setup_key=${key}&token=${key}`,
      { data: attacker }
    );
    expect(response.status()).toBe(403);
    await expectNoUserCreated(request);
  });

  test("a valid key cannot create a second administrator", async ({ request }) => {
    // Make sure one user exists first (the shared admin from the helpers).
    const first = await request.post(`${API_BASE_URL}/api/setup`, {
      headers: { "X-Setup-Token": key },
      data: { username: "e2e_admin", password: E2E_ADMIN_PASSWORD },
    });
    expect([201, 409]).toContain(first.status());

    const second = await request.post(`${API_BASE_URL}/api/setup`, {
      headers: { "X-Setup-Token": key },
      data: attacker,
    });
    expect(second.status()).toBe(409);
    expect(await second.text()).not.toContain(key);
    await expectNoUserCreated(request);
  });
});
