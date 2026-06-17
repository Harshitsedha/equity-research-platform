"""report provenance: model_version + prompt_version (ADR-011)

First-class, nullable provenance of WHAT produced a report (the LLM model id and
prompt-template version). Null on the deterministic/stub path. Adding columns does
not touch the existing report_immutable trigger (it remains in force).

Revision ID: 0004_report_provenance
Revises: 0003_report_table
Create Date: 2026-06-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_report_provenance"
down_revision = "0003_report_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "report", sa.Column("model_version", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "report", sa.Column("prompt_version", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("report", "prompt_version")
    op.drop_column("report", "model_version")
