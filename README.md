# Equity Research Platform — Phase 0 spine

The thinnest end-to-end vertical slice that proves the architectural spine of the
platform described in [`Architecture_Decision_Record.md`](Architecture_Decision_Record.md)
and [`Implementation_Blueprint_v1.md`](Implementation_Blueprint_v1.md): a modular
monolith with a **hexagonal (ports-and-adapters)** architecture around an
**immutable, reproducible research ledger**.

> Phase 0 builds the spine only. **No** LLM, report generation, frontend, async
> jobs, drift tracking, or live data fetching — those are later phases.

## What the spine proves

```
file (JSON/CSV)
   │  DocumentPort  (ingestion adapter — the only "messy world" code)
   ▼
ParsedDocument ──► ledger.freeze_snapshot ──► Snapshot (content-hashed, version-stamped)
   │                  (pure domain logic)            │
   │                                                 │ RepositoryPort (storage adapter)
   ▼                                                 ▼
ValuationPort.run(inputs, assumptions) ◄──────  reload from Postgres (immutable row)
   │  DCF (pure function)
   ▼
ValuationRun ──► store ──► reload ──► re-derive ──► assert identical  ✅ reproducible
```

Every output is a pure function of versioned, immutable, stored inputs (ADR-003).

## Architectural guarantees (enforced, not aspirational)

| Guarantee | How it is enforced |
|---|---|
| **Domain purity** — `domain/` imports only stdlib, pydantic, and its own `ports` | `import-linter` contract `domain-is-pure` (fails the build) |
| **Inward-only dependencies** | `import-linter` `layers` contract; ports are `typing.Protocol`s the domain owns |
| **SQLAlchemy confined to `storage/`** | layering contract + code review |
| **Snapshots/ValuationRuns immutable at the DB** | PostgreSQL trigger (`block_mutation`) installed by Alembic migration `0002`; rejects `UPDATE`/`DELETE` |
| **Reproducibility** | `tests/reproducibility/` + the e2e script re-derive from a stored snapshot and assert identical results |
| **Single composition root** | only [`app/composition.py`](src/research_platform/app/composition.py) wires adapters to ports |

## Layout

```
src/research_platform/
  domain/            ★ pure: stdlib + pydantic + own ports only
    models.py          Stock, Snapshot, ValuationRun, ParsedDocument (Pydantic; ledger artifacts frozen)
    ledger.py          freeze_snapshot — content hash + version stamp (pure, no I/O)
    version.py         CODE_VERSION constant
    ports/             DocumentPort, RepositoryPort, ValuationPort (typing.Protocol)
  ingestion/
    document_adapter.py  FileDocumentAdapter — JSON/CSV -> ParsedDocument (fragility quarantined)
  valuation/
    dcf.py             DCFModel — deterministic pure-function DCF (a ValuationPort)
  storage/             SQLAlchemy lives ONLY here
    models.py          DeclarativeBase + Mapped models
    repository.py      PostgresRepository (RepositoryPort)
    db.py              engine/session wiring
    migrations/        Alembic (0001 tables, 0002 immutability triggers)
  app/
    composition.py     the single composition root
tests/{domain,storage,reproducibility}
scripts/phase0_e2e.py
data/                  sample_infy.json, sample_infy.csv, sample_assumptions.json
```

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) (Python packaging; also provisions Python 3.12)
- Docker (for the local PostgreSQL)
- Optional: `make` (raw equivalents are listed below for Windows/PowerShell)

## Quick start

```bash
make install      # uv sync — create venv, install deps
make up           # start PostgreSQL (docker-compose), wait until healthy
make migrate      # alembic upgrade head — tables + immutability triggers
make test         # import-linter + full pytest suite
make e2e          # the end-to-end reproducibility script
```

### Without `make` (PowerShell / Windows)

```powershell
uv sync
docker compose up -d db          # Postgres on host port 5434 (avoids native 5432)
uv run alembic upgrade head
uv run lint-imports              # boundary contracts
uv run pytest -q                 # all tests (DB tests skip automatically if DB is down)
uv run python scripts/phase0_e2e.py
```

The database URL defaults to
`postgresql+psycopg://research:research@localhost:5434/research`; override with the
`DATABASE_URL` environment variable.

## The six Phase 0 definition-of-done criteria

1. `pytest tests/domain` passes with **no network and no database** — `uv run pytest tests/domain`.
2. `import-linter` passes — `uv run lint-imports`.
3. A stock + snapshot can be created from an uploaded file and stored — see the e2e script.
4. The DB immutability trigger rejects `UPDATE`/`DELETE` on the ledger tables — `tests/storage/`.
5. `tests/reproducibility` passes: a valuation re-derives identically from its stored snapshot, version stamps intact.
6. One end-to-end script: `scripts/phase0_e2e.py` (`make e2e`).

DB-backed tests are marked `db` and **skip automatically** when Postgres is
unreachable, so the domain suite always runs anywhere. To run only the
infrastructure-free proof: `uv run pytest -m "not db"`.

## Notes / recorded deviations from the blueprint

- **Top-level package renamed `platform` → `research_platform`.** The blueprint
  named the package `platform`, which shadows Python's stdlib `platform` module
  and makes the package un-importable under a src-layout install (verified:
  `'platform' is not a package`). Renaming is the standard fix and is recorded as
  **ADR-009** in the Architecture Decision Record. All boundaries and intent are
  unchanged.
- **Container host port `5434`** (not 5432) to avoid colliding with a native
  PostgreSQL install; container-internal port is still 5432.
