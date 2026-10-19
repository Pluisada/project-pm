/**
 * E2E tests for the multi-user authentication flow: first-run admin setup,
 * login/logout, and admin-only user management.
 */

import { expect, test } from "@playwright/test";
import {
  API_BASE_URL,
  E2E_ADMIN_PASSWORD,
  E2E_ADMIN_USERNAME,
  E2E_MEMBER_USERNAME,
  loginAsMember,
} from "./helpers";


test.describe("Setup flow", () => {
  // These only exercise real behavior the first time this suite runs
  // against a fresh database - once the e2e admin exists, setup is
  // permanently done, so later runs skip them rather than fail on stale
  // state (there is no per-run database reset in this project yet).
  test("shows setup screen when no users exist yet", async ({ page, request }) => {
    const status = await request.get(`${API_BASE_URL}/api/setup/status`);
    const { needs_setup } = await status.json();
    test.skip(!needs_setup, "Admin was already set up by an earlier test run");

    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "Create Admin Account" })
    ).toBeVisible();
  });

  test("creates the admin account and logs straight in", async ({ page, request }) => {
    const status = await request.get(`${API_BASE_URL}/api/setup/status`);
    const { needs_setup } = await status.json();
    test.skip(!needs_setup, "Admin was already set up by an earlier test run");

    await page.goto("/");
    await page.getByLabel("Setup key").fill(process.env.PM_E2E_SETUP_TOKEN ?? "");
    await page.getByLabel("Username").fill(E2E_ADMIN_USERNAME);
    await page.getByLabel("Password", { exact: true }).fill(E2E_ADMIN_PASSWORD);
    await page.getByLabel("Confirm Password").fill(E2E_ADMIN_PASSWORD);
    await page.getByRole("button", { name: /create admin account/i }).click();

    // Scoped to the header: the username also appears on cards it's the
    // salesperson for.
    await expect(page.getByText(/welcome/i)).toContainText(E2E_ADMIN_USERNAME);
  });
});

test.describe("Login flow", () => {
  test.beforeAll(async ({ request }) => {
    // Make sure the e2e admin exists before the rest of this block runs.
    const setupResponse = await request.post(`${API_BASE_URL}/api/setup`, {
      headers: { "X-Setup-Token": process.env.PM_E2E_SETUP_TOKEN ?? "" },
      data: { username: E2E_ADMIN_USERNAME, password: E2E_ADMIN_PASSWORD },
    });
    if (!setupResponse.ok()) {
      const loginResponse = await request.post(`${API_BASE_URL}/api/login`, {
        data: { username: E2E_ADMIN_USERNAME, password: E2E_ADMIN_PASSWORD },
      });
      if (!loginResponse.ok()) {
        throw new Error(
          "Could not bootstrap the e2e admin - was setup already completed " +
            "with different credentials?"
        );
      }
    }
  });

  test("displays login form once an admin exists", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Sign In" })).toBeVisible();
    await expect(page.getByLabel("Username")).toBeVisible();
    await expect(page.getByLabel("Password")).toBeVisible();
  });

  test("login with valid credentials shows the board", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Username").fill(E2E_ADMIN_USERNAME);
    await page.getByLabel("Password").fill(E2E_ADMIN_PASSWORD);
    await page.getByRole("button", { name: /sign in/i }).click();

    await expect(page.getByText(/welcome/i)).toContainText(E2E_ADMIN_USERNAME);
  });

  test("shows error with invalid credentials", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Username").fill(E2E_ADMIN_USERNAME);
    await page.getByLabel("Password").fill("definitely-wrong-password");
    await page.getByRole("button", { name: /sign in/i }).click();

    await expect(page.getByText("Invalid username or password")).toBeVisible();
  });

  test("password is cleared on login error", async ({ page }) => {
    await page.goto("/");
    const passwordInput = page.getByLabel("Password");

    await page.getByLabel("Username").fill(E2E_ADMIN_USERNAME);
    await passwordInput.fill("definitely-wrong-password");
    await page.getByRole("button", { name: /sign in/i }).click();

    await expect(page.getByText("Invalid username or password")).toBeVisible();
    await expect(passwordInput).toHaveValue("");
  });

  test("username field remembers input on error", async ({ page }) => {
    const usernameInput = page.getByLabel("Username");

    await page.goto("/");
    await usernameInput.fill(E2E_ADMIN_USERNAME);
    await page.getByLabel("Password").fill("definitely-wrong-password");
    await page.getByRole("button", { name: /sign in/i }).click();

    await expect(page.getByText("Invalid username or password")).toBeVisible();
    await expect(usernameInput).toHaveValue(E2E_ADMIN_USERNAME);
  });

  test("sign in button is disabled with empty credentials", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("button", { name: /sign in/i })).toBeDisabled();
  });

  test("logout redirects back to login", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Username").fill(E2E_ADMIN_USERNAME);
    await page.getByLabel("Password").fill(E2E_ADMIN_PASSWORD);
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page.getByText(/welcome/i)).toBeVisible();

    await page.getByRole("button", { name: /logout/i }).click();
    await expect(page.getByRole("heading", { name: "Sign In" })).toBeVisible();
  });

  test("session persists after page refresh", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Username").fill(E2E_ADMIN_USERNAME);
    await page.getByLabel("Password").fill(E2E_ADMIN_PASSWORD);
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page.getByText(/welcome/i)).toBeVisible();

    await page.reload();
    await expect(page.getByText(/welcome/i)).toBeVisible();
  });

  test("clearing localStorage logs out user", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Username").fill(E2E_ADMIN_USERNAME);
    await page.getByLabel("Password").fill(E2E_ADMIN_PASSWORD);
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page.getByText(/welcome/i)).toBeVisible();

    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await expect(page.getByRole("heading", { name: "Sign In" })).toBeVisible();
  });
});

test.describe("Admin user management", () => {
  test.beforeEach(async ({ page, request }) => {
    const setupResponse = await request.post(`${API_BASE_URL}/api/setup`, {
      headers: { "X-Setup-Token": process.env.PM_E2E_SETUP_TOKEN ?? "" },
      data: { username: E2E_ADMIN_USERNAME, password: E2E_ADMIN_PASSWORD },
    });
    if (!setupResponse.ok()) {
      const loginResponse = await request.post(`${API_BASE_URL}/api/login`, {
        data: { username: E2E_ADMIN_USERNAME, password: E2E_ADMIN_PASSWORD },
      });
      if (!loginResponse.ok()) {
        throw new Error("Could not bootstrap the e2e admin");
      }
    }

    await page.goto("/");
    await page.getByLabel("Username").fill(E2E_ADMIN_USERNAME);
    await page.getByLabel("Password").fill(E2E_ADMIN_PASSWORD);
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page.getByText(/welcome/i)).toBeVisible();
  });

  test("admin sees the Manage Users action", async ({ page }) => {
    await expect(page.getByRole("button", { name: /manage users/i })).toBeVisible();
  });

  test("admin invites a member by email, who cannot log in until the email is verified", async ({ page }) => {
    const inviteeEmail = `invitee_${Date.now()}@example.com`;

    await page.getByRole("button", { name: /manage users/i }).click();
    await expect(page.getByRole("heading", { name: "Manage Users" })).toBeVisible();

    await page.getByLabel("Email").fill(inviteeEmail);
    await page.getByRole("button", { name: /invite user/i }).click();

    const memberRow = page.getByRole("row", { name: new RegExp(inviteeEmail) });
    await expect(memberRow).toBeVisible();
    await expect(memberRow.getByRole("cell", { name: "member", exact: true })).toBeVisible();

    // The invitee has no usable password and no verified email: no guess logs in.
    await page.getByRole("button", { name: /logout/i }).click();
    await page.getByLabel("Username").fill(inviteeEmail);
    await page.getByLabel("Password").fill("Member_password123!");
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page.getByText("Invalid username or password")).toBeVisible();
  });

  test("inviting the same email twice shows an error", async ({ page }) => {
    const inviteeEmail = `duplicate_${Date.now()}@example.com`;

    await page.getByRole("button", { name: /manage users/i }).click();
    await expect(page.getByRole("heading", { name: "Manage Users" })).toBeVisible();

    await page.getByLabel("Email").fill(inviteeEmail);
    await page.getByRole("button", { name: /invite user/i }).click();
    await expect(page.getByRole("row", { name: new RegExp(inviteeEmail) })).toBeVisible();

    await page.getByLabel("Email").fill(inviteeEmail);
    await page.getByRole("button", { name: /invite user/i }).click();
    await expect(page.getByText(/already exists/i)).toBeVisible();
  });

  test("the provisioned e2e member logs in and has no admin actions", async ({ page, request }) => {
    await loginAsMember(page, request);
    await page.goto("/");
    await expect(page.getByText(/welcome/i)).toContainText(E2E_MEMBER_USERNAME);
    await expect(page.getByRole("button", { name: /manage users/i })).toHaveCount(0);
  });
});
