"""B3 — the swap proof. No network.

The Claude adapter (fed fixture output) flows through the SAME run_report_pipeline
+ verification harness + repository as the stub, with identical outcomes. This
demonstrates the LLMPort abstraction held and the domain/harness/pipeline/storage
were untouched by Part B.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from research_platform.domain.models import OverallStatus, Snapshot, SnapshotKind
from research_platform.domain.reporting import run_report_pipeline
from research_platform.ingestion.llm.claude_adapter import ClaudeLLMAdapter
from tests.adapters.test_claude_adapter import GOOD_JSON
from tests.support.fakes import InMemoryRepository

INPUTS = {
    "revenue": 1500.0, "prev_revenue": 1200.0, "ebit": 360.0, "net_income": 240.0,
    "total_assets": 2000.0, "current_liabilities": 500.0, "total_debt": 300.0,
    "equity": 1000.0,
}

# Same as GOOD_JSON but with a deliberately wrong numeric value.
BAD_NUMERIC_JSON = json.dumps(
    {
        "claims": [
            {"id": "b1", "claim_type": "FACTUAL", "statement": "Revenue is 1500.",
             "section": "business", "metric_key": None, "asserted_value": None, "citation": "revenue"},
            {"id": "f1", "claim_type": "NUMERIC", "statement": "EBIT margin is 0.99.",
             "section": "financials", "metric_key": "ebit_margin", "asserted_value": 0.99, "citation": "ebit"},
            {"id": "v1", "claim_type": "QUALITATIVE", "statement": "Valued via DCF.",
             "section": "valuation", "metric_key": None, "asserted_value": None, "citation": None},
            {"id": "r1", "claim_type": "QUALITATIVE", "statement": "Risks: demand.",
             "section": "risks", "metric_key": None, "asserted_value": None, "citation": None},
        ],
        "section_order": ["business", "financials", "valuation", "risks"],
    }
)


def _setup(raw_json: str):
    repo = InMemoryRepository()
    snap = repo.save_snapshot(
        Snapshot(stock_id=1, as_of=dt.date(2026, 3, 31), kind=SnapshotKind.annual,
                 inputs=INPUTS, code_version="t", content_hash="h")
    )
    llm = ClaudeLLMAdapter(complete_fn=lambda _i, _s: raw_json)
    return run_report_pipeline(snap, llm=llm, repo=repo, kind="initiation"), repo, snap


def test_claude_good_output_flows_through_pipeline_and_stores() -> None:
    result, repo, snap = _setup(GOOD_JSON)
    assert result.stored and result.report is not None
    assert result.verification.overall is OverallStatus.PASSED
    stored = repo.get_report_by_snapshot(snap.id)
    assert stored.content["sections"]["financials"]["prose"]


def test_claude_bad_numeric_is_hard_failed_and_not_stored() -> None:
    result, repo, snap = _setup(BAD_NUMERIC_JSON)
    assert result.verification.overall is OverallStatus.HARD_FAILED
    assert result.stored is False and result.report is None
    assert repo.get_report_by_snapshot(snap.id) is None  # absent — same as the stub
