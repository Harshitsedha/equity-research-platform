"""RepositoryPort — persistence contract owned by the domain.

``storage`` implements this against PostgreSQL. The domain speaks only domain
models (``Stock``, ``Snapshot``, ``ValuationRun``); it never sees SQLAlchemy.
``save_*`` return the persisted entity with its assigned ``id`` and any
server-set fields (e.g. ``created_at``).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from research_platform.domain.models import Snapshot, Stock, ValuationRun


@runtime_checkable
class RepositoryPort(Protocol):
    # --- Stock -------------------------------------------------------------
    def save_stock(self, stock: Stock) -> Stock: ...
    def get_stock(self, stock_id: int) -> Stock | None: ...
    def get_stock_by_ticker(self, ticker: str) -> Stock | None: ...

    # --- Snapshot (immutable) ---------------------------------------------
    def save_snapshot(self, snapshot: Snapshot) -> Snapshot: ...
    def get_snapshot(self, snapshot_id: int) -> Snapshot | None: ...

    # --- ValuationRun (immutable) -----------------------------------------
    def save_valuation_run(self, run: ValuationRun) -> ValuationRun: ...
    def get_valuation_run(self, run_id: int) -> ValuationRun | None: ...
