# Equity Research Platform — Architecture Decision Record

*Status: living document. Decisions ADR-001 through ADR-006 are **locked**. The data model (Lock #3) below is **proposed — awaiting ratification**.*

*Audience: solo operator (occasionally shared, read-only). Quality and correctness prioritised over delivery speed. This document is the durable "why" record; code is written against it, not ahead of it.*

---

## How to read this

Each ADR follows the standard form a disciplined team uses: **Context** (the forces at play), **Decision** (what we chose), **Alternatives rejected** (and why), **Consequences** (what this commits us to, good and bad). The point of recording rejected alternatives is so that six months from now, when something feels awkward, we can see whether the awkwardness was a known, accepted tradeoff or a genuine mistake to revisit.

---

## ADR-001 — Architectural style: modular monolith

**Context.** The system spans data ingestion, LLM reasoning, valuation, news, persistence, and a web UI. The operator is a single person whose binding constraint is time. The temptation in a "production-grade" build is to reach for microservices because they sound serious.

**Decision.** Build a **modular monolith**: one deployable artifact, with hard internal module boundaries (enforced in code, not just convention). Modules communicate through explicit interfaces, never by reaching into each other's internals.

**Alternatives rejected.**
- *Microservices.* Rejected. For one operator they are negative value: distributed-systems overhead, network failure modes, deployment orchestration, and cross-service debugging — all cost, no benefit at this scale. Premature distribution is a well-known anti-pattern.
- *Unstructured monolith ("just a Flask app").* Rejected. Ships fast, rots faster. Without hard boundaries the fragile parts (NSE scraping, LLM calls) leak into the core and the system becomes untestable and unreproducible — failing the primary goal.

**Consequences.**
- One repo, one deploy, simple ops. Good for a solo operator.
- Module boundaries must be *enforced* (import-linting / package structure), or the monolith degrades into a big ball of mud. This is a discipline cost we accept.
- If the system ever needs to scale to a team or multi-tenant product, a cleanly-bounded module can be extracted into a service later. The boundaries leave that door open without paying for it now.

---

## ADR-002 — Audience and scope: solo, with team-scale as an explicit anti-goal

**Context.** "Production-grade" can be misread as "build for thousands of users." The actual audience is the operator, occasionally sharing a read-only view.

**Decision.** Build for **a single authenticated user**, with an optional read-only share later. Declare the following **explicit anti-goals** — things we will *not* build until/unless the audience genuinely changes:
- Multi-tenancy, organisations, per-tenant data isolation
- Role-based access control beyond single-user + read-only share
- Horizontal autoscaling / high-availability clustering
- Public-facing hardening (rate-limiting hostile traffic, abuse prevention)

**Alternatives rejected.**
- *Build SaaS-ready from day one.* Rejected as the textbook over-engineering failure: complexity that serves a scale that isn't coming, slowing the build that matters.

**Consequences.**
- Auth is simple and correct for scope (single-user; secrets in env/secret-store, not a user-management system).
- The clean module boundaries (ADR-001/004) mean team-scale remains *possible* later without being *paid for* now.
- We will periodically re-check this assumption; if it changes, it becomes a new ADR, not a silent scope creep.

---

## ADR-003 — Core model: reproducibility-first immutable ledger

**Context.** This is a tool whose outputs may inform capital allocation. The operator has been previously burned by a backtest figure that would not reproduce. Three production concerns were declared non-negotiable — reproducibility, observability, pluggable adapters — with **reproducibility ranked first**.

**Decision.** The architectural heart is an **immutable research ledger**. The governing invariant:

> **Every output is a pure function of versioned, immutable, stored inputs.**

When a report (or any derived number) is produced, the system freezes an immutable **snapshot** of the exact inputs it was computed from — source data, prices, model version, prompt version, code version, LLM response — and the output is deterministically re-derivable from that snapshot. Stocks accumulate frozen snapshots over time; reports, theses, and drift-flags are **derived views** over the ledger, not free-floating artifacts.

**Alternatives rejected.**
- *Generate-and-view (run → output → discard).* Rejected. Cannot support thesis-drift tracking (the #1 desired feature), cannot reproduce a past report, cannot audit a number. Fails the primary goal.
- *Store outputs but not inputs.* Rejected. A report you can't regenerate from its inputs is not reproducible; you can only reread it, not verify it.

**Consequences (this is the high-leverage ADR — read the consequences carefully).**
- **It structurally forces hexagonal architecture (ADR-004) for free.** To replay a report you must run domain logic against *stored* data, not the live (since-changed) source — which is only possible if the domain depends on a port, not a live vendor call. Reproducibility-first *delivers* pluggable adapters as a side effect; they are not independent concerns.
- **It makes thesis-drift tracking trustworthy.** Comparing this quarter to last is only meaningful if both are frozen and comparable.
- **It demands versioning of everything that affects output** — model, prompt, assumptions, code — stamped on every snapshot. This is the audit trail, and the system cannot emit a number it cannot trace and re-derive.
- **Storage grows monotonically.** We accept this; research history is the asset, and the data volumes (tens of stocks, quarterly cadence) are tiny. Snapshots are append-only and never mutated.
- Observability (next in priority) layers on as the runtime view of this same ingest → snapshot → compute → report flow.

---

## ADR-004 — Module boundaries and the dependency rule

**Context.** With a modular monolith (ADR-001) and an immutable-ledger core (ADR-003), we need the specific bounded contexts and the rule governing how they relate. The system's biggest operational risk is external fragility: NSE endpoints break, PDFs are inconsistent, LLMs change and hallucinate, data vendors get swapped.

**Decision.** Five bounded modules, with an **inward-only dependency rule** (hexagonal / ports-and-adapters):

1. **Research Domain (core).** Owns the stock entity, the structured thesis, report assembly, drift detection, the verification harness, and the ledger rules. Depends on **ports (interfaces)** only — knows nothing of NSE, Claude, PDFs, or Postgres.
2. **Ingestion (adapters).** Turns messy external sources — NSE, Upstox, document parsing, **and the LLM itself** — into clean, normalised domain data. All fragile code is quarantined here.
3. **Valuation Engine.** Deterministic models (DCF, comps, etc.) as **pure functions**: assumptions in, numbers out. No I/O, no LLM.
4. **Catalyst / News.** The per-stock slice of the existing PreMarket Pro feed.
5. **Storage.** Immutable-ledger persistence.

**The Dependency Rule:** *dependencies point inward only.* Presentation → Domain; Domain → ports. Ingestion, Valuation, Catalyst, Storage implement ports the Domain defines. **The Domain never imports a vendor.**

**Alternatives rejected.**
- *LLM as the system's brain/core.* Rejected. The reasoning model is an **unreliable external source** and is placed as an Ingestion adapter whose output is normalised and verified — a deliberately humble, swappable placement. Building the core *around* a specific model would couple the whole system to one vendor and one failure mode.
- *Verification harness in the LLM/ingestion layer.* Rejected. Deciding whether to trust an output is **core business logic** and lives in the Domain. The operator's judgement standards are not delegated to a vendor.

**Consequences.**
- LLM provider, data vendor, and document parser are each a one-adapter swap; the core and its tests are untouched.
- The Domain is unit-testable in isolation (no network, no vendor) — which is *why* decoupling matters, not just aesthetics.
- Boundary enforcement is mandatory (import rules in CI); a violation is a build failure, not a code-review nicety.

---

## ADR-005 — Reliability posture: data integrity and graceful degradation (not uptime nines)

**Context.** "Reliable" must be scoped correctly or it becomes over-engineering. A solo research tool's failure that matters is **a wrong number trusted, or accumulated research lost** — not an hour of downtime.

**Decision.** Concentrate reliability engineering on:
- **Ledger integrity** — append-only, immutable snapshots; research is never corrupted or silently lost. Automated off-site backups (formalising the operator's existing pg_dump intent).
- **Loud failure on bad data** — when a source breaks or returns suspect data, the system **flags and halts that path**, never fabricates or silently fills. (Formalises the operator's existing loud-failure-guard discipline.)
- **Reproducibility as correctness insurance** (per ADR-003) — every number re-derivable and traceable.
- **Verification harness** — citation-grounding + numeric re-computation gate every LLM-derived output before it is trusted.

Explicitly **not** prioritised: high-availability clustering, multi-region failover, 99.9%+ uptime SLAs. Single-instance with good backups is the correct posture.

**Alternatives rejected.**
- *Uptime-first / HA clustering.* Rejected — solves a problem this system does not have, at real cost.

**Consequences.** Engineering effort goes to integrity and honesty about data, where the real risk is. A single instance is acceptable; a corrupted or unreproducible ledger is not.

---

## ADR-006 — Performance posture: latency and responsiveness via async + ledger-as-cache (not throughput)

**Context.** Workload is tens of stocks at quarterly cadence, not high-throughput serving. The slow paths are LLM calls and document parsing (seconds to minutes). Performance must target the operator's actual experience, not web scale.

**Decision.**
- **Slow work runs as background jobs** (report generation, document fetch, LLM calls) — never blocking the screen. The UI shows progress; the operator is never made to wait synchronously.
- **The ledger doubles as cache.** A frozen snapshot or generated report is stored; the system never re-fetches or re-computes what is already frozen. This is a direct, free consequence of ADR-003.
- **Browsing accumulated research = fast Postgres reads** on a small dataset; trivially performant.

Explicitly **not** prioritised: high request throughput, horizontal scaling, aggressive low-latency tuning.

**Alternatives rejected.**
- *Synchronous request-time generation.* Rejected — would block the operator on minute-long LLM/parse work and couple UX to vendor latency.
- *Designing for throughput/scale.* Rejected — wrong target for the actual workload.

**Consequences.** An async/jobs subsystem (retries, idempotency) becomes a required component — this is **Lock #4**, decided next. The performance story is "never wait at the screen; never recompute what's frozen," which the architecture delivers structurally rather than through tuning.

---

# LOCK #3 — PROPOSED: the immutable-ledger data model

*This is the next decision to ratify. It realises ADR-003 concretely. Reasoning given; entities and rules proposed; your sign-off (or adjustments) requested before it locks.*

## The shape of it

Everything hangs off a persistent **Stock** entity. The durable thesis lives on the stock; perishable analysis accumulates as immutable, versioned records beneath it. The design rule from our earlier discussion is baked in: **the thesis is structured data (checkable claims), not narrative prose** — without this, drift tracking is impossible.

```
Stock (1) ───< Snapshot (many, immutable, versioned)
  │                 │
  │                 └──< (each Report/Valuation references the exact Snapshot it used)
  ├──< Thesis (1 current + version history)
  │        └──< ThesisClaim (many — discrete, measurable assertions)
  ├──< Report (many — initiation | quarterly, each tied to a Snapshot)
  ├──< ValuationRun (many — model + assumptions + result, tied to a Snapshot)
  ├──< Assumption set (named, versioned — feeds ValuationRun)
  ├──< DriftFlag (derived — a claim checked against a later Snapshot)
  └──< Note (operator's own, free-form, timestamped)
```

## The entities (proposed)

**Stock** — the persistent anchor. Ticker, identity, sector, business profile (the durable "what it is"). Long-lived; rarely changes.

**Snapshot** — the heart of the ledger. An **immutable, append-only** freeze of all inputs at a point in time: source financials, prices, parsed filing data, plus the **version stamps** (data-source versions, code version, and — when LLM output is involved — model + prompt version). Every derived artifact points at the snapshot it was computed from. Never mutated; corrections create a *new* snapshot, never edit an old one.

**Thesis** + **ThesisClaim** — the structured, durable investment view. The Thesis is versioned (you can see how your view evolved). Each **ThesisClaim** is a discrete, *measurable* assertion — e.g. "MDO segment revenue ramps," "CFO/PAT trends toward 1.0," "order inflow stays above a threshold" — each with the metric, direction, and threshold that make it checkable. **This is what makes thesis-drift tracking trustworthy and the single most important design choice in the model.**

**Report** — initiation (annual) or quarterly, each an immutable derived view tied to its Snapshot, regenerable from it. This is what the two-report v2 design writes.

**ValuationRun** + **Assumption set** — a model execution: which model, which (named, versioned) assumptions, which snapshot, and the result. Re-running with new assumptions creates a new run; old ones persist. This is what makes v3's editable models reproducible and gives sensitivity analysis for free.

**DriftFlag** — derived, not stored-by-hand: a ThesisClaim evaluated against a later Snapshot, flagged when reality diverges. Your #1 feature, and it falls out almost for free once claims and snapshots both exist.

**Note** — your own free-form research, timestamped and attached to the stock. The human layer.

## The invariants that make it a ledger (not just tables)

1. **Snapshots are immutable and append-only.** No updates, no deletes. History is the asset.
2. **Every derived artifact references the exact snapshot it used.** No orphan numbers.
3. **Everything that affects an output is versioned** (data, code, model, prompt, assumptions) and stamped on the snapshot.
4. **Theses and assumptions are versioned**, not overwritten — you can always see what you believed, and when.
5. **Reproducibility test:** any past report/valuation must regenerate byte-for-deterministic-value from its snapshot. This is a CI test, not an aspiration.

## What this buys you

- Thesis-drift tracking — trustworthy, because both sides of every comparison are frozen.
- Reproducible, auditable reports and valuations — every number traceable to source.
- The ledger as cache — no recompute of frozen work (ADR-006).
- A clean home for v2 (quarterly reports accrue) and v3 (snapshots, models, news attach) **as additions, not rewrites**.

---

## Decision needed

Ratify this entity model and its five ledger invariants as **Lock #3**, or flag adjustments. Once locked, the remaining decisions are **Lock #4 (async/jobs model)** and **Lock #5 (tech stack)** — both already shaped by the ADRs above — after which we have a complete, recorded architecture to build against.

---

## ADR-009 — Top-level package name: `research_platform` (not `platform`)

*Raised during the Phase 0 build, per the blueprint's rule that a spec proven wrong changes via a new ADR, never a silent edit.*

**Context.** The Implementation Blueprint specified the root import package as `platform` (`src/platform/`, `root_package = platform`). Python ships a standard-library module also named `platform`. Under the chosen src-layout + editable install (uv/hatchling), the stdlib module wins on `sys.path`, so `import platform.domain` resolves to the stdlib `platform` and fails — verified empirically: `ModuleNotFoundError: ... 'platform' is not a package`. The package is simply not importable. This is a defect in the spec, not an ambiguity.

**Decision.** Rename the top-level import package to **`research_platform`** (`src/research_platform/`, `root_package = research_platform`). Module structure, layering, boundaries, and the dependency rule are otherwise exactly as the blueprint specifies.

**Alternatives rejected.**
- *Keep `platform` and manipulate `sys.path` ordering to shadow the stdlib.* Rejected — fragile, surprising, and it would break any code (ours or a dependency's) that legitimately imports stdlib `platform`.
- *Use a PEP 420 namespace or a distribution-name/import-name split.* Rejected as needless complexity for a solo build; a plain, unambiguous package name is the correct fix.

**Consequences.** All imports use `research_platform.*`; the import-linter `root_package` is `research_platform`. No architectural meaning is lost — only the collision is removed.
