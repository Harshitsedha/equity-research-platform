"""Drift significance — the VERDICT layer over 3b's raw drift (Phase 3c, ADR-016).

3b answers *"how far has it drifted?"* as raw fact (in-band / crossed + side +
magnitude, or unresolved). 3c answers *"is that drift big enough to act on?"* — it
bands the raw projection into an ordered verdict (HOLDS < REVIEW < RE_THESIS),
folds in thesis staleness, and flags coverage. Like 3b it is a **computed
projection, NEVER stored** (ADR-015 reaffirmed by ADR-016): this module is pure
domain (stdlib + pydantic + sibling domain modules only), takes the 3b projection
plus an INJECTED clock, and persists nothing. Recomputed every call — fully
reproducible from (thesis + snapshot + clock + the echoed threshold-set).

Strategic call (ADR-016): the verdict is **assumption-driven by design**. Model-
fair-value drift is deferred (it needs valuation-run reachability that would couple
the domain to the valuation adapter, HARD RULE 1), so 3c bands the per-assumption
band drift only. The thesis-static ``ModelJudgmentGap`` passes through 3b's output
untouched but does NOT enter severity composition (it does not move with the
snapshot). Composition is a **max-severity OR seam** so a future valuation-
magnitude term slots in with zero refactor.

Calibration is still deferred — we are on synthetic fuel with a single live breach
(~4.5% past a floor). The default thresholds are HONEST PLACEHOLDERS, marked
``calibrated=False`` with a version string that echoes on every verdict (same
provenance discipline as Phase-1 model/prompt/code_version). ADR open question:
revisit freezing thresholds onto the thesis once real calibration exists.
"""

from __future__ import annotations

import datetime as dt
import enum

from pydantic import BaseModel, ConfigDict

from research_platform.domain.drift import (
    AssumptionDrift,
    BandSide,
    DriftStatus,
    ThesisDriftProjection,
    UnresolvedReason,
)


class Verdict(str, enum.Enum):
    """A significance verdict.

    ``HOLDS`` < ``REVIEW`` < ``RE_THESIS`` are the ORDERED severities used in
    composition. ``UNKNOWN`` is NOT a fourth severity and never participates in the
    max-composition — it is the coverage outcome produced only by the coverage
    invariant (a would-be-HOLDS thesis with an unresolved premise cannot read as a
    clean HOLDS) and as the per-assumption verdict of an unresolved premise.
    """

    HOLDS = "HOLDS"
    REVIEW = "REVIEW"
    RE_THESIS = "RE_THESIS"
    UNKNOWN = "UNKNOWN"


class Coverage(str, enum.Enum):
    """Whether every load-bearing premise could be resolved against NOW."""

    COMPLETE = "COMPLETE"  # all assumptions resolved
    PARTIAL = "PARTIAL"    # >= 1 assumption UNRESOLVED (the orthogonal UNKNOWN flag)


# Severity ordering — defined ONLY over the three real severities. UNKNOWN is
# deliberately absent: it is not comparable, so it can never win a max().
_SEVERITY_RANK: dict[Verdict, int] = {
    Verdict.HOLDS: 0,
    Verdict.REVIEW: 1,
    Verdict.RE_THESIS: 2,
}


def _max_severity(a: Verdict, b: Verdict) -> Verdict:
    """Max over the ordered severities. Operands must be HOLDS/REVIEW/RE_THESIS."""
    return a if _SEVERITY_RANK[a] >= _SEVERITY_RANK[b] else b


# ---------------------------------------------------------------------------
# Thresholds — provenanced, injected, NOT frozen on the thesis (ADR-016).
# ---------------------------------------------------------------------------
class SignificanceThresholds(BaseModel):
    """The band edges significance is judged against — a recorded platform policy.

    NOT frozen on the thesis (interpretations compute, commitments freeze — a
    significance verdict is an interpretation). The defaults are uncalibrated
    placeholders: we have one live breach (~4.5%) on synthetic data. ``version`` +
    ``calibrated`` echo on every verdict so a reading is reproducible given the
    threshold-set that produced it.
    """

    model_config = ConfigDict(frozen=True)

    #: IN_BAND slack within this fraction of the nearest bound -> REVIEW (near edge).
    warn_fraction: float = 0.05
    #: CROSSED breach beyond this fraction of the crossed bound -> RE_THESIS.
    breach_edge: float = 0.15
    #: Thesis at/after this many elapsed calendar quarters -> REVIEW (staleness).
    stale_quarters: int = 4

    # Provenance — honest placeholder marker (echoed on the wire).
    version: str = "uncalibrated-placeholder-v0"
    calibrated: bool = False


# ---------------------------------------------------------------------------
# Per-assumption verdict.
# ---------------------------------------------------------------------------
class AssumptionSignificance(BaseModel):
    """One premise's band drift banded into a verdict.

    ``verdict`` is one of the three severities when resolved, or ``UNKNOWN`` when
    the premise could not be resolved (carrying its ``unresolved_reason`` verbatim
    — an unresolved premise contributes NO severity, only a coverage downgrade).
    ``normalized_breach`` is the single universal normalizer ``breach_magnitude /
    |crossed_bound|`` (fraction-of-crossed-bound — the only convention that works
    for width-less one-directional bands); it is ``None`` when in-band, unresolved,
    or the crossed bound is zero (normalizer undefined; see the zero-bound rule).
    """

    model_config = ConfigDict(frozen=True)

    name: str
    metric_key: str
    status: DriftStatus
    verdict: Verdict
    normalized_breach: float | None = None
    unresolved_reason: UnresolvedReason | None = None


def _crossed_bound(drift: AssumptionDrift) -> float:
    """The bound a CROSSED value lies beyond (always present when CROSSED)."""
    if drift.crossed_side is BandSide.BELOW:
        assert drift.band.lower is not None  # BELOW can only happen with a lower
        return drift.band.lower
    assert drift.band.upper is not None  # ABOVE can only happen with an upper
    return drift.band.upper


def _is_two_sided(drift: AssumptionDrift) -> bool:
    return drift.band.lower is not None and drift.band.upper is not None


def _crossed_verdict(
    drift: AssumptionDrift, t: SignificanceThresholds
) -> tuple[Verdict, float | None]:
    """Band a CROSSED assumption. A crossing is always >= REVIEW (it IS out of band).

    Directionality is encoded by band SHAPE, no thesis tag (ADR-016):
      * one-directional band — a floor can only cross below (compression), a
        ceiling only above (rising): the crossed side IS the dangerous side, so the
        crossing is RE_THESIS-eligible once the breach passes ``breach_edge``;
      * two-sided band — the dangerous side is not encoded, so BOTH crossings cap
        at REVIEW (the confirming direction must NEVER read as RE_THESIS); banding
        a dangerous side onto a two-sided thesis is deferred (it would need a frozen
        column -> a migration -> baked judgement, with zero live demand).

    Zero-bound rule: crossing a 0.0 bound is categorically significant — the
    fraction normalizer is undefined (division by zero), so any breach past a zero
    bound on a one-directional band is max-severity (RE_THESIS). ``normalized`` is
    ``None`` in that case.
    """
    bound = _crossed_bound(drift)

    if _is_two_sided(drift):
        # Capped at REVIEW regardless of magnitude or a zero bound — without a
        # dangerous-side tag we never auto-escalate a two-sided crossing.
        normalized = (
            None if bound == 0 else drift.breach_magnitude / abs(bound)  # type: ignore[operator]
        )
        return Verdict.REVIEW, normalized

    if bound == 0:
        return Verdict.RE_THESIS, None  # zero-bound rule

    normalized = drift.breach_magnitude / abs(bound)  # type: ignore[operator]
    # Strict escalation, mirroring 3b's strict-inequality crossing: a breach
    # exactly at the edge stays on the gentler side (REVIEW).
    verdict = Verdict.RE_THESIS if normalized > t.breach_edge else Verdict.REVIEW
    return verdict, normalized


def _in_band_verdict(drift: AssumptionDrift, t: SignificanceThresholds) -> Verdict:
    """Band an IN_BAND assumption: HOLDS unless it is sitting near a bound.

    The near-edge normalizer is slack / |nearest bound|. If the nearest bound is
    0.0 the normalizer is meaningless, so near-edge is skipped (-> HOLDS). Strict
    escalation again: slack exactly at ``warn_fraction`` stays HOLDS.
    """
    value = drift.resolved_value
    assert value is not None  # IN_BAND => resolved

    # Recover which bound is nearest (the one achieving distance_to_nearest_bound).
    candidates: list[tuple[float, float]] = []
    if drift.band.lower is not None:
        candidates.append((value - drift.band.lower, drift.band.lower))
    if drift.band.upper is not None:
        candidates.append((drift.band.upper - value, drift.band.upper))
    nearest_slack, nearest_bound = min(candidates, key=lambda c: c[0])

    if nearest_bound == 0:
        return Verdict.HOLDS  # no meaningful normalizer on a zero bound

    if nearest_slack / abs(nearest_bound) < t.warn_fraction:
        return Verdict.REVIEW
    return Verdict.HOLDS


def _assumption_significance(
    drift: AssumptionDrift, t: SignificanceThresholds
) -> AssumptionSignificance:
    common = dict(name=drift.name, metric_key=drift.metric_key, status=drift.status)

    if drift.status is DriftStatus.UNRESOLVED:
        return AssumptionSignificance(
            **common,
            verdict=Verdict.UNKNOWN,
            unresolved_reason=drift.unresolved_reason,
        )

    if drift.status is DriftStatus.CROSSED:
        verdict, normalized = _crossed_verdict(drift, t)
        return AssumptionSignificance(
            **common, verdict=verdict, normalized_breach=normalized
        )

    return AssumptionSignificance(**common, verdict=_in_band_verdict(drift, t))


# ---------------------------------------------------------------------------
# Staleness — thesis age, orthogonal to magnitude.
# ---------------------------------------------------------------------------
class Staleness(BaseModel):
    """Thesis age as a significance dimension. ``as_of`` is INJECTED (purity).

    ``age_days`` is the raw fact; ``quarters_elapsed`` is the judged unit (the
    quarterly business cadence). ``verdict`` caps at REVIEW — age is an expiry
    prompt, not a thesis falsification, so staleness ALONE never trips RE_THESIS.
    """

    model_config = ConfigDict(frozen=True)

    age_days: int
    quarters_elapsed: int
    verdict: Verdict  # HOLDS or REVIEW only


def _quarters_elapsed(recorded_at: dt.datetime, as_of: dt.datetime) -> int:
    """Whole calendar quarters between ``recorded_at`` and ``as_of`` (>= 0).

    Calendar-month based (not day-count), day-of-month aware, so a thesis recorded
    on the 1st is exactly N quarters old N*3 months later. Clock skew (as_of before
    recorded_at) clamps to 0.
    """
    months = (as_of.year - recorded_at.year) * 12 + (as_of.month - recorded_at.month)
    if as_of.day < recorded_at.day:
        months -= 1
    return max(0, months) // 3


def _staleness(
    recorded_at: dt.datetime, as_of: dt.datetime, t: SignificanceThresholds
) -> Staleness:
    age_days = max(0, (as_of - recorded_at).days)
    quarters = _quarters_elapsed(recorded_at, as_of)
    verdict = Verdict.REVIEW if quarters >= t.stale_quarters else Verdict.HOLDS
    return Staleness(age_days=age_days, quarters_elapsed=quarters, verdict=verdict)


# ---------------------------------------------------------------------------
# The thesis-level verdict.
# ---------------------------------------------------------------------------
class ThesisSignificance(BaseModel):
    """The composed verdict for one thesis. A pure projection, never stored.

    ``severity`` is the max over RESOLVED assumptions and staleness (always one of
    the three ordered severities — the "HOLDS-on-resolved" value). ``overall`` is
    the headline: it equals ``severity`` EXCEPT when the coverage invariant fires —
    a PARTIAL thesis that would otherwise be a clean HOLDS is reported as UNKNOWN,
    never HOLDS (an unresolved premise can never read as "no drift"). ``re_thesis``
    is the pure-derived OR result (``overall == RE_THESIS``); staleness is excluded
    from it by construction.
    """

    model_config = ConfigDict(frozen=True)

    overall: Verdict
    severity: Verdict
    coverage: Coverage
    re_thesis: bool
    staleness: Staleness
    assumptions: list[AssumptionSignificance]
    thresholds: SignificanceThresholds


def compute_significance(
    projection: ThesisDriftProjection,
    *,
    as_of: dt.datetime,
    thresholds: SignificanceThresholds,
) -> ThesisSignificance:
    """Band a 3b drift projection into a significance verdict. Pure, never stored.

    Composition is a max-severity OR seam: ``overall = max(resolved-assumption
    severities, staleness)`` — a future valuation-magnitude term slots in here with
    no refactor. The coverage invariant is applied last: a would-be-HOLDS thesis
    with any unresolved premise becomes UNKNOWN. ``as_of`` is the injected clock
    (the handler passes request-time now); ``thresholds`` echoes back on the
    verdict for reproducibility.
    """
    assumptions = [
        _assumption_significance(d, thresholds) for d in projection.assumption_drifts
    ]

    # Severity composes over RESOLVED premises only (UNRESOLVED contributes none).
    severity = Verdict.HOLDS
    for a in assumptions:
        if a.verdict is not Verdict.UNKNOWN:
            severity = _max_severity(severity, a.verdict)

    staleness = _staleness(projection.thesis_recorded_at, as_of, thresholds)
    severity = _max_severity(severity, staleness.verdict)

    coverage = (
        Coverage.PARTIAL
        if any(a.verdict is Verdict.UNKNOWN for a in assumptions)
        else Coverage.COMPLETE
    )

    # Coverage invariant: a clean HOLDS requires full coverage. A partial thesis
    # that would be HOLDS is reported UNKNOWN (HOLDS-on-resolved). Partial + a
    # resolved REVIEW/RE_THESIS keeps that severity — unresolved downgrades
    # confidence, never escalates severity.
    if coverage is Coverage.PARTIAL and severity is Verdict.HOLDS:
        overall = Verdict.UNKNOWN
    else:
        overall = severity

    return ThesisSignificance(
        overall=overall,
        severity=severity,
        coverage=coverage,
        re_thesis=overall is Verdict.RE_THESIS,
        staleness=staleness,
        assumptions=assumptions,
        thresholds=thresholds,
    )
