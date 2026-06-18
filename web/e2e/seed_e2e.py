"""Deterministic E2E seed for the Phase 2b-ui Playwright suite.

The web app is a thin projection over the sealed 2b-api; this script stands up
the data those tests render against, reusing the EXISTING repositories (the same
path the api tests seed through) — no new persistence code.

Disposable-DB posture (consistent with tests/conftest.py): the shared docker
Postgres is migrated to a CLEAN schema (downgrade base -> upgrade head) before
seeding, exactly as the pytest suite does. Run it against the dev/test DB only.

What it constructs (and exposes in manifest.json for the specs):
- A ``target`` stock (status ``active``) whose surrogate int PK is forced to a
  scannable SENTINEL (``ALTER SEQUENCE stock_id_seq RESTART WITH 990001``) so the
  guardrail spec can assert that exact value never appears in any URL or DOM
  (HARD RULE 2). It carries a real snapshot timeline, so its detail + leaf pages
  legitimately render snapshot_ids — the POSITIVE CONTROL proving the DOM scan
  can see an int when one is present (ADJUSTMENT 1).
- A ``candidate`` stock (no snapshots) and a ``dropped`` stock (full transition
  history) so per-state status colour-coding is covered (ADJUSTMENT / view spec).
- A deliberately constructed CROSS-STOCK 404 pair (ADJUSTMENT 3): a snapshot that
  belongs to ``target`` requested under ``candidate``'s isin must 404 on
  OWNERSHIP, not mere non-existence.
- A valid-format but UNSEEDED isin for the unknown-isin 404 path.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from sqlalchemy import text

# Repo root = two levels up from web/e2e/.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from research_platform.domain.ledger import freeze_snapshot  # noqa: E402
from research_platform.domain.models import SnapshotKind  # noqa: E402
from research_platform.domain.models import Stock as LegacyStock  # noqa: E402
from research_platform.domain.stock import CoverageStatus  # noqa: E402
from research_platform.storage.db import (  # noqa: E402
    get_database_url,
    make_engine,
    make_session_factory,
)
from research_platform.storage.repository import PostgresRepository  # noqa: E402
from research_platform.storage.stock_repository import SqlStockRepository  # noqa: E402

sys.path.insert(0, str(ROOT))
from tests.support.isins import synthetic_isin  # noqa: E402

SENTINEL_PK = 990001
# Snapshot ids are forced into a high, distinctive band too, so the guardrail's
# POSITIVE CONTROL (ADJUSTMENT 1) asserts a *distinctive* rendered int — a hit on
# 70001 can't be a date/hash/uuid coincidence the way a hit on "2" could.
SENTINEL_SNAP_BASE = 70001
INPUTS = {"revenue": 100.0, "ebit": 25.0}
MANIFEST = Path(__file__).resolve().parent / "manifest.json"


def _migrate_clean() -> None:
    """Clean schema via the real Alembic migrations (as tests/conftest does)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def _save_snapshot(snapshot_repo, *, stock_pk: int, as_of: dt.date) -> int:
    snap = snapshot_repo.save_snapshot(
        freeze_snapshot(
            stock_id=stock_pk,
            as_of=as_of,
            kind=SnapshotKind.annual,
            inputs=INPUTS,
        )
    )
    return snap.id


def _apply_status(stock_repo, isin: str, path: list[tuple[CoverageStatus, str]]) -> None:
    # LOAD by isin (carries the seeded row's uuid) so save UPDATES that one row.
    agg = stock_repo.get_by_isin(isin)
    for target, reason in path:
        agg.transition_to(target, reason)
    stock_repo.save(agg)


def main() -> None:
    _migrate_clean()

    engine = make_engine()
    session_factory = make_session_factory(engine)
    stock_repo = SqlStockRepository(session_factory)
    snapshot_repo = PostgresRepository(session_factory)

    cand_isin = synthetic_isin("ui-candidate")
    dropped_isin = synthetic_isin("ui-dropped")
    target_isin = synthetic_isin("ui-target")
    unknown_isin = synthetic_isin("ui-unknown")  # NOT seeded

    # --- candidate stock (no snapshots) — also the cross-stock "other" isin ----
    snapshot_repo.save_stock(
        LegacyStock(isin=cand_isin, ticker="UICAND", name="Candidate Co")
    )

    # --- dropped stock: candidate -> active -> dropped, with one snapshot -------
    snapshot_repo.save_stock(
        LegacyStock(isin=dropped_isin, ticker="UIDROP", name="Dropped Co")
    )
    # Resolve int pk via a direct read (kept inside this seed script only).
    with session_factory() as s:
        dropped_int = s.scalar(
            text("SELECT id FROM stock WHERE isin = :i"), {"i": dropped_isin}
        )
    _save_snapshot(snapshot_repo, stock_pk=dropped_int, as_of=dt.date(2024, 3, 31))
    _apply_status(
        stock_repo,
        dropped_isin,
        [
            (CoverageStatus.active, "thesis formed"),
            (CoverageStatus.dropped, "thesis broke"),
        ],
    )

    # --- target stock: force the SENTINEL int PK, then seed it -----------------
    with engine.begin() as conn:
        conn.execute(text(f"ALTER SEQUENCE stock_id_seq RESTART WITH {SENTINEL_PK}"))
    created = snapshot_repo.save_stock(
        LegacyStock(isin=target_isin, ticker="UITGT", name="Target Co")
    )
    assert created.id == SENTINEL_PK, (
        f"sentinel pk not assigned: got {created.id}, expected {SENTINEL_PK}"
    )

    with engine.begin() as conn:
        conn.execute(
            text(f"ALTER SEQUENCE snapshot_id_seq RESTART WITH {SENTINEL_SNAP_BASE}")
        )
    target_snap_ids: list[int] = []
    target_as_ofs = [dt.date(2023, 3, 31), dt.date(2024, 3, 31), dt.date(2025, 3, 31)]
    for as_of in target_as_ofs:
        target_snap_ids.append(
            _save_snapshot(snapshot_repo, stock_pk=SENTINEL_PK, as_of=as_of)
        )
    _apply_status(stock_repo, target_isin, [(CoverageStatus.active, "promote")])

    # The sentinel pk must be distinct from every rendered snapshot_id, else the
    # guardrail's absence check could be satisfied/violated by coincidence.
    assert SENTINEL_PK not in target_snap_ids, (SENTINEL_PK, target_snap_ids)

    manifest = {
        "sentinel_pk": SENTINEL_PK,
        "target": {
            "isin": target_isin,
            "ticker": "UITGT",
            "name": "Target Co",
            "status": "active",
            "snapshot_ids": target_snap_ids,
            "leaf_snapshot_id": target_snap_ids[0],
            "as_ofs": [d.isoformat() for d in target_as_ofs],
        },
        "candidate": {"isin": cand_isin, "ticker": "UICAND", "status": "candidate"},
        "dropped": {"isin": dropped_isin, "ticker": "UIDROP", "status": "dropped"},
        # Cross-stock 404: this snapshot_id belongs to `target`, requested under
        # `candidate`'s isin -> ownership 404 (not unknown isin, not non-existence).
        "cross_stock": {
            "owner_isin": target_isin,
            "other_isin": cand_isin,
            "snapshot_id": target_snap_ids[0],
        },
        "unknown_isin": unknown_isin,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2))
    engine.dispose()
    print(f"seeded E2E fixtures -> {MANIFEST} (db={get_database_url()})")


if __name__ == "__main__":
    main()
