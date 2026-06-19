"""Domain tests for Phase 3c drift significance. Pure — no DB, no network, no clock.

Covers the per-assumption banding (near-edge IN_BAND, CROSSED normalized against
fraction-of-crossed-bound, the zero-bound rule), directionality by band shape
(one-directional RE_THESIS-eligible vs two-sided capped at REVIEW), staleness as an
orthogonal REVIEW-only dimension, the max-severity composition, and — the
load-bearing invariant — that a PARTIAL thesis can never read as a clean HOLDS.

The clock is always injected (``as_of``); thresholds are constructed explicitly so
the band edges are unambiguous (the placeholder defaults are exercised separately).
"""

from __future__ import annotations

import datetime as dt

import pytest

from research_platform.domain.drift import DriftStatus, UnresolvedReason, compute_thesis_drift
from research_platform.domain.significance import (
    Coverage,
    SignificanceThresholds,
    Verdict,
    compute_significance,
)
from research_platform.domain.thesis import Thesis, ThesisAssumption, ToleranceBand

UTC = dt.timezone.utc
REC = dt.datetime(2024, 6, 1, tzinfo=UTC)        # thesis recorded
FRESH = dt.datetime(2024, 6, 2, tzinfo=UTC)      # 0 quarters elapsed
T = SignificanceThresholds()                     # placeholder defaults: 0.05 / 0.15 / 4q


def _assume(metric_key: str, band: ToleranceBand, recorded: float = 1.0) -> ThesisAssumption:
    return ThesisAssumption(
        name=metric_key, metric_key=metric_key, recorded_value=recorded, band=band
    )


def _thesis(assumptions: list[ThesisAssumption], recorded_at: dt.datetime = REC) -> Thesis:
    return Thesis(
        recorded_at=recorded_at,
        anchor_valuation_run_id=1,
        anchor_snapshot_id=1,
        anchor_value_per_share=100.0,
        assumptions=assumptions,
    )


def _sig(
    assumptions: list[ThesisAssumption],
    inputs: dict,
    *,
    as_of: dt.datetime = FRESH,
    recorded_at: dt.datetime = REC,
    thresholds: SignificanceThresholds = T,
):
    proj = compute_thesis_drift(_thesis(assumptions, recorded_at), inputs)
    return compute_significance(proj, as_of=as_of, thresholds=thresholds)


def _only(sig):
    """The single per-assumption verdict (tests that isolate one premise)."""
    assert len(sig.assumptions) == 1
    return sig.assumptions[0]


# === directional band edges (one-directional is RE_THESIS-eligible) ==========
def test_one_dir_floor_breach_exactly_at_edge_is_review() -> None:
    # normalized = (20 - 17)/20 = 0.15 == breach_edge -> strict >, so REVIEW.
    sig = _sig([_assume("x", ToleranceBand(lower=20.0, upper=None))], {"x": 17.0})
    a = _only(sig)
    assert a.status is DriftStatus.CROSSED
    assert a.normalized_breach == pytest.approx(0.15)
    assert a.verdict is Verdict.REVIEW
    assert sig.overall is Verdict.REVIEW and sig.re_thesis is False


def test_one_dir_floor_breach_past_edge_is_re_thesis() -> None:
    # normalized = (20 - 16)/20 = 0.20 > 0.15 -> RE_THESIS.
    sig = _sig([_assume("x", ToleranceBand(lower=20.0, upper=None))], {"x": 16.0})
    assert _only(sig).verdict is Verdict.RE_THESIS
    assert sig.overall is Verdict.RE_THESIS and sig.re_thesis is True


def test_one_dir_ceiling_above_is_re_thesis_eligible() -> None:
    # normalized = (50 - 40)/40 = 0.25 > 0.15 -> RE_THESIS on the dangerous side.
    sig = _sig([_assume("x", ToleranceBand(lower=None, upper=40.0))], {"x": 50.0})
    a = _only(sig)
    assert a.status is DriftStatus.CROSSED and a.verdict is Verdict.RE_THESIS


def test_two_sided_crossing_caps_at_review_either_direction() -> None:
    band = ToleranceBand(lower=20.0, upper=30.0)
    above = _sig([_assume("x", band)], {"x": 50.0})   # confirming/over-shoot side
    below = _sig([_assume("x", band)], {"x": 5.0})    # other side
    # Both are far past their bound (normalized >> breach_edge) yet capped at REVIEW:
    # a two-sided band has no encoded dangerous side, so never auto-RE_THESIS.
    assert _only(above).verdict is Verdict.REVIEW
    assert _only(below).verdict is Verdict.REVIEW
    assert above.re_thesis is False and below.re_thesis is False


# === near-edge IN_BAND ========================================================
def test_in_band_slack_exactly_at_warn_is_holds() -> None:
    # slack/|bound| = 1/20 = 0.05 == warn_fraction -> strict <, so HOLDS.
    sig = _sig([_assume("x", ToleranceBand(lower=20.0, upper=None))], {"x": 21.0})
    a = _only(sig)
    assert a.status is DriftStatus.IN_BAND and a.verdict is Verdict.HOLDS


def test_in_band_slack_inside_warn_is_review() -> None:
    # slack/|bound| = 0.5/20 = 0.025 < 0.05 -> REVIEW (sitting near the edge).
    sig = _sig([_assume("x", ToleranceBand(lower=20.0, upper=None))], {"x": 20.5})
    assert _only(sig).verdict is Verdict.REVIEW
    assert sig.overall is Verdict.REVIEW


def test_in_band_comfortable_slack_is_holds() -> None:
    sig = _sig([_assume("x", ToleranceBand(lower=20.0, upper=None))], {"x": 40.0})
    assert _only(sig).verdict is Verdict.HOLDS
    assert sig.overall is Verdict.HOLDS and sig.coverage is Coverage.COMPLETE


# === zero-bound rule ==========================================================
def test_crossing_a_zero_bound_is_max_severity() -> None:
    # crossed_bound == 0.0 -> normalizer undefined -> categorically RE_THESIS.
    sig = _sig([_assume("fcf", ToleranceBand(lower=0.0, upper=None))], {"fcf": -0.5})
    a = _only(sig)
    assert a.status is DriftStatus.CROSSED
    assert a.normalized_breach is None     # undefined, not a divide-by-zero
    assert a.verdict is Verdict.RE_THESIS


def test_near_edge_skipped_when_nearest_bound_is_zero() -> None:
    # In-band, nearest bound is 0.0 -> no meaningful normalizer -> HOLDS (not REVIEW).
    sig = _sig([_assume("x", ToleranceBand(lower=0.0, upper=1.0))], {"x": 0.001})
    a = _only(sig)
    assert a.status is DriftStatus.IN_BAND and a.verdict is Verdict.HOLDS


# === ABSENT / UNRESOLVED -> UNKNOWN, never a clean HOLDS ======================
@pytest.mark.parametrize(
    "metric_key, inputs, reason",
    [
        ("market_share", {"revenue": 1.0}, UnresolvedReason.KEY_ABSENT),   # raw absent
        ("null_field", {"null_field": None}, UnresolvedReason.VALUE_ABSENT),  # present null
        ("roe", {"net_income": 1.0}, UnresolvedReason.MISSING_INPUT),      # derived, no equity
    ],
)
def test_each_unresolved_reason_is_unknown_and_partial(metric_key, inputs, reason) -> None:
    sig = _sig([_assume(metric_key, ToleranceBand(lower=0.0, upper=2.0))], inputs)
    a = _only(sig)
    assert a.status is DriftStatus.UNRESOLVED
    assert a.verdict is Verdict.UNKNOWN
    assert a.unresolved_reason is reason
    assert a.normalized_breach is None
    # An unresolved premise alone can NEVER read as a clean HOLDS.
    assert sig.coverage is Coverage.PARTIAL
    assert sig.overall is Verdict.UNKNOWN
    assert sig.severity is Verdict.HOLDS  # no resolved breach -> severity stays HOLDS


def test_empty_snapshot_all_unresolved_is_unknown_partial() -> None:
    sig = _sig(
        [
            _assume("ebit_margin", ToleranceBand(lower=0.10, upper=None)),  # derived
            _assume("revenue", ToleranceBand(lower=0.0, upper=None)),       # raw
        ],
        {},
    )
    assert all(a.verdict is Verdict.UNKNOWN for a in sig.assumptions)
    assert sig.coverage is Coverage.PARTIAL
    assert sig.overall is Verdict.UNKNOWN


# === THE COVERAGE INVARIANT (the test that matters most) =====================
def test_partial_would_be_holds_is_unknown_not_holds() -> None:
    # One comfortably IN_BAND premise (-> HOLDS) + one permanently-unresolvable
    # premise. Fresh thesis (no staleness). Severity-on-resolved is HOLDS, but a
    # clean HOLDS REQUIRES FULL COVERAGE, so the headline is UNKNOWN, never HOLDS.
    sig = _sig(
        [
            _assume("x", ToleranceBand(lower=0.0, upper=None)),            # 0.40 -> HOLDS
            _assume("market_share", ToleranceBand(lower=0.25, upper=None)),  # KEY_ABSENT
        ],
        {"x": 0.40},
    )
    assert sig.severity is Verdict.HOLDS       # HOLDS-on-resolved
    assert sig.coverage is Coverage.PARTIAL
    assert sig.overall is Verdict.UNKNOWN      # <-- the invariant
    assert sig.overall is not Verdict.HOLDS


def test_partial_with_resolved_breach_keeps_severity_not_unknown() -> None:
    # Unresolved must DOWNGRADE confidence, never ESCALATE — but a resolved breach
    # still drives the verdict: partial + a CROSSED premise stays REVIEW/RE_THESIS.
    sig = _sig(
        [
            _assume("x", ToleranceBand(lower=20.0, upper=None)),             # 16 -> RE_THESIS
            _assume("market_share", ToleranceBand(lower=0.25, upper=None)),  # KEY_ABSENT
        ],
        {"x": 16.0},
    )
    assert sig.coverage is Coverage.PARTIAL
    assert sig.overall is Verdict.RE_THESIS
    assert sig.re_thesis is True


# === staleness (orthogonal, REVIEW-only) =====================================
def test_staleness_boundary_four_vs_five_quarters() -> None:
    thr = SignificanceThresholds(stale_quarters=5)
    in_band = [_assume("x", ToleranceBand(lower=0.0, upper=None))]
    four = _sig(in_band, {"x": 0.40}, as_of=dt.datetime(2025, 6, 1, tzinfo=UTC), thresholds=thr)
    five = _sig(in_band, {"x": 0.40}, as_of=dt.datetime(2025, 9, 1, tzinfo=UTC), thresholds=thr)
    assert four.staleness.quarters_elapsed == 4 and four.staleness.verdict is Verdict.HOLDS
    assert five.staleness.quarters_elapsed == 5 and five.staleness.verdict is Verdict.REVIEW
    assert four.overall is Verdict.HOLDS
    assert five.overall is Verdict.REVIEW  # stale-but-in-band -> REVIEW


def test_staleness_reports_age_days_and_quarters() -> None:
    sig = _sig(
        [_assume("x", ToleranceBand(lower=0.0, upper=None))],
        {"x": 0.40},
        as_of=dt.datetime(2025, 6, 1, tzinfo=UTC),
    )
    assert sig.staleness.age_days == 365
    assert sig.staleness.quarters_elapsed == 4


def test_stale_alone_never_trips_re_thesis() -> None:
    # Ancient thesis, everything comfortably in band -> REVIEW at most, never RE_THESIS.
    sig = _sig(
        [_assume("x", ToleranceBand(lower=0.0, upper=None))],
        {"x": 0.40},
        as_of=dt.datetime(2030, 6, 1, tzinfo=UTC),  # ~24 quarters old
    )
    assert sig.staleness.verdict is Verdict.REVIEW
    assert sig.overall is Verdict.REVIEW
    assert sig.re_thesis is False


def test_fresh_but_breached_is_re_thesis() -> None:
    sig = _sig([_assume("x", ToleranceBand(lower=20.0, upper=None))], {"x": 16.0})
    assert sig.staleness.verdict is Verdict.HOLDS  # fresh
    assert sig.overall is Verdict.RE_THESIS and sig.re_thesis is True


# === composition: the real DEMOA mix =========================================
def test_demoa_mix_is_review_partial_no_rethesis() -> None:
    # 1 CROSSED ebit_margin (0.21 < 0.22 floor, normalized 0.01/0.22 ~= 0.045 < 0.15
    # -> REVIEW) + 2 IN_BAND (revenue, debt_equity comfortable) + 1 UNRESOLVED
    # market_share (KEY_ABSENT). Fresh thesis. Overall REVIEW, coverage PARTIAL.
    inputs = {
        "revenue": 1_200_000.0, "ebit": 252_000.0, "equity": 1_000_000.0,
        "total_debt": 350_000.0,
    }
    sig = _sig(
        [
            _assume("ebit_margin", ToleranceBand(lower=0.22, upper=None), recorded=0.25),
            _assume("revenue", ToleranceBand(lower=900_000.0, upper=1_500_000.0), recorded=1_000_000.0),
            _assume("debt_equity", ToleranceBand(lower=None, upper=0.40), recorded=0.22),
            _assume("market_share", ToleranceBand(lower=0.25, upper=None), recorded=0.30),
        ],
        inputs,
    )
    by = {a.metric_key: a for a in sig.assumptions}
    assert by["ebit_margin"].status is DriftStatus.CROSSED
    assert by["ebit_margin"].normalized_breach == pytest.approx(0.01 / 0.22, rel=1e-3)
    assert by["ebit_margin"].verdict is Verdict.REVIEW
    assert by["revenue"].verdict is Verdict.HOLDS
    assert by["debt_equity"].verdict is Verdict.HOLDS  # slack 0.05/0.40=0.125 > warn
    assert by["market_share"].verdict is Verdict.UNKNOWN

    assert sig.severity is Verdict.REVIEW
    assert sig.coverage is Coverage.PARTIAL
    assert sig.overall is Verdict.REVIEW
    assert sig.re_thesis is False


# === degenerate ===============================================================
def test_all_in_band_full_coverage_is_clean_holds() -> None:
    # The "thesis at its anchor" shape: everything resolves IN_BAND, nothing
    # unresolved, fresh clock -> a clean HOLDS with COMPLETE coverage.
    sig = _sig(
        [
            _assume("x", ToleranceBand(lower=0.0, upper=None)),
            _assume("y", ToleranceBand(lower=None, upper=10.0)),
        ],
        {"x": 0.40, "y": 5.0},
    )
    assert sig.overall is Verdict.HOLDS
    assert sig.severity is Verdict.HOLDS
    assert sig.coverage is Coverage.COMPLETE
    assert sig.re_thesis is False


def test_no_assumptions_is_holds_complete() -> None:
    sig = _sig([], {})
    assert sig.overall is Verdict.HOLDS
    assert sig.coverage is Coverage.COMPLETE


# === threshold provenance =====================================================
def test_default_thresholds_are_flagged_uncalibrated() -> None:
    sig = _sig([_assume("x", ToleranceBand(lower=0.0, upper=None))], {"x": 0.40})
    assert sig.thresholds.calibrated is False
    assert sig.thresholds.version == "uncalibrated-placeholder-v0"


def test_thresholds_echo_back_on_the_verdict() -> None:
    thr = SignificanceThresholds(warn_fraction=0.07, breach_edge=0.2, stale_quarters=6)
    sig = _sig([_assume("x", ToleranceBand(lower=0.0, upper=None))], {"x": 0.40}, thresholds=thr)
    assert sig.thresholds.warn_fraction == 0.07
    assert sig.thresholds.breach_edge == 0.2
    assert sig.thresholds.stale_quarters == 6
