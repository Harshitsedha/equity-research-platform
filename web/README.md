# web/ — Coverage Terminal (Phase 2b-ui)

A read-only Next.js (App Router) viewer over the three sealed 2b-api endpoints.
Terminal aesthetic; no writes, no auth, no LLM, no live data (see **ADR-014**).

## Boundary (the one rule)

Data is fetched **server-side in React Server Components** from a **server-only**
`FASTAPI_BASE_URL` (never `NEXT_PUBLIC_`) through [`lib/api.ts`](lib/api.ts)
(`import "server-only"`, `cache: "no-store"`). The browser never holds the API
origin. Routes mirror the API identity contract — `/stocks` →
`/stocks/[isin]` → `/stocks/[isin]/snapshots/[snapshotId]` — and the stock
surrogate int PK appears in no URL or DOM.

## Local dev

```bash
# from the repo root: bring up Postgres + the API
docker compose up -d db
uv run alembic upgrade head
uv run uvicorn research_platform.api.app:create_app --factory --app-dir src --port 8000

# in web/
npm install
cp .env.example .env.local        # FASTAPI_BASE_URL=http://127.0.0.1:8000
npm run dev                        # http://localhost:3000
```

## E2E (Playwright)

The harness is self-contained: `globalSetup` seeds a freshly-migrated disposable
DB via the existing Python repositories (`e2e/seed_e2e.py` → `manifest.json`),
then `webServer` boots the **real** uvicorn API and `next start` against it.

```bash
npm install
npx playwright install chromium
npm run build
npm run test:e2e
```

Covers: the three routes render seeded data; the 404 path renders the terminal
error (unknown isin + cross-stock ownership); the **guardrail** (no stock int pk
in any URL/DOM, with a positive control); per-state status colour-coding.
