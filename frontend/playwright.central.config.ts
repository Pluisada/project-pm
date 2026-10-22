import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

/**
 * Browser E2E for the CENTRAL UI (company boards), kept apart from playwright.config.ts because it needs a backend in
 * central mode. Everything is disposable and isolated, like the legacy E2E:
 *
 * - A new temp directory holds a freshly seeded identity database and the three company databases
 *   (tests-central/seed_central.py); no real or development database is ever named here.
 * - Backend on :8002 (PM_AUTH_MODE=central), frontend on :3002 with its own build dir; neither is reused.
 * - A random SECRET_KEY generated here, no SMTP, no AI key, rate limits off.
 *
 * Seeding happens while this file is loaded (the backend refuses to start without its databases), once: the runner
 * creates the directory and every worker finds the seeded files.
 */
process.env.PM_E2E_CENTRAL_DIR ??= mkdtempSync(path.join(tmpdir(), "pm-e2e-central-"));
process.env.PM_E2E_CENTRAL_SECRET ??= randomBytes(48).toString("base64url");

const BACKEND_PORT = Number(process.env.PM_E2E_CENTRAL_BACKEND_PORT ?? 8002);
const FRONTEND_PORT = Number(process.env.PM_E2E_CENTRAL_FRONTEND_PORT ?? 3002);
const API_URL = `http://127.0.0.1:${BACKEND_PORT}`;
const APP_URL = `http://127.0.0.1:${FRONTEND_PORT}`;
const DIR = process.env.PM_E2E_CENTRAL_DIR;
const backend = path.resolve(__dirname, "../backend");

if (!existsSync(path.join(DIR, "identity.db"))) {
  execFileSync("uv", ["run", "python", path.resolve(__dirname, "tests-central/seed_central.py"), DIR], {
    cwd: backend,
    env: { ...process.env, PYTHONPATH: backend, SECRET_KEY: process.env.PM_E2E_CENTRAL_SECRET },
    stdio: "pipe",
  });
}
process.env.PM_E2E_CENTRAL_API_URL = API_URL;

export default defineConfig({
  testDir: "./tests-central",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  fullyParallel: false,
  outputDir: process.env.PM_E2E_CENTRAL_OUTPUT_DIR ?? "test-results-central",
  use: { baseURL: APP_URL, trace: "retain-on-failure" },
  webServer: [
    {
      name: "central backend",
      command: `uv run uvicorn main:app --host 127.0.0.1 --port ${BACKEND_PORT}`,
      cwd: "../backend",
      url: `${API_URL}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        PM_AUTH_MODE: "central",
        IDENTITY_DATABASE_URL: `sqlite:///${path.join(DIR, "identity.db")}`,
        PM_COMPANY_DB_SALCOMP: `sqlite:///${path.join(DIR, "salcomp.db")}`,
        PM_COMPANY_DB_LITY: `sqlite:///${path.join(DIR, "lity.db")}`,
        PM_COMPANY_DB_VALOR_LOG: `sqlite:///${path.join(DIR, "valor-log.db")}`,
        // Central mode never opens it, but the legacy module builds its engine at import: point it at a file that does not
        // exist (inside the disposable directory) so no inherited DATABASE_URL, and not pm.db, can be in play.
        DATABASE_URL: `sqlite:///${path.join(DIR, "unused-legacy.db")}`,
        SECRET_KEY: process.env.PM_E2E_CENTRAL_SECRET,
        SMTP_HOST: "",
        OPENROUTER_API_KEY: "",
        APP_BASE_URL: APP_URL,
        CORS_ALLOWED_ORIGINS: APP_URL,
        RATE_LIMIT_ENABLED: "false",
      },
    },
    {
      name: "central frontend",
      command: `npm run dev -- --hostname 127.0.0.1 --port ${FRONTEND_PORT}`,
      url: APP_URL,
      reuseExistingServer: false,
      timeout: 120_000,
      env: { NEXT_PUBLIC_API_URL: API_URL, PM_E2E_DIST_DIR: ".next-e2e-central" },
    },
  ],
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
