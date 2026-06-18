import { test, expect } from "@playwright/test";
import { loadManifest } from "./manifest";

const m = loadManifest();

test.describe("the three routes render seeded data", () => {
  test("/stocks lists seeded stocks (isin-keyed rows)", async ({ page }) => {
    await page.goto("/stocks");
    // All three seeded tickers present.
    await expect(page.getByText(m.target.ticker)).toBeVisible();
    await expect(page.getByText(m.candidate.ticker)).toBeVisible();
    await expect(page.getByText(m.dropped.ticker)).toBeVisible();
    // The row links to the detail view BY ISIN.
    const link = page.getByRole("link", { name: m.target.ticker });
    await expect(link).toHaveAttribute("href", `/stocks/${m.target.isin}`);
  });

  test("?status= filter narrows to one state", async ({ page }) => {
    await page.goto("/stocks?status=candidate");
    await expect(page.getByText(m.candidate.ticker)).toBeVisible();
    await expect(page.getByText(m.target.ticker)).toHaveCount(0);
    await expect(page.getByText(m.dropped.ticker)).toHaveCount(0);
  });

  test("/stocks/[isin] shows identity, timeline and transitions", async ({
    page,
  }) => {
    await page.goto(`/stocks/${m.target.isin}`);
    await expect(
      page.getByRole("heading", { name: m.target.ticker }),
    ).toBeVisible();
    // Identity contract: isin rendered.
    await expect(page.getByText(m.target.isin)).toBeVisible();
    // The full timeline is present: one "view leaf" link per seeded snapshot.
    await expect(page.getByRole("link", { name: "view leaf", exact: false })).toHaveCount(
      m.target.snapshot_ids.length,
    );
    // A transition row (candidate -> active) is present.
    await expect(page.getByText("promote")).toBeVisible();
  });

  test("/stocks/[isin]/snapshots/[id] renders the leaf", async ({ page }) => {
    const sid = m.target.leaf_snapshot_id;
    await page.goto(`/stocks/${m.target.isin}/snapshots/${sid}`);
    await expect(
      page.getByRole("heading", { name: `snapshot #${sid}` }),
    ).toBeVisible();
    await expect(page.getByText("content_hash")).toBeVisible();
    await expect(page.getByText("code_version")).toBeVisible();
    // inputs block rendered.
    await expect(page.getByText("revenue")).toBeVisible();
  });
});
