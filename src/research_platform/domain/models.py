"""Domain models — pure Pydantic, the language the rest of the system speaks.

These mirror the storage tables (see ``storage/models.py``) but know nothing of
SQLAlchemy or any database. Per ADR-003/004 the domain depends on stdlib +
pydantic only. Ledger artifacts (``Snapshot``, ``ValuationRun``) are ``frozen``:
immutability is expressed in the type system here and enforced again at the DB
level by a trigger (Implementation Blueprint 1.6).
"""

from __future__ import annotations

import datetime as dt
import enum

from pydantic import BaseModel, ConfigDict, Field


class SnapshotKind(str, enum.Enum):
    """The reporting period a snapshot represents."""

    quarterly = "quarterly"
    annual = "annual"
    adhoc = "adhoc"


class Stock(BaseModel):
    """The persistent anchor. Mutable (slowly): identity + durable profile."""

    id: int | None = None
    ticker: str
    name: str
    exchange: str = "NSE"
    sector: str | None = None
    profile: dict = Field(default_factory=dict)
    created_at: dt.datetime | None = None


class Snapshot(BaseModel):
    """The heart of the ledger: an immutable, version-stamped freeze of inputs.

    ``frozen=True`` makes the in-memory object immutable; the database trigger
    makes the stored row immutable. Both layers, deliberately.
    """

    model_config = ConfigDict(frozen=True)

    id: int | None = None
    stock_id: int
    as_of: dt.date
    kind: SnapshotKind
    inputs: dict
    source_versions: dict = Field(default_factory=dict)
    code_version: str
    content_hash: str
    created_at: dt.datetime | None = None


class ValuationRun(BaseModel):
    """One model execution: assumptions + result, tied to an immutable snapshot.

    Immutable for the same reasons as ``Snapshot``: a stored run must re-derive
    deterministically (the reproducibility invariant).
    """

    model_config = ConfigDict(frozen=True)

    id: int | None = None
    stock_id: int
    snapshot_id: int
    model_name: str
    assumptions: dict
    result: dict
    code_version: str
    created_at: dt.datetime | None = None


class ParsedDocument(BaseModel):
    """A normalized financial document, the output of a ``DocumentPort``.

    Carries everything an uploaded file contributes: the stock's identity, the
    period, the normalized financial line items (``inputs``), and the version
    stamps for the sources. The domain freezes ``inputs`` into a ``Snapshot``.
    """

    ticker: str
    name: str
    exchange: str = "NSE"
    sector: str | None = None
    profile: dict = Field(default_factory=dict)
    as_of: dt.date
    kind: SnapshotKind
    inputs: dict
    source_versions: dict = Field(default_factory=dict)
