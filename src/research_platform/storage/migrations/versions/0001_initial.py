"""initial ledger tables: stock, snapshot, valuation_run

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

snapshot_kind = postgresql.ENUM(
    "quarterly", "annual", "adhoc", name="snapshot_kind", create_type=False
)


def upgrade() -> None:
    snapshot_kind.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "stock",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("exchange", sa.String(length=16), nullable=False),
        sa.Column("sector", sa.String(length=128), nullable=True),
        sa.Column("profile", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_stock_ticker", "stock", ["ticker"], unique=True)

    op.create_table(
        "snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "stock_id", sa.Integer(), sa.ForeignKey("stock.id"), nullable=False
        ),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("kind", snapshot_kind, nullable=False),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("source_versions", postgresql.JSONB(), nullable=False),
        sa.Column("code_version", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_snapshot_stock_id", "snapshot", ["stock_id"])
    op.create_index("ix_snapshot_content_hash", "snapshot", ["content_hash"])

    op.create_table(
        "valuation_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "stock_id", sa.Integer(), sa.ForeignKey("stock.id"), nullable=False
        ),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("snapshot.id"),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(length=64), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("code_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_valuation_run_stock_id", "valuation_run", ["stock_id"])
    op.create_index(
        "ix_valuation_run_snapshot_id", "valuation_run", ["snapshot_id"]
    )


def downgrade() -> None:
    op.drop_table("valuation_run")
    op.drop_table("snapshot")
    op.drop_index("ix_stock_ticker", table_name="stock")
    op.drop_table("stock")
    snapshot_kind.drop(op.get_bind(), checkfirst=True)
