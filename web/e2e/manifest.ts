import { readFileSync } from "node:fs";
import path from "node:path";

/** Typed view of web/e2e/manifest.json (written by seed_e2e.py). */
export interface Manifest {
  sentinel_pk: number;
  target: {
    isin: string;
    ticker: string;
    name: string;
    status: "active";
    snapshot_ids: number[];
    leaf_snapshot_id: number;
    as_ofs: string[];
  };
  candidate: { isin: string; ticker: string; status: "candidate" };
  dropped: { isin: string; ticker: string; status: "dropped" };
  cross_stock: { owner_isin: string; other_isin: string; snapshot_id: number };
  unknown_isin: string;
}

export function loadManifest(): Manifest {
  const file = path.join(__dirname, "manifest.json");
  return JSON.parse(readFileSync(file, "utf-8")) as Manifest;
}
