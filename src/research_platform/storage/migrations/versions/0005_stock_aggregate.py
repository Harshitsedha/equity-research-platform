"""stock aggregate: uuid + isin + coverage status + status_transition audit log

Phase 2a promotes the ``stock`` registry into the home of the Stock aggregate:
a UUID identity, an ISIN natural key, and a coverage status — plus an
append-only ``status_transition`` audit log that reuses the SAME
``block_mutation()`` trigger the ledger tables use.

The ledger tables (snapshot, valuation_run, report), their FKs, and their
immutability triggers are UNTOUCHED. ``stock`` is the mutable registry and gets
no trigger. ``isin`` is added NOT NULL on a clean local schema (no deployed
data); ``uuid`` defaults to ``gen_random_uuid()`` (pgcrypto) so the legacy
insert path keeps working without naming a UUID.

Revision ID: 0005_stock_aggregate
Revises: 0004_report_provenance
Create Date: 2026-06-18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_stock_aggregate"
down_revision = "0004_report_provenance"
branch_labels = None
depends_on = None

coverage_status = postgresql.ENUM(
    "candidate", "active", "dropped", name="coverage_status", create_type=False
)


def upgrade() -> None:
    # gen_random_uuid() lives in pgcrypto; guarded so reruns are safe.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    coverage_status.create(op.get_bind(), checkfirst=True)

    # --- extend the mutable stock registry (NO immutability trigger) ---------
    op.add_column(
        "stock",
        sa.Column(
            "uuid",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint("uq_stock_uuid", "stock", ["uuid"])

    # NOT NULL with no default: valid because the local schema has no rows
    # (the ledger wipe is moot here — decision B left snapshot.stock_id int).
    op.add_column("stock", sa.Column("isin", sa.String(length=12), nullable=False))
    op.create_unique_constraint("uq_stock_isin", "stock", ["isin"])

    op.add_column(
        "stock",
        sa.Column(
            "status",
            coverage_status,
            server_default="candidate",
            nullable=False,
        ),
    )
    op.add_column(
        "stock",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # --- append-only coverage-status audit log -------------------------------
    op.create_table(
        "status_transition",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "stock_id", sa.Integer(), sa.ForeignKey("stock.id"), nullable=False
        ),
        sa.Column("from_status", coverage_status, nullable=False),
        sa.Column("to_status", coverage_status, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
    )
    op.create_index(
        "ix_status_transition_stock_id", "status_transition", ["stock_id"]
    )
    # Same immutability invariant as the ledger: reuse block_mutation() (0002).
    op.execute(
        """
        CREATE TRIGGER status_transition_immutable
        BEFORE UPDATE OR DELETE ON status_transition
        FOR EACH ROW EXECUTE FUNCTION block_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS status_transition_immutable ON status_transition;"
    )
    op.drop_index("ix_status_transition_stock_id", table_name="status_transition")
    op.drop_table("status_transition")

    op.drop_column("stock", "updated_at")
    op.drop_column("stock", "status")
    op.drop_constraint("uq_stock_isin", "stock", type_="unique")
    op.drop_column("stock", "isin")
    op.drop_constraint("uq_stock_uuid", "stock", type_="unique")
    op.drop_column("stock", "uuid")

    coverage_status.drop(op.get_bind(), checkfirst=True)
