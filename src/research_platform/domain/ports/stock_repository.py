"""StockRepository — persistence contract for the Stock aggregate (Phase 2a).

Owned by the domain; ``storage`` implements it against PostgreSQL. The domain
speaks only the aggregate (``domain.stock.Stock``) and addresses it by its
**UUID identity** or its **ISIN** natural key — never by the storage surrogate
int PK, which stays buried in the adapter (ADR-013 guardrail).
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from research_platform.domain.stock import Stock


@runtime_checkable
class StockRepository(Protocol):
    def get_by_isin(self, isin: str) -> Stock | None: ...
    def get_by_id(self, stock_id: uuid.UUID) -> Stock | None: ...
    def save(self, stock: Stock) -> Stock:
        """Upsert the aggregate.

        Mutable registry fields and ``status`` are updated in place; any new
        ``transition_history`` entries are appended in the SAME transaction as the
        status change. Returns the persisted aggregate (server-set fields filled).
        """
        ...

    def list(self) -> list[Stock]: ...
