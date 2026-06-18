import type { CoverageStatus } from "@/lib/types";

/**
 * Coverage status, colour-coded per the design system:
 * active=green, candidate=amber, dropped=dim grey. Server component (no client
 * JS). The `data-status` attribute gives the E2E suite a stable hook to assert
 * per-state colour rendering.
 */
const STATUS_CLASS: Record<CoverageStatus, string> = {
  active: "text-status-active",
  candidate: "text-status-candidate",
  dropped: "text-status-dropped",
};

export function StatusBadge({ status }: { status: CoverageStatus }) {
  return (
    <span
      data-status={status}
      className={`data inline-flex items-center gap-1.5 text-xs uppercase tracking-wider ${STATUS_CLASS[status]}`}
    >
      <span aria-hidden className="inline-block h-1.5 w-1.5 bg-current" />
      {status}
    </span>
  );
}
