import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  // The E2E dev server uses its own build dir: `next dev` locks its dist dir,
  // and the developer's own `next dev` may hold the default one.
  distDir: process.env.PM_E2E_DIST_DIR ?? "out",
};

export default nextConfig;
