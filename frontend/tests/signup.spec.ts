/**
 * E2E tests for email self-signup. The emailed link can't be read from a
 * real inbox here, so the two signup endpoints are stubbed; the backend side
 * is covered by backend/test_signup.py.
 */

import { expect, test } from "@playwright/test";

test("requests a signup link from the sign-in screen", async ({ page }) => {
  let requestedEmail: string | null = null;
  await page.route("**/api/signup", async (route) => {
    requestedEmail = route.request().postDataJSON().email;
    await route.fulfill({ status: 202, json: { message: "sent" } });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Sign up" }).click();
  await page.getByLabel("Email").fill("new.user@example.com");
  await page.getByLabel("Email").press("Enter");

  await expect(page.getByRole("heading", { name: "Check your inbox" })).toBeVisible();
  expect(requestedEmail).toBe("new.user@example.com");

  await page.getByRole("button", { name: "Back to sign in" }).click();
  await expect(page.getByRole("heading", { name: "Sign In" })).toBeVisible();
});

test("sets a password from the signup link and lands on sign-in", async ({ page }) => {
  let submitted: { token: string; password: string } | null = null;
  await page.route("**/api/signup/complete", async (route) => {
    submitted = route.request().postDataJSON();
    await route.fulfill({ status: 201, json: { message: "Account created" } });
  });

  await page.goto("/?signup_token=test-token");
  await expect(page.getByRole("heading", { name: "Set Your Password" })).toBeVisible();

  await page.getByLabel("Password", { exact: true }).fill("newpass123");
  await page.getByLabel("Confirm Password").fill("different123");
  await page.getByRole("button", { name: "Create Account" }).click();
  await expect(page.getByText("Passwords do not match")).toBeVisible();
  expect(submitted).toBeNull();

  await page.getByLabel("Confirm Password").fill("newpass123");
  await page.getByRole("button", { name: "Create Account" }).click();

  await expect(page.getByRole("heading", { name: "Sign In" })).toBeVisible();
  await expect(page.getByText(/account created/i)).toBeVisible();
  expect(page.url()).not.toContain("signup_token");
  expect(submitted).toEqual({ token: "test-token", password: "newpass123" });
});

test("shows the backend error for an invalid link", async ({ page }) => {
  await page.route("**/api/signup/complete", (route) =>
    route.fulfill({ status: 400, json: { detail: "Invalid or expired link" } })
  );

  await page.goto("/?signup_token=bad");
  await page.getByLabel("Password", { exact: true }).fill("newpass123");
  await page.getByLabel("Confirm Password").fill("newpass123");
  await page.getByRole("button", { name: "Create Account" }).click();

  await expect(page.getByText("Invalid or expired link")).toBeVisible();
});
