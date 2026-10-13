/**
 * Global setup for every Playwright config: refuse to run unless the backend
 * is the isolated E2E one, and fail the run if a development database changed.
 */
import { createHash } from "node:crypto";
import { existsSync, readFileSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const REPO_ROOT = path.resolve(__dirname, "../..");
const DEVELOPMENT_DATABASES = [
  path.join(REPO_ROOT, "backend", "pm.db"),
  path.join(REPO_ROOT, "pm.db"),
];

const fingerprint = (file: string) =>
  existsSync(file) ? createHash("sha256").update(readFileSync(file)).digest("hex") : "absent";

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
  if (DEVELOPMENT_DATABASES.some((db) => existsSync(db) && realpathSync(db) === realpathSync(dbPath))) {
    throw new Error("The E2E database is a development database - refusing to run.");
  }

  const before = DEVELOPMENT_DATABASES.map(fingerprint);

  return async () => {
    const changed = DEVELOPMENT_DATABASES.filter((db, i) => fingerprint(db) !== before[i]);
    // The E2E data is disposable: remove its temp dir (only ever pm-e2e-* in tmp).
    const dir = path.dirname(dbPath);
    if (path.basename(dir).startsWith("pm-e2e-") && isInside(dir, tmpdir())) {
      rmSync(dir, { recursive: true, force: true });
    }
    if (changed.length > 0) {
      throw new Error(
        `A development database changed during the E2E run: ${changed.join(", ")}. ` +
          "If you were using the dev app at the same time, that explains it; otherwise investigate."
      );
    }
  };
}
