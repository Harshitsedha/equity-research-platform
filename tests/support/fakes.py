"""In-memory RepositoryPort fake for DB-free domain/job tests.

It mirrors the production storage guard (refusing non-storable reports) by reusing
the same domain rule, so pipeline/job behaviour is faithful without a database.
"""

from __future__ import annotations

import datetime as dt

from research_platform.domain.models import Report, Snapshot, Stock, ValuationRun
from research_platform.domain.verification import is_storable_verification
from research_platform.storage.repository import NonStorableReportError


class InMemoryRepository:
    """Satisfies RepositoryPort structurally; stores rows in dicts."""

    def __init__(self) -> None:
        self.stocks: dict[int, Stock] = {}
        self.snapshots: dict[int, Snapshot] = {}
        self.runs: dict[int, ValuationRun] = {}
        self.reports: dict[int, Report] = {}
        self._seq = 0

    def _next_id(self) -> int:
        self._seq += 1
        return self._seq

    def _now(self) -> dt.datetime:
        return dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)

    # --- Stock -------------------------------------------------------------
    def save_stock(self, stock: Stock) -> Stock:
        saved = stock.model_copy(update={"id": self._next_id(), "created_at": self._now()})
        self.stocks[saved.id] = saved
        return saved

    def get_stock(self, stock_id: int) -> Stock | None:
        return self.stocks.get(stock_id)

    def get_stock_by_ticker(self, ticker: str) -> Stock | None:
        return next((s for s in self.stocks.values() if s.ticker == ticker), None)

    # --- Snapshot ----------------------------------------------------------
    def save_snapshot(self, snapshot: Snapshot) -> Snapshot:
        saved = snapshot.model_copy(update={"id": self._next_id(), "created_at": self._now()})
        self.snapshots[saved.id] = saved
        return saved

    def get_snapshot(self, snapshot_id: int) -> Snapshot | None:
        return self.snapshots.get(snapshot_id)

    # --- ValuationRun ------------------------------------------------------
    def save_valuation_run(self, run: ValuationRun) -> ValuationRun:
        saved = run.model_copy(update={"id": self._next_id(), "created_at": self._now()})
        self.runs[saved.id] = saved
        return saved

    def get_valuation_run(self, run_id: int) -> ValuationRun | None:
        return self.runs.get(run_id)

    # --- Report (guarded, same rule as production) -------------------------
    def save_report(self, report: Report) -> Report:
        if not is_storable_verification(report.verification):
            raise NonStorableReportError("refusing to persist a HARD_FAILED report")
        saved = report.model_copy(update={"id": self._next_id(), "created_at": self._now()})
        self.reports[saved.id] = saved
        return saved

    def get_report(self, report_id: int) -> Report | None:
        return self.reports.get(report_id)

    def get_report_by_snapshot(self, snapshot_id: int) -> Report | None:
        return next(
            (r for r in self.reports.values() if r.snapshot_id == snapshot_id), None
        )
