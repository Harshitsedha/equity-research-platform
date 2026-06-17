"""Step 1 — claim schema, metrics, schema spec, LLMPort. No network, no DB."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from research_platform.domain.metrics import (
    NUMERIC_METRIC_KEYS,
    MetricError,
    compute_metric,
)
from research_platform.domain.models import (
    Claim,
    ClaimType,
    Snapshot,
    SnapshotKind,
    StructuredReportDraft,
)
from research_platform.domain.report_spec import REQUIRED_SECTIONS, build_schema_spec

RICH_INPUTS = {
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
        id=42,
        stock_id=7,
        as_of=dt.date(2026, 3, 31),
        kind=SnapshotKind.annual,
        inputs=RICH_INPUTS,
        code_version="test",
        content_hash="deadbeef",
    )


# --- claim schema ----------------------------------------------------------
def test_claim_is_frozen() -> None:
    c = Claim(id="c1", claim_type=ClaimType.NUMERIC, statement="x", section="financials")
    with pytest.raises(ValidationError):
        c.statement = "y"  # type: ignore[misc]


def test_draft_holds_claims() -> None:
    draft = StructuredReportDraft(
        stock_id=7,
        snapshot_id=42,
        claims=[Claim(id="c1", claim_type=ClaimType.QUALITATIVE, statement="ok", section="risks")],
        section_order=["risks"],
    )
    assert draft.claims[0].claim_type is ClaimType.QUALITATIVE


# --- metrics ---------------------------------------------------------------
def test_metrics_are_correct_and_deterministic() -> None:
    assert compute_metric("ebit_margin", RICH_INPUTS) == 0.24
    assert compute_metric("net_margin", RICH_INPUTS) == 0.16
    assert compute_metric("roe", RICH_INPUTS) == 0.24
    assert compute_metric("debt_equity", RICH_INPUTS) == 0.3
    assert compute_metric("revenue_growth", RICH_INPUTS) == 0.25
    # roce = ebit / (assets - current_liabilities) = 360 / 1500
    assert compute_metric("roce", RICH_INPUTS) == 0.24
    # determinism
    assert compute_metric("roce", RICH_INPUTS) == compute_metric("roce", RICH_INPUTS)


def test_metric_unknown_raises() -> None:
    with pytest.raises(MetricError, match="unknown metric"):
        compute_metric("sharpe_ratio", RICH_INPUTS)


def test_metric_missing_input_raises() -> None:
    with pytest.raises(MetricError, match="missing input"):
        compute_metric("roe", {"net_income": 1.0})


def test_metric_division_by_zero_raises() -> None:
    with pytest.raises(MetricError, match="division by zero"):
        compute_metric("roe", {"net_income": 1.0, "equity": 0.0})


# --- schema spec -----------------------------------------------------------
def test_required_sections_are_the_core_checklist() -> None:
    assert REQUIRED_SECTIONS == ("business", "financials", "valuation", "risks")


def test_build_schema_spec_advertises_identity_and_metrics() -> None:
    spec = build_schema_spec(_snapshot())
    assert spec["stock_id"] == 7
    assert spec["snapshot_id"] == 42
    assert spec["required_sections"] == list(REQUIRED_SECTIONS)
    advertised = {m["metric_key"] for m in spec["numeric_metrics"]}
    assert advertised == set(NUMERIC_METRIC_KEYS)
    # every advertised metric carries its input dependencies
    for m in spec["numeric_metrics"]:
        assert m["requires"]
