import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for testing against Docker backend
 * Usage: npx playwright test --config=playwright.docker.config.ts
 *
 * Prerequisites:
 * - Docker container running on http://localhost:8000
 * - Start with: ./scripts/start.sh
 */

export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL: "http://127.0.0.1:8000",
    trace: "retain-on-failure",
  },
  // Don't start a server - assume Docker is already running
  webServer: undefined,
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
