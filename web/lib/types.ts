/**
 * TypeScript mirrors of the Phase 2b-api response DTOs (see
 * src/research_platform/api/schemas.py). These type the wire contract end to
 * end. By construction NONE of them carry the stock surrogate int PK — only the
 * `uuid` + `isin` identity (HARD RULE 2). Snapshots are int-addressed by design.
 */

export type CoverageStatus = "candidate" | "active" | "dropped";

export const COVERAGE_STATUSES: CoverageStatus[] = [
  "candidate",
  "active",
  "dropped",
];

export function isCoverageStatus(v: string): v is CoverageStatus {
  return (COVERAGE_STATUSES as string[]).includes(v);
}

/** StockSummaryResponse — lean list item. */
export interface StockSummary {
  uuid: string;
  isin: string;
  ticker: string;
  name: string;
  exchange: string;
  sector: string | null;
  status: CoverageStatus;
}

/** SnapshotRefResponse — one entry on a stock's snapshot timeline. */
export interface SnapshotRef {
  snapshot_id: number;
  as_of: string; // ISO date
}

/** StatusTransitionResponse — one audited coverage-status change. */
export interface StatusTransition {
  from_status: CoverageStatus;
  to_status: CoverageStatus;
  occurred_at: string; // ISO datetime
  reason: string;
}

/** StockResponse — full aggregate view. */
export interface Stock {
  uuid: string;
  isin: string;
  ticker: string;
  name: string;
  exchange: string;
  sector: string | null;
  status: CoverageStatus;
  snapshot_refs: SnapshotRef[];
  transition_history: StatusTransition[];
  created_at: string | null;
  updated_at: string | null;
}

/** SnapshotLeafResponse — a single immutable ledger snapshot leaf. */
export interface SnapshotLeaf {
  snapshot_id: number;
  isin: string;
  as_of: string;
  kind: string;
  inputs: Record<string, unknown>;
  source_versions: Record<string, unknown>;
  code_version: string;
  content_hash: string;
  created_at: string | null;
}
