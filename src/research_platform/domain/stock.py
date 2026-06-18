"""The Stock aggregate root — the living coverage record per ticker (Phase 2a).

Where ``models.Snapshot`` / ``models.ValuationRun`` are isolated immutable ledger
leaves, the ``Stock`` aggregate is the *mutable* entity that ties them into a
coverage history: identity, profile, coverage status (with an audited transition
history), and an append-only timeline of the snapshots taken for the ticker.

Purity (ADR-004): this module depends on **stdlib + pydantic only**. It knows
nothing of SQLAlchemy, the database, or any adapter — persistence is expressed
through the ``StockRepository`` Protocol (``domain.ports.stock_repository``).

Design rules baked in here (see ADR-013):
- **Identity is the UUID.** ISIN is the stable natural key. The storage surrogate
  int PK never appears in the domain.
- **No denormalised ledger state.** There is deliberately NO ``current_snapshot``
  field — "latest" is a query over the append-only timeline
  (``latest_snapshot_ref``), never a writable column.
- **Invariants live in the domain.** Status transitions are validated here, and
  the timeline is kept ordered on append here — not left to the query layer.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from bisect import insort

from pydantic import BaseModel, ConfigDict, Field


class CoverageStatus(str, enum.Enum):
    """Where a ticker sits in the research pipeline."""

    candidate = "candidate"  # on the radar, not yet under active coverage
    active = "active"        # under active coverage
    dropped = "dropped"      # coverage discontinued


#: The ONLY legal status edges. ``candidate`` is initial-only (no edge re-enters
#: it). Self-loops are absent by construction, so they are rejected too.
_LEGAL_TRANSITIONS: frozenset[tuple[CoverageStatus, CoverageStatus]] = frozenset(
    {
        (CoverageStatus.candidate, CoverageStatus.active),
        (CoverageStatus.candidate, CoverageStatus.dropped),
        (CoverageStatus.active, CoverageStatus.dropped),
        (CoverageStatus.dropped, CoverageStatus.active),
    }
)


class IllegalStatusTransition(Exception):
    """Raised when ``transition_to`` is asked for an edge outside the legal set.

    The aggregate is left untouched when this is raised: no status change, no
    transition recorded (the check happens before any mutation).
    """

    def __init__(self, from_status: CoverageStatus, to_status: CoverageStatus) -> None:
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"illegal coverage-status transition: "
            f"{from_status.value} -> {to_status.value}"
        )


class StatusTransition(BaseModel):
    """One audited coverage-status change. Immutable, append-only in the history."""

    model_config = ConfigDict(frozen=True)

    from_status: CoverageStatus
    to_status: CoverageStatus
    occurred_at: dt.datetime
    reason: str


class SnapshotRef(BaseModel):
    """A reference to one snapshot on the aggregate's timeline.

    Carries the snapshot's own identity (``snapshot_id``) and ``as_of`` period —
    enough to order the timeline and to load the full snapshot via the ledger.
    The stock's surrogate int PK is intentionally NOT here.
    """

    model_config = ConfigDict(frozen=True)

    snapshot_id: int
    as_of: dt.date


def _timeline_key(ref: SnapshotRef) -> tuple[dt.date, int]:
    """Canonical timeline order: by reporting period, then snapshot identity.

    This is the SAME order the storage adapter projects on reload, so an
    in-memory aggregate and a round-tripped one have an identical timeline.
    """
    return (ref.as_of, ref.snapshot_id)


class Stock(BaseModel):
    """The aggregate root: a ticker's living coverage record.

    Mutable (slowly): ``ticker``/``name``/``exchange``/``sector``/``profile`` and
    ``status`` change over time. ``id`` (UUID) and ``isin`` are stable identity.
    The two list fields are append-only timelines, maintained through the methods
    below — do not mutate them directly.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    isin: str                      # stable natural key (unique, required)
    ticker: str                    # mutable
    name: str                      # mutable
    exchange: str = "NSE"          # mutable
    sector: str | None = None      # mutable
    profile: dict = Field(default_factory=dict)
    status: CoverageStatus = CoverageStatus.candidate
    snapshot_refs: list[SnapshotRef] = Field(default_factory=list)
    transition_history: list[StatusTransition] = Field(default_factory=list)
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None

    # --- coverage status ---------------------------------------------------
    def transition_to(
        self,
        new_status: CoverageStatus,
        reason: str,
        *,
        occurred_at: dt.datetime | None = None,
    ) -> StatusTransition:
        """Move coverage to ``new_status``, recording the change.

        Raises ``IllegalStatusTransition`` (mutating nothing) for any edge outside
        the legal set. On a legal edge, appends a ``StatusTransition`` to the
        history and updates ``status``/``updated_at`` — all in memory; the
        repository persists the transition in the same transaction as the status.
        """
        edge = (self.status, new_status)
        if edge not in _LEGAL_TRANSITIONS:
            # Raise BEFORE any mutation: an illegal attempt leaves status and
            # history untouched (and the repo therefore writes no row).
            raise IllegalStatusTransition(self.status, new_status)

        when = occurred_at or dt.datetime.now(dt.timezone.utc)
        transition = StatusTransition(
            from_status=self.status,
            to_status=new_status,
            occurred_at=when,
            reason=reason,
        )
        self.transition_history.append(transition)
        self.status = new_status
        self.updated_at = when
        return transition

    # --- snapshot timeline -------------------------------------------------
    def add_snapshot_ref(self, snapshot_id: int, as_of: dt.date) -> SnapshotRef:
        """Append a snapshot to the timeline, keeping it ordered in the DOMAIN.

        The timeline is kept sorted by ``(as_of, snapshot_id)`` on insert, so an
        out-of-order append yields the same timeline as the aggregate reloaded
        from storage. The ordering invariant lives here, not in the query layer.
        """
        ref = SnapshotRef(snapshot_id=snapshot_id, as_of=as_of)
        insort(self.snapshot_refs, ref, key=_timeline_key)
        return ref

    @property
    def latest_snapshot_ref(self) -> SnapshotRef | None:
        """The most recent snapshot on the timeline, or ``None`` if empty.

        A *query* over the append-only timeline — not stored state. Because the
        timeline is kept ordered, "latest" is simply the last element.
        """
        return self.snapshot_refs[-1] if self.snapshot_refs else None
