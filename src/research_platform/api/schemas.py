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

from research_platform.domain.models import Snapshot
from research_platform.domain.stock import (
    CoverageStatus,
    SnapshotRef,
    StatusTransition,
    Stock,
)


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
