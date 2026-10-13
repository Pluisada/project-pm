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
      json: { message: "Reset link sent", email: "member@example.com" },
    });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Forgot my password?" }).click();
  await expect(page.getByRole("heading", { name: "Forgot Password" })).toBeVisible();
  await page.getByLabel("Username or email").fill("member@example.com");
  await page.getByLabel("Username or email").press("Enter");

  await expect(page.getByText("Reset link sent to")).toBeVisible();
  await expect(page.getByText("member@example.com")).toBeVisible();
  expect(identifier).toBe("member@example.com");
});

test("says when no account matches", async ({ page }) => {
  await page.route("**/api/password/forgot", (route) =>
    route.fulfill({
      status: 404,
      json: { detail: "No account found for that username or email" },
    })
  );

  await page.goto("/");
  await page.getByRole("button", { name: "Forgot my password?" }).click();
  await page.getByLabel("Username or email").fill("nobody@example.com");
  await page.getByRole("button", { name: "Send Reset Link" }).click();

  await expect(page.getByText("No account found for that username or email")).toBeVisible();
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
