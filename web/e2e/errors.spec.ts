import { test, expect } from "@playwright/test";
import { loadManifest } from "./manifest";

const m = loadManifest();

test.describe("404 paths render the terminal error, not Next's default", () => {
  test("unknown isin -> terminal STOCK NOT FOUND", async ({ page }) => {
    const res = await page.goto(`/stocks/${m.unknown_isin}`);
    expect(res?.status()).toBe(404);
    await expect(page.getByText("STOCK NOT FOUND")).toBeVisible();
    // Not the framework default.
    await expect(page.getByText("This page could not be found")).toHaveCount(0);
  });

  test("cross-stock snapshot -> terminal SNAPSHOT NOT FOUND (ownership)", async ({
    page,
  }) => {
    // snapshot belongs to owner_isin (target); requested under other_isin
    // (candidate) -> the API 404s on OWNERSHIP, not mere non-existence.
    const { other_isin, snapshot_id } = m.cross_stock;
    const res = await page.goto(
      `/stocks/${other_isin}/snapshots/${snapshot_id}`,
    );
    expect(res?.status()).toBe(404);
    await expect(page.getByText("SNAPSHOT NOT FOUND")).toBeVisible();

    // Proof the pair is a real cross-stock case: the SAME snapshot_id under its
    // OWNER's isin resolves 200 (so the 404 above is ownership, not existence).
    const ok = await page.goto(
      `/stocks/${m.cross_stock.owner_isin}/snapshots/${snapshot_id}`,
    );
    expect(ok?.status()).toBe(200);
  });
});
