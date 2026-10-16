import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    // Build dir of the isolated E2E dev server (playwright.config.ts).
    ".next-e2e/**",
    // Generated dev build dir (.next-eval).
    ".next-eval/**",
    // Generated build dir used for validation checks (.next-check).
    ".next-check/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
