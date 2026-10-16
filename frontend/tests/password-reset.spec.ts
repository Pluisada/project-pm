/**
 * E2E tests for forgot/reset password. The reset endpoints are stubbed (the
 * emailed link can't be read here); the backend side is covered by
 * backend/test_password_reset.py.
 */

import { expect, test } from "@playwright/test";

test("sends a reset link from the sign-in screen", async ({ page }) => {
  let identifier: string | null = null;
  await page.route("**/api/password/forgot", async (route) => {
    identifier = route.request().postDataJSON().identifier;
    await route.fulfill({
      status: 200,
      json: { message: "If an account exists for that address, a reset link was sent" },
    });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Forgot my password?" }).click();
  await expect(page.getByRole("heading", { name: "Forgot Password" })).toBeVisible();
  await page.getByLabel("Username or email").fill("member@example.com");
  await page.getByLabel("Username or email").press("Enter");

  await expect(page.getByText("If an account exists for that username or email")).toBeVisible();
  await expect(page.getByText("member@example.com")).toHaveCount(0);
  expect(identifier).toBe("member@example.com");
});

test("sets a new password from the reset link and lands on sign-in", async ({ page }) => {
  let submitted: { token: string; password: string } | null = null;
  await page.route("**/api/password/reset", async (route) => {
    submitted = route.request().postDataJSON();
    await route.fulfill({ status: 200, json: { message: "Password updated" } });
  });

  await page.goto("/?reset_token=test-token");
  await expect(page.getByRole("heading", { name: "Reset Your Password" })).toBeVisible();
  await page.getByLabel("Password", { exact: true }).fill("newpass123");
  await page.getByLabel("Confirm Password").fill("newpass123");
  await page.getByRole("button", { name: "Update Password" }).click();

  await expect(page.getByRole("heading", { name: "Sign In" })).toBeVisible();
  await expect(page.getByText(/password updated/i)).toBeVisible();
  expect(page.url()).not.toContain("reset_token");
  expect(submitted).toEqual({ token: "test-token", password: "newpass123" });
});
