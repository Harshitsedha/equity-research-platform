"""FastAPI dependency providers — the ONLY bridge from api to the composition root.

These resolve repositories from the ONE composition root (``build_platform``);
they never construct a repository or import storage directly (HARD RULE 3). The
api depends on the domain PORTS (``StockRepository`` / ``RepositoryPort``), not on
any concrete adapter.

The Platform is built once and cached: both repositories are session-per-operation
(they open a fresh session from the factory on every call), so an app-lifetime
Platform holds no live session and is safe to reuse across requests.

Tests override ``get_stock_repository`` / ``get_snapshot_repository`` via
``app.dependency_overrides`` to point at a test-DB-bound repository — so the
cached default Platform (and its real DB connection) is never constructed in tests.
"""

from __future__ import annotations

from functools import lru_cache

from research_platform.app.composition import Platform, build_platform
from research_platform.domain.ports.repository import RepositoryPort
from research_platform.domain.ports.stock_repository import StockRepository


@lru_cache(maxsize=1)
def get_platform() -> Platform:
    """The wired application, built once via the composition root."""
    return build_platform()


def get_stock_repository() -> StockRepository:
    """The Stock aggregate repository (domain port)."""
    return get_platform().stock_repository


def get_snapshot_repository() -> RepositoryPort:
    """The ledger repository (domain port) — used read-only for snapshot leaves."""
    return get_platform().repository
