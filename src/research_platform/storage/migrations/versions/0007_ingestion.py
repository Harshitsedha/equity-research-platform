"""ingestion_draft (mutable staging) + snapshot_source (immutable provenance)

Phase 0 ingestion (ADR-017). Adds the ingestion write-path's two new tables:

- ``ingestion_draft`` — the MUTABLE staging buffer, the ONE table deliberately
  WITHOUT a ``block_mutation`` trigger: normalize, human correction, and
  re-validation mutate it freely before the gated promote. The immutable ledger
  never sees an unvalidated number.
- ``snapshot_source`` — IMMUTABLE 1:1 source provenance for a promoted snapshot,
  reusing the SAME ``block_mutation()`` trigger (from 0002) the ledger uses. This
  is a side table by design (ADR-017 decision 1): the sealed ``snapshot`` row gets
  NO new columns.

The ``snapshot`` / ``valuation_run`` / ledger tables, their FKs, and their triggers
are UNTOUCHED. ``block_mutation()`` already exists (0002); 0007 only adds a trigger
that uses it, and the downgrade drops that trigger (never the shared function).

Revision ID: 0007_ingestion
Revises: 0006_thesis
Create Date: 2026-06-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_ingestion"
down_revision = "0006_thesis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- ingestion_draft: MUTABLE staging (NO immutability trigger) -----------
    op.create_table(
        "ingestion_draft",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stock_id", sa.Integer(), sa.ForeignKey("stock.id"), nullable=True),
        sa.Column("proposed_isin", sa.String(length=12), nullable=True),
        sa.Column("proposed_ticker", sa.String(length=32), nullable=True),
        sa.Column("proposed_name", sa.String(length=256), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("raw_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("present_values", postgresql.JSONB(), nullable=False),
        sa.Column("proposed_inputs", postgresql.JSONB(), nullable=False),
        sa.Column("validation", postgresql.JSONB(), nullable=False),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("source_doc_ref", sa.Text(), nullable=False),
        sa.Column("ingested_by", sa.String(length=128), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column(
            "promoted_snapshot_id",
            sa.Integer(),
            sa.ForeignKey("snapshot.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_ingestion_draft_stock_id", "ingestion_draft", ["stock_id"])
    # NOTE: deliberately NO block_mutation trigger here — staging is mutable.

    # --- snapshot_source: IMMUTABLE source provenance (1:1, side table) --------
    op.create_table(
        "snapshot_source",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "snapshot_id",
            sa.Integer(),
            sa.ForeignKey("snapshot.id"),
            nullable=False,
        ),
        sa.Column("source_kind", sa.String(length=16), nullable=False),
        sa.Column("source_doc_ref", sa.Text(), nullable=False),
        sa.Column("raw_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_by", sa.String(length=128), nullable=False),
        sa.Column("ingestion_code_version", sa.String(length=64), nullable=False),
        sa.Column(
            "supersedes_snapshot_id",
            sa.Integer(),
            sa.ForeignKey("snapshot.id"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_snapshot_source_snapshot_id", "snapshot_source", ["snapshot_id"]
    )
    op.create_index(
        "ix_snapshot_source_raw_payload_hash", "snapshot_source", ["raw_payload_hash"]
    )

    # Same immutability invariant as the ledger: reuse block_mutation() (0002).
    op.execute(
        """
        CREATE TRIGGER snapshot_source_immutable
        BEFORE UPDATE OR DELETE ON snapshot_source
        FOR EACH ROW EXECUTE FUNCTION block_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS snapshot_source_immutable ON snapshot_source;"
    )
    op.drop_index(
        "ix_snapshot_source_raw_payload_hash", table_name="snapshot_source"
    )
    op.drop_constraint(
        "uq_snapshot_source_snapshot_id", "snapshot_source", type_="unique"
    )
    op.drop_table("snapshot_source")

    op.drop_index("ix_ingestion_draft_stock_id", table_name="ingestion_draft")
    op.drop_table("ingestion_draft")
    # block_mutation() (from 0002) is shared — left intact. snapshot untouched.
