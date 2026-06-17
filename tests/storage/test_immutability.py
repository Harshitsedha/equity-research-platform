"""Storage tests — the DB-level immutability invariant (Blueprint 3.3 #4).

Requires a live PostgreSQL (docker-compose). Proves the trigger rejects UPDATE
and DELETE on the ledger tables — convention is not enough, the DB enforces it.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from research_platform.domain.ledger import freeze_snapshot
from research_platform.domain.models import SnapshotKind, Stock, ValuationRun

pytestmark = pytest.mark.db


@pytest.fixture
def stored_snapshot(repository):
    # Unique ticker per test: the ledger is append-only (rows can't be deleted),
    # so tests share one schema and must not collide on the unique ticker.
    ticker = f"IMMUT_{uuid.uuid4().hex[:8]}"
    stock = repository.save_stock(Stock(ticker=ticker, name="Immutable Co"))
    snap = freeze_snapshot(
        stock_id=stock.id,
        as_of=dt.date(2026, 3, 31),
        kind=SnapshotKind.annual,
        inputs={"revenue": 100.0},
        source_versions={"manual_upload": "1.0"},
    )
    return repository.save_snapshot(snap), stock


def test_snapshot_update_is_rejected(session_factory, stored_snapshot):
    snap, _ = stored_snapshot
    with pytest.raises(ProgrammingError, match="immutable"):
        with session_factory() as session:
            session.execute(
                text("UPDATE snapshot SET code_version = 'hacked' WHERE id = :id"),
                {"id": snap.id},
            )
            session.commit()


def test_snapshot_delete_is_rejected(session_factory, stored_snapshot):
    snap, _ = stored_snapshot
    with pytest.raises(ProgrammingError, match="immutable"):
        with session_factory() as session:
            session.execute(
                text("DELETE FROM snapshot WHERE id = :id"), {"id": snap.id}
            )
            session.commit()


def test_valuation_run_update_is_rejected(session_factory, repository, stored_snapshot):
    snap, stock = stored_snapshot
    run = repository.save_valuation_run(
        ValuationRun(
            stock_id=stock.id,
            snapshot_id=snap.id,
            model_name="dcf",
            assumptions={"wacc": 0.11},
            result={"value_per_share": 1.0},
            code_version="0.1.0",
        )
    )
    with pytest.raises(ProgrammingError, match="immutable"):
        with session_factory() as session:
            session.execute(
                text("UPDATE valuation_run SET model_name = 'x' WHERE id = :id"),
                {"id": run.id},
            )
            session.commit()


def test_valuation_run_delete_is_rejected(session_factory, repository, stored_snapshot):
    snap, stock = stored_snapshot
    run = repository.save_valuation_run(
        ValuationRun(
            stock_id=stock.id,
            snapshot_id=snap.id,
            model_name="dcf",
            assumptions={},
            result={},
            code_version="0.1.0",
        )
    )
    with pytest.raises(ProgrammingError, match="immutable"):
        with session_factory() as session:
            session.execute(
                text("DELETE FROM valuation_run WHERE id = :id"), {"id": run.id}
            )
            session.commit()
