"""SQLAlchemy 2.x models — SQLAlchemy lives HERE ONLY, never in the domain.

These mirror the Pydantic domain models. The ``snapshot`` and ``valuation_run``
tables are append-only ledger tables; their immutability is enforced by a
database trigger installed in an Alembic migration (not by these classes).
"""

from __future__ import annotations

import datetime as dt
import uuid as uuid_lib

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from research_platform.domain.models import SnapshotKind
from research_platform.domain.stock import CoverageStatus


class Base(DeclarativeBase):
    pass


# Shared enum type for the coverage status across the stock + status_transition
# tables. ``create_type=False``: the type is created/dropped by the Alembic
# migration (0005), not by ORM metadata.
_coverage_status = Enum(CoverageStatus, name="coverage_status", create_type=False)


class Stock(Base):
    """MUTABLE registry — deliberately NO immutability trigger (cf. the ledger
    tables). The int ``id`` is the storage surrogate PK that the ledger FK graph
    points at; it never leaves this layer. ``uuid`` is the aggregate's identity
    and ``isin`` its natural key (ADR-013)."""

    __tablename__ = "stock"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Aggregate identity (UUID) and natural key (ISIN) — added in Phase 2a.
    uuid: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True),
        unique=True,
        server_default=text("gen_random_uuid()"),
    )
    isin: Mapped[str] = mapped_column(String(12), unique=True)
    ticker: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256))
    exchange: Mapped[str] = mapped_column(String(16), default="NSE")
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    profile: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[CoverageStatus] = mapped_column(
        _coverage_status, server_default=CoverageStatus.candidate.value
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    snapshots: Mapped[list["Snapshot"]] = relationship(back_populates="stock")
    transitions: Mapped[list["StatusTransition"]] = relationship(
        back_populates="stock"
    )


class Snapshot(Base):
    __tablename__ = "snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stock.id"), index=True)
    as_of: Mapped[dt.date] = mapped_column()
    kind: Mapped[SnapshotKind] = mapped_column(
        Enum(SnapshotKind, name="snapshot_kind")
    )
    inputs: Mapped[dict] = mapped_column(JSONB)
    source_versions: Mapped[dict] = mapped_column(JSONB, default=dict)
    code_version: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    stock: Mapped[Stock] = relationship(back_populates="snapshots")
    # IMMUTABLE: enforced by DB trigger (see migrations) — no UPDATE/DELETE.


class ValuationRun(Base):
    __tablename__ = "valuation_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stock.id"), index=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshot.id"), index=True)
    model_name: Mapped[str] = mapped_column(String(64))
    assumptions: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB)
    code_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # IMMUTABLE: enforced by DB trigger (see migrations) — no UPDATE/DELETE.


class Report(Base):
    __tablename__ = "report"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stock.id"), index=True)
    # one report per snapshot in Part A (supports idempotency by snapshot_id).
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshot.id"), unique=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    content: Mapped[dict] = mapped_column(JSONB)
    verification: Mapped[dict] = mapped_column(JSONB)
    code_version: Mapped[str] = mapped_column(String(64))
    # Generic LLM provenance (ADR-011); nullable — null on the deterministic path.
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # IMMUTABLE: enforced by DB trigger (see migrations) — no UPDATE/DELETE.


class StatusTransition(Base):
    """APPEND-ONLY audit log of coverage-status changes (Phase 2a).

    One row per ``Stock.transition_to``. Immutable: the same ``block_mutation()``
    trigger used on the ledger tables rejects UPDATE/DELETE (installed in 0005).
    ``occurred_at`` carries the domain timestamp (not a server default) so a
    persisted transition re-reads identically to the in-memory one.
    """

    __tablename__ = "status_transition"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stock.id"), index=True)
    from_status: Mapped[CoverageStatus] = mapped_column(_coverage_status)
    to_status: Mapped[CoverageStatus] = mapped_column(_coverage_status)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(512))

    stock: Mapped[Stock] = relationship(back_populates="transitions")
    # IMMUTABLE: enforced by DB trigger (see migrations) — no UPDATE/DELETE.


class Thesis(Base):
    """APPEND-ONLY recorded analyst view (Phase 3a, ADR-015).

    Immutable: the same ``block_mutation()`` trigger the ledger tables use rejects
    UPDATE/DELETE (installed in 0006). The anchor facts
    (``anchor_value_per_share``, ``anchor_snapshot_id``) are FROZEN columns — the
    fair value committed to at record time, not projected from the (versioned)
    run on load. ``recorded_at`` carries the domain timestamp (not a server
    default) so a persisted thesis re-reads identically to the in-memory one.
    """

    __tablename__ = "thesis"

    id: Mapped[int] = mapped_column(primary_key=True)
    uuid: Mapped[uuid_lib.UUID] = mapped_column(
        UUID(as_uuid=True),
        unique=True,
        server_default=text("gen_random_uuid()"),
    )
    stock_id: Mapped[int] = mapped_column(ForeignKey("stock.id"), index=True)

    # Provenance (required) + the frozen anchor facts (ADR-015).
    anchor_valuation_run_id: Mapped[int] = mapped_column(
        ForeignKey("valuation_run.id"), index=True
    )
    anchor_snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshot.id"))
    anchor_value_per_share: Mapped[float] = mapped_column(Float)

    # The analyst's adjusted view (optional) and why it differs from the model.
    analyst_target: Mapped[float | None] = mapped_column(Float, nullable=True)
    override_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    recorded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    # Structural prose (pre-LLM).
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    bull: Mapped[str | None] = mapped_column(Text, nullable=True)
    bear: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    assumptions: Mapped[list["ThesisAssumption"]] = relationship(
        back_populates="thesis"
    )
    # IMMUTABLE: enforced by DB trigger (see migrations) — no UPDATE/DELETE.


class ThesisAssumption(Base):
    """APPEND-ONLY load-bearing premise of a thesis (Phase 3a, ADR-015).

    One row per assumption, child of ``thesis`` — mirrors the
    ``status_transition`` pattern (append-only child under an immutable parent).
    ``metric_key`` is a flat key into a future ``Snapshot.inputs`` (3b resolves
    it; 3a only records it). Immutable via the same trigger.
    """

    __tablename__ = "thesis_assumption"

    id: Mapped[int] = mapped_column(primary_key=True)
    thesis_id: Mapped[int] = mapped_column(ForeignKey("thesis.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    metric_key: Mapped[str] = mapped_column(String(256))
    recorded_value: Mapped[float] = mapped_column(Float)
    lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    upper: Mapped[float | None] = mapped_column(Float, nullable=True)

    thesis: Mapped[Thesis] = relationship(back_populates="assumptions")
    # IMMUTABLE: enforced by DB trigger (see migrations) — no UPDATE/DELETE.
