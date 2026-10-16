/**
 * Global setup for every Playwright config: refuse to run unless the backend
 * is the isolated E2E one on a temporary database. Development databases are
 * never opened or hashed (a concurrent dev session may legitimately change them).
 */
import { existsSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const REPO_ROOT = path.resolve(__dirname, "../..");
const DEVELOPMENT_DATABASES = [
  path.join(REPO_ROOT, "backend", "pm.db"),
  path.join(REPO_ROOT, "pm.db"),
];

const isInside = (child: string, parent: string) => {
  const relative = path.relative(realpathSync(parent), realpathSync(child));
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
};

export default async function e2eSetup() {
  const apiUrl = process.env.PM_E2E_API_URL;
  const dbPath = process.env.PM_E2E_DB_PATH;
  if (!apiUrl || !dbPath) {
    throw new Error(
      "E2E isolation is not configured (PM_E2E_API_URL / PM_E2E_DB_PATH missing). " +
        "Run through playwright.config.ts; there is no fallback to the development backend."
    );
  }

  const health = await (await fetch(`${apiUrl}/api/health`)).json();
  if (health.test_mode !== true) {
    throw new Error(
      `The backend at ${apiUrl} is not in E2E test mode (PM_E2E=1) - refusing to run ` +
        "tests that would modify its database."
    );
  }
  if (!existsSync(dbPath) || !isInside(dbPath, tmpdir())) {
    throw new Error(`The E2E database is not the expected temporary file (${dbPath}).`);
  }
  // Path comparison only: the development databases are never opened or read.
  const resolvedDb = path.join(realpathSync(path.dirname(dbPath)), path.basename(dbPath));
  if (DEVELOPMENT_DATABASES.some((db) => path.resolve(db) === resolvedDb)) {
    throw new Error("The E2E database is a development database - refusing to run.");
  }

  return async () => {
    if (!existsSync(dbPath) || !isInside(dbPath, tmpdir())) {
      throw new Error(`The E2E database left its temporary location during the run (${dbPath}).`);
    }
    // The E2E data is disposable: remove its temp dir (only ever pm-e2e-* in tmp).
    const dir = path.dirname(dbPath);
    if (path.basename(dir).startsWith("pm-e2e-") && isInside(dir, tmpdir())) {
      rmSync(dir, { recursive: true, force: true });
    }
  };
}
