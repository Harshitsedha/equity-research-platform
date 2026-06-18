import Link from "next/link";
import { apiGetOr404 } from "@/lib/api";
import type { Stock } from "@/lib/types";
import { StatusBadge } from "../../_components/StatusBadge";

export const dynamic = "force-dynamic";

/**
 * /stocks/[isin] — the full aggregate view: identity header, snapshot timeline
 * (each row links to the leaf), and the audited transition history. Unknown
 * isin -> apiGetOr404 -> notFound() -> ./not-found.tsx (terminal error).
 */
export default async function StockDetailPage({
  params,
}: {
  params: Promise<{ isin: string }>;
}) {
  const { isin } = await params;
  const stock = await apiGetOr404<Stock>(`/stocks/${encodeURIComponent(isin)}`);

  return (
    <div className="space-y-8">
      <header className="border border-border bg-bg-elevated p-4">
        <div className="flex items-baseline gap-3">
          <h1 className="font-mono text-xl font-semibold text-fg">
            {stock.ticker}
          </h1>
          <span className="data text-sm text-fg-dim">{stock.name}</span>
          <span className="ml-auto">
            <StatusBadge status={stock.status} />
          </span>
        </div>
        <dl className="data mt-4 grid grid-cols-1 gap-x-8 gap-y-1 text-sm sm:grid-cols-2">
          <Field label="isin" value={stock.isin} />
          <Field label="uuid" value={stock.uuid} />
          <Field label="exchange" value={stock.exchange} />
          <Field label="sector" value={stock.sector ?? "—"} />
        </dl>
      </header>

      <section>
        <h2 className="mb-2 font-sans text-xs uppercase tracking-wider text-fg-dim">
          snapshot timeline · {stock.snapshot_refs.length}
        </h2>
        {stock.snapshot_refs.length === 0 ? (
          <p className="data text-sm text-fg-dim">no snapshots on the ledger.</p>
        ) : (
          <table className="term-table">
            <thead>
              <tr>
                <th>as_of</th>
                <th className="num">snapshot_id</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {stock.snapshot_refs.map((ref) => (
                <tr key={ref.snapshot_id}>
                  <td>{ref.as_of}</td>
                  <td className="num">{ref.snapshot_id}</td>
                  <td>
                    <Link
                      href={`/stocks/${encodeURIComponent(stock.isin)}/snapshots/${ref.snapshot_id}`}
                    >
                      view leaf &rarr;
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2 className="mb-2 font-sans text-xs uppercase tracking-wider text-fg-dim">
          transition history · {stock.transition_history.length}
        </h2>
        {stock.transition_history.length === 0 ? (
          <p className="data text-sm text-fg-dim">no status transitions.</p>
        ) : (
          <table className="term-table">
            <thead>
              <tr>
                <th>occurred_at</th>
                <th>from</th>
                <th>to</th>
                <th>reason</th>
              </tr>
            </thead>
            <tbody>
              {stock.transition_history.map((t, i) => (
                <tr key={i}>
                  <td className="text-fg-dim">{t.occurred_at}</td>
                  <td>
                    <StatusBadge status={t.from_status} />
                  </td>
                  <td>
                    <StatusBadge status={t.to_status} />
                  </td>
                  <td>{t.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-3">
      <dt className="w-20 shrink-0 text-fg-dim">{label}</dt>
      <dd className="break-all text-fg">{value}</dd>
    </div>
  );
}
