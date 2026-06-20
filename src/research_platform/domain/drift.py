"""Thesis drift — COMPUTED PROJECTIONS over the frozen thesis (Phase 3b).

Drift is the gap between what an analyst committed to (the immutable ``Thesis``,
3a) and what is true *now* (the current ``Snapshot.inputs``). Per ADR-015 it is a
**computed projection, NEVER stored**: this module is pure domain (stdlib +
pydantic + sibling domain modules only) and computes drift on demand from a
thesis and a snapshot's inputs. Nothing here persists, and nothing decides
*significance* — thresholds, cadence, and re-thesis triggers are 3c. 3b reports
raw fact: resolved value, in-band / crossed (+ side + magnitude), or unresolved.

The two projections shipped here (ADR-015, Phase 3b amendment):
  1. per-assumption drift — each premise's ``metric_key`` resolved against the
     current snapshot and judged against its frozen ``ToleranceBand``;
  2. the model->judgment gap — ``analyst_target`` vs ``anchor_value_per_share``
     (both frozen, so thesis-static; needs no current data).
Model-fair-value drift (current model output vs the frozen anchor) is DEFERRED to
a future slice: it needs valuation-run reachability the aggregate does not expose,
and re-running the engine would couple the domain to the valuation adapter
(HARD RULE 1).

The resolver is dual-convention with **derived-wins precedence** (ADR-013 reserved
namespace): a ``metric_key`` naming a derived metric (``NUMERIC_METRIC_KEYS``) is
recomputed via the verification harness's ``compute_metric``; otherwise it is a
flat/dotted key resolved structurally via the shared ``resolve_path``. Both reuse
the existing verification paths — there is no parallel resolver.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from pydantic import BaseModel, ConfigDict

from research_platform.domain.metrics import (
    NUMERIC_METRIC_KEYS,
    MetricError,
    compute_metric,
)
from research_platform.domain.thesis import Thesis, ThesisAssumption, ToleranceBand
from research_platform.domain.verification import resolve_path


class DriftStatus(str, enum.Enum):
    """An assumption's drift state against its band. Raw fact, not significance."""

    IN_BAND = "IN_BAND"        # resolved and within the tolerance band
    CROSSED = "CROSSED"        # resolved and outside the band (see crossed_side)
    UNRESOLVED = "UNRESOLVED"  # could not resolve the metric_key (see reason)


class UnresolvedReason(str, enum.Enum):
    """Why an assumption could not be resolved — split at the resolver boundary.

    ``compute_metric`` conflates KEY_ABSENT and MISSING_INPUT under one
    ``MetricError``; the resolver separates them by checking the derived set
    FIRST, so a failure on a *known* derived metric can only be an inputs problem.
    """

    KEY_ABSENT = "KEY_ABSENT"        # non-derived key whose path is NOT present (found=False)
    MISSING_INPUT = "MISSING_INPUT"  # known derived metric, inputs can't compute it
    VALUE_ABSENT = "VALUE_ABSENT"    # path present but value is null / non-numeric


class BandSide(str, enum.Enum):
    """Which bound a crossed value lies beyond. Always set when CROSSED."""

    BELOW = "below"  # value < lower
    ABOVE = "above"  # value > upper


# ---------------------------------------------------------------------------
# Resolution — dual-convention, derived-wins, never raises.
# ---------------------------------------------------------------------------
class AssumptionResolution(BaseModel):
    """The outcome of resolving one ``metric_key`` against snapshot inputs.

    ``resolved`` true => ``value`` is the numeric value; false => ``reason`` says
    why. This never raises: an unresolvable key is data, not an error (ADR-015).
    """

    model_config = ConfigDict(frozen=True)

    resolved: bool
    value: float | None = None
    reason: UnresolvedReason | None = None


def _unresolved(reason: UnresolvedReason) -> AssumptionResolution:
    return AssumptionResolution(resolved=False, reason=reason)


def resolve_assumption(metric_key: str, inputs: dict) -> AssumptionResolution:
    """Resolve ``metric_key`` against ``inputs``. Dual-convention, derived-wins.

    Precedence (ADR-013 reserved namespace): a derived-metric name is recomputed
    via ``compute_metric``; only a non-derived name falls through to a raw path
    lookup. Because a raw ``Snapshot.inputs`` field is forbidden from shadowing a
    derived-metric name (that would be denormalised current state), derived-wins
    can never mask a real raw field.

    Resolution outcomes, all without raising:
      * derived metric, computes        -> resolved value
      * derived metric, inputs can't     -> UNRESOLVED(MISSING_INPUT)
      * raw key absent / not a path      -> UNRESOLVED(KEY_ABSENT)
      * raw key present but null/non-num -> UNRESOLVED(VALUE_ABSENT)
      * raw key present, numeric         -> resolved value
    """
    if metric_key in NUMERIC_METRIC_KEYS:
        # Name is a KNOWN derived metric, so any failure is an inputs problem
        # (compute_metric never reaches its "unknown metric" branch here).
        try:
            return AssumptionResolution(
                resolved=True, value=compute_metric(metric_key, inputs)
            )
        except MetricError:
            return _unresolved(UnresolvedReason.MISSING_INPUT)

    found, value = resolve_path(inputs, metric_key)
    if not found:
        return _unresolved(UnresolvedReason.KEY_ABSENT)
    if value is None:
        return _unresolved(UnresolvedReason.VALUE_ABSENT)
    # Present, non-null: it must be a usable number to judge against a band. A
    # present-but-non-numeric leaf (e.g. a nested dict) is "no value to judge",
    # treated like present-but-null rather than raising.
    try:
        return AssumptionResolution(resolved=True, value=float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _unresolved(UnresolvedReason.VALUE_ABSENT)


# ---------------------------------------------------------------------------
# Band evaluation — strict inequality; equal-to-bound is in-band.
# ---------------------------------------------------------------------------
class _BandVerdict(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: DriftStatus
    crossed_side: BandSide | None = None
    breach_magnitude: float | None = None          # CROSSED: distance past the crossed bound
    distance_to_nearest_bound: float | None = None  # IN_BAND: neutral slack (>= 0)


def _evaluate_band(value: float, band: ToleranceBand) -> _BandVerdict:
    """Judge ``value`` against ``band``. One-directional bands never symmetrize.

    Crossing is STRICT (``value`` equal to a bound is in-band). A bound that is
    ``None`` cannot be crossed on that side, so an upper-only band can only be
    crossed ABOVE and a lower-only band only BELOW. ``breach_magnitude`` is the
    distance past the crossed bound and is set ONLY when crossed; for an in-band
    value, ``distance_to_nearest_bound`` is the (non-negative) slack to the
    closest existing bound — a neutral observation, typed distinctly from a breach.
    """
    if band.lower is not None and value < band.lower:
        return _BandVerdict(
            status=DriftStatus.CROSSED,
            crossed_side=BandSide.BELOW,
            breach_magnitude=band.lower - value,
        )
    if band.upper is not None and value > band.upper:
        return _BandVerdict(
            status=DriftStatus.CROSSED,
            crossed_side=BandSide.ABOVE,
            breach_magnitude=value - band.upper,
        )

    slacks: list[float] = []
    if band.lower is not None:
        slacks.append(value - band.lower)
    if band.upper is not None:
        slacks.append(band.upper - value)
    return _BandVerdict(
        status=DriftStatus.IN_BAND,
        distance_to_nearest_bound=min(slacks) if slacks else None,
    )


# ---------------------------------------------------------------------------
# Projections (1) per-assumption drift and (2) model->judgment gap.
# ---------------------------------------------------------------------------
class AssumptionDrift(BaseModel):
    """Per-assumption drift: the frozen premise + how it stands against NOW.

    Echoes the frozen premise (``name``/``metric_key``/``recorded_value``/``band``)
    then the computed projection. ``resolved_value`` and the band fields are set
    only when resolved. ``recorded_delta`` (``resolved_value - recorded_value``) is
    a SUPPLEMENTARY observation, NOT the drift signal — crossing status comes from
    the band alone (the recorded value is an independent analyst number, not a
    snapshot-derived figure; see ADR-015 Phase 3b amendment).
    """

    model_config = ConfigDict(frozen=True)

    name: str
    metric_key: str
    recorded_value: float
    band: ToleranceBand

    status: DriftStatus
    unresolved_reason: UnresolvedReason | None = None
    resolved_value: float | None = None
    crossed_side: BandSide | None = None
    breach_magnitude: float | None = None
    distance_to_nearest_bound: float | None = None
    recorded_delta: float | None = None  # supplementary observation, not the signal


class ModelJudgmentGap(BaseModel):
    """Thesis-static gap between the analyst's target and the model anchor.

    ``present`` is false when the thesis has no ``analyst_target`` (the analyst did
    not override the model): represented as ABSENT, NOT a zero gap, so it never
    reads as "model and judgment agree". Needs no current data — both operands are
    frozen on the thesis.
    """

    model_config = ConfigDict(frozen=True)

    present: bool
    gap: float | None = None  # analyst_target - anchor_value_per_share
    analyst_target: float | None = None
    anchor_value_per_share: float | None = None


class ThesisDriftProjection(BaseModel):
    """The full computed drift view for one thesis. Never stored (ADR-015).

    ``thesis_recorded_at`` is echoed off the frozen thesis (3a column) so a
    downstream pure consumer can judge staleness (Phase 3c significance) without a
    second load — it is the *recorded* fact, not a clock read here.
    """

    model_config = ConfigDict(frozen=True)

    thesis_id: uuid.UUID
    thesis_recorded_at: dt.datetime
    assumption_drifts: list[AssumptionDrift]
    model_judgment_gap: ModelJudgmentGap


def _assumption_drift(assumption: ThesisAssumption, inputs: dict) -> AssumptionDrift:
    resolution = resolve_assumption(assumption.metric_key, inputs)
    common = dict(
        name=assumption.name,
        metric_key=assumption.metric_key,
        recorded_value=assumption.recorded_value,
        band=assumption.band,
    )
    if not resolution.resolved:
        return AssumptionDrift(
            **common,
            status=DriftStatus.UNRESOLVED,
            unresolved_reason=resolution.reason,
        )

    value = resolution.value
    assert value is not None  # resolved => value present (narrowing for the type)
    verdict = _evaluate_band(value, assumption.band)
    return AssumptionDrift(
        **common,
        status=verdict.status,
        resolved_value=value,
        crossed_side=verdict.crossed_side,
        breach_magnitude=verdict.breach_magnitude,
        distance_to_nearest_bound=verdict.distance_to_nearest_bound,
        recorded_delta=value - assumption.recorded_value,
    )


def _model_judgment_gap(thesis: Thesis) -> ModelJudgmentGap:
    if thesis.analyst_target is None:
        return ModelJudgmentGap(present=False)
    return ModelJudgmentGap(
        present=True,
        gap=thesis.analyst_target - thesis.anchor_value_per_share,
        analyst_target=thesis.analyst_target,
        anchor_value_per_share=thesis.anchor_value_per_share,
    )


def compute_thesis_drift(
    thesis: Thesis, current_snapshot_inputs: dict
) -> ThesisDriftProjection:
    """Compute the drift projection for ``thesis`` against the current inputs.

    Pure: the caller is responsible for the two-step load (the aggregate for the
    active thesis + the ledger ``get_snapshot`` for the current inputs); this
    function neither fetches nor stores. That orchestration / any read surface is
    a separately-scoped future slice — 3b surfaces nothing, exactly as 3a did.
    """
    return ThesisDriftProjection(
        thesis_id=thesis.id,
        thesis_recorded_at=thesis.recorded_at,
        assumption_drifts=[
            _assumption_drift(a, current_snapshot_inputs) for a in thesis.assumptions
        ],
        model_judgment_gap=_model_judgment_gap(thesis),
    )
