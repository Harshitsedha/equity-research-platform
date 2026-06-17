"""Ledger tests — proving the content hash and freeze are pure & deterministic."""

from __future__ import annotations

import datetime as dt

from research_platform.domain.ledger import (
    compute_content_hash,
    freeze_snapshot,
)
from research_platform.domain.models import SnapshotKind
from research_platform.domain.version import CODE_VERSION


def test_content_hash_is_deterministic_and_order_independent() -> None:
    a = {"revenue": 100, "margin": 0.2, "tax": 0.25}
    b = {"tax": 0.25, "margin": 0.2, "revenue": 100}  # same data, different order
    assert compute_content_hash(a) == compute_content_hash(b)


def test_content_hash_changes_with_inputs() -> None:
    assert compute_content_hash({"revenue": 100}) != compute_content_hash(
        {"revenue": 101}
    )


def test_freeze_stamps_version_and_hash() -> None:
    inputs = {"revenue": 1500.0, "ebit_margin": 0.24}
    snap = freeze_snapshot(
        stock_id=7,
        as_of=dt.date(2026, 3, 31),
        kind=SnapshotKind.annual,
        inputs=inputs,
        source_versions={"manual_upload": "1.0"},
    )
    assert snap.stock_id == 7
    assert snap.code_version == CODE_VERSION
    assert snap.content_hash == compute_content_hash(inputs)
    assert snap.source_versions == {"manual_upload": "1.0"}
    # purity: no wall-clock stamped by the domain
    assert snap.created_at is None
    assert snap.id is None


def test_freeze_is_reproducible() -> None:
    inputs = {"revenue": 1500.0, "ebit_margin": 0.24}
    kw = dict(
        stock_id=7,
        as_of=dt.date(2026, 3, 31),
        kind=SnapshotKind.annual,
        inputs=inputs,
    )
    assert freeze_snapshot(**kw) == freeze_snapshot(**kw)
