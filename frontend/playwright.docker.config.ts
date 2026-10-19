import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for testing against Docker backend
 * Usage: npx playwright test --config=playwright.docker.config.ts
 *
 * Prerequisites:
 * - Docker container running on http://localhost:8000
 * - Start with: ./scripts/start.sh
 *
 * Guarded like the default config: tests/e2e-setup.ts refuses to run unless
 * the container reports test_mode (PM_E2E=1) with a throwaway temp database.
 * The standard compose setup mounts the real ./pm.db, so this config fails
 * on purpose until a dedicated Docker test profile exists.
 */
process.env.PM_E2E_API_URL = "http://127.0.0.1:8000";
process.env.PM_E2E_DB_PATH ??= "";

export default defineConfig({
  testDir: "./tests",
  workers: 1,
  globalSetup: "./tests/e2e-setup.ts",
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
