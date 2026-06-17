"""Step 3 — the deterministic stub LLM adapter. No network, no DB.

Each mode must produce exactly the intended single failure (or none), so the
verification branches stay separable.
"""

from __future__ import annotations

import datetime as dt

from research_platform.domain.models import (
    ClaimStatus,
    ClaimType,
    OverallStatus,
    Snapshot,
    SnapshotKind,
)
from research_platform.domain.ports.llm import LLMPort
from research_platform.domain.report_spec import build_schema_spec
from research_platform.domain.verification import verify_draft
from research_platform.ingestion.llm.stub_adapter import StubLLMAdapter, StubMode

INPUTS = {
    "revenue": 1500.0,
    "prev_revenue": 1200.0,
    "ebit": 360.0,
    "net_income": 240.0,
    "total_assets": 2000.0,
    "current_liabilities": 500.0,
    "total_debt": 300.0,
    "equity": 1000.0,
}


def _snapshot() -> Snapshot:
    return Snapshot(
        id=99, stock_id=5, as_of=dt.date(2026, 3, 31), kind=SnapshotKind.annual,
        inputs=INPUTS, code_version="t", content_hash="h",
    )


def _draft(mode: StubMode):
    snap = _snapshot()
    return StubLLMAdapter(mode).generate_report_draft(snap.inputs, build_schema_spec(snap)), snap


def _counts(result):
    failed = sum(c.status is ClaimStatus.FAILED for c in result.claims)
    flagged = sum(c.status is ClaimStatus.FLAGGED for c in result.claims)
    return failed, flagged


def test_stub_satisfies_llm_port() -> None:
    assert isinstance(StubLLMAdapter(), LLMPort)
    assert StubLLMAdapter().model_name == "stub-llm-v1"


def test_stub_is_deterministic() -> None:
    snap = _snapshot()
    spec = build_schema_spec(snap)
    a = StubLLMAdapter(StubMode.GOOD).generate_report_draft(snap.inputs, spec)
    b = StubLLMAdapter(StubMode.GOOD).generate_report_draft(snap.inputs, spec)
    assert a == b


def test_good_mode_passes_cleanly() -> None:
    draft, snap = _draft(StubMode.GOOD)
    result = verify_draft(draft, snap)
    assert result.overall is OverallStatus.PASSED
    assert _counts(result) == (0, 0)
    # there really are NUMERIC claims being verified (not a vacuous pass)
    assert any(c.claim_type is ClaimType.NUMERIC for c in draft.claims)


def test_bad_numeric_mode_hard_fails_exactly_once() -> None:
    draft, snap = _draft(StubMode.BAD_NUMERIC)
    result = verify_draft(draft, snap)
    assert result.overall is OverallStatus.HARD_FAILED
    assert not result.is_storable
    failed, flagged = _counts(result)
    assert failed == 1 and flagged == 0  # exactly one defect, numeric only
    assert result.missing_sections == []


def test_bad_citation_mode_flags_not_fails() -> None:
    draft, snap = _draft(StubMode.BAD_CITATION)
    result = verify_draft(draft, snap)
    assert result.overall is OverallStatus.PASSED_WITH_FLAGS
    assert result.is_storable
    failed, flagged = _counts(result)
    assert failed == 0 and flagged == 1  # correct number, dangling citation only
    # the flagged claim is the NUMERIC one whose value was still verified
    flagged_claim = next(c for c in result.claims if c.status is ClaimStatus.FLAGGED)
    assert flagged_claim.claim_type is ClaimType.NUMERIC
    assert flagged_claim.computed_value is not None


def test_incomplete_mode_is_incomplete_only() -> None:
    draft, snap = _draft(StubMode.INCOMPLETE)
    result = verify_draft(draft, snap)
    assert result.overall is OverallStatus.INCOMPLETE
    assert result.is_storable
    assert result.missing_sections == ["risks"]
    assert _counts(result) == (0, 0)  # no numeric/citation defects
