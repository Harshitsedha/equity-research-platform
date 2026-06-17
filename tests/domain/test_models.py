"""Domain model tests — no DB, no network."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from research_platform.domain.models import (
    ParsedDocument,
    Snapshot,
    SnapshotKind,
    Stock,
    ValuationRun,
)


def test_stock_defaults() -> None:
    s = Stock(ticker="INFY", name="Infosys Ltd")
    assert s.exchange == "NSE"
    assert s.profile == {}
    assert s.id is None


def test_snapshot_is_frozen() -> None:
    snap = Snapshot(
        stock_id=1,
        as_of=dt.date(2026, 3, 31),
        kind=SnapshotKind.annual,
        inputs={"revenue": 100},
        code_version="0.1.0",
        content_hash="abc",
    )
    with pytest.raises(ValidationError):
        snap.inputs = {"revenue": 200}  # type: ignore[misc]


def test_valuation_run_is_frozen() -> None:
    run = ValuationRun(
        stock_id=1,
        snapshot_id=1,
        model_name="dcf",
        assumptions={},
        result={},
        code_version="0.1.0",
    )
    with pytest.raises(ValidationError):
        run.result = {"value_per_share": 1}  # type: ignore[misc]


def test_parsed_document_round_trip() -> None:
    doc = ParsedDocument(
        ticker="INFY",
        name="Infosys Ltd",
        as_of=dt.date(2026, 3, 31),
        kind=SnapshotKind.annual,
        inputs={"revenue": 100},
    )
    assert doc.kind is SnapshotKind.annual
    assert doc.exchange == "NSE"
