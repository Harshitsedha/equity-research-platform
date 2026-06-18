"""SqlStockRepository — implements StockRepository against PostgreSQL (Phase 2a).

Maps the ``domain.stock.Stock`` aggregate to/from SQLAlchemy rows. The domain
never sees ORM types or the surrogate int PK: the aggregate is addressed by its
UUID identity or ISIN, and the int PK is resolved and used ONLY inside this
module (ADR-013 guardrail).

Two invariants this adapter must honour:
- **One transaction** for a status change and its audit row: ``save`` writes the
  ``stock.status`` update and any new ``status_transition`` rows together.
- **No denormalised timeline.** ``snapshot_refs`` is *projected* from the
  ``snapshot`` table on load (ordered by the same key the domain keeps); it is
  never stored as aggregate state. Snapshots themselves are written by the
  Phase-1 ledger path, not here.
"""

from __future__ import annotations

import uuid as uuid_lib

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from research_platform.domain.stock import (
    SnapshotRef,
    StatusTransition,
    Stock,
)
from research_platform.storage import models as orm
from research_platform.storage.db import make_session_factory
from research_platform.storage.thesis_repository import load_thesis_history


class SqlStockRepository:
    """Concrete StockRepository. Construct with a session factory (or default)."""

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory or make_session_factory()

    # --- reads -------------------------------------------------------------
    def get_by_isin(self, isin: str) -> Stock | None:
        with self._session_factory() as session:
            row = session.scalar(select(orm.Stock).where(orm.Stock.isin == isin))
            return self._to_domain(session, row) if row else None

    def get_by_id(self, stock_id: uuid_lib.UUID) -> Stock | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(orm.Stock).where(orm.Stock.uuid == stock_id)
            )
            return self._to_domain(session, row) if row else None

    def list(self) -> list[Stock]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(orm.Stock).order_by(orm.Stock.id)
            ).all()
            return [self._to_domain(session, row) for row in rows]

    # --- write -------------------------------------------------------------
    def save(self, stock: Stock) -> Stock:
        with self._session_factory() as session:
            row = session.scalar(
                select(orm.Stock).where(orm.Stock.uuid == stock.id)
            )
            if row is None:
                row = orm.Stock(
                    uuid=stock.id,
                    isin=stock.isin,
                    ticker=stock.ticker,
                    name=stock.name,
                    exchange=stock.exchange,
                    sector=stock.sector,
                    profile=stock.profile,
                    status=stock.status,
                )
                session.add(row)
                session.flush()  # assign the surrogate int PK for FK use below
            else:
                # Mutable registry: update the slowly-changing attrs + status.
                row.isin = stock.isin
                row.ticker = stock.ticker
                row.name = stock.name
                row.exchange = stock.exchange
                row.sector = stock.sector
                row.profile = stock.profile
                row.status = stock.status

            # Append-only: persist transitions not yet stored, in the SAME
            # transaction as the status change above. History is ordered, so the
            # already-stored prefix is exactly the first ``stored`` entries.
            stored = (
                session.scalar(
                    select(func.count())
                    .select_from(orm.StatusTransition)
                    .where(orm.StatusTransition.stock_id == row.id)
                )
                or 0
            )
            for transition in stock.transition_history[stored:]:
                session.add(
                    orm.StatusTransition(
                        stock_id=row.id,
                        from_status=transition.from_status,
                        to_status=transition.to_status,
                        occurred_at=transition.occurred_at,
                        reason=transition.reason,
                    )
                )

            session.commit()
            session.refresh(row)
            return self._to_domain(session, row)

    # --- ORM -> domain -----------------------------------------------------
    def _to_domain(self, session: Session, row: orm.Stock) -> Stock:
        # Timeline projected from the ledger, ordered identically to the domain's
        # own ``add_snapshot_ref`` invariant: (as_of, snapshot_id).
        snapshot_rows = session.scalars(
            select(orm.Snapshot)
            .where(orm.Snapshot.stock_id == row.id)
            .order_by(orm.Snapshot.as_of, orm.Snapshot.id)
        ).all()
        snapshot_refs = [
            SnapshotRef(snapshot_id=s.id, as_of=s.as_of) for s in snapshot_rows
        ]

        # Audit log in chronological (insertion) order.
        transition_rows = session.scalars(
            select(orm.StatusTransition)
            .where(orm.StatusTransition.stock_id == row.id)
            .order_by(orm.StatusTransition.id)
        ).all()
        transition_history = [
            StatusTransition(
                from_status=t.from_status,
                to_status=t.to_status,
                occurred_at=t.occurred_at,
                reason=t.reason,
            )
            for t in transition_rows
        ]

        # Thesis history projected via the SHARED loader (one mapping definition,
        # also used by SqlThesisRepository), ordered identically to the domain's
        # insert order so ``active_thesis`` agrees in memory and after reload.
        thesis_history = load_thesis_history(session, row.id)

        return Stock(
            id=row.uuid,
            isin=row.isin,
            ticker=row.ticker,
            name=row.name,
            exchange=row.exchange,
            sector=row.sector,
            profile=row.profile or {},
            status=row.status,
            snapshot_refs=snapshot_refs,
            transition_history=transition_history,
            thesis_history=thesis_history,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
