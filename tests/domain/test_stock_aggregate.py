"""Stock aggregate — pure domain invariants (no DB, no network).

Covers the two invariants that MUST live in the domain, not the query/storage
layer: the legal coverage-status transition graph, and the timeline staying
ordered on append regardless of insertion order.
"""

from __future__ import annotations

import datetime as dt

import pytest

from research_platform.domain.stock import (
    CoverageStatus,
    IllegalStatusTransition,
    Stock,
)
from tests.support.isins import synthetic_isin

ISIN = synthetic_isin("aggregate-unit")


def _stock(status: CoverageStatus = CoverageStatus.candidate) -> Stock:
    s = Stock(isin=ISIN, ticker="UNIT", name="Unit Co")
    # Walk the legal graph to reach the requested starting status.
    if status is CoverageStatus.active:
        s.transition_to(CoverageStatus.active, "promote")
    elif status is CoverageStatus.dropped:
        s.transition_to(CoverageStatus.dropped, "drop")
    return s


def test_new_stock_starts_candidate() -> None:
    s = Stock(isin=ISIN, ticker="UNIT", name="Unit Co")
    assert s.status is CoverageStatus.candidate
    assert s.transition_history == []
    assert s.snapshot_refs == []


def test_explicit_initial_status_allowed() -> None:
    s = Stock(isin=ISIN, ticker="UNIT", name="Unit Co", status=CoverageStatus.active)
    assert s.status is CoverageStatus.active


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (CoverageStatus.candidate, CoverageStatus.active),
        (CoverageStatus.candidate, CoverageStatus.dropped),
        (CoverageStatus.active, CoverageStatus.dropped),
        (CoverageStatus.dropped, CoverageStatus.active),
    ],
)
def test_legal_transitions_succeed(
    start: CoverageStatus, target: CoverageStatus
) -> None:
    s = _stock(start)
    before = len(s.transition_history)

    t = s.transition_to(target, reason="because")

    assert s.status is target
    assert t.from_status is start and t.to_status is target
    assert t.reason == "because"
    assert s.transition_history[-1] is t
    assert len(s.transition_history) == before + 1


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (CoverageStatus.active, CoverageStatus.candidate),   # can't re-enter candidate
        (CoverageStatus.dropped, CoverageStatus.candidate),  # can't re-enter candidate
        (CoverageStatus.active, CoverageStatus.active),       # self-loop
        (CoverageStatus.candidate, CoverageStatus.candidate),  # self-loop
    ],
)
def test_illegal_transitions_raise_and_mutate_nothing(
    start: CoverageStatus, target: CoverageStatus
) -> None:
    s = _stock(start)
    history_before = list(s.transition_history)

    with pytest.raises(IllegalStatusTransition):
        s.transition_to(target, reason="nope")

    # No status change, NO transition recorded.
    assert s.status is start
    assert s.transition_history == history_before


def test_timeline_kept_ordered_on_out_of_order_append() -> None:
    s = Stock(isin=ISIN, ticker="UNIT", name="Unit Co")

    # Append deliberately out of order (by as_of and by id).
    s.add_snapshot_ref(snapshot_id=30, as_of=dt.date(2025, 3, 31))
    s.add_snapshot_ref(snapshot_id=10, as_of=dt.date(2024, 3, 31))
    s.add_snapshot_ref(snapshot_id=20, as_of=dt.date(2024, 9, 30))

    ordered = [(r.as_of, r.snapshot_id) for r in s.snapshot_refs]
    assert ordered == [
        (dt.date(2024, 3, 31), 10),
        (dt.date(2024, 9, 30), 20),
        (dt.date(2025, 3, 31), 30),
    ]
    # "latest" is a query over the ordered timeline, not stored state.
    assert s.latest_snapshot_ref is not None
    assert s.latest_snapshot_ref.snapshot_id == 30
    assert not hasattr(s, "current_snapshot")


def test_latest_snapshot_ref_empty_timeline() -> None:
    s = Stock(isin=ISIN, ticker="UNIT", name="Unit Co")
    assert s.latest_snapshot_ref is None
