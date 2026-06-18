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
from research_platform.storage.stock_repository import SqlStockRepository
from research_platform.storage.thesis_repository import SqlThesisRepository
from tests.support.isins import synthetic_isin

pytestmark = pytest.mark.db


@pytest.fixture
def stored_snapshot(repository):
    # Unique ticker per test: the ledger is append-only (rows can't be deleted),
    # so tests share one schema and must not collide on the unique ticker.
    ticker = f"IMMUT_{uuid.uuid4().hex[:8]}"
    stock = repository.save_stock(
        Stock(ticker=ticker, name="Immutable Co", isin=synthetic_isin(ticker))
    )
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


# --- thesis + thesis_assumption immutability (Phase 3a, HARD RULE 1) -------
@pytest.fixture
def stored_thesis(session_factory, repository, stored_snapshot):
    """A persisted thesis (with one assumption) anchored to a real run.

    Returns (thesis_id, assumption_id) — the int PKs, so the SQL below can target
    the exact rows the trigger must protect.
    """
    snap, stock = stored_snapshot
    run = repository.save_valuation_run(
        ValuationRun(
            stock_id=stock.id, snapshot_id=snap.id, model_name="dcf",
            assumptions={}, result={"value_per_share": 42.0}, code_version="0.1.0",
        )
    )
    from research_platform.domain.thesis import ThesisAssumption, ToleranceBand

    stock_repo = SqlStockRepository(session_factory)
    thesis_repo = SqlThesisRepository(session_factory)
    agg = stock_repo.get_by_isin(stock.isin)
    thesis = agg.record_thesis(
        anchor_valuation_run_id=run.id,
        anchor_snapshot_id=run.snapshot_id,
        anchor_value_per_share=run.result["value_per_share"],
        recorded_at=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
        assumptions=[
            ThesisAssumption(
                name="rev", metric_key="revenue", recorded_value=100.0,
                band=ToleranceBand(lower=90.0, upper=None),
            )
        ],
    )
    thesis_repo.record(agg.id, thesis)
    with session_factory() as session:
        thesis_id = session.execute(
            text("SELECT id FROM thesis WHERE uuid = :u"), {"u": str(thesis.id)}
        ).scalar_one()
        assumption_id = session.execute(
            text("SELECT id FROM thesis_assumption WHERE thesis_id = :t"),
            {"t": thesis_id},
        ).scalar_one()
    return thesis_id, assumption_id


def test_thesis_update_is_rejected(session_factory, stored_thesis):
    thesis_id, _ = stored_thesis
    with pytest.raises(ProgrammingError, match="immutable"):
        with session_factory() as session:
            session.execute(
                text("UPDATE thesis SET analyst_target = 999 WHERE id = :id"),
                {"id": thesis_id},
            )
            session.commit()


def test_thesis_delete_is_rejected(session_factory, stored_thesis):
    thesis_id, _ = stored_thesis
    with pytest.raises(ProgrammingError, match="immutable"):
        with session_factory() as session:
            session.execute(
                text("DELETE FROM thesis WHERE id = :id"), {"id": thesis_id}
            )
            session.commit()


def test_thesis_assumption_update_is_rejected(session_factory, stored_thesis):
    _, assumption_id = stored_thesis
    with pytest.raises(ProgrammingError, match="immutable"):
        with session_factory() as session:
            session.execute(
                text(
                    "UPDATE thesis_assumption SET recorded_value = 0 WHERE id = :id"
                ),
                {"id": assumption_id},
            )
            session.commit()


def test_thesis_assumption_delete_is_rejected(session_factory, stored_thesis):
    _, assumption_id = stored_thesis
    with pytest.raises(ProgrammingError, match="immutable"):
        with session_factory() as session:
            session.execute(
                text("DELETE FROM thesis_assumption WHERE id = :id"),
                {"id": assumption_id},
            )
            session.commit()
