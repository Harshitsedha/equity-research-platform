"""Database connection wiring for the storage adapter.

The URL comes from the ``DATABASE_URL`` env var (set by docker-compose / Make),
falling back to the local compose default. Nothing outside ``storage`` imports
this.
"""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "postgresql+psycopg://research:research@localhost:5434/research"


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def make_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    return create_engine(url or get_database_url(), echo=echo, future=True)


def make_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=engine or make_engine(), expire_on_commit=False)
