"""immutability triggers on snapshot and valuation_run (ledger invariant)

Convention is not enough: the database itself must reject any UPDATE or DELETE on
the append-only ledger tables (ADR-003, Blueprint 1.6).

Revision ID: 0002_immutability_triggers
Revises: 0001_initial
Create Date: 2026-06-17
"""
from __future__ import annotations

from alembic import op

revision = "0002_immutability_triggers"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_IMMUTABLE_TABLES = ("snapshot", "valuation_run")


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION block_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                '% rows are immutable (ledger invariant): % is forbidden',
                TG_TABLE_NAME, TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in _IMMUTABLE_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_immutable
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION block_mutation();
            """
        )


def downgrade() -> None:
    for table in _IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {table};")
    op.execute("DROP FUNCTION IF EXISTS block_mutation();")
