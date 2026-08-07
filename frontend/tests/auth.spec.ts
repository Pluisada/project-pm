/**
 * E2E tests for authentication flow
 */

import { expect, test } from "@playwright/test";

test.describe("Authentication Flow", () => {
  test("shows login page on first load", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Sign In" })).toBeVisible();
    await expect(
      page.getByText(/enter your credentials to access/i)
    ).toBeVisible();
  });

  test("displays login form", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByLabel("Username")).toBeVisible();
    await expect(page.getByLabel("Password")).toBeVisible();
    await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
  });

  test("shows demo credentials", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("Demo Credentials")).toBeVisible();
    await expect(page.locator("code").first()).toContainText("user");
    await expect(page.locator("code").last()).toContainText("password");
  });

  test("login with valid credentials redirects to kanban", async ({ page }) => {
    await page.goto("/");

    // Fill in credentials
    await page.getByLabel("Username").fill("user");
    await page.getByLabel("Password").fill("password");

    // Submit
    await page.getByRole("button", { name: /sign in/i }).click();

    // Should redirect to kanban board
    await expect(
      page.getByRole("heading", { name: "Kanban Studio" })
    ).toBeVisible();
  });

  test("shows error with invalid credentials", async ({ page }) => {
    await page.goto("/");

    // Fill in wrong credentials
    await page.getByLabel("Username").fill("wrong");
    await page.getByLabel("Password").fill("wrong");

    // Submit
    await page.getByRole("button", { name: /sign in/i }).click();

    // Should show error
    await expect(
      page.getByText("Invalid username or password")
    ).toBeVisible();
  });

  test("shows error with empty credentials", async ({ page }) => {
    await page.goto("/");

    // Try to submit empty form
    await page.getByRole("button", { name: /sign in/i }).click();

    // Sign in button should be disabled
    await expect(page.getByRole("button", { name: /sign in/i })).toBeDisabled();
  });

  test("logout redirects back to login", async ({ page }) => {
    // Login first
    await page.goto("/");
    await page.getByLabel("Username").fill("user");
    await page.getByLabel("Password").fill("password");
    await page.getByRole("button", { name: /sign in/i }).click();

    // Verify we're on kanban
    await expect(
      page.getByRole("heading", { name: "Kanban Studio" })
    ).toBeVisible();

    // Click logout
    await page.getByRole("button", { name: /logout/i }).click();

    // Should redirect back to login
    await expect(page.getByRole("heading", { name: "Sign In" })).toBeVisible();
  });

  test("session persists after page refresh", async ({ page, context }) => {
    // Login
    await page.goto("/");
    await page.getByLabel("Username").fill("user");
    await page.getByLabel("Password").fill("password");
    await page.getByRole("button", { name: /sign in/i }).click();

    // Verify we're on kanban
    await expect(
      page.getByRole("heading", { name: "Kanban Studio" })
    ).toBeVisible();

    // Refresh the page
    await page.reload();

    // Should still be on kanban (session persisted)
    await expect(
      page.getByRole("heading", { name: "Kanban Studio" })
    ).toBeVisible();
  });

  test("clearing localStorage logs out user", async ({ page }) => {
    // Login
    await page.goto("/");
    await page.getByLabel("Username").fill("user");
    await page.getByLabel("Password").fill("password");
    await page.getByRole("button", { name: /sign in/i }).click();

    // Verify we're on kanban
    await expect(
      page.getByRole("heading", { name: "Kanban Studio" })
    ).toBeVisible();

    // Clear localStorage
    await page.evaluate(() => localStorage.clear());

    // Refresh
    await page.reload();

    // Should be back at login
    await expect(page.getByRole("heading", { name: "Sign In" })).toBeVisible();
  });

  test("displays username in header after login", async ({ page }) => {
    // Login
    await page.goto("/");
    await page.getByLabel("Username").fill("user");
    await page.getByLabel("Password").fill("password");
    await page.getByRole("button", { name: /sign in/i }).click();

    // Username should appear in header
    await expect(page.getByText(/welcome/i)).toBeVisible();
    await expect(page.getByText("user")).toBeVisible();
  });

  test("login button is disabled while submitting", async ({ page }) => {
    await page.goto("/");
    await page.getByLabel("Username").fill("user");
    await page.getByLabel("Password").fill("password");

    const signInButton = page.getByRole("button", { name: /sign in/i });

    // Click and check immediately
    await signInButton.click();
    // After submit, button should show loading state
    await expect(signInButton).toContainText(/signing in/i);
  });

  test("password is cleared on login error", async ({ page }) => {
    await page.goto("/");

    const passwordInput = page.getByLabel("Password");

    // Fill in wrong credentials
    await page.getByLabel("Username").fill("wrong");
    await passwordInput.fill("wrong");

    // Submit
    await page.getByRole("button", { name: /sign in/i }).click();

    // Error should show
    await expect(
      page.getByText("Invalid username or password")
    ).toBeVisible();

    // Password field should be cleared
    await expect(passwordInput).toHaveValue("");
  });

  test("username field remembers input on error", async ({ page }) => {
    await page.goto("/");

    const usernameInput = page.getByLabel("Username");

    // Fill in wrong credentials
    await usernameInput.fill("testuser");
    await page.getByLabel("Password").fill("wrong");

    // Submit
    await page.getByRole("button", { name: /sign in/i }).click();

    // Error should show
    await expect(
      page.getByText("Invalid username or password")
    ).toBeVisible();

    // Username should still be there
    await expect(usernameInput).toHaveValue("testuser");
  });

  test("login persists across browser close simulation", async ({ page, context }) => {
    // Create persistent context
    const page1 = await context.newPage();
    await page1.goto("/");
    await page1.getByLabel("Username").fill("user");
    await page1.getByLabel("Password").fill("password");
    await page1.getByRole("button", { name: /sign in/i }).click();

    // Verify logged in
    await expect(
      page1.getByRole("heading", { name: "Kanban Studio" })
    ).toBeVisible();

    // Close first page and open new one (simulating browser restart)
    await page1.close();
    const page2 = await context.newPage();
    await page2.goto("/");

    // Should still be logged in (sessionStorage/localStorage persists in same context)
    await expect(
      page2.getByRole("heading", { name: "Kanban Studio" })
    ).toBeVisible();
  });
});
