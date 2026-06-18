"""Domain tests for the Thesis model + the Stock aggregate's thesis history.

Pure (no DB, no network): the fair-value resolution, the append-only history +
``active_thesis`` projection, the anchor-ownership rule (HARD RULE 3), tolerance
band validation, and immutability of the recorded view.
"""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from research_platform.domain.stock import CrossStockAnchorError, Stock
from research_platform.domain.thesis import (
    Thesis,
    ThesisAssumption,
    ToleranceBand,
)
from tests.support.isins import synthetic_isin


def _utc(y: int, mo: int, d: int) -> dt.datetime:
    return dt.datetime(y, mo, d, tzinfo=dt.timezone.utc)


def _stock_owning(snapshot_id: int, *, as_of: dt.date | None = None) -> Stock:
    """A Stock aggregate that owns one snapshot on its timeline."""
    stock = Stock(isin=synthetic_isin("thesis-dom"), ticker="THDOM", name="Thesis Co")
    stock.add_snapshot_ref(snapshot_id, as_of or dt.date(2024, 3, 31))
    return stock


# --- ToleranceBand validation ----------------------------------------------
def test_band_one_directional_is_allowed() -> None:
    assert ToleranceBand(lower=None, upper=0.30).upper == 0.30
    assert ToleranceBand(lower=0.05, upper=None).lower == 0.05


def test_band_needs_at_least_one_bound() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        ToleranceBand(lower=None, upper=None)


def test_band_lower_must_not_exceed_upper() -> None:
    with pytest.raises(ValidationError, match="exceeds upper"):
        ToleranceBand(lower=0.30, upper=0.05)


# --- fair_value resolution (HARD RULE 4) -----------------------------------
def test_fair_value_uses_analyst_target_when_set() -> None:
    thesis = Thesis(
        recorded_at=_utc(2024, 6, 1),
        anchor_valuation_run_id=1,
        anchor_snapshot_id=1,
        anchor_value_per_share=100.0,
        analyst_target=125.0,
    )
    assert thesis.fair_value() == 125.0


def test_fair_value_falls_back_to_anchor_output_when_no_target() -> None:
    thesis = Thesis(
        recorded_at=_utc(2024, 6, 1),
        anchor_valuation_run_id=1,
        anchor_snapshot_id=1,
        anchor_value_per_share=100.0,
        analyst_target=None,
    )
    assert thesis.fair_value() == 100.0


# --- record_thesis + active_thesis projection (HARD RULE 2) ----------------
def test_record_thesis_appends_and_active_is_latest() -> None:
    stock = _stock_owning(snapshot_id=42)
    assert stock.active_thesis is None

    a = stock.record_thesis(
        anchor_valuation_run_id=7,
        anchor_snapshot_id=42,
        anchor_value_per_share=100.0,
        recorded_at=_utc(2024, 1, 1),
        analyst_target=110.0,
    )
    b = stock.record_thesis(
        anchor_valuation_run_id=9,
        anchor_snapshot_id=42,
        anchor_value_per_share=105.0,
        recorded_at=_utc(2024, 6, 1),
    )

    # B supersedes A; A remains in the append-only history.
    assert stock.active_thesis == b
    assert stock.thesis_history == [a, b]
    assert a in stock.thesis_history


def test_history_orders_by_recorded_at_regardless_of_insert_order() -> None:
    """Out-of-order recording still yields the latest-by-recorded_at as active."""
    stock = _stock_owning(snapshot_id=42)
    later = stock.record_thesis(
        anchor_valuation_run_id=2,
        anchor_snapshot_id=42,
        anchor_value_per_share=105.0,
        recorded_at=_utc(2024, 6, 1),
    )
    earlier = stock.record_thesis(
        anchor_valuation_run_id=1,
        anchor_snapshot_id=42,
        anchor_value_per_share=100.0,
        recorded_at=_utc(2024, 1, 1),
    )
    assert stock.thesis_history == [earlier, later]
    assert stock.active_thesis == later


# --- anchor ownership (HARD RULE 3) ----------------------------------------
def test_cross_stock_anchor_is_rejected_and_records_nothing() -> None:
    stock = _stock_owning(snapshot_id=42)  # owns snapshot 42 only
    with pytest.raises(CrossStockAnchorError):
        stock.record_thesis(
            anchor_valuation_run_id=7,
            anchor_snapshot_id=999,  # a snapshot this stock does NOT own
            anchor_value_per_share=100.0,
            recorded_at=_utc(2024, 1, 1),
        )
    # The aggregate is untouched: no thesis recorded.
    assert stock.thesis_history == []
    assert stock.active_thesis is None


# --- immutability of the recorded view -------------------------------------
def test_thesis_is_frozen() -> None:
    thesis = Thesis(
        recorded_at=_utc(2024, 6, 1),
        anchor_valuation_run_id=1,
        anchor_snapshot_id=1,
        anchor_value_per_share=100.0,
    )
    with pytest.raises(ValidationError):
        thesis.analyst_target = 200.0  # type: ignore[misc]


def test_assumptions_carry_metric_key_and_band() -> None:
    """An assumption records its key into snapshot.inputs + a tolerance band."""
    assumption = ThesisAssumption(
        name="revenue growth",
        metric_key="revenue",
        recorded_value=1_650_000.0,
        band=ToleranceBand(lower=1_500_000.0, upper=1_800_000.0),
    )
    stock = _stock_owning(snapshot_id=42)
    thesis = stock.record_thesis(
        anchor_valuation_run_id=7,
        anchor_snapshot_id=42,
        anchor_value_per_share=100.0,
        recorded_at=_utc(2024, 1, 1),
        assumptions=[assumption],
    )
    assert thesis.assumptions[0].metric_key == "revenue"
    assert thesis.assumptions[0].band.lower == 1_500_000.0
