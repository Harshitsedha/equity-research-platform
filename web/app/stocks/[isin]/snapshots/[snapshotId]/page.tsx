import Link from "next/link";
import { apiGetOr404 } from "@/lib/api";
import type { SnapshotLeaf } from "@/lib/types";

export const dynamic = "force-dynamic";

/**
 * /stocks/[isin]/snapshots/[snapshotId] — one immutable ledger leaf. The API
 * returns 404 for an unknown snapshot OR one owned by a DIFFERENT stock under
 * this isin (ownership, not mere existence) — both surface the terminal error.
 */
export default async function SnapshotLeafPage({
  params,
}: {
  params: Promise<{ isin: string; snapshotId: string }>;
}) {
  const { isin, snapshotId } = await params;
  const leaf = await apiGetOr404<SnapshotLeaf>(
    `/stocks/${encodeURIComponent(isin)}/snapshots/${encodeURIComponent(snapshotId)}`,
  );

  return (
    <div className="space-y-6">
      <div className="data text-sm text-fg-dim">
        <Link href={`/stocks/${encodeURIComponent(leaf.isin)}`}>
          &larr; {leaf.isin}
        </Link>
        <span> / snapshot {leaf.snapshot_id}</span>
      </div>

      <header className="border border-border bg-bg-elevated p-4">
        <h1 className="font-mono text-lg text-fg">
          snapshot <span className="num">#{leaf.snapshot_id}</span>
        </h1>
        <dl className="data mt-4 grid grid-cols-1 gap-x-8 gap-y-1 text-sm sm:grid-cols-2">
          <Field label="isin" value={leaf.isin} />
          <Field label="as_of" value={leaf.as_of} />
          <Field label="kind" value={leaf.kind} />
          <Field label="code_version" value={leaf.code_version} />
          <Field label="content_hash" value={leaf.content_hash} />
          <Field label="created_at" value={leaf.created_at ?? "—"} />
        </dl>
      </header>

      <Block title="inputs" value={leaf.inputs} />
      <Block title="source_versions" value={leaf.source_versions} />
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-3">
      <dt className="w-28 shrink-0 text-fg-dim">{label}</dt>
      <dd className="break-all text-fg">{value}</dd>
    </div>
  );
}

function Block({
  title,
  value,
}: {
  title: string;
  value: Record<string, unknown>;
}) {
  return (
    <section>
      <h2 className="mb-2 font-sans text-xs uppercase tracking-wider text-fg-dim">
        {title}
      </h2>
      <pre className="data overflow-x-auto border border-border bg-bg-elevated p-3 text-sm text-fg">
        {JSON.stringify(value, null, 2)}
      </pre>
    </section>
  );
}
