"""API tests for the read-only Stock endpoints (Phase 2b-api).

DB-backed tests drive the real FastAPI app via ``TestClient``, with the
composition-root dependencies overridden to test-DB-bound repositories (so the
cached default Platform / real connection is never built in tests).

Seeding (Amendment 1 — ONE row per isin): the stock is created exactly ONCE via
the legacy ledger path (int id + server uuid). The aggregate is then LOADED by
isin — carrying that row's uuid — before any status transition, so
``SqlStockRepository.save`` (which reconciles by uuid) UPDATES that same row
rather than inserting a second. Ledger snapshots and status history therefore
both hang off a single identity. The ``test_detail_*`` test asserts the loaded
timeline actually contains the seeded snapshot id.
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
from research_platform.domain.ledger import freeze_snapshot
from research_platform.domain.models import SnapshotKind
from research_platform.domain.models import Stock as LegacyStock
from research_platform.domain.stock import CoverageStatus
from research_platform.storage.repository import PostgresRepository
from research_platform.storage.stock_repository import SqlStockRepository
from tests.support.isins import synthetic_isin

INPUTS = {"revenue": 100.0, "ebit": 25.0}


# --- no-DB: read-only route surface ----------------------------------------
def test_route_table_is_get_only() -> None:
    """HARD RULE 2: the entire route surface is GET — no write operations exist.

    Asserted via the OpenAPI schema (the canonical, version-stable view of the
    registered operations).
    """
    paths = create_app().openapi()["paths"]
    write_methods = {"post", "put", "patch", "delete"}
    for path, operations in paths.items():
        assert not (set(operations) & write_methods), f"{path} exposes {operations}"
    # The three read endpoints are present and GET.
    assert "get" in paths["/stocks"]
    assert "get" in paths["/stocks/{isin}"]
    assert "get" in paths["/stocks/{isin}/snapshots/{snapshot_id}"]


def _all_ints(obj: object) -> list[int]:
    """Every integer VALUE in a parsed-JSON structure (bools excluded)."""
    if isinstance(obj, bool):
        return []
    if isinstance(obj, int):
        return [obj]
    if isinstance(obj, dict):
        return [i for v in obj.values() for i in _all_ints(v)]
    if isinstance(obj, list):
        return [i for v in obj for i in _all_ints(v)]
    return []


# --- DB fixtures ------------------------------------------------------------
@pytest.fixture
def repos(session_factory):
    """Test-DB-bound repositories sharing ONE session factory."""
    return (
        SqlStockRepository(session_factory),
        PostgresRepository(session_factory),
    )


@pytest.fixture
def client(repos):
    stock_repo, snapshot_repo = repos
    app = create_app()
    app.dependency_overrides[get_stock_repository] = lambda: stock_repo
    app.dependency_overrides[get_snapshot_repository] = lambda: snapshot_repo
    return TestClient(app)


def _seed(
    repos,
    *,
    isin: str,
    ticker: str,
    name: str = "Seed Co",
    as_ofs: list[dt.date] | None = None,
    status_path: list[tuple[CoverageStatus, str]] | None = None,
) -> tuple[int, list[int]]:
    """Seed ONE stock row + its snapshots + status history. Returns (int_pk, snap_ids).

    The int pk is returned ONLY so tests can assert it never reaches the wire.
    """
    stock_repo, snapshot_repo = repos
    created = snapshot_repo.save_stock(
        LegacyStock(isin=isin, ticker=ticker, name=name)
    )
    int_pk = created.id
    snapshot_ids: list[int] = []
    for as_of in as_ofs or []:
        snap = snapshot_repo.save_snapshot(
            freeze_snapshot(
                stock_id=int_pk,
                as_of=as_of,
                kind=SnapshotKind.annual,
                inputs=INPUTS,
            )
        )
        snapshot_ids.append(snap.id)
    if status_path:
        # LOAD by isin (carries the seeded row's uuid) -> save UPDATES that row.
        agg = stock_repo.get_by_isin(isin)
        for target, reason in status_path:
            agg.transition_to(target, reason)
        stock_repo.save(agg)
    return int_pk, snapshot_ids


# --- GET /stocks (+ ?status filter) ----------------------------------------
@pytest.mark.db
def test_list_and_status_filter(client, repos):
    active_isin = synthetic_isin("api-list-active")
    cand_isin = synthetic_isin("api-list-cand")
    _seed(
        repos, isin=active_isin, ticker="APIACT",
        status_path=[(CoverageStatus.active, "promote")],
    )
    _seed(repos, isin=cand_isin, ticker="APICAND")

    all_isins = {s["isin"] for s in client.get("/stocks").json()}
    assert {active_isin, cand_isin} <= all_isins

    active = client.get("/stocks", params={"status": "active"}).json()
    active_set = {s["isin"] for s in active}
    assert active_isin in active_set
    assert cand_isin not in active_set
    assert all(s["status"] == "active" for s in active)

    cand = client.get("/stocks", params={"status": "candidate"}).json()
    cand_set = {s["isin"] for s in cand}
    assert cand_isin in cand_set
    assert active_isin not in cand_set


@pytest.mark.db
def test_invalid_status_filter_rejected(client):
    assert client.get("/stocks", params={"status": "bogus"}).status_code == 422


# --- GET /stocks/{isin} -----------------------------------------------------
@pytest.mark.db
def test_detail_full_shape_and_timeline_contains_seeded_snapshot(client, repos):
    isin = synthetic_isin("api-detail")
    _, snap_ids = _seed(
        repos, isin=isin, ticker="APIDET", name="Detail Co",
        as_ofs=[dt.date(2025, 3, 31), dt.date(2023, 3, 31), dt.date(2024, 3, 31)],
        status_path=[
            (CoverageStatus.active, "thesis formed"),
            (CoverageStatus.dropped, "thesis broke"),
        ],
    )

    # Amendment 1: the dual-seed resolved to ONE row whose timeline actually
    # holds the seeded snapshots (not "both seeded, both green" on split identity).
    stock_repo, _ = repos
    loaded = stock_repo.get_by_isin(isin)
    loaded_ids = {ref.snapshot_id for ref in loaded.snapshot_refs}
    assert set(snap_ids) == loaded_ids

    body = client.get(f"/stocks/{isin}").json()
    assert set(body) == {
        "uuid", "isin", "ticker", "name", "exchange", "sector", "status",
        "snapshot_refs", "transition_history", "created_at", "updated_at",
    }
    assert body["isin"] == isin
    assert body["status"] == "dropped"
    # Timeline ordered by (as_of, snapshot_id); contains the seeded snapshot ids.
    assert [r["as_of"] for r in body["snapshot_refs"]] == [
        "2023-03-31", "2024-03-31", "2025-03-31"
    ]
    assert {r["snapshot_id"] for r in body["snapshot_refs"]} == set(snap_ids)
    assert [
        [t["from_status"], t["to_status"]] for t in body["transition_history"]
    ] == [["candidate", "active"], ["active", "dropped"]]


@pytest.mark.db
def test_unknown_isin_returns_404(client):
    assert client.get("/stocks/INE000XXXXXX9").status_code == 404


# --- GET /stocks/{isin}/snapshots/{snapshot_id} -----------------------------
@pytest.mark.db
def test_snapshot_leaf_ok_and_drops_int_pk(client, repos):
    isin = synthetic_isin("api-leaf")
    int_pk, snap_ids = _seed(
        repos, isin=isin, ticker="APILEAF", as_ofs=[dt.date(2024, 3, 31)]
    )
    resp = client.get(f"/stocks/{isin}/snapshots/{snap_ids[0]}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["snapshot_id"] == snap_ids[0]
    assert body["isin"] == isin
    assert body["inputs"] == INPUTS
    assert "content_hash" in body
    # The domain Snapshot.stock_id IS the stock int pk — it must be dropped.
    assert "stock_id" not in body


@pytest.mark.db
def test_unknown_snapshot_returns_404(client, repos):
    isin = synthetic_isin("api-leaf-miss")
    _seed(repos, isin=isin, ticker="APIMISS", as_ofs=[dt.date(2024, 3, 31)])
    assert client.get(f"/stocks/{isin}/snapshots/99999999").status_code == 404


@pytest.mark.db
def test_cross_stock_snapshot_is_404_ownership(client, repos):
    """A real snapshot, but owned by a DIFFERENT stock under this isin -> 404."""
    # Synthetic (unique-per-seed) isins so the shared append-only test DB never
    # collides with another test's constant on the isin UNIQUE constraint.
    owner_isin = synthetic_isin("api-owner")
    other_isin = synthetic_isin("api-other")
    _, owner_snaps = _seed(
        repos, isin=owner_isin, ticker="APIOWNER", as_ofs=[dt.date(2024, 3, 31)]
    )
    _seed(repos, isin=other_isin, ticker="APIOTHER", as_ofs=[dt.date(2024, 3, 31)])

    # owner_snaps[0] exists, but is not on other_isin's timeline.
    resp = client.get(f"/stocks/{other_isin}/snapshots/{owner_snaps[0]}")
    assert resp.status_code == 404


# --- guardrails: uuid+isin present, int pk absent; DTOs not domain ----------
@pytest.mark.db
def test_uuid_present_int_pk_absent_from_populated_json(client, repos):
    """HARD RULE 4 on the RISK-BEARING shape: a fully populated aggregate.

    The detail view carries the max int-typed surface (the SnapshotRefResponse
    list), so that is where a stock int PK could leak through the mapping loop.
    We seed a full aggregate (multiple snapshots + a multi-entry transition
    history) and make the target's int PK DISTINCT-BY-CONSTRUCTION from every
    snapshot_id on the wire: a helper stock first advances the snapshot sequence
    by ``pk`` rows, so every one of the target's own snapshot_ids is > pk.
    """
    stock_repo, snapshot_repo = repos

    # Seed the target FIRST (no snapshots yet) so we know its int PK.
    target_isin = synthetic_isin("api-pk-target")
    pk, _ = _seed(repos, isin=target_isin, ticker="APIPKT")

    # Advance the snapshot sequence past ``pk`` via throwaway snapshots under a
    # helper stock -> the target's own snapshot_ids are then guaranteed > pk.
    helper_isin = synthetic_isin("api-pk-helper")
    helper_pk, _ = _seed(repos, isin=helper_isin, ticker="APIPKH")
    for _ in range(pk):
        snapshot_repo.save_snapshot(
            freeze_snapshot(
                stock_id=helper_pk, as_of=dt.date(2020, 1, 1),
                kind=SnapshotKind.annual, inputs=INPUTS,
            )
        )

    # Now give the TARGET a full timeline (ids > pk) + a multi-entry history.
    target_snap_ids: list[int] = []
    for as_of in (dt.date(2025, 3, 31), dt.date(2023, 3, 31), dt.date(2024, 3, 31)):
        snap = snapshot_repo.save_snapshot(
            freeze_snapshot(
                stock_id=pk, as_of=as_of,
                kind=SnapshotKind.annual, inputs=INPUTS,
            )
        )
        target_snap_ids.append(snap.id)
    agg = stock_repo.get_by_isin(target_isin)
    agg.transition_to(CoverageStatus.active, "promote")
    agg.transition_to(CoverageStatus.dropped, "drop")
    stock_repo.save(agg)

    # Disjoint BY CONSTRUCTION (not by luck): every seeded snapshot_id > pk.
    assert all(sid > pk for sid in target_snap_ids), (pk, target_snap_ids)

    detail = client.get(f"/stocks/{target_isin}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["uuid"] and body["isin"] == target_isin
    assert len(body["snapshot_refs"]) == 3        # populated timeline
    assert len(body["transition_history"]) == 2   # populated history

    # Exact scan over the POPULATED response. The only ints present are the
    # target's snapshot_ids (all > pk), so a pk match here would be a real leak.
    ints = _all_ints(body)
    assert set(target_snap_ids) <= set(ints)      # the int surface is really there
    assert pk not in ints                          # ...and the stock int PK is not


@pytest.mark.db
def test_responses_are_api_dtos_not_domain(client, repos):
    """Boundary proof: domain-only fields never appear (HARD RULE 1)."""
    isin = synthetic_isin("api-dto")
    _, snap_ids = _seed(
        repos, isin=isin, ticker="APIDTO", as_ofs=[dt.date(2024, 3, 31)]
    )
    detail = client.get(f"/stocks/{isin}").json()
    # 'profile' is a domain Stock field, never on the wire; 'id' (the domain's
    # UUID attr) is exposed as 'uuid', not 'id'.
    assert "profile" not in detail
    assert "id" not in detail

    leaf = client.get(f"/stocks/{isin}/snapshots/{snap_ids[0]}").json()
    assert "stock_id" not in leaf  # domain Snapshot field, dropped
