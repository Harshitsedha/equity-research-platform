"""Shared test fixtures.

DB-backed tests are marked ``db``. If PostgreSQL is unreachable they are skipped
(so ``tests/domain`` always runs with no infrastructure). When reachable, the
real Alembic migrations (including the immutability trigger) are applied to a
clean schema — the tests exercise the actual production DDL, not a shortcut.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from research_platform.storage.db import get_database_url, make_engine, make_session_factory

ROOT = Path(__file__).resolve().parent.parent


def _postgres_reachable(url: str) -> bool:
    try:
        engine = make_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except OperationalError:
        return False


@pytest.fixture(scope="session")
def migrated_engine():
    """A clean, fully-migrated DB engine, or skip if Postgres is down."""
    url = get_database_url()
    if not _postgres_reachable(url):
        pytest.skip(
            f"PostgreSQL not reachable at {url} — run `make up` first "
            "(DB-backed tests skipped)"
        )

    # Apply the real migrations to a clean schema (down to base, then up to head).
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = make_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(migrated_engine):
    return make_session_factory(migrated_engine)


@pytest.fixture
def repository(session_factory):
    from research_platform.storage.repository import PostgresRepository

    return PostgresRepository(session_factory)
