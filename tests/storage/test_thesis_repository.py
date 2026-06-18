"""SqlThesisRepository — thesis persistence against a live PostgreSQL (Phase 3a).

Proves the 3a invariants end-to-end:
- round-trip with assumptions + a set analyst target (identity, frozen anchor,
  target, rationale, full assumption set with bands, prose) is identical on reload;
- with no target, fair_value falls back to the anchored run's frozen output;
- supersede: thesis A then B -> active_thesis is B; A stays in the history;
- cross-stock anchor is rejected in the DOMAIN (HARD RULE 3);
- the frozen anchor facts are guarded at persist time (ADR-015): a tampered anchor
  value or snapshot is rejected by the repo, a matching one persists.

Seeding uses the Phase-1 ledger path (legacy stock + snapshot + valuation_run) so
a real, owned anchor run exists; the aggregate is then loaded by isin and records
its thesis through ``SqlThesisRepository``.
"""

from __future__ import annotations

import datetime as dt
import hashlib

import pytest
from sqlalchemy import text

from research_platform.domain.ledger import freeze_snapshot
from research_platform.domain.models import SnapshotKind
from research_platform.domain.models import Stock as LegacyStock
from research_platform.domain.models import ValuationRun
from research_platform.domain.stock import CrossStockAnchorError
from research_platform.domain.thesis import ThesisAssumption, ToleranceBand
from research_platform.storage.stock_repository import SqlStockRepository
from research_platform.storage.thesis_repository import (
    SqlThesisRepository,
    ThesisAnchorMismatchError,
)
from tests.support.isins import synthetic_isin

pytestmark = pytest.mark.db

INPUTS = {"revenue": 1_500_000.0, "ebit_margin": 0.24}
ASSUMPTIONS = {
    "growth_rate": 0.10, "projection_years": 5, "wacc": 0.11, "terminal_growth": 0.04,
}
ANCHOR_VALUE = 123.456789  # a run output with decimals, to prove exact round-trip


@pytest.fixture
def stock_repo(session_factory) -> SqlStockRepository:
    return SqlStockRepository(session_factory)


@pytest.fixture
def thesis_repo(session_factory) -> SqlThesisRepository:
    return SqlThesisRepository(session_factory)


def _seed_anchor(
    repository,
    *,
    seed: str,
    as_ofs: list[dt.date] | None = None,
    value_per_share: float = ANCHOR_VALUE,
):
    """Seed a stock + snapshot(s) + a valuation run on the FIRST snapshot.

    Returns (isin, run, snapshot_ids). The run anchors a thesis; its snapshot is
    owned by this stock.
    """
    isin = synthetic_isin(seed)
    # Distinct, ≤32-char ticker per seed (the append-only test DB keeps rows, so
    # two seeds must not collide on the ticker UNIQUE constraint).
    ticker = "T" + hashlib.sha1(seed.encode()).hexdigest()[:10].upper()
    stock = repository.save_stock(
        LegacyStock(isin=isin, ticker=ticker, name=f"{seed} Co")
    )
    snap_ids: list[int] = []
    for as_of in as_ofs or [dt.date(2024, 3, 31)]:
        snap = repository.save_snapshot(
            freeze_snapshot(
                stock_id=stock.id, as_of=as_of,
                kind=SnapshotKind.annual, inputs=INPUTS,
            )
        )
        snap_ids.append(snap.id)
    run = repository.save_valuation_run(
        ValuationRun(
            stock_id=stock.id,
            snapshot_id=snap_ids[0],
            model_name="dcf",
            assumptions=ASSUMPTIONS,
            result={"value_per_share": value_per_share, "equity_value": 1.0},
            code_version="0.1.0",
        )
    )
    return isin, run, snap_ids


def _row_counts(session_factory, isin: str) -> tuple[int, int]:
    """Direct (thesis, thesis_assumption) row counts for the stock under ``isin``.

    A DB-level count (not the projection) so a rejected record can be proven to
    have left ZERO rows in BOTH tables — provenance guard rolls back atomically.
    """
    with session_factory() as session:
        n_thesis = session.execute(
            text(
                "SELECT count(*) FROM thesis t JOIN stock s ON t.stock_id = s.id "
                "WHERE s.isin = :isin"
            ),
            {"isin": isin},
        ).scalar_one()
        n_assumption = session.execute(
            text(
                "SELECT count(*) FROM thesis_assumption a "
                "JOIN thesis t ON a.thesis_id = t.id "
                "JOIN stock s ON t.stock_id = s.id WHERE s.isin = :isin"
            ),
            {"isin": isin},
        ).scalar_one()
    return n_thesis, n_assumption


# --- round-trip: assumptions + a set analyst target ------------------------
def test_round_trip_with_assumptions_and_target(repository, stock_repo, thesis_repo):
    isin, run, _ = _seed_anchor(repository, seed="thesis-rt")
    assumptions = [
        ThesisAssumption(
            name="revenue growth", metric_key="revenue",
            recorded_value=1_650_000.0,
            band=ToleranceBand(lower=1_500_000.0, upper=1_800_000.0),
        ),
        ThesisAssumption(
            name="margin floor", metric_key="ebit_margin",
            recorded_value=0.24, band=ToleranceBand(lower=0.20, upper=None),
        ),
    ]
    agg = stock_repo.get_by_isin(isin)
    thesis = agg.record_thesis(
        anchor_valuation_run_id=run.id,
        anchor_snapshot_id=run.snapshot_id,
        anchor_value_per_share=run.result["value_per_share"],
        recorded_at=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
        analyst_target=150.0,
        override_rationale="re-rating on durable margin expansion",
        assumptions=assumptions,
        summary="quality compounder", bull="margins hold", bear="growth stalls",
    )
    persisted = thesis_repo.record(agg.id, thesis)

    # Reload THROUGH the aggregate; the active thesis must equal what we recorded.
    reloaded = stock_repo.get_by_isin(isin).active_thesis
    assert reloaded is not None
    assert reloaded.id == persisted.id == thesis.id
    assert reloaded.anchor_valuation_run_id == run.id
    assert reloaded.anchor_snapshot_id == run.snapshot_id
    assert reloaded.anchor_value_per_share == ANCHOR_VALUE
    assert reloaded.analyst_target == 150.0
    assert reloaded.override_rationale == "re-rating on durable margin expansion"
    assert reloaded.summary == "quality compounder"
    assert reloaded.bull == "margins hold"
    assert reloaded.bear == "growth stalls"
    assert reloaded.recorded_at == thesis.recorded_at
    # Full assumption set with bands survives the round-trip, in order.
    assert reloaded.assumptions == assumptions
    # With a target set, fair_value is the target.
    assert reloaded.fair_value() == 150.0


# --- no-target round-trip: fair_value falls back to the run output ----------
def test_no_target_fair_value_is_anchored_run_output(repository, stock_repo, thesis_repo):
    isin, run, _ = _seed_anchor(repository, seed="thesis-notarget")
    agg = stock_repo.get_by_isin(isin)
    thesis = agg.record_thesis(
        anchor_valuation_run_id=run.id,
        anchor_snapshot_id=run.snapshot_id,
        anchor_value_per_share=run.result["value_per_share"],
        recorded_at=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
        analyst_target=None,
    )
    thesis_repo.record(agg.id, thesis)

    reloaded = stock_repo.get_by_isin(isin).active_thesis
    assert reloaded.analyst_target is None
    assert reloaded.fair_value() == ANCHOR_VALUE
    assert reloaded.fair_value() == run.result["value_per_share"]


# --- supersede: A then B -> active is B, A remains -------------------------
def test_supersede_active_is_latest_and_history_retained(
    repository, stock_repo, thesis_repo
):
    isin, run, _ = _seed_anchor(repository, seed="thesis-supersede")
    agg = stock_repo.get_by_isin(isin)
    a = agg.record_thesis(
        anchor_valuation_run_id=run.id, anchor_snapshot_id=run.snapshot_id,
        anchor_value_per_share=run.result["value_per_share"],
        recorded_at=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
        analyst_target=110.0,
    )
    thesis_repo.record(agg.id, a)
    # Re-load before recording B (the aggregate is the consistency boundary).
    agg = stock_repo.get_by_isin(isin)
    b = agg.record_thesis(
        anchor_valuation_run_id=run.id, anchor_snapshot_id=run.snapshot_id,
        anchor_value_per_share=run.result["value_per_share"],
        recorded_at=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
        analyst_target=140.0,
    )
    thesis_repo.record(agg.id, b)

    reloaded = stock_repo.get_by_isin(isin)
    assert len(reloaded.thesis_history) == 2
    assert reloaded.active_thesis.id == b.id
    assert reloaded.active_thesis.analyst_target == 140.0
    # A remains in the append-only history.
    assert {t.id for t in reloaded.thesis_history} == {a.id, b.id}


# --- HARD RULE 3: cross-stock anchor rejected in the domain ----------------
def test_cross_stock_anchor_rejected(repository, stock_repo, thesis_repo):
    # Stock A and stock B each with their own snapshot + run.
    isin_a, _run_a, _ = _seed_anchor(repository, seed="thesis-owner")
    _isin_b, run_b, _ = _seed_anchor(repository, seed="thesis-other")

    agg_a = stock_repo.get_by_isin(isin_a)
    # B's run anchors B's snapshot, which A does NOT own -> rejected, nothing recorded.
    with pytest.raises(CrossStockAnchorError):
        agg_a.record_thesis(
            anchor_valuation_run_id=run_b.id,
            anchor_snapshot_id=run_b.snapshot_id,
            anchor_value_per_share=run_b.result["value_per_share"],
            recorded_at=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
        )
    assert stock_repo.get_by_isin(isin_a).thesis_history == []


# --- ADR-015: frozen anchor must match its provenance (repo guard) ---------
def test_tampered_anchor_value_is_rejected_at_record(
    session_factory, repository, stock_repo, thesis_repo
):
    isin, run, _ = _seed_anchor(repository, seed="thesis-tamper-val")
    agg = stock_repo.get_by_isin(isin)
    # A frozen anchor value that disagrees with the cited run's output. Carry an
    # assumption too, so "zero assumption rows" is a non-trivial assertion.
    tampered = agg.record_thesis(
        anchor_valuation_run_id=run.id,
        anchor_snapshot_id=run.snapshot_id,
        anchor_value_per_share=run.result["value_per_share"] + 1.0,
        recorded_at=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
        assumptions=[
            ThesisAssumption(
                name="rev", metric_key="revenue", recorded_value=1.0,
                band=ToleranceBand(lower=0.0, upper=None),
            )
        ],
    )
    with pytest.raises(ThesisAnchorMismatchError, match="value_per_share"):
        thesis_repo.record(agg.id, tampered)
    # ZERO rows in BOTH tables — the guard fired before any INSERT.
    assert _row_counts(session_factory, isin) == (0, 0)
    assert stock_repo.get_by_isin(isin).thesis_history == []


def test_tampered_anchor_snapshot_is_rejected_at_record(
    session_factory, repository, stock_repo, thesis_repo
):
    # Two owned snapshots; the run anchors the first. Point the thesis at the
    # second (owned, so the domain check passes) -> the repo provenance check
    # must catch that it is not the run's snapshot.
    isin, run, snap_ids = _seed_anchor(
        repository, seed="thesis-tamper-snap",
        as_ofs=[dt.date(2023, 3, 31), dt.date(2024, 3, 31)],
    )
    other_owned_snapshot = snap_ids[1]
    assert other_owned_snapshot != run.snapshot_id
    agg = stock_repo.get_by_isin(isin)
    tampered = agg.record_thesis(
        anchor_valuation_run_id=run.id,
        anchor_snapshot_id=other_owned_snapshot,
        anchor_value_per_share=run.result["value_per_share"],
        recorded_at=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
        assumptions=[
            ThesisAssumption(
                name="rev", metric_key="revenue", recorded_value=1.0,
                band=ToleranceBand(lower=0.0, upper=None),
            )
        ],
    )
    with pytest.raises(ThesisAnchorMismatchError, match="snapshot"):
        thesis_repo.record(agg.id, tampered)
    # ZERO rows in BOTH tables — the guard fired before any INSERT.
    assert _row_counts(session_factory, isin) == (0, 0)
    assert stock_repo.get_by_isin(isin).thesis_history == []


# --- a matching anchor persists (the positive control for the guard) -------
def test_matching_anchor_persists(repository, stock_repo, thesis_repo):
    isin, run, _ = _seed_anchor(repository, seed="thesis-match")
    agg = stock_repo.get_by_isin(isin)
    thesis = agg.record_thesis(
        anchor_valuation_run_id=run.id,
        anchor_snapshot_id=run.snapshot_id,
        anchor_value_per_share=run.result["value_per_share"],
        recorded_at=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
    )
    persisted = thesis_repo.record(agg.id, thesis)
    assert persisted.id == thesis.id
    assert stock_repo.get_by_isin(isin).active_thesis.id == thesis.id
