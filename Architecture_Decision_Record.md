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

---

## ADR-010 — The verification harness: claims-as-output, mixed failure policy, numeric truth in the domain

*Decided and built in Phase 1 Part A. This is the concrete realisation of ADR-004's "verification is core domain logic" and ADR-005's "loud failure on bad data".*

**Context.** Reports may inform capital allocation, and the LLM is an unreliable external source (ADR-004). The danger is a wrong number, stated fluently, being trusted. We need a structural guarantee that arithmetic reaching a report is the platform's own, not the model's.

**Decision.**

1. **Claims as the LLM contract (prose FROM verified claims).** An `LLMPort` returns a `StructuredReportDraft` of discrete, checkable `Claim`s (`NUMERIC | FACTUAL | QUALITATIVE`), never trusted prose or final arithmetic. The domain verifies every claim against the frozen snapshot, then renders prose *from the verified claims* with deterministic templating. **The LLM never does arithmetic that reaches a report.**

2. **Numeric truth lives in `domain/metrics.py`, not the valuation adapter.** The harness independently recomputes each `NUMERIC` claim from the snapshot's raw inputs using pure domain code. This is a deliberate split:
   - **Snapshot-only metrics** (ratios: roce, roe, debt_equity, margins, revenue_growth) are the *standard of truth* → they live in the **domain** (pure, no I/O). The domain may not import an adapter (HARD RULE 1), and the trust standard must not depend on one.
   - **Assumption-driven models** (DCF, comps — need levers beyond the snapshot) remain **valuation adapters** behind `ValuationPort`. They are not verification targets here (there is no single snapshot-derived "true" DCF without assumptions).

3. **Mixed failure policy with explicit precedence.**
   - **NUMERIC mismatch** (asserted vs recomputed beyond tolerance) **or un-recomputable metric ⇒ `HARD_FAILED`**: the report is **not stored**. Wrong is wrong; an un-confirmable number is not trusted.
   - **Citation missing/dangling ⇒ `FLAGGED`**, and **missing required section ⇒ `INCOMPLETE`**: both are **flag-and-store** — surfaced for human judgement, not fatal.
   - **Precedence:** numeric failure dominates. A draft with both a bad number and a missing section is `HARD_FAILED` (not stored).
   - **Correct number but dangling citation ⇒ `FLAGGED`, not `FAILED`.** The value is independently confirmed; only the (existence-only) citation is weak, so it is surfaced rather than rejected.

4. **Citation check is existence-only.** A `FACTUAL`/`NUMERIC` citation must resolve to a key/path that exists in the snapshot inputs (dotted paths supported). We deliberately do **not** attempt semantic support-checking ("does this datum actually support the statement").

5. **Storage-boundary guard (defence in depth, single source of truth).** Storability is one domain rule — `is_storable_verification()` (storable iff not `HARD_FAILED`). It is enforced at three layers: the harness sets the status, the pipeline never assembles/saves a hard-fail, and **`save_report()` itself re-checks and raises `NonStorableReportError` before any INSERT**. So a hard-failed report cannot reach the database even if a caller bypasses the pipeline.

**New decisions made while building Steps 5–6 (async path).**
- **Hard-fail is a non-`Retry` exception ⇒ structurally not re-enqueued.** The arq job raises `ReportHardFailure` (not arq's `Retry`) on a verification hard-fail, so the worker records a terminal, loud failed-job state and never retries it. Only `TransientJobError`/connection-style errors raise `Retry`, with a bounded budget (`MAX_TRIES`) and exponential backoff. Retrying a deterministic wrong number is pointless and is made impossible by construction.
- **Idempotency via `report.snapshot_id` unique constraint.** A re-run for the same snapshot returns the existing report (no duplicate, no noisy error). Postgres remains the ledger/idempotency/cache store (ADR-007 intent).
- **arq is Redis-backed — discrepancy with ADR-007 now RESOLVED** (see the ADR-007 Amendment 2026-06-17). arq's broker is Redis, which contradicted ADR-007's "Postgres-backed queue / no new datastore" wording. Resolution: **Redis is accepted as the job broker only**; Postgres remains the system of record / ledger / cache / idempotency store. Redis is already operated in production, so marginal surface area is low and the ADR-008 "avoid *unfamiliar* infra" principle is honoured. The `-m "not db"` test path still needs neither Postgres nor Redis (job logic proven by direct invocation).

**Alternatives rejected.**
- *Let the LLM emit final prose/numbers and spot-check.* Rejected — that trusts the unreliable source by default; ADR-004 places verification in the domain precisely to invert this.
- *Put ratio math in the valuation adapter and inject it into the harness via a port.* Rejected — it would either breach domain purity or make the standard of truth depend on an adapter; pure ratios are core domain logic.
- *Hard-fail on a dangling citation.* Rejected — over-fails on citation hygiene when the number itself is independently verified; flag-and-store preserves human judgement.

**Consequences.**
- Every stored number is independently recomputed by domain code and re-derivable from its snapshot (reproducibility test). Citation/structural weaknesses are recorded on the report, not hidden.
- The real LLM (Part B) is a one-adapter swap behind `LLMPort`; the harness, policy, and tests are untouched.
- **Explicitly deferred (recorded, not forgotten):** semantic citation support-checking, general fabrication/hallucination detection beyond numeric + citation-existence, PDF/HTML rendering, and thesis/claim-drift tracking.

---

## ADR-011 — Report generation provenance is first-class

*Decided in the Phase 1 hardening pass, closing the provenance debt flagged at the end of Part B. Recorded as an ADR rather than slipped in silently.*

**Context.** ADR-003 makes reproducibility the governing invariant: every output must be traceable and re-derivable from versioned, immutable, stored inputs. A report's value is a function not only of its snapshot but of **what produced it** — the LLM model and the prompt template. In Part B that provenance rode informally on the LLM port's `model_name` string and (in the demo) on the snapshot's `source_versions` dict. That was expedient but not first-class: you could not cleanly query "which reports came from `claude-opus-4-8` / prompt `report-claims-v1`", and the snapshot is the wrong home (it is created at ingest, before and independent of any report generation; one snapshot can back many reports from different models). The blueprint's schema (1.4/1.5) always intended model + prompt version as dedicated fields.

**Decision.** Promote **`model_version`** and **`prompt_version`** to first-class, **nullable** columns on the `report` table, with matching fields on the domain `Report` model, threaded through `run_report_pipeline` → `assemble_report` → storage. The pipeline reads them from the LLM adapter via `getattr(llm, "model_version"/"prompt_version", None)`; the `LLMPort` Protocol still only mandates `model_name`, so adapters that don't carry versions (the deterministic stub) simply yield `None`. `code_version` (the git SHA, ADR-009) is persisted on every report as well, not just on snapshots — so a stored report records the code, the model, and the prompt that produced it.

- **Nullable rationale.** The deterministic/stub path has no model or prompt — those columns are `NULL`, and that is semantically correct (a report with no LLM provenance), not a missing value to backfill. Only the LLM-backed path populates them.
- **Domain stays vendor-neutral (HARD RULE 1 preserved).** These are *generic* provenance strings — `model_version`, `prompt_version` — not Anthropic-specific. The domain and storage never import or name a vendor; the concrete values (`claude-opus-4-8`, `report-claims-v1`) originate in the ingestion adapter and travel as plain strings. This is why touching the domain here is correct, whereas in Part B (wiring the concrete Claude adapter) touching the domain would have been an architecture smell.
- **Immutability intact.** The Alembic migration only `ADD COLUMN`s; the `report_immutable` trigger (ADR-003 / Blueprint 1.6) is untouched and still rejects UPDATE/DELETE.

**Alternatives rejected.**
- *Leave provenance on `model_name` / `source_versions`.* Rejected — not queryable as structured columns, and `source_versions` conflates the snapshot's input provenance with the report's generation provenance.
- *Put model/prompt version on the snapshot instead of the report.* Rejected — the snapshot predates report generation and is shared across reports; report-generation provenance belongs on the report.
- *Make the columns NOT NULL with a sentinel (e.g. `"none"`).* Rejected — a sentinel lies about there having been a model; `NULL` is the honest representation of the deterministic path.

**Consequences.** A stored report is now fully self-describing for audit and reproducibility: `code_version` + `model_version` + `prompt_version` + its `snapshot_id`. Reports are queryable by the model/prompt that produced them. A reproducibility test asserts a Claude-fixture-generated report carries the correct three stamps and re-derives identically, while a stub-generated report carries `NULL` model/prompt versions and a valid `code_version`.

---

## ADR-012 — Deployment topology: dedicated VPS, fully isolated from PreMarket Pro

*Decided in the Phase-2a planning session and recorded here as owed. This concerns where and how the platform runs, not its internals.*

**Context.** The Catalyst/News slice (ADR-004) is sourced from the operator's existing **PreMarket Pro** product. The tempting shortcut is to co-locate the research platform on PreMarket Pro's infrastructure to "reuse what's there" — its database, its host, its network. But the two systems have different purposes, different change cadences, and different blast radii. PreMarket Pro is a live, externally-facing product; the research platform is the operator's reproducibility-first ledger (ADR-003/005), where the failure that matters is a corrupted or unreproducible research history. Coupling their runtimes would let an incident, deploy, migration, or resource spike in one degrade or corrupt the other, and would erode the hard module boundaries the architecture is built on (ADR-001/004) by re-introducing them as a shared-infrastructure dependency.

**Decision.** The research platform runs on its **own dedicated VPS**, with **full runtime isolation from PreMarket Pro**: separate host, separate PostgreSQL instance, separate Redis (the job broker, ADR-010 amendment), separate deploy lifecycle, separate backups (ADR-005). The only coupling between the two systems is a **network-boundary integration**: the platform consumes PreMarket Pro's catalyst/news data across an explicit network interface (an API/feed), exactly as it consumes any other external source — through an Ingestion adapter behind a port (ADR-004). No shared database, no shared process, no in-process imports, no shared filesystem.

**Alternatives rejected.**
- *Co-locate on PreMarket Pro's host/database.* Rejected — couples blast radius (one system's incident/deploy/migration can corrupt or stall the other), violates the ledger-integrity posture (ADR-005), and dissolves the module boundary into a shared-infra dependency. The marginal hosting saving is not worth risking the research history.
- *Serverless / managed-PaaS spread across providers.* Rejected for this scope (ADR-002, ADR-008): a single well-backed-up VPS is the correct, familiar, low-surface-area posture for a solo operator; multi-service hosting adds operational surface without solving a problem this system has.
- *Shared database, separate schemas.* Rejected — "separate schemas" still shares an instance's failure domain, connection limits, backup/restore lifecycle, and migration surface. It is isolation in name only.

**Consequences.**
- The PreMarket Pro integration is a **one-adapter swap behind a port** (ADR-004), reachable only over the network — it can be stubbed in tests and replaced without touching the core, and its outages degrade gracefully (ADR-005) rather than taking the platform down.
- Independent deploy/migration/backup lifecycles: a schema migration here (e.g. Phase 2a's `0005`) cannot affect PreMarket Pro, and vice-versa.
- A modest additional hosting/ops cost (one more VPS to run and back up) is accepted as the price of isolation. Should team-scale ever arrive (ADR-002), the clean network boundary already drawn here is exactly what an extraction would need — paid for only if and when required.

---

## ADR-013 — The mutable aggregate over immutable leaves; no denormalised ledger state

*Decided and built in Phase 2a. This is the concrete realisation, at the aggregate level, of the ADR-003 ledger invariant — and it records a deliberate, time-boxed piece of tech debt created by introducing the aggregate without reworking Phase-1.*

**Context.** Phase 1 gave us immutable, append-only ledger *leaves* — `Snapshot`, `ValuationRun`, `Report` — each a frozen, content-stamped row protected by a DB trigger (ADR-003, Blueprint 1.6). What was missing was the *entity* that turns isolated leaves into a living coverage record per ticker: identity that persists across snapshots, a mutable profile (symbol/name/exchange change over time), a coverage lifecycle (`candidate → active → dropped`), and an ordered view of the ticker's snapshot history. That entity — the **`Stock` aggregate root** — is, by nature, **mutable**, and it sits *above* immutable leaves. The risk in modelling it is the classic one: smuggling derived/denormalised state (a stored "current snapshot", a writable "latest valuation", a cached status) onto the mutable entity, which then drifts from the ledger and quietly becomes a second, untrustworthy source of truth — exactly the failure ADR-003 exists to prevent.

**Decision.**

1. **A mutable aggregate composed over immutable leaves.** `domain.stock.Stock` (pure Pydantic, ADR-004) is the aggregate root. Its identity is an **internal UUID**; **ISIN** is the stable natural key; `ticker`/`name`/`exchange`/`sector`/`profile` and coverage `status` are mutable. The leaves it references stay immutable and trigger-protected. The aggregate's storage home — the `stock` table — is the **mutable registry** and deliberately carries **no immutability trigger**; its coverage-status changes are audited in an **append-only `status_transition` table** that *does* reuse the ledger's `block_mutation()` trigger. So mutability and immutability are cleanly separated by table, and every status change leaves an immutable audit trail.

2. **No denormalised ledger state ("latest" is always a query).** The aggregate owns an **append-only timeline of snapshot references** and has **no** `current_snapshot` / `current_valuation` writable field. "Latest anything" is a *query over the timeline* (`latest_snapshot_ref`), never a stored column that could drift. Likewise the snapshot timeline is **not** persisted as aggregate state — it is *projected* from the `snapshot` table on load, ordered by `(as_of, snapshot_id)`. The single source of truth remains the ledger.

3. **Invariants live in the domain, not the query/storage layer.** The legal status-transition graph (`candidate→active`, `candidate→dropped`, `active→dropped`, `dropped→active`; `candidate` initial-only; no self-loops) is enforced by `Stock.transition_to`, which raises `IllegalStatusTransition` and mutates nothing on an illegal edge. Timeline ordering is enforced on append (`add_snapshot_ref` insorts), so an in-memory aggregate and a round-tripped one have a byte-for-byte identical timeline — the ordering guarantee does not depend on the reload query.

4. **The surrogate int PK stays buried in storage.** Phase 1's ledger FK graph keys on the `stock` table's surrogate **int** PK. Rather than churn that graph (and the frozen leaf models) to UUID, Phase 2a keeps the int PK and **adds** a unique `uuid` column as the aggregate's identity. The guardrail: `StockRepository` is addressed by **UUID** (`get_by_id`) or **ISIN** (`get_by_isin`); nothing above the storage adapter references the int PK. The adapter resolves int↔UUID internally; snapshots (written via the Phase-1 ledger path, which is inherently int-keyed) get their stock's int PK only through the existing storage-boundary API.

5. **Two `Stock` types is DELIBERATE, TEMPORARY tech debt — with a named retirement condition.** Introducing the aggregate without reworking the Phase-1 persistence path leaves **two** `Stock` representations mapping the same row: the legacy registry **DTO** `domain.models.Stock` (used by the Phase-1 `RepositoryPort`/`PostgresRepository`) and the **aggregate root** `domain.stock.Stock` (used by `StockRepository`/`SqlStockRepository`). **The aggregate root is canonical** as of Phase 2a; the DTO is legacy. `isin` was added to the DTO additively so the shared `stock` row carries the natural key (it is `NOT NULL UNIQUE` in storage). **Retirement condition:** the legacy DTO and `RepositoryPort` are removed once their consumers (ingestion upsert, the report jobs/pipeline, and the Phase-0/1 scripts/tests) migrate to the aggregate and `StockRepository`. Until then this is accepted, recorded debt — not a permanent two-headed model. Naming it here, with the canonical entity and the exit criterion explicit, is what stops "temporary" from silently becoming permanent.

**Alternatives rejected.**
- *Store "current snapshot"/"latest valuation" on the stock for read convenience.* Rejected — it is denormalised ledger state that drifts from the append-only truth; the whole point of ADR-003 is that derived facts are *computed* from frozen inputs, not cached on a mutable row. Reads over a tiny dataset are trivially fast (ADR-006).
- *Migrate the stock PK and the ledger FK graph from int to UUID now.* Rejected for Phase 2a — it would retype the frozen leaf models' `stock_id`, churn the sealed FK graph, and ripple across ingestion/jobs/scripts/tests, for no behavioural gain over a unique `uuid` column. The int PK is harmless as long as it stays buried (guardrail above). Revisitable if the FK graph is reworked later.
- *Put the aggregate's mutability under the same immutability trigger as the leaves.* Rejected — the stock registry is *meant* to be mutable (symbol/status change). Immutability belongs on the leaves and on the **status-transition audit log**, not on the registry row itself.
- *Collapse the legacy DTO and the aggregate immediately.* Rejected as out of Phase-2a scope — reworking the Phase-1 repo/pipeline is a separate, larger change; doing it under this phase would breach the "ledger tables/contracts untouched" guarantee. Hence the recorded debt + retirement condition instead.

**Consequences.**
- The coverage record is a faithful, mutable view *over* the immutable ledger: status history is itself an append-only, trigger-protected audit log, and the snapshot timeline is always a projection of frozen rows — there is no second source of truth to drift.
- Domain-level invariants (legal transitions, timeline order) hold identically in memory and after a DB round-trip; tests assert both, including that an illegal transition writes no audit row.
- The platform temporarily carries two `Stock` types; the canonical one is the aggregate, and the debt has an explicit exit. Anyone reading this in six months knows which entity to build against and what "done" looks like for retiring the other.
- The int↔UUID split is invisible above storage, leaving the door open to a future full-UUID migration without forcing it now.

---

## ADR-014 — Frontend topology: monorepo `web/`, server-side RSC fetch over the network boundary, read-only viewer

*Decided and built in Phase 2b-ui — the first frontend. It records where the web app lives and the one boundary that governs it; the backend (2a aggregate + 2b-api) is untouched.*

**Context.** Phase 2b-api sealed a read-only HTTP interface over the Stock aggregate, with the wire contract deliberately owning its DTOs and keeping the surrogate int PK off the wire (HARD RULE 1/4 there). The first UI must project those three endpoints without re-opening any of that: no writes, no auth, no LLM, no live data, and — critically — without handing the browser the API origin or letting the stock int PK creep back into a URL or the DOM, which is exactly where building from the wrong identifier is easiest.

**Decision.** The web app is a **Next.js (App Router) + TypeScript + Tailwind** project at **`web/`**, a sibling of `src/` with its **own** `package.json`/`tsconfig`/`node_modules`, fully isolated from the Python tooling (`node_modules`, `.next`, and the E2E `manifest.json` are git-ignored). All backend data is fetched in **React Server Components, server-side**, from a **server-only `FASTAPI_BASE_URL`** (never `NEXT_PUBLIC_`) through a single `lib/api.ts` marked `import "server-only"` — every page goes through it, with `cache: "no-store"` on each request so a read-only viewer over mutable coverage data never serves a stale Next data cache. The browser therefore never holds the API origin or any credential; only plain serialized data is passed down. Routes **mirror the API identity contract** — `/stocks` → `/stocks/[isin]` → `/stocks/[isin]/snapshots/[snapshotId]` (stocks ISIN-keyed, snapshots int-addressed) — and the stock int PK appears in no route, link, or rendered text, asserted by a Playwright guardrail that scans the DOM for a seeded sentinel pk (with a paired positive control proving the scan can see a rendered int). The API's 404s map onto Next's `notFound()` to render terminal-style error pages, not the framework default.

**Alternatives rejected.**
- *Client-side data fetching (browser → FastAPI).* Rejected — it exposes the API origin to the browser, forces CORS, and invites a client `fetch` that bypasses the server-only boundary. RSC server-side fetch keeps the origin and the whole wire contract on the server.
- *Co-locating the web app inside the Python package / sharing tooling.* Rejected — the Python package and the web app have different toolchains and lifecycles; bleeding them together erodes the boundary for no gain. An isolated `web/` keeps each clean.
- *A stubbed/recorded API for the E2E run.* Rejected — the guardrail and the 404/ownership paths only mean something against the REAL running API; the harness boots actual uvicorn against a disposable, freshly-migrated DB (the same posture the pytest suite already uses).

**Consequences.** The frontend is a thin, typed projection: TS DTOs mirror the API DTOs so the wire contract is typed end to end, and the int PK guardrail now holds on both sides of the network boundary. The viewer adds no write/auth/LLM/live-data surface and no deploy config (ADR-012 still defers provisioning — local dev only). Swapping where the API runs is a single env var; the browser is never coupled to it.
