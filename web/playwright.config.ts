import { defineConfig, devices } from "@playwright/test";

/**
 * E2E harness (kept LIGHT). Boot order:
 *   1. globalSetup: `uv run python web/e2e/seed_e2e.py` — migrate a CLEAN schema
 *      and seed deterministic fixtures + manifest.json (disposable-DB posture,
 *      same as the pytest suite).
 *   2. webServer[0]: the REAL FastAPI over uvicorn (no stub — a stub would
 *      defeat the guardrail) against that DB.
 *   3. webServer[1]: `next start`, pointed at the API via the SERVER-ONLY
 *      FASTAPI_BASE_URL. Next is built first (`next build`) so `start` serves a
 *      production build.
 */
const API_PORT = 8000;
const WEB_PORT = 3000;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      // The real backend. `--app-dir src` so the src-layout package imports.
      command: `uv run uvicorn research_platform.api.app:create_app --factory --host 127.0.0.1 --port ${API_PORT} --app-dir src`,
      cwd: "..",
      url: `http://127.0.0.1:${API_PORT}/stocks`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: `next start -p ${WEB_PORT}`,
      cwd: ".",
      url: `http://127.0.0.1:${WEB_PORT}/stocks`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        FASTAPI_BASE_URL: `http://127.0.0.1:${API_PORT}`,
      },
    },
  ],
});
