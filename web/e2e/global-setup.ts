import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";

/**
 * Seed the disposable DB before the servers come up: migrate a clean schema and
 * write manifest.json via the existing Python repositories. Synchronous — it
 * must finish before any spec navigates.
 */
export default function globalSetup() {
  const repoRoot = path.resolve(__dirname, "..", "..");
  const seed = path.join("web", "e2e", "seed_e2e.py");
  execFileSync("uv", ["run", "python", seed], {
    cwd: repoRoot,
    stdio: "inherit",
  });
  const manifest = path.join(__dirname, "manifest.json");
  if (!existsSync(manifest)) {
    throw new Error(`seed did not write ${manifest}`);
  }
}
