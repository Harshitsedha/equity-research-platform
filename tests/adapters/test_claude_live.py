"""B3 — opt-in LIVE Anthropic integration. Skipped by default.

Runs ONE real Claude call and asserts the result flows through the verification
harness. Marked ``live`` (deselected by default via pyproject addopts) and also
guarded on ANTHROPIC_API_KEY, so the standard test run needs neither network nor
a key. Run explicitly with:  uv run pytest -m live

.env is loaded lazily INSIDE the test (not at import) so the default — deselected —
collection of this module never pulls a real key into the test session.
"""

from __future__ import annotations

import datetime as dt
import os

import pytest

from research_platform.domain.models import Snapshot, SnapshotKind, VerificationResult
from research_platform.domain.report_spec import build_schema_spec
from research_platform.domain.verification import verify_draft
from research_platform.ingestion.llm.claude_adapter import ClaudeLLMAdapter

pytestmark = pytest.mark.live

INPUTS = {
    "revenue": 1500.0, "prev_revenue": 1200.0, "ebit": 360.0, "net_income": 240.0,
    "total_assets": 2000.0, "current_liabilities": 500.0, "total_debt": 300.0,
    "equity": 1000.0,
}


def test_live_claude_draft_flows_through_harness() -> None:
    from dotenv import load_dotenv

    load_dotenv()  # entry-point load, only when this opt-in test actually runs
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — live Claude test skipped")

    snap = Snapshot(
        id=1, stock_id=1, as_of=dt.date(2026, 3, 31), kind=SnapshotKind.annual,
        inputs=INPUTS, code_version="live", content_hash="h",
    )
    adapter = ClaudeLLMAdapter()  # real API client
    draft = adapter.generate_report_draft(snap.inputs, build_schema_spec(snap))

    assert draft.claims, "model returned no claims"
    result = verify_draft(draft, snap)
    # The harness ran over real model output and produced a structured verdict.
    assert isinstance(result, VerificationResult)
    assert len(result.claims) == len(draft.claims)
