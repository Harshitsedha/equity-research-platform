import { test, expect } from "@playwright/test";
import { loadManifest } from "./manifest";

const m = loadManifest();

// Design-system semantic status colours, asserted as RENDERED computed color.
const EXPECTED: Record<string, string> = {
  active: "rgb(52, 211, 153)", // --status-active  green
  candidate: "rgb(251, 191, 36)", // --status-candidate amber
  dropped: "rgb(107, 114, 128)", // --status-dropped  grey
};

test("status badges render the per-state semantic colour", async ({ page }) => {
  await page.goto("/stocks");
  for (const status of ["active", "candidate", "dropped"] as const) {
    const badge = page.locator(`[data-status="${status}"]`).first();
    await expect(badge).toBeVisible();
    const color = await badge.evaluate(
      (el) => getComputedStyle(el).color,
    );
    expect(color).toBe(EXPECTED[status]);
  }
});
