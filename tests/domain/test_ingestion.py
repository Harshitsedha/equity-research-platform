"""Pure ingestion tests (Phase 0, ADR-017) — no DB, no network.

Covers the source-agnostic pipeline: the manual adapter wrapping a form into a
neutral RawExtraction (absence preserved as None, distinct from "0"), normalize
proposing the canonical mapping (unmapped/absent OMITTED — never zero), validate
splitting "wrong" (hard-block) from "incomplete-but-honest" (soft-flag + promote),
and the pure promote gate (forbidden shadow, provenance completeness).

The promote WRITE path (the immutable ledger txn) is DB-backed and lives in
tests/storage/test_ingestion_promote.py.
"""

from __future__ import annotations

import pytest

from research_platform.domain.ingestion import (
    ClaimedPeriod,
    PromotionBlocked,
    Severity,
    SourceKind,
    assert_promotable,
    normalize,
    prepare,
    validate,
)
from research_platform.ingestion.manual_adapter import ManualAssistedAdapter

GOOD_PERIOD = ClaimedPeriod(as_of="2025-03-31", kind="annual")


def _form(fields: dict, *, as_of="2025-03-31", kind="annual", ref="filing-x") -> dict:
    return {"source_doc_ref": ref, "period": {"as_of": as_of, "kind": kind}, "fields": fields}


def _read(fields: dict, **kw):
    return ManualAssistedAdapter().read(_form(fields, **kw))


# === the adapter: thin, absence preserved as None (distinct from "0") =========
def test_adapter_wraps_verbatim_and_keeps_source_kind() -> None:
    ext = _read({"revenue": "1200000"})
    assert ext.source_kind is SourceKind.manual_paste
    assert ext.source_doc_ref == "filing-x"
    assert ext.claimed_period == GOOD_PERIOD
    assert ext.raw_fields == {"revenue": "1200000"}


def test_adapter_preserves_absence_as_none_not_zero() -> None:
    ext = _read({"revenue": "100", "equity": None})
    # None stays None (the explicit absent state); it is NOT coerced to "0".
    assert ext.raw_fields["equity"] is None
    assert ext.raw_fields["equity"] != "0"


def test_adapter_zero_is_a_real_value() -> None:
    ext = _read({"total_debt": 0})
    assert ext.raw_fields["total_debt"] == "0"  # a real zero, present, not absence


# === normalize: unmapped / absent OMITTED (never zero, never null) ============
def test_normalize_maps_canonical_and_aliases() -> None:
    proposal = normalize(_read({"revenue": "100", "PAT": "20", "net_worth": "500"}))
    assert proposal.mapping == {"revenue": "revenue", "net_income": "PAT", "equity": "net_worth"}
    assert proposal.present_values == {"revenue": "100", "net_income": "20", "equity": "500"}


def test_normalize_omits_unmapped_and_absent() -> None:
    proposal = normalize(_read({"revenue": "100", "equity": None, "mystery_line": "9"}))
    assert proposal.present_values == {"revenue": "100"}
    # absent (None) and unmapped both omitted — and NOT present as 0 or null.
    assert "equity" not in proposal.present_values
    assert "mystery_line" not in proposal.present_values
    assert set(proposal.omitted_fields) == {"equity", "mystery_line"}


# === validate: wrong blocks, incomplete-but-honest flags + promotes ===========
def test_validate_partial_is_promotable_with_soft_flags() -> None:
    report = validate({"revenue": "1200000", "ebit": "252000"}, GOOD_PERIOD)
    assert report.promotable is True
    assert report.proposed_inputs == {"revenue": 1_200_000.0, "ebit": 252_000.0}
    missing = {i.field for i in report.issues if i.code == "missing_fundamental"}
    assert "equity" in missing and "total_assets" in missing
    assert all(i.severity is Severity.SOFT_FLAG for i in report.issues)


def test_validate_absence_is_not_zero() -> None:
    report = validate({"revenue": "100"}, GOOD_PERIOD)
    # the 7 unsupplied fundamentals are ABSENT, never 0 — they are simply not keys.
    for key in ("equity", "ebit", "total_debt", "net_income"):
        assert key not in report.proposed_inputs


def test_validate_derived_key_as_raw_hard_blocks() -> None:
    report = validate({"revenue": "100", "ebit_margin": "0.21"}, GOOD_PERIOD)
    assert report.promotable is False
    blocks = {i.code for i in report.hard_blocks()}
    assert "derived_key_as_raw" in blocks
    assert "ebit_margin" not in report.proposed_inputs  # never frozen as raw


def test_validate_non_numeric_present_hard_blocks() -> None:
    report = validate({"revenue": "N/A"}, GOOD_PERIOD)  # a sentinel is WRONG, not absent
    assert report.promotable is False
    assert {i.code for i in report.hard_blocks()} == {"non_numeric"}
    assert "revenue" not in report.proposed_inputs


@pytest.mark.parametrize(
    "period, code",
    [
        (ClaimedPeriod(as_of="not-a-date", kind="annual"), "period_malformed"),
        (ClaimedPeriod(as_of="2025-03-31", kind="weekly"), "kind_malformed"),
        (ClaimedPeriod(as_of=None, kind="annual"), "period_missing"),
    ],
)
def test_validate_malformed_period_hard_blocks(period, code) -> None:
    report = validate({"revenue": "100"}, period)
    assert report.promotable is False
    assert code in {i.code for i in report.hard_blocks()}


def test_prepare_is_normalize_then_validate() -> None:
    proposal, report = prepare(_read({"sales": "100", "ebit": "20"}))
    assert proposal.present_values == {"revenue": "100", "ebit": "20"}
    assert report.proposed_inputs == {"revenue": 100.0, "ebit": 20.0}


# === the pure promote gate ====================================================
def _prov(**over):
    base = dict(
        source_kind=SourceKind.manual_paste,
        source_doc_ref="filing-x",
        raw_payload_hash_value="abc123",
        ingested_by="analyst",
        ingestion_code_version="v0",
    )
    base.update(over)
    return base


def test_assert_promotable_returns_inputs_on_clean() -> None:
    as_of, kind, inputs = assert_promotable(
        present_values={"revenue": "100", "ebit": "20"}, period=GOOD_PERIOD, **_prov()
    )
    assert as_of.isoformat() == "2025-03-31"
    assert kind.value == "annual"
    assert inputs == {"revenue": 100.0, "ebit": 20.0}


def test_assert_promotable_blocks_incomplete_provenance() -> None:
    with pytest.raises(PromotionBlocked, match="raw_payload_hash"):
        assert_promotable(
            present_values={"revenue": "100"}, period=GOOD_PERIOD,
            **_prov(raw_payload_hash_value=None),
        )


def test_assert_promotable_blocks_derived_shadow() -> None:
    with pytest.raises(PromotionBlocked, match="derived_key_as_raw"):
        assert_promotable(
            present_values={"ebit_margin": "0.21"}, period=GOOD_PERIOD, **_prov()
        )


def test_assert_promotable_blocks_malformed_period() -> None:
    with pytest.raises(PromotionBlocked):
        assert_promotable(
            present_values={"revenue": "100"},
            period=ClaimedPeriod(as_of="nope", kind="annual"), **_prov(),
        )
