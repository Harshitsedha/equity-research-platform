"""Provenance reproducibility (ADR-011). Requires a live PostgreSQL; no live API.

A Claude-fixture-generated report persists correct model_version + prompt_version +
code_version, is queryable by them, and re-derives identically. A stub-generated
report carries NULL model/prompt versions but a valid code_version.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from research_platform.domain.models import OverallStatus, Snapshot, SnapshotKind, Stock
from research_platform.domain.report_spec import build_schema_spec
from research_platform.domain.reporting import assemble_report, run_report_pipeline
from research_platform.domain.verification import verify_draft
from research_platform.domain.version import CODE_VERSION
from research_platform.ingestion.llm.claude_adapter import (
    ClaudeLLMAdapter,
    parse_claude_draft,
)
from research_platform.ingestion.llm.stub_adapter import StubLLMAdapter
from research_platform.storage import models as orm
from tests.adapters.test_claude_adapter import GOOD_JSON
from tests.support.isins import synthetic_isin

pytestmark = pytest.mark.db

INPUTS = {
    "revenue": 1500.0, "prev_revenue": 1200.0, "ebit": 360.0, "net_income": 240.0,
    "total_assets": 2000.0, "current_liabilities": 500.0, "total_debt": 300.0,
    "equity": 1000.0,
}


def _snapshot(repository, ticker: str) -> Snapshot:
    stock = repository.save_stock(
        Stock(ticker=ticker, name="Prov Co", isin=synthetic_isin(ticker))
    )
    return repository.save_snapshot(
        Snapshot(stock_id=stock.id, as_of=dt.date(2026, 3, 31), kind=SnapshotKind.annual,
                 inputs=INPUTS, code_version=CODE_VERSION, content_hash="h")
    )


def test_claude_report_persists_full_provenance(repository, session_factory):
    snap = _snapshot(repository, "PROV_CLAUDE")
    llm = ClaudeLLMAdapter(complete_fn=lambda _i, _s: GOOD_JSON)
    result = run_report_pipeline(snap, llm=llm, repo=repository, kind="initiation")

    assert result.stored and result.verification.overall is OverallStatus.PASSED
    stored = repository.get_report_by_snapshot(snap.id)
    # all three stamps present and correct
    assert stored.model_version == "claude-opus-4-8"
    assert stored.prompt_version == "report-claims-v1"
    assert stored.code_version == CODE_VERSION


def test_report_is_queryable_by_model_version(repository, session_factory):
    snap = _snapshot(repository, "PROV_QUERY")
    llm = ClaudeLLMAdapter(complete_fn=lambda _i, _s: GOOD_JSON)
    run_report_pipeline(snap, llm=llm, repo=repository, kind="initiation")

    with session_factory() as session:
        rows = session.scalars(
            select(orm.Report).where(
                orm.Report.model_version == "claude-opus-4-8",
                orm.Report.prompt_version == "report-claims-v1",
                orm.Report.snapshot_id == snap.id,
            )
        ).all()
    assert len(rows) == 1


def test_claude_report_re_derives_identically_with_provenance(repository):
    snap = _snapshot(repository, "PROV_REDERIVE")
    llm = ClaudeLLMAdapter(complete_fn=lambda _i, _s: GOOD_JSON)
    run_report_pipeline(snap, llm=llm, repo=repository, kind="initiation")
    stored = repository.get_report_by_snapshot(snap.id)

    # Re-derive purely from the stored snapshot + fixture + the same provenance.
    spec = build_schema_spec(snap)
    draft = parse_claude_draft(GOOD_JSON, spec)
    rebuilt = assemble_report(
        draft, verify_draft(draft, snap), snap, kind="initiation",
        model_version=llm.model_version, prompt_version=llm.prompt_version,
    )
    assert rebuilt.content == stored.content
    assert rebuilt.verification == stored.verification
    assert rebuilt.model_version == stored.model_version == "claude-opus-4-8"
    assert rebuilt.prompt_version == stored.prompt_version == "report-claims-v1"


def test_stub_report_has_null_provenance_but_valid_code_version(repository):
    snap = _snapshot(repository, "PROV_STUB")
    result = run_report_pipeline(snap, llm=StubLLMAdapter(), repo=repository, kind="initiation")

    assert result.stored
    stored = repository.get_report_by_snapshot(snap.id)
    assert stored.model_version is None
    assert stored.prompt_version is None
    assert stored.code_version == CODE_VERSION  # code provenance still present
