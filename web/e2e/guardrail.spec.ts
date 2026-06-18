import { test, expect, type Page } from "@playwright/test";
import { loadManifest } from "./manifest";

const m = loadManifest();

/**
 * The integer VALUES visible to a user on a page: every `\d+` token in the
 * rendered text, plus every href, plus the URL itself. Mirrors the api
 * guardrail's `_all_ints` scan, on the DOM side.
 */
async function renderedInts(page: Page): Promise<Set<number>> {
  const text = await page.evaluate(() => {
    const hrefs = Array.from(document.querySelectorAll("a[href]"))
      .map((a) => (a as HTMLAnchorElement).getAttribute("href") ?? "")
      .join(" ");
    return `${document.body.innerText}\n${hrefs}\n${location.href}`;
  });
  const tokens = text.match(/\d+/g) ?? [];
  return new Set(tokens.map((t) => Number(t)));
}

test.describe("HARD RULE 2 — stock int PK never in a URL or the DOM", () => {
  test("positive control proves the scan reads rendered ints, THEN sentinel is absent", async ({
    page,
  }) => {
    // Leaf page legitimately renders snapshot_ids (int-addressed by design).
    await page.goto(
      `/stocks/${m.target.isin}/snapshots/${m.target.leaf_snapshot_id}`,
    );
    const leafInts = await renderedInts(page);

    // POSITIVE CONTROL (ADJUSTMENT 1): the scanner DOES find a known-rendered,
    // distinctive int. Without this, "sentinel absent" could pass trivially on a
    // page that renders no ints at all.
    expect(leafInts.has(m.target.leaf_snapshot_id)).toBe(true);

    // ...only now is the sentinel's absence a real result.
    expect(leafInts.has(m.sentinel_pk)).toBe(false);
  });

  test("sentinel pk absent across list + detail + leaf; URLs are isin-keyed", async ({
    page,
  }) => {
    // Detail: the richest int surface (full snapshot timeline).
    await page.goto(`/stocks/${m.target.isin}`);
    expect(page.url()).toContain(m.target.isin);
    expect(page.url()).not.toContain(String(m.sentinel_pk));
    const detailInts = await renderedInts(page);
    // Positive control here too: the timeline's snapshot_ids really are rendered.
    for (const sid of m.target.snapshot_ids) {
      expect(detailInts.has(sid)).toBe(true);
    }
    expect(detailInts.has(m.sentinel_pk)).toBe(false);

    // List: every detail link is keyed by isin, never the int pk.
    await page.goto("/stocks");
    const hrefs = await page
      .locator("tbody a")
      .evaluateAll((els) =>
        els.map((e) => (e as HTMLAnchorElement).getAttribute("href") ?? ""),
      );
    expect(hrefs.length).toBeGreaterThan(0);
    for (const href of hrefs) {
      expect(href).not.toContain(String(m.sentinel_pk));
    }
    expect((await renderedInts(page)).has(m.sentinel_pk)).toBe(false);
  });
});
