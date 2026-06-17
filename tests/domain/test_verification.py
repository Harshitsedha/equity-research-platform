"""Step 2 — verification harness + failure policy. No network, no DB.

Drafts are hand-built here (not via the stub) so each check and each policy
branch is exercised in isolation.
"""

from __future__ import annotations

import datetime as dt

from research_platform.domain.models import (
    Claim,
    ClaimStatus,
    ClaimType,
    OverallStatus,
    Snapshot,
    SnapshotKind,
    StructuredReportDraft,
)
from research_platform.domain.verification import citation_exists, verify_draft

INPUTS = {
    "revenue": 1500.0,
    "prev_revenue": 1200.0,
    "ebit": 360.0,
    "net_income": 240.0,
    "total_assets": 2000.0,
    "current_liabilities": 500.0,
    "total_debt": 300.0,
    "equity": 1000.0,
    "segments": {"mdo": {"revenue": 100.0}},  # for dotted-path citation test
}


def _snapshot() -> Snapshot:
    return Snapshot(
        id=1, stock_id=1, as_of=dt.date(2026, 3, 31), kind=SnapshotKind.annual,
        inputs=INPUTS, code_version="t", content_hash="h",
    )


def _claim(**kw) -> Claim:
    base = dict(id="c", claim_type=ClaimType.QUALITATIVE, statement="s", section="business")
    base.update(kw)
    return Claim(**base)


def _full_sections() -> list[Claim]:
    """One trivially-valid claim per required section (qualitative)."""
    return [
        _claim(id="b", section="business"),
        _claim(id="f", section="financials"),
        _claim(id="v", section="valuation"),
        _claim(id="r", section="risks"),
    ]


def _draft(claims: list[Claim]) -> StructuredReportDraft:
    return StructuredReportDraft(stock_id=1, snapshot_id=1, claims=claims)


def _status(result, claim_id: str) -> ClaimStatus:
    return next(c.status for c in result.claims if c.claim_id == claim_id)


# --- (a) NUMERIC verification ----------------------------------------------
def test_numeric_correct_is_verified_and_passes() -> None:
    claims = _full_sections() + [
        Claim(id="n", claim_type=ClaimType.NUMERIC, statement="roce=0.24",
              section="financials", metric_key="roce", asserted_value=0.24,
              citation="ebit"),
    ]
    result = verify_draft(_draft(claims), _snapshot())
    assert _status(result, "n") is ClaimStatus.VERIFIED
    assert result.overall is OverallStatus.PASSED
    assert result.is_storable


def test_numeric_mismatch_is_hard_fail_not_storable() -> None:
    claims = _full_sections() + [
        Claim(id="n", claim_type=ClaimType.NUMERIC, statement="roce=0.99",
              section="financials", metric_key="roce", asserted_value=0.99,
              citation="ebit"),
    ]
    result = verify_draft(_draft(claims), _snapshot())
    assert _status(result, "n") is ClaimStatus.FAILED
    assert result.overall is OverallStatus.HARD_FAILED
    assert result.hard_failed and not result.is_storable
    # the computed value is recorded for audit
    n = next(c for c in result.claims if c.claim_id == "n")
    assert n.computed_value == 0.24


def test_numeric_uncomputable_metric_is_hard_fail() -> None:
    claims = _full_sections() + [
        Claim(id="n", claim_type=ClaimType.NUMERIC, statement="bad",
              section="financials", metric_key="roe",
              asserted_value=0.5, citation="net_income"),
    ]
    snap = Snapshot(id=1, stock_id=1, as_of=dt.date(2026, 3, 31),
                    kind=SnapshotKind.annual, inputs={"net_income": 240.0},  # no equity
                    code_version="t", content_hash="h")
    result = verify_draft(_draft(claims), snap)
    assert _status(result, "n") is ClaimStatus.FAILED
    assert result.overall is OverallStatus.HARD_FAILED


# --- (b) CITATION-existence -------------------------------------------------
def test_factual_with_existing_citation_verified() -> None:
    claims = _full_sections() + [
        Claim(id="x", claim_type=ClaimType.FACTUAL, statement="rev exists",
              section="business", citation="revenue"),
    ]
    result = verify_draft(_draft(claims), _snapshot())
    assert _status(result, "x") is ClaimStatus.VERIFIED
    assert result.overall is OverallStatus.PASSED


def test_factual_with_dangling_citation_flagged_but_stored() -> None:
    claims = _full_sections() + [
        Claim(id="x", claim_type=ClaimType.FACTUAL, statement="bogus",
              section="business", citation="ebitda_does_not_exist"),
    ]
    result = verify_draft(_draft(claims), _snapshot())
    assert _status(result, "x") is ClaimStatus.FLAGGED
    assert result.overall is OverallStatus.PASSED_WITH_FLAGS
    assert result.is_storable  # FLAG-AND-STORE


def test_numeric_correct_but_dangling_citation_is_flagged_not_failed() -> None:
    claims = _full_sections() + [
        Claim(id="n", claim_type=ClaimType.NUMERIC, statement="roce ok bad cite",
              section="financials", metric_key="roce", asserted_value=0.24,
              citation="nope"),
    ]
    result = verify_draft(_draft(claims), _snapshot())
    assert _status(result, "n") is ClaimStatus.FLAGGED  # value right, citation wrong
    assert result.overall is OverallStatus.PASSED_WITH_FLAGS
    assert result.is_storable


def test_citation_exists_supports_dotted_paths() -> None:
    assert citation_exists(INPUTS, "segments.mdo.revenue") is True
    assert citation_exists(INPUTS, "segments.mdo.missing") is False
    assert citation_exists(INPUTS, None) is False


# --- (c) STRUCTURAL completeness -------------------------------------------
def test_missing_section_is_incomplete_but_stored() -> None:
    claims = [c for c in _full_sections() if c.section != "risks"]
    result = verify_draft(_draft(claims), _snapshot())
    assert result.overall is OverallStatus.INCOMPLETE
    assert result.missing_sections == ["risks"]
    assert result.is_storable


def test_qualitative_always_verified() -> None:
    result = verify_draft(_draft(_full_sections()), _snapshot())
    assert all(c.status is ClaimStatus.VERIFIED for c in result.claims)
    assert result.overall is OverallStatus.PASSED


# --- policy precedence ------------------------------------------------------
def test_numeric_failure_dominates_incompleteness() -> None:
    # both a numeric mismatch AND a missing section -> HARD_FAILED wins.
    claims = [c for c in _full_sections() if c.section != "risks"] + [
        Claim(id="n", claim_type=ClaimType.NUMERIC, statement="wrong",
              section="financials", metric_key="roe", asserted_value=99.0,
              citation="equity"),
    ]
    result = verify_draft(_draft(claims), _snapshot())
    assert result.overall is OverallStatus.HARD_FAILED
    assert not result.is_storable


def test_verification_result_stamps_code_version() -> None:
    result = verify_draft(_draft(_full_sections()), _snapshot(), code_version="abc123")
    assert result.code_version == "abc123"
