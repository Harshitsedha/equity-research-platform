"""Ingestion — the source-agnostic normalize/validate logic (Phase 0, ADR-017).

Ingestion turns a messy external filing into validated numbers that can be frozen
into the immutable Snapshot ledger. This module is the **fat, shared, pure** core
of that pipeline: a source adapter (manual-assisted now, Screener later) produces a
neutral ``RawExtraction``; this module proposes a field->canonical-key mapping and
validates it; the *gated promote* (the one irreversible write) lives in storage.

Two load-bearing rules live here (ADR-017):

1. **The 8-raw-fundamentals schema.** Ingestion targets ONLY genuinely-raw line
   items (``RAW_FUNDAMENTAL_KEYS``) — the inputs the metrics/drift stack consumes.
   It is FORBIDDEN from writing any of the six derived keys (``NUMERIC_METRIC_KEYS``:
   ebit_margin, roe, …) as a raw input: that is the ADR-013 "forbidden shadow" — the
   resolver recomputes a derived key and would silently ignore an ingested value.

2. **Absence is never zero.** A missing fundamental is *omitted from inputs*, never
   zero-filled and never null-as-data, so the drift resolver reports the real gap
   (``KEY_ABSENT`` / ``VALUE_ABSENT`` / ``MISSING_INPUT``). A fabricated zero would
   silently poison the whole drift/significance stack. Absence enters as ``None`` in
   ``raw_fields`` (a state distinct from the string ``"0"``) and is dropped; a
   present-but-unparseable value is *wrong* and hard-blocks.

Purity (ADR-004): stdlib + pydantic + domain siblings only. No DB, no network.
"""

from __future__ import annotations

import datetime as dt
import enum

from pydantic import BaseModel, ConfigDict, Field

from research_platform.domain.ledger import compute_content_hash
from research_platform.domain.metrics import NUMERIC_METRIC_KEYS
from research_platform.domain.models import SnapshotKind

#: The genuinely-raw fundamentals ingestion maps INTO (ADR-017 schema resolution).
#: The union of every raw input the derived metrics require — and NOTHING derived.
RAW_FUNDAMENTAL_KEYS: tuple[str, ...] = (
    "revenue",
    "prev_revenue",
    "ebit",
    "net_income",
    "equity",
    "total_debt",
    "total_assets",
    "current_liabilities",
)

#: A small set of common NSE label variants -> canonical key. Modest by design: the
#: human is the oracle (manual-assisted), so this only *proposes*; it never forces.
_ALIASES: dict[str, str] = {
    "sales": "revenue",
    "total_revenue": "revenue",
    "revenue_from_operations": "revenue",
    "prior_revenue": "prev_revenue",
    "previous_revenue": "prev_revenue",
    "operating_profit": "ebit",
    "ebitda": "ebit",  # proposed only; human corrects if it is truly EBITDA
    "pat": "net_income",
    "net_profit": "net_income",
    "profit_after_tax": "net_income",
    "net_worth": "equity",
    "shareholders_equity": "equity",
    "shareholder_equity": "equity",
    "borrowings": "total_debt",
    "total_borrowings": "total_debt",
    "debt": "total_debt",
}


class SourceKind(str, enum.Enum):
    """Where an ingestion originated. Stamped on the immutable ``snapshot_source``."""

    manual_paste = "manual_paste"
    screener = "screener"  # future adapter (out of scope this slice)
    filing = "filing"


class DraftStatus(str, enum.Enum):
    """Lifecycle of a mutable ``ingestion_draft`` row."""

    draft = "draft"          # mutable: normalize / human correction / re-validate
    promoted = "promoted"    # an immutable Snapshot was written from it
    discarded = "discarded"  # abandoned, never promoted


# ---------------------------------------------------------------------------
# The neutral structure every source adapter produces (the PORT's payload).
# ---------------------------------------------------------------------------
class ClaimedPeriod(BaseModel):
    """The reporting period a source claims, as raw strings (parsed in validation).

    Strings, not a ``date``/enum, because the adapter must NOT interpret — a
    malformed period is a validation finding, not a parse crash in the adapter.
    """

    model_config = ConfigDict(frozen=True)

    as_of: str | None = None
    kind: str | None = None


class RawExtraction(BaseModel):
    """What a source adapter emits — verbatim, neutral, uninterpreted.

    ``raw_fields`` maps a source field label to its value as a string, or ``None``
    for an explicitly-absent field (the state that is distinct from ``"0"`` — the
    absence-is-not-zero guarantee starts here). ``raw_payload`` is the original blob,
    retained for the reproducibility hash. The adapter does NO canonical mapping and
    NO validation — those are the shared pipeline below.
    """

    model_config = ConfigDict(frozen=True)

    source_kind: SourceKind
    source_doc_ref: str
    raw_payload: dict
    raw_fields: dict[str, str | None]
    claimed_period: ClaimedPeriod


def raw_payload_hash(raw_payload: dict) -> str:
    """The reproducibility anchor: sha256 over the canonical raw payload.

    Reuses the ledger's content-hash machinery (one canonicalize/hash definition in
    the codebase, never a parallel one).
    """
    return compute_content_hash(raw_payload)


# ---------------------------------------------------------------------------
# Normalize — propose the field -> canonical-key mapping (pure, never forces).
# ---------------------------------------------------------------------------
class NormalizedProposal(BaseModel):
    """The proposed mapping. ``present_values`` is canonical_key -> raw string for
    fields that mapped AND carried a value; absent/unmapped fields are dropped here,
    so they can NEVER reappear as a fabricated number downstream."""

    model_config = ConfigDict(frozen=True)

    mapping: dict[str, str] = Field(default_factory=dict)        # canonical_key -> source_field
    present_values: dict[str, str] = Field(default_factory=dict)  # canonical_key -> raw value
    omitted_fields: list[str] = Field(default_factory=list)       # unmapped OR None-valued


def _canonical_for(field: str) -> str | None:
    key = field.strip().lower().replace(" ", "_")
    if key in RAW_FUNDAMENTAL_KEYS:
        return key
    if key in _ALIASES:
        return _ALIASES[key]
    return None


def normalize(extraction: RawExtraction) -> NormalizedProposal:
    """Propose a canonical mapping for ``extraction.raw_fields``.

    A field maps to a canonical key by exact match or a known alias; an unmapped
    field, or a field whose value is ``None`` (explicitly absent), is OMITTED — it
    never enters ``present_values``, so absence stays absence (rule 2).
    """
    mapping: dict[str, str] = {}
    present_values: dict[str, str] = {}
    omitted: list[str] = []
    for field, value in extraction.raw_fields.items():
        canonical = _canonical_for(field)
        if canonical is None or value is None:
            omitted.append(field)
            continue
        mapping[canonical] = field
        present_values[canonical] = value
    return NormalizedProposal(
        mapping=mapping, present_values=present_values, omitted_fields=omitted
    )


# ---------------------------------------------------------------------------
# Validate — "wrong" hard-blocks, "incomplete-but-honest" soft-flags + promotes.
# ---------------------------------------------------------------------------
class Severity(str, enum.Enum):
    HARD_BLOCK = "HARD_BLOCK"  # wrong — blocks promote
    SOFT_FLAG = "SOFT_FLAG"    # incomplete-but-honest — surfaced, promote allowed


class ValidationIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    severity: Severity
    field: str | None = None
    detail: str


class ValidationReport(BaseModel):
    """The validation verdict + the parsed ``proposed_inputs`` ready to freeze.

    ``promotable`` is false iff any issue is ``HARD_BLOCK``. ``proposed_inputs`` holds
    only successfully-parsed present values (absent fundamentals stay absent), so it
    is exactly what ``freeze_snapshot`` will content-hash.
    """

    model_config = ConfigDict(frozen=True)

    proposed_inputs: dict[str, float] = Field(default_factory=dict)
    issues: list[ValidationIssue] = Field(default_factory=list)
    promotable: bool

    def hard_blocks(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.HARD_BLOCK]


def parse_period(period: ClaimedPeriod) -> tuple[dt.date | None, SnapshotKind | None, list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    as_of: dt.date | None = None
    kind: SnapshotKind | None = None
    if not period.as_of:
        issues.append(ValidationIssue(code="period_missing", severity=Severity.HARD_BLOCK,
                                      field="as_of", detail="as_of is required"))
    else:
        try:
            as_of = dt.date.fromisoformat(period.as_of)
        except ValueError:
            issues.append(ValidationIssue(code="period_malformed", severity=Severity.HARD_BLOCK,
                                          field="as_of",
                                          detail=f"as_of {period.as_of!r} is not an ISO date"))
    if not period.kind:
        issues.append(ValidationIssue(code="kind_missing", severity=Severity.HARD_BLOCK,
                                      field="kind", detail="kind is required"))
    else:
        try:
            kind = SnapshotKind(period.kind)
        except ValueError:
            valid = ", ".join(k.value for k in SnapshotKind)
            issues.append(ValidationIssue(code="kind_malformed", severity=Severity.HARD_BLOCK,
                                          field="kind",
                                          detail=f"kind {period.kind!r} not in: {valid}"))
    return as_of, kind, issues


def validate(present_values: dict[str, str], period: ClaimedPeriod) -> ValidationReport:
    """Validate a proposed mapping into a promotable verdict + parsed inputs.

    Hard blocks (wrong): a derived-named key (ADR-013 forbidden shadow), a present
    value that is non-numeric, a malformed/absent period. Soft flags (honest): a
    fundamental missing from the 8, or an unrecognized extra key. Absent fundamentals
    are simply not present here — they are not zero-filled.
    """
    issues: list[ValidationIssue] = []
    proposed_inputs: dict[str, float] = {}

    for key, raw_value in present_values.items():
        if key in NUMERIC_METRIC_KEYS:
            # The forbidden shadow: a derived metric written as a raw input.
            issues.append(ValidationIssue(
                code="derived_key_as_raw", severity=Severity.HARD_BLOCK, field=key,
                detail=f"{key!r} is a derived metric (recomputed); it must not be a raw input",
            ))
            continue
        if key not in RAW_FUNDAMENTAL_KEYS:
            issues.append(ValidationIssue(
                code="unknown_key", severity=Severity.SOFT_FLAG, field=key,
                detail=f"{key!r} is not one of the 8 fundamentals; it will not be consumed",
            ))
        try:
            proposed_inputs[key] = float(raw_value)
        except (TypeError, ValueError):
            issues.append(ValidationIssue(
                code="non_numeric", severity=Severity.HARD_BLOCK, field=key,
                detail=f"{key!r} value {raw_value!r} is present but not numeric (wrong, not absent)",
            ))
            proposed_inputs.pop(key, None)

    _, _, period_issues = parse_period(period)
    issues.extend(period_issues)

    for key in RAW_FUNDAMENTAL_KEYS:
        if key not in proposed_inputs:
            issues.append(ValidationIssue(
                code="missing_fundamental", severity=Severity.SOFT_FLAG, field=key,
                detail=f"{key!r} absent — honest gap, snapshot will resolve it as absent",
            ))

    promotable = not any(i.severity is Severity.HARD_BLOCK for i in issues)
    return ValidationReport(proposed_inputs=proposed_inputs, issues=issues, promotable=promotable)


def prepare(extraction: RawExtraction) -> tuple[NormalizedProposal, ValidationReport]:
    """Convenience: normalize then validate. Pure; the whole pre-promote pipeline."""
    proposal = normalize(extraction)
    report = validate(proposal.present_values, extraction.claimed_period)
    return proposal, report


# ---------------------------------------------------------------------------
# The PROMOTE gate (pure half) — re-asserted at the irreversible boundary.
# ---------------------------------------------------------------------------
class PromotionBlocked(ValueError):
    """Raised at the promote gate when a draft must NOT reach the immutable ledger.

    The ingestion analog of ``NonStorableReportError``: the gate lives at the
    persistence boundary and writes NOTHING when it fires.
    """


def assert_promotable(
    *,
    present_values: dict[str, str],
    period: ClaimedPeriod,
    source_kind: SourceKind | None,
    source_doc_ref: str | None,
    raw_payload_hash_value: str | None,
    ingested_by: str | None,
    ingestion_code_version: str | None,
) -> tuple[dt.date, SnapshotKind, dict[str, float]]:
    """Pure promote gate. Returns ``(as_of, kind, inputs)`` or raises PromotionBlocked.

    The gate RE-VALIDATES the draft's declared ``present_values`` (the editable
    source of truth) rather than trusting a pre-stripped float cache — so a derived
    shadow, a non-numeric value, or a malformed period that ``validate`` flagged
    still hard-blocks here. It then asserts source provenance is complete. The
    reproducibility hash equality + the content-hash recompute are checked in the
    storage txn (they need the retained payload), so this is the pure half of the
    gate. The returned ``inputs`` are the freshly-validated floats to freeze.
    """
    report = validate(present_values, period)
    hard = report.hard_blocks()
    if hard:
        raise PromotionBlocked(
            "draft not promotable: "
            + "; ".join(f"{i.code}({i.field}): {i.detail}" for i in hard)
        )

    missing_prov = [
        name for name, val in (
            ("source_kind", source_kind),
            ("source_doc_ref", source_doc_ref),
            ("raw_payload_hash", raw_payload_hash_value),
            ("ingested_by", ingested_by),
            ("ingestion_code_version", ingestion_code_version),
        ) if not val
    ]
    if missing_prov:
        raise PromotionBlocked(f"incomplete source provenance: {', '.join(missing_prov)}")

    as_of, kind, _ = parse_period(period)
    assert as_of is not None and kind is not None  # validate() already hard-blocked otherwise
    return as_of, kind, report.proposed_inputs
