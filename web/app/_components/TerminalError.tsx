import Link from "next/link";

/**
 * Terminal-style error panel rendered for not-found states (unknown isin,
 * unknown / cross-stock snapshot). Deliberately NOT Next's default 404 page.
 */
export function TerminalError({
  code,
  title,
  detail,
}: {
  code: string;
  title: string;
  detail?: string;
}) {
  return (
    <div className="data border border-border bg-bg-elevated p-6">
      <div className="text-sm text-status-dropped">
        <span className="text-status-candidate">!</span> {code}
      </div>
      <h1 className="mt-2 text-lg text-fg">{title}</h1>
      {detail ? <p className="mt-1 text-sm text-fg-dim">{detail}</p> : null}
      <p className="mt-4 text-sm">
        <Link href="/stocks">&larr; back to coverage list</Link>
      </p>
    </div>
  );
}
