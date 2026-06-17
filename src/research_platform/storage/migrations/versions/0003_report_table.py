"""report table (immutable, derived) + immutability trigger

Phase 1A: reports are immutable derived artifacts tied to the snapshot they were
built from. Reuses the block_mutation() function installed in 0002.

Revision ID: 0003_report_table
Revises: 0002_immutability_triggers
Create Date: 2026-06-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_report_table"
down_revision = "0002_immutability_triggers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stock_id", sa.Integer(), sa.ForeignKey("stock.id"), nullable=False),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("snapshot.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("verification", postgresql.JSONB(), nullable=False),
        sa.Column("code_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_report_stock_id", "report", ["stock_id"])
    # one report per snapshot (idempotency anchor)
    op.create_index(
        "ix_report_snapshot_id", "report", ["snapshot_id"], unique=True
    )
    op.execute(
        """
        CREATE TRIGGER report_immutable
        BEFORE UPDATE OR DELETE ON report
        FOR EACH ROW EXECUTE FUNCTION block_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS report_immutable ON report;")
    op.drop_index("ix_report_snapshot_id", table_name="report")
    op.drop_index("ix_report_stock_id", table_name="report")
    op.drop_table("report")
