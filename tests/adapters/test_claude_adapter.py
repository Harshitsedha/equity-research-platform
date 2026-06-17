"""B3 — Claude adapter parsing/validation. No network, no API key.

Exercises the pure parse function with fixture payloads (good + malformed) and
the construction-time config guard. The live API path is covered only by the
opt-in test in test_claude_live.py.
"""

from __future__ import annotations

import pytest

from research_platform.domain.models import ClaimType, StructuredReportDraft
from research_platform.domain.ports.llm import LLMPort
from research_platform.ingestion.llm.claude_adapter import (
    ClaudeConfigError,
    ClaudeLLMAdapter,
    ClaudeParseError,
    parse_claude_draft,
)

SPEC = {
    "stock_id": 7,
    "snapshot_id": 42,
    "required_sections": ["business", "financials", "valuation", "risks"],
    "section_order": ["business", "financials", "valuation", "risks"],
    "numeric_metrics": [{"metric_key": "ebit_margin", "requires": ["ebit", "revenue"]}],
}

# A well-formed model response (numbers correct for the standard rich inputs).
GOOD_JSON = """
{
  "claims": [
    {"id": "b1", "claim_type": "FACTUAL", "statement": "Revenue is 1500.",
     "section": "business", "metric_key": null, "asserted_value": null, "citation": "revenue"},
    {"id": "f1", "claim_type": "NUMERIC", "statement": "EBIT margin is 0.24.",
     "section": "financials", "metric_key": "ebit_margin", "asserted_value": 0.24, "citation": "ebit"},
    {"id": "v1", "claim_type": "QUALITATIVE", "statement": "Valued via DCF.",
     "section": "valuation", "metric_key": null, "asserted_value": null, "citation": null},
    {"id": "r1", "claim_type": "QUALITATIVE", "statement": "Risks: demand.",
     "section": "risks", "metric_key": null, "asserted_value": null, "citation": null}
  ],
  "section_order": ["business", "financials", "valuation", "risks"]
}
"""


def test_parse_good_payload() -> None:
    draft = parse_claude_draft(GOOD_JSON, SPEC)
    assert isinstance(draft, StructuredReportDraft)
    assert draft.stock_id == 7 and draft.snapshot_id == 42  # identity from spec
    assert len(draft.claims) == 4
    numeric = next(c for c in draft.claims if c.claim_type is ClaimType.NUMERIC)
    assert numeric.metric_key == "ebit_margin" and numeric.asserted_value == 0.24


def test_parse_rejects_malformed_json() -> None:
    with pytest.raises(ClaudeParseError, match="not valid JSON"):
        parse_claude_draft("{ this is not json", SPEC)


def test_parse_rejects_missing_claims() -> None:
    with pytest.raises(ClaudeParseError, match="claims"):
        parse_claude_draft('{"section_order": []}', SPEC)


def test_parse_rejects_invalid_claim_type() -> None:
    bad = '{"claims": [{"id": "x", "claim_type": "BOGUS", "statement": "s", "section": "a"}]}'
    with pytest.raises(ClaudeParseError, match="failed validation"):
        parse_claude_draft(bad, SPEC)


def test_parse_rejects_non_object_top_level() -> None:
    with pytest.raises(ClaudeParseError, match="JSON object"):
        parse_claude_draft("[1, 2, 3]", SPEC)


def test_parse_requires_identity_in_spec() -> None:
    with pytest.raises(ClaudeParseError, match="stock/snapshot id"):
        parse_claude_draft(GOOD_JSON, {"section_order": []})


def test_construction_without_key_fails_loudly(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ClaudeConfigError, match="ANTHROPIC_API_KEY"):
        ClaudeLLMAdapter()


def test_injected_adapter_needs_no_key_and_satisfies_port() -> None:
    adapter = ClaudeLLMAdapter(complete_fn=lambda _inputs, _spec: GOOD_JSON)
    assert isinstance(adapter, LLMPort)
    assert adapter.model_name == "claude:claude-opus-4-8:report-claims-v1"
    assert adapter.model_version == "claude-opus-4-8"
    assert adapter.prompt_version == "report-claims-v1"


def test_generate_report_draft_uses_complete_fn() -> None:
    adapter = ClaudeLLMAdapter(complete_fn=lambda _inputs, _spec: GOOD_JSON)
    draft = adapter.generate_report_draft({"revenue": 1500.0}, SPEC)
    assert len(draft.claims) == 4
