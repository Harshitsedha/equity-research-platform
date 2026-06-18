import Link from "next/link";
import { apiGet } from "@/lib/api";
import {
  COVERAGE_STATUSES,
  isCoverageStatus,
  type StockSummary,
} from "@/lib/types";
import { StatusBadge } from "../_components/StatusBadge";

export const dynamic = "force-dynamic";

/**
 * /stocks — dense coverage table. Optional ?status= filter is passed straight
 * to the API (the API owns the filter + rejects bogus values). Each row links
 * to the detail view BY ISIN (HARD RULE 1/2 — never the int PK).
 */
export default async function StocksPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const { status } = await searchParams;
  const active = status && isCoverageStatus(status) ? status : undefined;
  const qs = active ? `?status=${encodeURIComponent(active)}` : "";
  const stocks = await apiGet<StockSummary[]>(`/stocks${qs}`);

  return (
    <div>
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="font-mono text-base text-fg">
          coverage
          <span className="text-fg-dim"> · {stocks.length} stocks</span>
        </h1>
        <div className="data flex items-center gap-3 text-xs">
          <FilterLink label="all" href="/stocks" activeNow={!active} />
          {COVERAGE_STATUSES.map((s) => (
            <FilterLink
              key={s}
              label={s}
              href={`/stocks?status=${s}`}
              activeNow={active === s}
            />
          ))}
        </div>
      </div>

      <table className="term-table">
        <thead>
          <tr>
            <th>ticker</th>
            <th>isin</th>
            <th>name</th>
            <th>exchange</th>
            <th>sector</th>
            <th>status</th>
          </tr>
        </thead>
        <tbody>
          {stocks.map((s) => (
            <tr key={s.uuid}>
              <td>
                <Link href={`/stocks/${s.isin}`} className="font-medium">
                  {s.ticker}
                </Link>
              </td>
              <td className="text-fg-dim">{s.isin}</td>
              <td>{s.name}</td>
              <td className="text-fg-dim">{s.exchange}</td>
              <td className="text-fg-dim">{s.sector ?? "—"}</td>
              <td>
                <StatusBadge status={s.status} />
              </td>
            </tr>
          ))}
          {stocks.length === 0 ? (
            <tr>
              <td colSpan={6} className="text-fg-dim">
                no stocks{active ? ` with status ${active}` : ""}
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

function FilterLink({
  label,
  href,
  activeNow,
}: {
  label: string;
  href: string;
  activeNow: boolean;
}) {
  return (
    <Link
      href={href}
      className={`uppercase tracking-wider ${
        activeNow ? "!text-fg !no-underline" : "text-fg-dim"
      }`}
    >
      {label}
    </Link>
  );
}
