"""API response DTOs — the api layer OWNS the wire format (HARD RULE 1).

Every DTO is mapped EXPLICITLY from the domain aggregate via a ``from_*``
classmethod; a handler never returns the domain model or calls ``.model_dump()``
on it. This keeps the wire contract decoupled from the domain's internal shape.

Guardrails encoded here:
- **No int PK on the wire (HARD RULE 4).** ``StockResponse`` exposes ``uuid`` +
  ``isin`` (never the storage surrogate int PK); ``SnapshotLeafResponse`` drops
  the domain ``Snapshot.stock_id`` (which *is* that int PK) and identifies the
  owning stock by ``isin`` instead. Snapshots keep their own int id in the URL
  and payload by design (append-only log entries).

Deliberate coupling (Amendment 4): ``CoverageStatus`` is the *domain* value enum,
reused here as a stable primitive for the wire/query contract. This is an
intentional shared-value coupling — not the aggregate leaking. If those string
values ever change, the API contract changes with them; re-declaring the enum
would be worse duplication.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel

from research_platform.domain.drift import (
    AssumptionDrift,
    BandSide,
    DriftStatus,
    ModelJudgmentGap,
    ThesisDriftProjection,
    UnresolvedReason,
)
from research_platform.domain.models import Snapshot
from research_platform.domain.stock import (
    CoverageStatus,
    SnapshotRef,
    StatusTransition,
    Stock,
)
from research_platform.domain.thesis import Thesis, ToleranceBand


class SnapshotRefResponse(BaseModel):
    """One entry on a stock's snapshot timeline."""

    snapshot_id: int
    as_of: dt.date

    @classmethod
    def from_ref(cls, ref: SnapshotRef) -> SnapshotRefResponse:
        return cls(snapshot_id=ref.snapshot_id, as_of=ref.as_of)


class StatusTransitionResponse(BaseModel):
    """One audited coverage-status change."""

    from_status: CoverageStatus
    to_status: CoverageStatus
    occurred_at: dt.datetime
    reason: str

    @classmethod
    def from_transition(cls, t: StatusTransition) -> StatusTransitionResponse:
        return cls(
            from_status=t.from_status,
            to_status=t.to_status,
            occurred_at=t.occurred_at,
            reason=t.reason,
        )


class StockSummaryResponse(BaseModel):
    """Lean list item: identity + profile + status, no timelines."""

    uuid: uuid.UUID
    isin: str
    ticker: str
    name: str
    exchange: str
    sector: str | None
    status: CoverageStatus

    @classmethod
    def from_aggregate(cls, stock: Stock) -> StockSummaryResponse:
        return cls(
            uuid=stock.id,
            isin=stock.isin,
            ticker=stock.ticker,
            name=stock.name,
            exchange=stock.exchange,
            sector=stock.sector,
            status=stock.status,
        )


class StockResponse(BaseModel):
    """Full aggregate view: identity, profile, status, timeline + history."""

    uuid: uuid.UUID
    isin: str
    ticker: str
    name: str
    exchange: str
    sector: str | None
    status: CoverageStatus
    snapshot_refs: list[SnapshotRefResponse]
    transition_history: list[StatusTransitionResponse]
    created_at: dt.datetime | None
    updated_at: dt.datetime | None

    @classmethod
    def from_aggregate(cls, stock: Stock) -> StockResponse:
        return cls(
            uuid=stock.id,
            isin=stock.isin,
            ticker=stock.ticker,
            name=stock.name,
            exchange=stock.exchange,
            sector=stock.sector,
            status=stock.status,
            snapshot_refs=[
                SnapshotRefResponse.from_ref(r) for r in stock.snapshot_refs
            ],
            transition_history=[
                StatusTransitionResponse.from_transition(t)
                for t in stock.transition_history
            ],
            created_at=stock.created_at,
            updated_at=stock.updated_at,
        )


class SnapshotLeafResponse(BaseModel):
    """A read-only view of an immutable ledger snapshot leaf.

    Carries the snapshot's own ``snapshot_id`` (int, by design) and the owning
    ``isin`` — NEVER the stock's surrogate int PK (the domain ``Snapshot``'s
    ``stock_id`` is intentionally dropped, HARD RULE 4).
    """

    snapshot_id: int
    isin: str
    as_of: dt.date
    kind: str
    inputs: dict
    source_versions: dict
    code_version: str
    content_hash: str
    created_at: dt.datetime | None

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot, *, isin: str) -> SnapshotLeafResponse:
        return cls(
            snapshot_id=snapshot.id,
            isin=isin,
            as_of=snapshot.as_of,
            kind=snapshot.kind.value,
            inputs=snapshot.inputs,
            source_versions=snapshot.source_versions,
            code_version=snapshot.code_version,
            content_hash=snapshot.content_hash,
            created_at=snapshot.created_at,
        )


# ===========================================================================
# Thesis drift surfacing (read-only projection of ADR-015 3b drift).
#
# Drift is a COMPUTED PROJECTION, never stored (ADR-015): the endpoint
# recomputes on every call and the response is marked Cache-Control: no-store.
# The wire shape preserves the honest-state distinctions the domain fought for —
# status vs reason are separate fields; breach vs in-band slack are separate
# fields; the model->judgment gap's ABSENT state is an explicit boolean (not a
# zero or a bare null); and the supplementary resolved-recorded delta is nested
# under ``observation`` so it can never be mistaken for the drift signal.
# ===========================================================================


class BandResponse(BaseModel):
    """A tolerance band; either bound nullable (one-directional bands preserved)."""

    lower: float | None
    upper: float | None

    @classmethod
    def from_band(cls, band: ToleranceBand) -> BandResponse:
        return cls(lower=band.lower, upper=band.upper)


class DriftObservation(BaseModel):
    """Supplementary observations — NOT the drift signal (band is authoritative).

    ``resolved_minus_recorded`` compares today's resolved value to the analyst's
    independently-recorded assumption value; it is informational only and is
    nested here precisely so a client cannot read it as the crossing signal.
    """

    resolved_minus_recorded: float | None


class AssumptionDriftResponse(BaseModel):
    """One premise's drift against NOW. Raw fact only — no significance judgment."""

    name: str
    metric_key: str
    recorded_value: float
    band: BandResponse

    # ``status`` and ``unresolved_reason`` are SEPARATE fields: reason is non-null
    # iff status is UNRESOLVED, and each of KEY_ABSENT / VALUE_ABSENT /
    # MISSING_INPUT is independently readable as its own string on the wire.
    status: DriftStatus
    unresolved_reason: UnresolvedReason | None

    resolved_value: float | None
    crossed_side: BandSide | None
    # ``breach_magnitude`` (past the crossed bound) and ``distance_to_nearest_bound``
    # (in-band slack) are DISTINCT fields, never merged: exactly one is non-null
    # when resolved, both null when UNRESOLVED.
    breach_magnitude: float | None
    distance_to_nearest_bound: float | None

    observation: DriftObservation

    @classmethod
    def from_assumption_drift(cls, d: AssumptionDrift) -> AssumptionDriftResponse:
        return cls(
            name=d.name,
            metric_key=d.metric_key,
            recorded_value=d.recorded_value,
            band=BandResponse.from_band(d.band),
            status=d.status,
            unresolved_reason=d.unresolved_reason,
            resolved_value=d.resolved_value,
            crossed_side=d.crossed_side,
            breach_magnitude=d.breach_magnitude,
            distance_to_nearest_bound=d.distance_to_nearest_bound,
            observation=DriftObservation(resolved_minus_recorded=d.recorded_delta),
        )


class ModelJudgmentGapResponse(BaseModel):
    """Thesis-STATIC gap (analyst_target vs anchor) — does NOT drift over snapshots.

    ``present`` is the explicit ABSENT flag: ``present=false`` (analyst set no
    target) is a distinct state from ``present=true, gap=0.0`` (target equals the
    anchor). A client reads "no override" off the boolean, never off a null/zero.
    """

    present: bool
    gap: float | None
    analyst_target: float | None
    anchor_value_per_share: float | None

    @classmethod
    def from_gap(cls, g: ModelJudgmentGap) -> ModelJudgmentGapResponse:
        return cls(
            present=g.present,
            gap=g.gap,
            analyst_target=g.analyst_target,
            anchor_value_per_share=g.anchor_value_per_share,
        )


class ThesisDriftBody(BaseModel):
    """The 3b projection shape: per-assumption drifts + the static gap.

    ``model_judgment_gap`` sits beside ``assumption_drifts`` because that is the
    domain projection's shape — but it is thesis-static, not snapshot-dependent;
    only the assumption drifts move as the current snapshot changes.
    """

    assumption_drifts: list[AssumptionDriftResponse]
    model_judgment_gap: ModelJudgmentGapResponse

    @classmethod
    def from_projection(cls, p: ThesisDriftProjection) -> ThesisDriftBody:
        return cls(
            assumption_drifts=[
                AssumptionDriftResponse.from_assumption_drift(d)
                for d in p.assumption_drifts
            ],
            model_judgment_gap=ModelJudgmentGapResponse.from_gap(p.model_judgment_gap),
        )


class ThesisDriftResponse(BaseModel):
    """Envelope identifying BOTH ends of the drift interval + the projection.

    The interval runs from the thesis (``thesis_recorded_at`` anchored at
    ``anchor_snapshot``, the "then") to ``current_snapshot`` (the "now"). The two
    booleans/nullables keep the degenerate states separately readable:
    ``thesis_present=false`` (no thesis at all) is distinct from a present thesis
    with ``current_snapshot=null`` (nothing to resolve against yet); ``drift`` is
    non-null only when a current snapshot was resolved.
    """

    isin: str
    thesis_present: bool
    thesis_id: uuid.UUID | None
    thesis_recorded_at: dt.datetime | None
    anchor_snapshot: SnapshotRefResponse | None
    current_snapshot: SnapshotRefResponse | None
    drift: ThesisDriftBody | None

    @classmethod
    def no_thesis(cls, isin: str) -> ThesisDriftResponse:
        """No active thesis — a real read result, not an error."""
        return cls(
            isin=isin,
            thesis_present=False,
            thesis_id=None,
            thesis_recorded_at=None,
            anchor_snapshot=None,
            current_snapshot=None,
            drift=None,
        )

    @classmethod
    def no_current_snapshot(
        cls, isin: str, thesis: Thesis, anchor_snapshot: SnapshotRef | None
    ) -> ThesisDriftResponse:
        """A thesis exists but there is no current snapshot to resolve against."""
        return cls(
            isin=isin,
            thesis_present=True,
            thesis_id=thesis.id,
            thesis_recorded_at=thesis.recorded_at,
            anchor_snapshot=(
                SnapshotRefResponse.from_ref(anchor_snapshot)
                if anchor_snapshot is not None
                else None
            ),
            current_snapshot=None,
            drift=None,
        )

    @classmethod
    def from_projection(
        cls,
        isin: str,
        thesis: Thesis,
        anchor_snapshot: SnapshotRef | None,
        current_snapshot: SnapshotRef,
        projection: ThesisDriftProjection,
    ) -> ThesisDriftResponse:
        return cls(
            isin=isin,
            thesis_present=True,
            thesis_id=thesis.id,
            thesis_recorded_at=thesis.recorded_at,
            anchor_snapshot=(
                SnapshotRefResponse.from_ref(anchor_snapshot)
                if anchor_snapshot is not None
                else None
            ),
            current_snapshot=SnapshotRefResponse.from_ref(current_snapshot),
            drift=ThesisDriftBody.from_projection(projection),
        )
