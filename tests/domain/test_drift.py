"""Domain tests for Phase 3b drift computation. Pure — no DB, no network.

Covers the dual-convention resolver (derived-wins, the three UNRESOLVED reasons),
the resolve_path<->citation_exists characterization, band-crossing across all
three bound-shapes (incl. equal-to-bound in-band and one-directional bands never
symmetrized), the model->judgment gap (incl. None->absent), the assembled
per-assumption projection, and — because 3a's recorded_value is an INDEPENDENT
analyst number, NOT snapshot-derived — there is deliberately NO identity test.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from research_platform.domain.drift import (
    BandSide,
    DriftStatus,
    UnresolvedReason,
    compute_thesis_drift,
    resolve_assumption,
)
from research_platform.domain.thesis import (
    Thesis,
    ThesisAssumption,
    ToleranceBand,
)
from research_platform.domain.verification import citation_exists, resolve_path

# A snapshot inputs dict mirroring the verification fixture: flat raw fields the
# derived metrics are computed from, plus a nested path and a null leaf.
INPUTS = {
    "revenue": 1500.0,
    "prev_revenue": 1200.0,
    "ebit": 360.0,          # ebit_margin = 360/1500 = 0.24 (derived)
    "net_income": 240.0,
    "total_assets": 2000.0,
    "current_liabilities": 500.0,
    "total_debt": 300.0,
    "equity": 1000.0,
    "segments": {"mdo": {"revenue": 100.0}},
    "null_field": None,
}


# --- resolver: derived-wins + the three UNRESOLVED reasons ------------------
def test_resolve_derived_metric_recomputes() -> None:
    res = resolve_assumption("ebit_margin", INPUTS)
    assert res.resolved and res.value == 0.24


def test_resolve_raw_key_returns_value() -> None:
    res = resolve_assumption("revenue", INPUTS)
    assert res.resolved and res.value == 1500.0


def test_resolve_dotted_raw_path_returns_value() -> None:
    res = resolve_assumption("segments.mdo.revenue", INPUTS)
    assert res.resolved and res.value == 100.0


def test_resolve_absent_key_is_unresolved_key_absent() -> None:
    res = resolve_assumption("does_not_exist", INPUTS)
    assert not res.resolved
    assert res.reason is UnresolvedReason.KEY_ABSENT
    assert res.value is None


def test_resolve_known_derived_missing_input_is_missing_input() -> None:
    # roe needs net_income + equity; drop equity -> compute_metric raises, but the
    # NAME is known, so the reason must be MISSING_INPUT, never KEY_ABSENT.
    res = resolve_assumption("roe", {"net_income": 240.0})
    assert not res.resolved
    assert res.reason is UnresolvedReason.MISSING_INPUT


def test_resolve_present_but_none_is_value_absent() -> None:
    res = resolve_assumption("null_field", INPUTS)
    assert not res.resolved
    assert res.reason is UnresolvedReason.VALUE_ABSENT


def test_resolve_present_but_non_numeric_is_value_absent() -> None:
    # "segments" resolves to a dict — present, but no value to judge.
    res = resolve_assumption("segments", INPUTS)
    assert not res.resolved
    assert res.reason is UnresolvedReason.VALUE_ABSENT


def test_derived_wins_precedence() -> None:
    # If a raw field somehow shadows a derived name, the derived computation wins
    # (reserved namespace). 0.24 is the computed ebit_margin, not the raw 9.99.
    res = resolve_assumption("ebit_margin", {**INPUTS, "ebit_margin": 9.99})
    assert res.resolved and res.value == 0.24


# --- characterization: resolve_path.found agrees with citation_exists -------
@pytest.mark.parametrize(
    "key",
    [
        "revenue",                 # flat present
        "segments.mdo.revenue",    # dotted present
        "segments.mdo.missing",    # dotted missing leaf
        "nope",                    # flat missing
        "null_field",              # present-but-None (still "exists")
        None,                      # empty
        "",                        # empty string
    ],
)
def test_resolve_path_found_matches_citation_exists(key) -> None:
    found, _ = resolve_path(INPUTS, key)
    assert found == citation_exists(INPUTS, key)


def test_resolve_path_returns_value_including_present_but_none() -> None:
    assert resolve_path(INPUTS, "revenue") == (True, 1500.0)
    assert resolve_path(INPUTS, "null_field") == (True, None)  # purely structural
    assert resolve_path(INPUTS, "nope") == (False, None)


# --- band crossing: both bounds --------------------------------------------
def _both() -> ToleranceBand:
    return ToleranceBand(lower=0.20, upper=0.30)


def _assume(metric_key: str, recorded: float, band: ToleranceBand) -> ThesisAssumption:
    return ThesisAssumption(
        name=metric_key, metric_key=metric_key, recorded_value=recorded, band=band
    )


def _drift_of(assumption: ThesisAssumption, inputs: dict):
    thesis = Thesis(
        recorded_at=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
        anchor_valuation_run_id=1,
        anchor_snapshot_id=1,
        anchor_value_per_share=100.0,
        assumptions=[assumption],
    )
    return compute_thesis_drift(thesis, inputs).assumption_drifts[0]


def test_both_bounds_in_band() -> None:
    d = _drift_of(_assume("ebit_margin", 0.24, _both()), INPUTS)  # 0.24 within [0.20,0.30]
    assert d.status is DriftStatus.IN_BAND
    assert d.crossed_side is None and d.breach_magnitude is None
    # nearest bound: min(0.24-0.20, 0.30-0.24) = 0.04
    assert d.distance_to_nearest_bound == pytest.approx(0.04)


def test_both_bounds_crossed_below() -> None:
    d = _drift_of(_assume("ebit_margin", 0.24, ToleranceBand(lower=0.30, upper=0.40)), INPUTS)
    assert d.status is DriftStatus.CROSSED
    assert d.crossed_side is BandSide.BELOW
    assert d.breach_magnitude == pytest.approx(0.06)  # 0.30 - 0.24
    assert d.distance_to_nearest_bound is None


def test_both_bounds_crossed_above() -> None:
    d = _drift_of(_assume("ebit_margin", 0.24, ToleranceBand(lower=0.10, upper=0.20)), INPUTS)
    assert d.status is DriftStatus.CROSSED
    assert d.crossed_side is BandSide.ABOVE
    assert d.breach_magnitude == pytest.approx(0.04)  # 0.24 - 0.20


def test_equal_to_bound_is_in_band() -> None:
    # value exactly on the upper bound -> in-band (strict inequality).
    d = _drift_of(_assume("ebit_margin", 0.24, ToleranceBand(lower=0.10, upper=0.24)), INPUTS)
    assert d.status is DriftStatus.IN_BAND
    assert d.distance_to_nearest_bound == pytest.approx(0.0)


# --- band crossing: one-directional, never symmetrized ----------------------
def test_upper_only_can_only_cross_above() -> None:
    # lower=None: a low value can NEVER be "crossed below".
    low = _drift_of(_assume("ebit_margin", 0.24, ToleranceBand(lower=None, upper=0.50)), INPUTS)
    assert low.status is DriftStatus.IN_BAND
    assert low.crossed_side is None
    high = _drift_of(_assume("ebit_margin", 0.24, ToleranceBand(lower=None, upper=0.10)), INPUTS)
    assert high.status is DriftStatus.CROSSED and high.crossed_side is BandSide.ABOVE


def test_lower_only_can_only_cross_below() -> None:
    # upper=None: a high value can NEVER be "crossed above".
    high = _drift_of(_assume("ebit_margin", 0.24, ToleranceBand(lower=0.10, upper=None)), INPUTS)
    assert high.status is DriftStatus.IN_BAND
    assert high.crossed_side is None
    low = _drift_of(_assume("ebit_margin", 0.24, ToleranceBand(lower=0.30, upper=None)), INPUTS)
    assert low.status is DriftStatus.CROSSED and low.crossed_side is BandSide.BELOW


# --- per-assumption projection assembly -------------------------------------
def test_projection_assembles_all_fields_when_crossed() -> None:
    d = _drift_of(_assume("revenue", 1000.0, ToleranceBand(lower=2000.0, upper=3000.0)), INPUTS)
    assert d.name == "revenue" and d.metric_key == "revenue"
    assert d.recorded_value == 1000.0
    assert d.status is DriftStatus.CROSSED
    assert d.crossed_side is BandSide.BELOW
    assert d.resolved_value == 1500.0
    assert d.breach_magnitude == pytest.approx(500.0)   # 2000 - 1500
    # supplementary observation: resolved - recorded = 1500 - 1000
    assert d.recorded_delta == pytest.approx(500.0)


def test_unresolved_projection_carries_reason_and_no_band_fields() -> None:
    d = _drift_of(_assume("gone", 1.0, _both()), INPUTS)
    assert d.status is DriftStatus.UNRESOLVED
    assert d.unresolved_reason is UnresolvedReason.KEY_ABSENT
    assert d.resolved_value is None
    assert d.crossed_side is None
    assert d.breach_magnitude is None
    assert d.distance_to_nearest_bound is None
    assert d.recorded_delta is None  # nothing resolved to compare


# --- model->judgment gap ----------------------------------------------------
def _thesis(analyst_target: float | None) -> Thesis:
    return Thesis(
        id=uuid.uuid4(),
        recorded_at=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
        anchor_valuation_run_id=1,
        anchor_snapshot_id=1,
        anchor_value_per_share=100.0,
        analyst_target=analyst_target,
    )


def test_model_judgment_gap_present() -> None:
    gap = compute_thesis_drift(_thesis(125.0), {}).model_judgment_gap
    assert gap.present is True
    assert gap.gap == pytest.approx(25.0)  # 125 - 100
    assert gap.analyst_target == 125.0 and gap.anchor_value_per_share == 100.0


def test_model_judgment_gap_absent_when_no_target() -> None:
    gap = compute_thesis_drift(_thesis(None), {}).model_judgment_gap
    assert gap.present is False
    # ABSENT, not a zero gap — never reads as "model and judgment agree".
    assert gap.gap is None


def test_projection_carries_thesis_id() -> None:
    thesis = _thesis(125.0)
    proj = compute_thesis_drift(thesis, {})
    assert proj.thesis_id == thesis.id
    assert proj.assumption_drifts == []
