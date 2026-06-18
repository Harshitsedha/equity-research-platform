"""SqlStockRepository — aggregate persistence against a live PostgreSQL.

Proves the Phase-2a invariants end-to-end:
- aggregate round-trip (identity, timeline order, status, full transition history),
- the in-memory timeline equals the reloaded one byte-for-byte (ordering lives in
  the domain, not the query layer),
- Phase-1 reproducibility reached THROUGH the aggregate,
- illegal transitions persist no audit row and leave status unchanged,
- status_transition is immutable at the DB level.

Setup note: snapshots are written via the Phase-1 ledger path, which is keyed by
the stock's surrogate int PK. That int is obtained ONLY via the Phase-1 storage
API (``get_stock_by_ticker``) — the aggregate object never carries it (ADR-013).
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import ProgrammingError

from research_platform.app.composition import build_platform
from research_platform.domain.ledger import freeze_from_document, freeze_snapshot
from research_platform.domain.models import SnapshotKind, ValuationRun
from research_platform.domain.stock import CoverageStatus, SnapshotRef, Stock
from research_platform.domain.version import CODE_VERSION
from research_platform.ingestion.document_adapter import FileDocumentAdapter
from research_platform.storage import models as orm
from research_platform.storage.stock_repository import SqlStockRepository
from tests.conftest import ROOT
from tests.support.isins import synthetic_isin

pytestmark = pytest.mark.db

INPUTS = {
    "revenue": 1500.0, "prev_revenue": 1200.0, "ebit": 360.0, "net_income": 240.0,
    "total_assets": 2000.0, "current_liabilities": 500.0, "total_debt": 300.0,
    "equity": 1000.0,
}
ASSUMPTIONS = {
    "growth_rate": 0.10, "projection_years": 5, "wacc": 0.11, "terminal_growth": 0.04,
}


@pytest.fixture
def stock_repository(session_factory) -> SqlStockRepository:
    return SqlStockRepository(session_factory)


def _unique(prefix: str) -> tuple[str, str]:
    """A unique (ticker, isin) pair so tests don't collide on the constraints."""
    token = dt.datetime.now(dt.timezone.utc).strftime("%H%M%S%f")
    ticker = f"{prefix}{token}"
    return ticker, synthetic_isin(ticker)


def _ledger_stock_id(repository, ticker: str) -> int:
    """Resolve the surrogate int PK via the Phase-1 API (storage-boundary only)."""
    legacy = repository.get_stock_by_ticker(ticker)
    assert legacy is not None and legacy.id is not None
    return legacy.id


def _add_snapshot(repository, stock_int_id: int, as_of: dt.date):
    return repository.save_snapshot(
        freeze_snapshot(
            stock_id=stock_int_id,
            as_of=as_of,
            kind=SnapshotKind.annual,
            inputs=INPUTS,
        )
    )


# --- aggregate round-trip --------------------------------------------------
def test_round_trip_identity_timeline_status_history(
    stock_repository, repository
):
    ticker, isin = _unique("RT")
    saved = stock_repository.save(Stock(isin=isin, ticker=ticker, name="Round Trip Co"))
    assert saved.status is CoverageStatus.candidate

    int_id = _ledger_stock_id(repository, ticker)
    # Snapshots written out of chronological order on purpose.
    _add_snapshot(repository, int_id, dt.date(2025, 3, 31))
    _add_snapshot(repository, int_id, dt.date(2023, 3, 31))
    _add_snapshot(repository, int_id, dt.date(2024, 3, 31))

    # Drive coverage through two legal edges and persist them.
    s = stock_repository.get_by_isin(isin)
    s.transition_to(CoverageStatus.active, "thesis formed")
    s.transition_to(CoverageStatus.dropped, "thesis broke")
    persisted = stock_repository.save(s)

    reloaded = stock_repository.get_by_id(s.id)

    # Identity + mutable attrs.
    assert reloaded.id == saved.id == s.id
    assert reloaded.isin == isin
    assert reloaded.ticker == ticker
    assert reloaded.status is CoverageStatus.dropped

    # Timeline is ordered by (as_of, snapshot_id), not insertion order.
    assert [r.as_of for r in reloaded.snapshot_refs] == [
        dt.date(2023, 3, 31), dt.date(2024, 3, 31), dt.date(2025, 3, 31)
    ]

    # Full transition history, in order.
    assert [(t.from_status, t.to_status) for t in reloaded.transition_history] == [
        (CoverageStatus.candidate, CoverageStatus.active),
        (CoverageStatus.active, CoverageStatus.dropped),
    ]
    assert [t.reason for t in reloaded.transition_history] == [
        "thesis formed", "thesis broke"
    ]

    # The two DB-sourced views are identical.
    assert persisted.model_dump() == reloaded.model_dump()


# --- in-memory timeline == reloaded timeline, byte-for-byte ----------------
def test_in_memory_out_of_order_matches_reloaded(stock_repository, repository):
    ticker, isin = _unique("ORD")
    created = stock_repository.save(Stock(isin=isin, ticker=ticker, name="Order Co"))
    int_id = _ledger_stock_id(repository, ticker)

    snaps = [
        _add_snapshot(repository, int_id, dt.date(2024, 9, 30)),
        _add_snapshot(repository, int_id, dt.date(2024, 3, 31)),
        _add_snapshot(repository, int_id, dt.date(2025, 3, 31)),
    ]

    # Build the same aggregate in memory, appending refs in scrambled order.
    in_memory = Stock(id=created.id, isin=isin, ticker=ticker, name="Order Co")
    for snap in [snaps[2], snaps[0], snaps[1]]:
        in_memory.add_snapshot_ref(snapshot_id=snap.id, as_of=snap.as_of)

    reloaded = stock_repository.get_by_id(created.id)

    assert [r.model_dump() for r in in_memory.snapshot_refs] == [
        r.model_dump() for r in reloaded.snapshot_refs
    ]
    # And both equal the explicit sorted expectation.
    expected = sorted(
        (SnapshotRef(snapshot_id=s.id, as_of=s.as_of) for s in snaps),
        key=lambda r: (r.as_of, r.snapshot_id),
    )
    assert reloaded.snapshot_refs == expected


# --- Phase-1 reproducibility, reached THROUGH the aggregate -----------------
def test_valuation_re_derives_through_aggregate(stock_repository, repository):
    platform = build_platform(repository=repository)
    ticker, isin = _unique("REPRO")
    stock_repository.save(Stock(isin=isin, ticker=ticker, name="Repro Co"))
    int_id = _ledger_stock_id(repository, ticker)
    # DCF needs DCF-shaped inputs — freeze the sample document, like the Phase-1
    # reproducibility test, so the model has real levers to re-derive from.
    doc = FileDocumentAdapter().parse(ROOT / "data" / "sample_infy.json")
    snapshot = repository.save_snapshot(freeze_from_document(doc, stock_id=int_id))

    run = repository.save_valuation_run(
        ValuationRun(
            stock_id=int_id,
            snapshot_id=snapshot.id,
            model_name=platform.valuation.model_name,
            assumptions=ASSUMPTIONS,
            result=platform.valuation.run(snapshot.inputs, ASSUMPTIONS),
            code_version=CODE_VERSION,
        )
    )

    # Reach the snapshot ONLY through the aggregate's timeline.
    agg = stock_repository.get_by_isin(isin)
    ref = agg.latest_snapshot_ref
    assert ref is not None and ref.snapshot_id == snapshot.id
    source_snapshot = repository.get_snapshot(ref.snapshot_id)

    rederived = platform.valuation.run(source_snapshot.inputs, run.assumptions)
    assert rederived == run.result
    assert source_snapshot.content_hash == snapshot.content_hash


# --- illegal transition: no audit row, status unchanged (persisted) --------
def test_illegal_transition_persists_no_row(stock_repository, repository):
    ticker, isin = _unique("ILL")
    s = stock_repository.save(Stock(isin=isin, ticker=ticker, name="Illegal Co"))
    s.transition_to(CoverageStatus.active, "promote")
    stock_repository.save(s)

    reloaded = stock_repository.get_by_id(s.id)
    from research_platform.domain.stock import IllegalStatusTransition

    with pytest.raises(IllegalStatusTransition):
        reloaded.transition_to(CoverageStatus.candidate, "back to candidate?")
    # Saving the unchanged aggregate must add no row.
    stock_repository.save(reloaded)

    final = stock_repository.get_by_id(s.id)
    assert final.status is CoverageStatus.active
    assert len(final.transition_history) == 1

    int_id = _ledger_stock_id(repository, ticker)
    with stock_repository._session_factory() as session:
        count = session.scalar(
            select(func.count())
            .select_from(orm.StatusTransition)
            .where(orm.StatusTransition.stock_id == int_id)
        )
    assert count == 1


# --- status_transition is immutable at the DB level ------------------------
def test_status_transition_update_is_rejected(
    stock_repository, repository, session_factory
):
    ticker, isin = _unique("IMU")
    s = stock_repository.save(Stock(isin=isin, ticker=ticker, name="Immutable Trans Co"))
    s.transition_to(CoverageStatus.active, "promote")
    stock_repository.save(s)
    int_id = _ledger_stock_id(repository, ticker)

    with pytest.raises(ProgrammingError, match="immutable"):
        with session_factory() as session:
            session.execute(
                text(
                    "UPDATE status_transition SET reason = 'hacked' "
                    "WHERE stock_id = :sid"
                ),
                {"sid": int_id},
            )
            session.commit()


def test_status_transition_delete_is_rejected(
    stock_repository, repository, session_factory
):
    ticker, isin = _unique("IMD")
    s = stock_repository.save(Stock(isin=isin, ticker=ticker, name="Immutable Del Co"))
    s.transition_to(CoverageStatus.active, "promote")
    stock_repository.save(s)
    int_id = _ledger_stock_id(repository, ticker)

    with pytest.raises(ProgrammingError, match="immutable"):
        with session_factory() as session:
            session.execute(
                text("DELETE FROM status_transition WHERE stock_id = :sid"),
                {"sid": int_id},
            )
            session.commit()
