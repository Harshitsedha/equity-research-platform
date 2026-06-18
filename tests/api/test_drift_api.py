"""Wire-level tests for the thesis-drift surfacing endpoint (read-only slice).

DB-free: the composition-root dependencies are overridden with in-memory fakes,
so the route is exercised end-to-end through ``TestClient`` and every honest-state
distinction is asserted on the SERIALIZED JSON body (not just the DTO object) —
that is the whole point of the slice. No drift is stored; the endpoint recomputes
on every call and must send ``Cache-Control: no-store``.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from research_platform.api.app import create_app
from research_platform.api.dependencies import (
    get_snapshot_repository,
    get_stock_repository,
)
from research_platform.domain.models import Snapshot, SnapshotKind
from research_platform.domain.stock import Stock
from research_platform.domain.thesis import ThesisAssumption, ToleranceBand
from tests.support.isins import synthetic_isin

ISIN = synthetic_isin("drift-api")

# Current-snapshot inputs chosen to exercise every status/reason in one call:
#   revenue=1500   -> CROSSED below band [2000,3000]
#   ebit_margin    -> 360/1500 = 0.24 -> IN_BAND [0.20,0.30]
#   roe            -> needs equity (absent) -> MISSING_INPUT
#   gone           -> KEY_ABSENT
#   null_field     -> present but null -> VALUE_ABSENT
CURRENT_INPUTS = {"revenue": 1500.0, "ebit": 360.0, "net_income": 240.0, "null_field": None}

ANCHOR_SNAP_ID = 1
CURRENT_SNAP_ID = 2


def _assumptions() -> list[ThesisAssumption]:
    return [
        ThesisAssumption(name="rev", metric_key="revenue", recorded_value=1000.0,
                         band=ToleranceBand(lower=2000.0, upper=3000.0)),
        ThesisAssumption(name="margin", metric_key="ebit_margin", recorded_value=0.24,
                         band=ToleranceBand(lower=0.20, upper=0.30)),
        ThesisAssumption(name="roe", metric_key="roe", recorded_value=0.20,
                         band=ToleranceBand(lower=0.10, upper=None)),
        ThesisAssumption(name="missing", metric_key="gone", recorded_value=1.0,
                         band=ToleranceBand(lower=0.0, upper=2.0)),
        ThesisAssumption(name="null", metric_key="null_field", recorded_value=1.0,
                         band=ToleranceBand(lower=0.0, upper=2.0)),
    ]


class _FakeStockRepo:
    """Minimal StockRepository: only get_by_isin is exercised by the route."""

    def __init__(self, stock: Stock | None) -> None:
        self._stock = stock

    def get_by_isin(self, isin: str) -> Stock | None:
        if self._stock is not None and isin == self._stock.isin:
            return self._stock
        return None

    def get_by_id(self, stock_id):  # pragma: no cover - unused by the route
        return None

    def list(self):  # pragma: no cover - unused by the route
        return [self._stock] if self._stock else []

    def save(self, stock):  # pragma: no cover - read-only route
        raise AssertionError("the drift route must never write")


class _FakeSnapshotRepo:
    """Minimal RepositoryPort surface: only get_snapshot is exercised."""

    def __init__(self, snapshots: dict[int, Snapshot]) -> None:
        self._snapshots = snapshots

    def get_snapshot(self, snapshot_id: int) -> Snapshot | None:
        return self._snapshots.get(snapshot_id)


def _snapshot(snap_id: int, inputs: dict) -> Snapshot:
    return Snapshot(id=snap_id, stock_id=1, as_of=dt.date(2026, 3, 31),
                    kind=SnapshotKind.annual, inputs=inputs, code_version="t",
                    content_hash="h")


def _stock_with_thesis(
    *, analyst_target: float | None, assumptions: list[ThesisAssumption] | None = None
) -> Stock:
    """A stock owning an anchor (id=1) + a newer current (id=2) snapshot, with a thesis."""
    stock = Stock(isin=ISIN, ticker="DFT", name="Drift Co")
    stock.add_snapshot_ref(ANCHOR_SNAP_ID, dt.date(2024, 3, 31))
    stock.add_snapshot_ref(CURRENT_SNAP_ID, dt.date(2026, 3, 31))  # latest -> "now"
    stock.record_thesis(
        anchor_valuation_run_id=7,
        anchor_snapshot_id=ANCHOR_SNAP_ID,
        anchor_value_per_share=100.0,
        recorded_at=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
        analyst_target=analyst_target,
        assumptions=assumptions if assumptions is not None else _assumptions(),
    )
    return stock


def _client(stock: Stock | None, snapshots: dict[int, Snapshot] | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_stock_repository] = lambda: _FakeStockRepo(stock)
    app.dependency_overrides[get_snapshot_repository] = lambda: _FakeSnapshotRepo(
        snapshots or {}
    )
    return TestClient(app)


def _drift_by_name(body: dict, name: str) -> dict:
    return next(d for d in body["drift"]["assumption_drifts"] if d["name"] == name)


# --- the happy path: every honest state, asserted on the JSON ---------------
@pytest.fixture
def full_body() -> dict:
    stock = _stock_with_thesis(analyst_target=125.0)
    client = _client(stock, {CURRENT_SNAP_ID: _snapshot(CURRENT_SNAP_ID, CURRENT_INPUTS)})
    resp = client.get(f"/stocks/{ISIN}/drift")
    assert resp.status_code == 200
    return resp.json()


def test_cache_control_no_store() -> None:
    stock = _stock_with_thesis(analyst_target=125.0)
    client = _client(stock, {CURRENT_SNAP_ID: _snapshot(CURRENT_SNAP_ID, CURRENT_INPUTS)})
    resp = client.get(f"/stocks/{ISIN}/drift")
    assert resp.headers["cache-control"] == "no-store"


def test_envelope_identifies_both_interval_ends(full_body) -> None:
    assert full_body["thesis_present"] is True
    assert full_body["thesis_id"] is not None
    assert full_body["thesis_recorded_at"] is not None
    assert full_body["anchor_snapshot"] == {"snapshot_id": ANCHOR_SNAP_ID, "as_of": "2024-03-31"}
    assert full_body["current_snapshot"] == {"snapshot_id": CURRENT_SNAP_ID, "as_of": "2026-03-31"}


def test_crossed_row_has_breach_not_distance(full_body) -> None:
    rev = _drift_by_name(full_body, "rev")
    assert rev["status"] == "CROSSED"
    assert rev["crossed_side"] == "below"
    assert rev["breach_magnitude"] == pytest.approx(500.0)   # 2000 - 1500
    assert rev["distance_to_nearest_bound"] is None          # never merged
    assert rev["unresolved_reason"] is None


def test_in_band_row_has_distance_not_breach(full_body) -> None:
    margin = _drift_by_name(full_body, "margin")
    assert margin["status"] == "IN_BAND"
    assert margin["breach_magnitude"] is None
    assert margin["distance_to_nearest_bound"] == pytest.approx(0.04)  # min(0.04, 0.06)
    assert margin["crossed_side"] is None


def test_each_unresolved_reason_appears_as_its_string(full_body) -> None:
    assert _drift_by_name(full_body, "roe")["unresolved_reason"] == "MISSING_INPUT"
    assert _drift_by_name(full_body, "missing")["unresolved_reason"] == "KEY_ABSENT"
    assert _drift_by_name(full_body, "null")["unresolved_reason"] == "VALUE_ABSENT"
    for nm in ("roe", "missing", "null"):
        d = _drift_by_name(full_body, nm)
        assert d["status"] == "UNRESOLVED"
        assert d["resolved_value"] is None
        assert d["breach_magnitude"] is None
        assert d["distance_to_nearest_bound"] is None


def test_resolved_minus_recorded_only_under_observation(full_body) -> None:
    rev = _drift_by_name(full_body, "rev")
    # nested under observation, never a top-level signal field
    assert "resolved_minus_recorded" not in rev
    assert rev["observation"]["resolved_minus_recorded"] == pytest.approx(500.0)  # 1500 - 1000
    # an unresolved row has no delta to report
    assert _drift_by_name(full_body, "missing")["observation"]["resolved_minus_recorded"] is None


# --- the ABSENT gap must not collapse into a zero ---------------------------
def test_gap_present_serializes_value() -> None:
    stock = _stock_with_thesis(analyst_target=125.0, assumptions=[])
    client = _client(stock, {CURRENT_SNAP_ID: _snapshot(CURRENT_SNAP_ID, {})})
    gap = client.get(f"/stocks/{ISIN}/drift").json()["drift"]["model_judgment_gap"]
    assert gap["present"] is True
    assert gap["gap"] == pytest.approx(25.0)  # 125 - 100


def test_gap_absent_is_distinct_from_zero_gap() -> None:
    # present=false (no target) MUST serialize distinctly from present=true, gap=0.0.
    absent_stock = _stock_with_thesis(analyst_target=None, assumptions=[])
    absent = _client(absent_stock, {CURRENT_SNAP_ID: _snapshot(CURRENT_SNAP_ID, {})}) \
        .get(f"/stocks/{ISIN}/drift").json()["drift"]["model_judgment_gap"]

    zero_stock = _stock_with_thesis(analyst_target=100.0, assumptions=[])  # == anchor
    zero = _client(zero_stock, {CURRENT_SNAP_ID: _snapshot(CURRENT_SNAP_ID, {})}) \
        .get(f"/stocks/{ISIN}/drift").json()["drift"]["model_judgment_gap"]

    assert absent["present"] is False and absent["gap"] is None
    assert zero["present"] is True and zero["gap"] == pytest.approx(0.0)
    assert absent != zero  # the two states are not conflated on the wire


# --- degenerate states: all 200 except unknown ISIN -------------------------
def test_unknown_isin_is_404() -> None:
    resp = _client(None).get("/stocks/NONEXISTENT/drift")
    assert resp.status_code == 404


def test_no_active_thesis_is_200_with_all_null() -> None:
    stock = Stock(isin=ISIN, ticker="DFT", name="Drift Co")
    stock.add_snapshot_ref(CURRENT_SNAP_ID, dt.date(2026, 3, 31))
    resp = _client(stock, {CURRENT_SNAP_ID: _snapshot(CURRENT_SNAP_ID, CURRENT_INPUTS)}) \
        .get(f"/stocks/{ISIN}/drift")
    assert resp.status_code == 200
    body = resp.json()
    assert body["thesis_present"] is False
    for field in ("thesis_id", "thesis_recorded_at", "anchor_snapshot", "current_snapshot", "drift"):
        assert body[field] is None


def test_thesis_but_no_current_snapshot_is_200_drift_null() -> None:
    # Thesis exists, but the current snapshot leaf is absent from the ledger
    # (defensive get_snapshot -> None). thesis_present stays true; drift is null.
    stock = _stock_with_thesis(analyst_target=125.0)
    resp = _client(stock, snapshots={}).get(f"/stocks/{ISIN}/drift")  # no snapshot leaves
    assert resp.status_code == 200
    body = resp.json()
    assert body["thesis_present"] is True
    assert body["thesis_id"] is not None
    assert body["anchor_snapshot"] == {"snapshot_id": ANCHOR_SNAP_ID, "as_of": "2024-03-31"}
    assert body["current_snapshot"] is None  # distinct from "no thesis"
    assert body["drift"] is None


def test_empty_inputs_yields_full_drift_all_unresolved() -> None:
    stock = _stock_with_thesis(analyst_target=125.0)
    resp = _client(stock, {CURRENT_SNAP_ID: _snapshot(CURRENT_SNAP_ID, {})}) \
        .get(f"/stocks/{ISIN}/drift")
    assert resp.status_code == 200
    drifts = resp.json()["drift"]["assumption_drifts"]
    assert len(drifts) == 5
    assert all(d["status"] == "UNRESOLVED" for d in drifts)


# --- HARD RULE 2: the new route is GET-only ---------------------------------
def test_drift_route_is_get_only() -> None:
    paths = create_app().openapi()["paths"]
    drift_path = "/stocks/{isin}/drift"
    assert drift_path in paths
    assert "get" in paths[drift_path]
    assert not (set(paths[drift_path]) & {"post", "put", "patch", "delete"})
