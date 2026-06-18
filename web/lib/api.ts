import "server-only";
import { notFound } from "next/navigation";

/**
 * The ONLY bridge from the web app to the FastAPI backend (HARD RULE 3).
 *
 * `import "server-only"` makes this module a build error if it is ever pulled
 * into a Client Component — the API origin (`FASTAPI_BASE_URL`, a server-only
 * env var, never `NEXT_PUBLIC_`) therefore can never reach the browser. Every
 * page fetches EXCLUSIVELY through here; no page issues a bare `fetch` to the
 * API or relies on Next's default caching.
 *
 * `cache: "no-store"` on EVERY request (ADJUSTMENT 2): coverage data is mutable,
 * so a read-only viewer must never serve a stale Next data cache (e.g. a stock
 * transitioned to `dropped` out-of-band must not still render `active`).
 */
const BASE = process.env.FASTAPI_BASE_URL;

export class ApiNotFoundError extends Error {
  constructor(path: string) {
    super(`upstream 404 for ${path}`);
    this.name = "ApiNotFoundError";
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  if (!BASE) {
    throw new Error(
      "FASTAPI_BASE_URL is not set — the server cannot reach the API.",
    );
  }
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (res.status === 404) {
    throw new ApiNotFoundError(path);
  }
  if (!res.ok) {
    throw new Error(`upstream ${res.status} for ${path}`);
  }
  return (await res.json()) as T;
}

/**
 * Fetch, mapping the API's 404 onto Next's `notFound()` so the route renders its
 * terminal-style `not-found.tsx` rather than a generic error. Any other failure
 * propagates (a real upstream/infra fault, not a missing resource).
 */
export async function apiGetOr404<T>(path: string): Promise<T> {
  try {
    return await apiGet<T>(path);
  } catch (err) {
    if (err instanceof ApiNotFoundError) {
      notFound();
    }
    throw err;
  }
}
