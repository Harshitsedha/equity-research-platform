"""HTTP interface (Phase 2b-api).

The FIRST and ONLY presentation layer: read-only FastAPI endpoints over the Stock
aggregate. It owns its own wire format (``schemas``), obtains repositories from the
ONE composition root (``dependencies``), and depends on domain PORTS — never on
storage implementations, and never on the surrogate int PK.

Out of scope here (later sub-phases): writes, auth, the frontend, LLM, async.
Single-operator local for now — no auth layer is built (noted, not built).
"""
