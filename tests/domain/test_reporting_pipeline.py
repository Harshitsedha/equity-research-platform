"""Step 4 — report assembly + pipeline policy. No network, no DB (in-memory repo).

Proves both policy branches at the pipeline level: storable results are persisted;
a HARD_FAILED result is NOT persisted.
"""

from __future__ import annotations

import datetime as dt

from research_platform.domain.models import OverallStatus, Snapshot, SnapshotKind
from research_platform.domain.reporting import run_report_pipeline
from research_platform.ingestion.llm.stub_adapter import StubLLMAdapter, StubMode
from tests.support.fakes import InMemoryRepository

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


def _setup(mode: StubMode):
    repo = InMemoryRepository()
    snap = repo.save_snapshot(
        Snapshot(
            stock_id=1, as_of=dt.date(2026, 3, 31), kind=SnapshotKind.annual,
            inputs=INPUTS, code_version="t", content_hash="h",
        )
    )
    result = run_report_pipeline(snap, llm=StubLLMAdapter(mode), repo=repo, kind="initiation")
    return result, repo, snap


def test_good_is_assembled_and_stored() -> None:
    result, repo, snap = _setup(StubMode.GOOD)
    assert result.stored and result.report is not None
    assert result.verification.overall is OverallStatus.PASSED
    stored = repo.get_report_by_snapshot(snap.id)
    assert stored is not None
    # prose rendered from claims; verification recorded on the row
    assert stored.content["sections"]["financials"]["prose"]
    assert stored.verification["overall"] == "PASSED"


def test_bad_numeric_is_not_stored() -> None:
    result, repo, snap = _setup(StubMode.BAD_NUMERIC)
    assert result.verification.overall is OverallStatus.HARD_FAILED
    assert result.stored is False and result.report is None
    assert repo.get_report_by_snapshot(snap.id) is None  # ABSENT
    assert repo.reports == {}


def test_bad_citation_is_stored_with_flags() -> None:
    result, repo, snap = _setup(StubMode.BAD_CITATION)
    assert result.stored and result.report is not None
    assert result.verification.overall is OverallStatus.PASSED_WITH_FLAGS
    stored = repo.get_report_by_snapshot(snap.id)
    # the flagged claim is annotated in the rendered prose
    assert "[UNVERIFIED CITATION]" in stored.content["sections"]["financials"]["prose"]


def test_incomplete_is_stored_with_missing_section() -> None:
    result, repo, snap = _setup(StubMode.INCOMPLETE)
    assert result.stored and result.report is not None
    assert result.verification.overall is OverallStatus.INCOMPLETE
    assert result.verification.missing_sections == ["risks"]


def test_pipeline_is_idempotent_by_snapshot() -> None:
    repo = InMemoryRepository()
    snap = repo.save_snapshot(
        Snapshot(stock_id=1, as_of=dt.date(2026, 3, 31), kind=SnapshotKind.annual,
                 inputs=INPUTS, code_version="t", content_hash="h")
    )
    first = run_report_pipeline(snap, llm=StubLLMAdapter(StubMode.GOOD), repo=repo)
    second = run_report_pipeline(snap, llm=StubLLMAdapter(StubMode.GOOD), repo=repo)
    assert second.idempotent is True
    assert second.report.id == first.report.id
    assert len(repo.reports) == 1  # not duplicated


def test_report_content_is_reproducible() -> None:
    """Same snapshot + same stub -> identical content & verification."""
    r1, _, _ = _setup(StubMode.GOOD)
    r2, _, _ = _setup(StubMode.GOOD)
    assert r1.report.content == r2.report.content
    assert r1.report.verification == r2.report.verification
