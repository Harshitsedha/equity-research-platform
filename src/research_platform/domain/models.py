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


# ===========================================================================
# Phase 1A — claims, verification, and reports (ADR-010)
#
# The LLM never does arithmetic that reaches a report. It emits structured,
# checkable Claims; the domain verifies each against frozen snapshot data before
# any prose is trusted. The models below are that contract.
# ===========================================================================


class ClaimType(str, enum.Enum):
    """What kind of assertion a claim makes (drives which checks apply)."""

    NUMERIC = "NUMERIC"        # a number recomputable from snapshot inputs
    FACTUAL = "FACTUAL"        # a fact that must cite an existing input
    QUALITATIVE = "QUALITATIVE"  # judgement/prose; nothing to recompute


class Claim(BaseModel):
    """One structured, checkable assertion produced by an LLM adapter.

    The LLM must return these; it does not return free prose. Verification (the
    domain) decides whether each is trustworthy before assembly.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    claim_type: ClaimType
    statement: str                       # human-readable
    section: str                         # which report section it belongs to
    metric_key: str | None = None        # NUMERIC: which metric (e.g. "roce")
    asserted_value: float | None = None  # NUMERIC: the number the model claims
    citation: str | None = None          # key/path into snapshot.inputs


class StructuredReportDraft(BaseModel):
    """What an ``LLMPort`` returns: claims + intended section ordering."""

    model_config = ConfigDict(frozen=True)

    stock_id: int
    snapshot_id: int
    claims: list[Claim]
    section_order: list[str] = Field(default_factory=list)


class ClaimStatus(str, enum.Enum):
    """Per-claim verification outcome."""

    VERIFIED = "VERIFIED"   # passed every applicable check
    FAILED = "FAILED"       # NUMERIC mismatch / uncomputable -> hard fail
    FLAGGED = "FLAGGED"     # citation missing/dangling -> surfaced, not fatal


class OverallStatus(str, enum.Enum):
    """Draft-level verification outcome; ``HARD_FAILED`` is the only un-storable one."""

    PASSED = "PASSED"
    PASSED_WITH_FLAGS = "PASSED_WITH_FLAGS"  # citation flags only (stored)
    INCOMPLETE = "INCOMPLETE"                # missing required section (stored)
    HARD_FAILED = "HARD_FAILED"              # any numeric mismatch (NOT stored)


class ClaimVerification(BaseModel):
    """The verifier's verdict on a single claim."""

    model_config = ConfigDict(frozen=True)

    claim_id: str
    claim_type: ClaimType
    status: ClaimStatus
    detail: str
    asserted_value: float | None = None
    computed_value: float | None = None
    citation: str | None = None


class VerificationResult(BaseModel):
    """The full verdict on a draft. Recorded on the Report for auditability."""

    model_config = ConfigDict(frozen=True)

    overall: OverallStatus
    claims: list[ClaimVerification]
    missing_sections: list[str] = Field(default_factory=list)
    code_version: str

    @property
    def hard_failed(self) -> bool:
        return self.overall is OverallStatus.HARD_FAILED

    @property
    def is_storable(self) -> bool:
        """FLAG-AND-STORE policy: everything except a hard numeric fail is stored."""
        return not self.hard_failed


class Report(BaseModel):
    """An immutable, derived report tied to the exact Snapshot it was built from.

    ``content`` is the rendered (deterministic-template) prose + claims;
    ``verification`` is the serialized VerificationResult. Both are stored as
    JSONB. Immutable in the type system here and via DB trigger in storage.
    """

    model_config = ConfigDict(frozen=True)

    id: int | None = None
    stock_id: int
    snapshot_id: int
    kind: str
    content: dict
    verification: dict
    code_version: str
    # Generic provenance of WHAT produced the report (ADR-011). Null on the
    # deterministic/stub path; set when an LLM adapter generated the draft. These
    # are vendor-neutral strings — the domain never names a specific provider.
    model_version: str | None = None
    prompt_version: str | None = None
    created_at: dt.datetime | None = None
