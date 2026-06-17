# Equity Research Platform — Implementation Blueprint v1

*Companion to the Architecture Decision Record. This translates the locked architecture into concrete, buildable specifications: the data schema (as reference + SQLAlchemy models), the enforced module skeleton, the boundary-enforcement config, and the Phase 0 vertical-slice plan.*

*This is a **design document you build from** — not a running system. Phase 0 (the actual first code) is defined at the end.*

---

## Front matter — completing the decision record

### ADR-007 — Async jobs: durable Postgres-backed queue (arq)

**Decision.** Background jobs persist as durable rows in PostgreSQL; an **arq** worker (async, pairs with FastAPI) executes them. Idempotency comes from the ledger (a job's output is keyed to an immutable snapshot, so re-runs are safe). Retries on fragile boundaries (NSE/LLM/PDF) use bounded exponential backoff; final failure is a **loud, visible failed-job state**, never a silent drop.

**Rejected.** Celery+Redis broker and event-streaming (Kafka) — both add infrastructure and failure modes for throughput this workload doesn't have. A hand-rolled poller — rejected as reinventing arq.

**Consequences.** ~~No new datastore (Postgres is queue + ledger + cache).~~ *(superseded — see Amendment 2026-06-17.)* Slow paths async; fast paths (ledger reads) synchronous. Jobs are observable end-to-end.

> **Amendment — 2026-06-17 (supersedes the "no new datastore" / "durable Postgres-backed queue" wording above).**
>
> **What changed.** Jobs use **arq with a Redis broker**. **PostgreSQL remains the system of record / ledger / cache / idempotency store** — all immutable research data and the idempotency anchor (`report.snapshot_id` unique constraint) stay in Postgres. Redis is **solely the arq job broker** (ephemeral queue state); no research data lives in it and losing it loses no ledger history.
>
> **Why.** The original wording was factually wrong: **arq requires Redis** — it cannot run on Postgres — so "Postgres-backed queue (arq)" was internally contradictory (flagged during the Phase 1A build). The ADR-008 *consolidation* principle was about avoiding **unfamiliar** infrastructure, not forbidding all additions; **Redis is already operated by this operator in production**, so its marginal operational surface area is low. Adding a familiar broker for the one component that mandates it honours the principle better than contorting around it.
>
> **What did NOT change.** Idempotency still comes from the ledger/snapshot (re-runs safe). Retries are still bounded exponential backoff; final failure is still a loud, visible failed-job state. The dependency rule is unaffected — the broker lives behind the `jobs`/composition layers; the domain never sees arq or Redis (enforced by import-linter). Local dev gets a Redis service in `docker-compose.yml`, configurable via `REDIS_URL`; the test path proves job logic by direct invocation and needs neither Postgres nor Redis for the `-m "not db"` set.

### ADR-008 — Tech stack: consolidation over novelty

**Decision.** Python backend; FastAPI (API); PostgreSQL + JSONB (persistence/queue/cache); SQLAlchemy 2.x (in Storage adapter only); arq (jobs); Next.js/React (frontend); provider-agnostic LLM port; Hetzner VPS + Docker (single instance); structlog + tracing (observability). The Research Domain is **framework-free pure Python + Pydantic**.

**Rejected.** A second backend language; an ORM in the domain; Kubernetes/cloud-scale orchestration — all wrong-sized for a solo operator.

**Consequences.** Minimal surface area; reuses the operator's existing stack. Every external dependency sits behind a port and is swappable.

---

## Part 1 — The data schema

### 1.1 Design rules carried from the architecture

1. **Snapshots are immutable + append-only.** No `UPDATE`, no `DELETE`. Corrections create a new snapshot.
2. **Every derived row references the snapshot it used** (`snapshot_id` FK). No orphan numbers.
3. **Everything affecting an output is version-stamped** on the snapshot (data-source versions, code version, model + prompt version).
4. **Theses and assumption sets are versioned**, not overwritten.
5. **Reproducibility is a CI test:** a stored Report/ValuationRun must re-derive deterministically from its snapshot.

### 1.2 Entity reference (the shape)

| Entity | Mutability | Purpose | Key relationships |
|---|---|---|---|
| **Stock** | mutable (slow) | Persistent anchor; identity + durable business profile | has many everything below |
| **Snapshot** | **immutable** | Frozen, versioned inputs at a point in time | belongs to Stock; referenced by Report/ValuationRun |
| **Thesis** | versioned | The durable investment view (current + history) | belongs to Stock; has many ThesisClaim |
| **ThesisClaim** | versioned | One discrete, **measurable** assertion | belongs to Thesis; evaluated by DriftFlag |
| **Report** | **immutable** | Initiation or quarterly note, derived | belongs to Stock; references Snapshot |
| **AssumptionSet** | versioned | Named valuation assumptions | belongs to Stock; feeds ValuationRun |
| **ValuationRun** | **immutable** | One model execution + result | references Snapshot + AssumptionSet |
| **DriftFlag** | derived | A claim checked vs a later Snapshot | references ThesisClaim + Snapshot |
| **Note** | mutable | Operator's own research | belongs to Stock |
| **Job** | mutable (status) | Durable background-job record | optional FK to Stock |

### 1.3 ThesisClaim — the most important table

The claim is **structured so it can be machine-checked**, which is what makes drift tracking real. A claim is not a sentence; it is a metric + operator + threshold + horizon.

```
ThesisClaim:
  id
  thesis_id            -> Thesis
  statement            text         # human-readable: "CFO/PAT trends toward 1.0"
  metric_key           text         # machine key: "cfo_pat_ratio"
  comparator           enum         # >=, <=, trend_up, trend_down, within_band
  threshold            jsonb        # {"value": 0.8} or {"low": 0.9, "high": 1.1}
  horizon_quarters     int          # by when it should hold
  weight               int          # 1-10 importance to the thesis
  status               enum         # holding | breaking | broken | confirmed (derived)
  created_at, version
```

Drift tracking then becomes: for each claim, pull `metric_key` from the latest Snapshot, apply `comparator` vs `threshold`, write a `DriftFlag` if it fails. Almost free, *because* the claim was stored as data.

### 1.4 Snapshot — the ledger heart

```
Snapshot:
  id
  stock_id             -> Stock
  as_of                date          # the period this represents (e.g. Q3 FY26)
  kind                 enum          # quarterly | annual | adhoc
  inputs               jsonb         # normalized financials, prices, parsed filing data
  source_versions      jsonb         # {"upstox":"v3","pnsea":"1.2","filing":"<hash>"}
  code_version         text          # git sha of the logic at compute time
  model_version        text|null     # LLM model id, if any output used it
  prompt_version       text|null     # prompt template version, if any
  content_hash         text          # hash of inputs -> dedupe + integrity
  created_at
  # IMMUTABLE: enforced by DB trigger blocking UPDATE/DELETE
```

### 1.5 SQLAlchemy 2.x models (starting code — lives in the Storage adapter only)

```python
# storage/models.py  — SQLAlchemy lives HERE ONLY, never in the domain
from __future__ import annotations
import datetime as dt
from sqlalchemy import (String, Integer, Date, DateTime, ForeignKey, Enum, Text,
                        func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
import enum

class Base(DeclarativeBase): ...

class Stock(Base):
    __tablename__ = "stock"
    id:        Mapped[int]  = mapped_column(primary_key=True)
    ticker:    Mapped[str]  = mapped_column(String(32), unique=True, index=True)
    name:      Mapped[str]  = mapped_column(String(256))
    exchange:  Mapped[str]  = mapped_column(String(16), default="NSE")
    sector:    Mapped[str | None] = mapped_column(String(128))
    profile:   Mapped[dict] = mapped_column(JSONB, default=dict)   # durable business profile
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    snapshots:  Mapped[list[Snapshot]] = relationship(back_populates="stock")

class SnapshotKind(enum.Enum):
    quarterly = "quarterly"; annual = "annual"; adhoc = "adhoc"

class Snapshot(Base):
    __tablename__ = "snapshot"
    id:        Mapped[int]  = mapped_column(primary_key=True)
    stock_id:  Mapped[int]  = mapped_column(ForeignKey("stock.id"), index=True)
    as_of:     Mapped[dt.date] = mapped_column(Date)
    kind:      Mapped[SnapshotKind] = mapped_column(Enum(SnapshotKind))
    inputs:         Mapped[dict] = mapped_column(JSONB)
    source_versions:Mapped[dict] = mapped_column(JSONB, default=dict)
    code_version:   Mapped[str]  = mapped_column(String(64))
    model_version:  Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    content_hash:   Mapped[str]  = mapped_column(String(64), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stock: Mapped[Stock] = relationship(back_populates="snapshots")
    # immutability enforced at DB level (see 1.6)

class Thesis(Base):
    __tablename__ = "thesis"
    id:       Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stock.id"), index=True)
    version:  Mapped[int] = mapped_column(Integer, default=1)
    summary:  Mapped[str] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    claims: Mapped[list[ThesisClaim]] = relationship(back_populates="thesis")

class Comparator(enum.Enum):
    gte="gte"; lte="lte"; trend_up="trend_up"; trend_down="trend_down"; within_band="within_band"

class ThesisClaim(Base):
    __tablename__ = "thesis_claim"
    id:        Mapped[int] = mapped_column(primary_key=True)
    thesis_id: Mapped[int] = mapped_column(ForeignKey("thesis.id"), index=True)
    statement: Mapped[str] = mapped_column(Text)
    metric_key:Mapped[str] = mapped_column(String(64))
    comparator:Mapped[Comparator] = mapped_column(Enum(Comparator))
    threshold: Mapped[dict] = mapped_column(JSONB)
    horizon_quarters: Mapped[int] = mapped_column(Integer, default=4)
    weight:    Mapped[int] = mapped_column(Integer, default=5)
    status:    Mapped[str] = mapped_column(String(16), default="holding")
    thesis: Mapped[Thesis] = relationship(back_populates="claims")

class Report(Base):
    __tablename__ = "report"
    id:         Mapped[int] = mapped_column(primary_key=True)
    stock_id:   Mapped[int] = mapped_column(ForeignKey("stock.id"), index=True)
    snapshot_id:Mapped[int] = mapped_column(ForeignKey("snapshot.id"))   # the inputs it used
    kind:       Mapped[str] = mapped_column(String(16))                  # initiation | quarterly
    content:    Mapped[dict] = mapped_column(JSONB)                      # structured report
    code_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

# AssumptionSet, ValuationRun, DriftFlag, Note, Job follow the same patterns.
```

### 1.6 Immutability enforced at the database (not just convention)

```sql
-- snapshots cannot be updated or deleted; integrity guaranteed by the DB
CREATE OR REPLACE FUNCTION block_mutation() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'snapshot rows are immutable (ledger invariant)'; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER snapshot_immutable
BEFORE UPDATE OR DELETE ON snapshot
FOR EACH ROW EXECUTE FUNCTION block_mutation();
-- same trigger on report and valuation_run
```

---

## Part 2 — The enforced module skeleton

### 2.1 Repository layout (the boundaries made physical)

```
research-platform/
├── pyproject.toml
├── src/
│   └── platform/
│       ├── domain/                 # ★ THE CORE — pure Python, zero external deps
│       │   ├── models.py           #   Stock, Thesis, Claim as domain objects (Pydantic)
│       │   ├── ledger.py           #   snapshot/reproducibility rules
│       │   ├── thesis.py           #   claim evaluation, drift logic
│       │   ├── reporting.py        #   report assembly from a snapshot
│       │   ├── verification.py     #   citation-grounding + numeric re-check (core logic)
│       │   └── ports/              #   INTERFACES the domain depends on
│       │       ├── market_data.py  #     MarketDataPort (Protocol)
│       │       ├── documents.py    #     DocumentPort
│       │       ├── llm.py          #     LLMPort
│       │       ├── repository.py   #     RepositoryPort
│       │       └── valuation.py    #     ValuationPort
│       │
│       ├── ingestion/              # adapters: implement ports, talk to the messy world
│       │   ├── upstox_adapter.py
│       │   ├── nse_adapter.py
│       │   ├── document_adapter.py #   pdfplumber/pypdf — fragility quarantined here
│       │   └── llm/
│       │       ├── claude_adapter.py
│       │       └── openai_adapter.py
│       │
│       ├── valuation/              # deterministic models — pure functions
│       │   ├── dcf.py
│       │   └── comps.py
│       │
│       ├── catalyst/               # PreMarket Pro per-stock slice
│       │   └── premarketpro_adapter.py
│       │
│       ├── storage/                # SQLAlchemy lives ONLY here
│       │   ├── models.py
│       │   ├── repository.py       #   implements RepositoryPort
│       │   └── migrations/         #   alembic
│       │
│       ├── jobs/                   # arq worker + job definitions
│       │   └── worker.py
│       │
│       └── presentation/           # FastAPI — one consumer of the domain
│           ├── api.py
│           └── deps.py             #   wires adapters to ports (composition root)
│
├── frontend/                       # Next.js (separate)
└── tests/
    ├── domain/                     # unit tests — no network, no DB (the payoff of decoupling)
    ├── adapters/                   # adapter tests
    └── reproducibility/            # CI: every stored report re-derives from its snapshot
```

### 2.2 The dependency rule, enforced in CI

The boundaries are worthless if not enforced. Use `import-linter` to **fail the build** on a violation:

```ini
# .importlinter
[importlinter]
root_package = platform

[importlinter:contract:domain-is-pure]
name = Domain depends on nothing outward
type = forbidden
source_modules = platform.domain
forbidden_modules =
    platform.ingestion
    platform.storage
    platform.presentation
    platform.jobs
    platform.valuation
    platform.catalyst
    sqlalchemy
    fastapi
    httpx

[importlinter:contract:layers]
name = Inward-only layering
type = layers
layers =
    platform.presentation
    platform.jobs
    platform.ingestion | platform.storage | platform.valuation | platform.catalyst
    platform.domain
```

The domain depends only on its own `ports/` (interfaces). Adapters implement them. Wiring happens once, in `presentation/deps.py` (the composition root). A boundary violation is a red build, not a code-review comment.

### 2.3 A port, concretely

```python
# domain/ports/market_data.py  — the domain owns this interface
from typing import Protocol
from datetime import date

class MarketDataPort(Protocol):
    def fundamentals(self, ticker: str, as_of: date) -> dict: ...
    def prices(self, ticker: str, start: date, end: date) -> list[dict]: ...

# ingestion/upstox_adapter.py — the adapter implements it; domain never sees Upstox
class UpstoxAdapter:   # satisfies MarketDataPort structurally
    def fundamentals(self, ticker, as_of) -> dict: ...
    def prices(self, ticker, start, end) -> list[dict]: ...
```

Swapping Upstox for TrueData = new adapter, same port, domain and tests untouched. Swapping Claude for GPT = same.

---

## Part 3 — Phase 0: the vertical slice

### 3.1 Goal

Prove the **spine** end-to-end on the thinnest possible path, with the boundaries holding and reproducibility working — *before* adding any breadth. No reports, no LLM, no UI.

### 3.2 Scope (deliberately minimal)

**In:** one stock, manually-uploaded financial data (a small JSON/CSV — sidesteps NSE fragility per our earlier reasoning) → normalized through a `DocumentPort` adapter → frozen as an immutable `Snapshot` via the domain's `ledger` → stored through `RepositoryPort` → read back → **reproducibility test passes** (re-deriving from the snapshot yields identical output). One deterministic valuation (a basic DCF, pure function) run against the snapshot to prove the ValuationPort path.

**Out (deferred):** NSE auto-fetch, LLM, report generation, drift tracking, frontend, async jobs (Phase 0 can run synchronously; jobs come in Phase 1 when the LLM/fetch slowness appears).

### 3.3 Phase 0 definition of done

1. `pytest tests/domain` passes with **no network and no database** (proves the domain is decoupled).
2. `import-linter` passes (proves boundaries hold).
3. A stock + snapshot can be created from an uploaded file and stored.
4. The DB immutability trigger rejects any `UPDATE`/`DELETE` on a snapshot (proves the ledger invariant).
5. `tests/reproducibility` passes: a valuation re-runs from the stored snapshot and produces an identical result, with version stamps intact.
6. One end-to-end script: `ingest(file) -> snapshot -> store -> reload -> value -> assert reproducible`.

When those six are green, the spine is proven and every later feature (LLM reasoning, reports, drift, UI) is **breadth bolted onto a trusted core** — exactly the architecture-first payoff.

### 3.4 Build order within Phase 0

1. Repo skeleton + `pyproject` + import-linter config (boundaries first, empty modules).
2. Domain models + ports (pure, tested in isolation).
3. Storage adapter + migrations + immutability trigger.
4. Document adapter (file → normalized inputs).
5. Ledger logic (freeze snapshot, version-stamp, hash).
6. One DCF as a ValuationPort pure function.
7. The reproducibility test + the end-to-end script. **Done.**

---

## Where this leaves us

Architecture: **locked** (ADR 001–008). Schema, skeleton, enforcement, and the first slice: **specified**. The next action is to **build Phase 0** against this blueprint — starting with the skeleton and import-linter, so the boundaries exist before any logic does.

This document is the contract the build follows. If something here proves wrong during Phase 0, it changes via a new ADR — not a silent edit — keeping the decision record honest.
