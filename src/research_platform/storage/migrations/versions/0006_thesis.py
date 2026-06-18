"""thesis + thesis_assumption: the immutable recorded analyst view (Phase 3a)

Adds the ``thesis`` table (the recorded commitment: frozen anchor facts, optional
analyst target, structural prose) and its append-only child ``thesis_assumption``
(the load-bearing premises, each with a tolerance band). Both reuse the SAME
``block_mutation()`` trigger the ledger tables use — a thesis is immutable
history (HARD RULE 1 / ADR-015): you record a new thesis to supersede, never edit.

This mirrors the 0005 ``status_transition`` shape (append-only child under an
immutable parent). The ledger tables, their FKs, and their triggers are
UNTOUCHED; ``stock`` stays the mutable registry. ``gen_random_uuid()`` (pgcrypto)
is already enabled by 0005.

Revision ID: 0006_thesis
Revises: 0005_stock_aggregate
Create Date: 2026-06-19
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_thesis"
down_revision = "0005_stock_aggregate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- thesis: the recorded analyst view (immutable) -----------------------
    op.create_table(
        "thesis",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "uuid",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("stock_id", sa.Integer(), sa.ForeignKey("stock.id"), nullable=False),
        # Provenance (required) + the frozen anchor facts (ADR-015).
        sa.Column(
            "anchor_valuation_run_id",
            sa.Integer(),
            sa.ForeignKey("valuation_run.id"),
            nullable=False,
        ),
        sa.Column(
            "anchor_snapshot_id",
            sa.Integer(),
            sa.ForeignKey("snapshot.id"),
            nullable=False,
        ),
        sa.Column("anchor_value_per_share", sa.Float(), nullable=False),
        # Optional analyst override.
        sa.Column("analyst_target", sa.Float(), nullable=True),
        sa.Column("override_rationale", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        # Structural prose (pre-LLM).
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("bull", sa.Text(), nullable=True),
        sa.Column("bear", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_unique_constraint("uq_thesis_uuid", "thesis", ["uuid"])
    op.create_index("ix_thesis_stock_id", "thesis", ["stock_id"])
    op.create_index(
        "ix_thesis_anchor_valuation_run_id", "thesis", ["anchor_valuation_run_id"]
    )

    # --- thesis_assumption: append-only premises (immutable) -----------------
    op.create_table(
        "thesis_assumption",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "thesis_id", sa.Integer(), sa.ForeignKey("thesis.id"), nullable=False
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("metric_key", sa.String(length=256), nullable=False),
        sa.Column("recorded_value", sa.Float(), nullable=False),
        sa.Column("lower", sa.Float(), nullable=True),
        sa.Column("upper", sa.Float(), nullable=True),
    )
    op.create_index(
        "ix_thesis_assumption_thesis_id", "thesis_assumption", ["thesis_id"]
    )

    # Same immutability invariant as the ledger: reuse block_mutation() (0002).
    for table in ("thesis", "thesis_assumption"):
        op.execute(
            f"""
            CREATE TRIGGER {table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION block_mutation();
            """
        )


def downgrade() -> None:
    for table in ("thesis_assumption", "thesis"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table};")

    op.drop_index("ix_thesis_assumption_thesis_id", table_name="thesis_assumption")
    op.drop_table("thesis_assumption")

    op.drop_index("ix_thesis_anchor_valuation_run_id", table_name="thesis")
    op.drop_index("ix_thesis_stock_id", table_name="thesis")
    op.drop_constraint("uq_thesis_uuid", "thesis", type_="unique")
    op.drop_table("thesis")
