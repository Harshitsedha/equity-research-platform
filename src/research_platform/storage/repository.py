"""PostgresRepository — implements RepositoryPort against PostgreSQL.

Maps between domain Pydantic models and SQLAlchemy rows. The domain never sees
these ORM types; it only ever receives domain models back. Snapshot/ValuationRun
are insert-only here — there are deliberately no update/delete methods, and the
database would reject them anyway (immutability trigger).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from research_platform.domain.models import (
    Snapshot as DomainSnapshot,
)
from research_platform.domain.models import (
    Stock as DomainStock,
)
from research_platform.domain.models import (
    ValuationRun as DomainValuationRun,
)
from research_platform.storage import models as orm
from research_platform.storage.db import make_session_factory


# --- ORM -> domain mappers -------------------------------------------------
def _to_domain_stock(row: orm.Stock) -> DomainStock:
    return DomainStock(
        id=row.id,
        ticker=row.ticker,
        name=row.name,
        exchange=row.exchange,
        sector=row.sector,
        profile=row.profile or {},
        created_at=row.created_at,
    )


def _to_domain_snapshot(row: orm.Snapshot) -> DomainSnapshot:
    return DomainSnapshot(
        id=row.id,
        stock_id=row.stock_id,
        as_of=row.as_of,
        kind=row.kind,
        inputs=row.inputs,
        source_versions=row.source_versions or {},
        code_version=row.code_version,
        content_hash=row.content_hash,
        created_at=row.created_at,
    )


def _to_domain_run(row: orm.ValuationRun) -> DomainValuationRun:
    return DomainValuationRun(
        id=row.id,
        stock_id=row.stock_id,
        snapshot_id=row.snapshot_id,
        model_name=row.model_name,
        assumptions=row.assumptions,
        result=row.result,
        code_version=row.code_version,
        created_at=row.created_at,
    )


class PostgresRepository:
    """Concrete RepositoryPort. Construct with a session factory (or default)."""

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory or make_session_factory()

    # --- Stock -------------------------------------------------------------
    def save_stock(self, stock: DomainStock) -> DomainStock:
        with self._session_factory() as session:
            row = orm.Stock(
                ticker=stock.ticker,
                name=stock.name,
                exchange=stock.exchange,
                sector=stock.sector,
                profile=stock.profile,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_domain_stock(row)

    def get_stock(self, stock_id: int) -> DomainStock | None:
        with self._session_factory() as session:
            row = session.get(orm.Stock, stock_id)
            return _to_domain_stock(row) if row else None

    def get_stock_by_ticker(self, ticker: str) -> DomainStock | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(orm.Stock).where(orm.Stock.ticker == ticker)
            )
            return _to_domain_stock(row) if row else None

    # --- Snapshot (immutable) ---------------------------------------------
    def save_snapshot(self, snapshot: DomainSnapshot) -> DomainSnapshot:
        with self._session_factory() as session:
            row = orm.Snapshot(
                stock_id=snapshot.stock_id,
                as_of=snapshot.as_of,
                kind=snapshot.kind,
                inputs=snapshot.inputs,
                source_versions=snapshot.source_versions,
                code_version=snapshot.code_version,
                content_hash=snapshot.content_hash,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_domain_snapshot(row)

    def get_snapshot(self, snapshot_id: int) -> DomainSnapshot | None:
        with self._session_factory() as session:
            row = session.get(orm.Snapshot, snapshot_id)
            return _to_domain_snapshot(row) if row else None

    # --- ValuationRun (immutable) -----------------------------------------
    def save_valuation_run(self, run: DomainValuationRun) -> DomainValuationRun:
        with self._session_factory() as session:
            row = orm.ValuationRun(
                stock_id=run.stock_id,
                snapshot_id=run.snapshot_id,
                model_name=run.model_name,
                assumptions=run.assumptions,
                result=run.result,
                code_version=run.code_version,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return _to_domain_run(row)

    def get_valuation_run(self, run_id: int) -> DomainValuationRun | None:
        with self._session_factory() as session:
            row = session.get(orm.ValuationRun, run_id)
            return _to_domain_run(row) if row else None
