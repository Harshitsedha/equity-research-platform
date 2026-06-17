"""SQLAlchemy 2.x models — SQLAlchemy lives HERE ONLY, never in the domain.

These mirror the Pydantic domain models. The ``snapshot`` and ``valuation_run``
tables are append-only ledger tables; their immutability is enforced by a
database trigger installed in an Alembic migration (not by these classes).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from research_platform.domain.models import SnapshotKind


class Base(DeclarativeBase):
    pass


class Stock(Base):
    __tablename__ = "stock"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256))
    exchange: Mapped[str] = mapped_column(String(16), default="NSE")
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    profile: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    snapshots: Mapped[list["Snapshot"]] = relationship(back_populates="stock")


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
