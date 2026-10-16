import { randomBytes } from "node:crypto";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

/**
 * E2E runs against its own backend and frontend, never the development ones:
 *
 * - Backend on :8001 with PM_E2E=1, a fresh SQLite file in a new temp dir and
 *   a random SECRET_KEY generated here (never printed or stored). The backend
 *   itself refuses PM_E2E=1 unless DATABASE_URL is a temp-dir file, so a
 *   broken config fails instead of falling back to pm.db.
 * - Frontend on :3001 with its own build dir (`next dev` holds a lock on the
 *   default one, which the developer's own server may be using).
 * - Neither server is ever reused: a process already listening on 3001/8001
 *   is an error, not something to test against.
 * - tests/e2e-setup.ts checks the backend reports test_mode before any test,
 *   and fails the run if either development pm.db changed afterwards.
 *
 * Config files are loaded by the runner and by every worker, so the temp dir
 * and key are created once and passed down through the environment.
 */
process.env.PM_E2E_DB_DIR ??= mkdtempSync(path.join(tmpdir(), "pm-e2e-"));
process.env.PM_E2E_SECRET_KEY ??= randomBytes(48).toString("base64url");
process.env.PM_E2E_SETUP_TOKEN ??= randomBytes(48).toString("base64url");

// Defaults are the standard E2E ports; set PM_E2E_BACKEND_PORT / PM_E2E_FRONTEND_PORT
// (and PM_E2E_DIST_DIR) to run beside another E2E or dev instance.
const BACKEND_PORT = Number(process.env.PM_E2E_BACKEND_PORT ?? 8001);
const FRONTEND_PORT = Number(process.env.PM_E2E_FRONTEND_PORT ?? 3001);
const API_URL = `http://127.0.0.1:${BACKEND_PORT}`;
const APP_URL = `http://127.0.0.1:${FRONTEND_PORT}`;
const DB_PATH = path.join(process.env.PM_E2E_DB_DIR, "e2e.db");

// Read by tests/helpers.ts and tests/e2e-setup.ts.
process.env.PM_E2E_API_URL = API_URL;
process.env.PM_E2E_DB_PATH = DB_PATH;

export default defineConfig({
  testDir: "./tests",
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  // One backend database shared by every test, and tests that change the
  // board: run serially until tests get their own boards.
  workers: 1,
  fullyParallel: false,
  globalSetup: "./tests/e2e-setup.ts",
  // Defaults to ./test-results (which Playwright empties at the start of a run).
  outputDir: process.env.PM_E2E_OUTPUT_DIR,
  use: {
    baseURL: APP_URL,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      name: "backend",
      command: `uv run uvicorn main:app --host 127.0.0.1 --port ${BACKEND_PORT}`,
      cwd: "../backend",
      url: `${API_URL}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      // Merged over process.env, so everything sensitive is set explicitly:
      // no real email, no paid AI calls, no inherited DATABASE_URL/SECRET_KEY.
      env: {
        PM_E2E: "1",
        DATABASE_URL: `sqlite:///${DB_PATH}`,
        SECRET_KEY: process.env.PM_E2E_SECRET_KEY,
        PM_SETUP_TOKEN: process.env.PM_E2E_SETUP_TOKEN,
        SMTP_HOST: "",
        OPENROUTER_API_KEY: "",
        APP_BASE_URL: APP_URL,
        // The frontend (:3001) calls the API (:8001) cross-origin.
        CORS_ALLOWED_ORIGINS: APP_URL,
        // Every test logs in from 127.0.0.1; the limits are covered by pytest.
        RATE_LIMIT_ENABLED: "false",
        SIGNUP_ALLOWED_DOMAINS: "example.com",
      },
    },
    {
      name: "frontend",
      command: `npm run dev -- --hostname 127.0.0.1 --port ${FRONTEND_PORT}`,
      url: APP_URL,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        NEXT_PUBLIC_API_URL: API_URL,
        PM_E2E_DIST_DIR: process.env.PM_E2E_DIST_DIR ?? ".next-e2e",
      },
    },
  ],
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
