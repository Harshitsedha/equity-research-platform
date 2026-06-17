"""Phase 1A end-to-end: the report pipeline through the ASYNC JOB path.

Proves all four verification outcomes against a real PostgreSQL, each driven via
``generate_report_job`` (not direct domain calls):

  GOOD         -> PASSED, stored, reproducible, verification recorded
  BAD_NUMERIC  -> HARD_FAILED, terminal (not retried), report ABSENT from the DB
  BAD_CITATION -> PASSED_WITH_FLAGS, stored, [UNVERIFIED CITATION] in prose
  INCOMPLETE   -> INCOMPLETE, stored, missing-section recorded

Requires:  make up && make migrate   (Postgres on :5434, migrated to head)
Run with:  uv run python scripts/phase1a_e2e.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
import uuid

from arq import Retry
from sqlalchemy import func, select

from research_platform.app.composition import build_platform
from research_platform.domain.ledger import freeze_snapshot
from research_platform.domain.models import SnapshotKind, Stock
from research_platform.domain.report_spec import build_schema_spec
from research_platform.domain.reporting import assemble_report
from research_platform.domain.verification import verify_draft
from research_platform.ingestion.llm.stub_adapter import StubLLMAdapter, StubMode
from research_platform.jobs.report_jobs import ReportHardFailure, generate_report_job
from research_platform.storage import models as orm
from research_platform.storage.db import make_session_factory

RICH_INPUTS = {
    "revenue": 1500.0, "prev_revenue": 1200.0, "ebit": 360.0, "net_income": 240.0,
    "total_assets": 2000.0, "current_liabilities": 500.0, "total_debt": 300.0,
    "equity": 1000.0,
}

_results: list[tuple[str, bool, str]] = []


def _record(name: str, ok: bool, detail: str) -> None:
    _results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name:<12} {detail}")


def _make_snapshot(repo):
    stock = repo.save_stock(
        Stock(ticker=f"E2E{uuid.uuid4().hex[:6].upper()}", name="E2E Co", sector="Test")
    )
    snap = freeze_snapshot(
        stock_id=stock.id, as_of=dt.date(2026, 3, 31), kind=SnapshotKind.annual,
        inputs=RICH_INPUTS, source_versions={"manual_upload": "1.0"},
    )
    return repo.save_snapshot(snap)


def _ctx(repo, mode: StubMode) -> dict:
    return {"repo": repo, "llm": StubLLMAdapter(mode), "report_kind": "initiation", "job_try": 1}


def _count_report_rows(snapshot_id: int) -> int:
    with make_session_factory()() as session:
        return session.scalar(
            select(func.count()).select_from(orm.Report).where(
                orm.Report.snapshot_id == snapshot_id
            )
        )


def main() -> int:
    platform = build_platform()
    repo = platform.repository

    # --- GOOD -------------------------------------------------------------
    snap = _make_snapshot(repo)
    out = asyncio.run(generate_report_job(_ctx(repo, StubMode.GOOD), snap.id))
    stored = repo.get_report_by_snapshot(snap.id)
    # reproducibility: re-derive content+verification from the same snapshot
    spec = build_schema_spec(snap)
    draft = StubLLMAdapter(StubMode.GOOD).generate_report_draft(snap.inputs, spec)
    rebuilt = assemble_report(draft, verify_draft(draft, snap), snap, kind="initiation")
    reproducible = (
        rebuilt.content == stored.content
        and rebuilt.verification == stored.verification
    )
    _record(
        "GOOD", ok=(out["overall"] == "PASSED" and stored is not None
                    and stored.verification["overall"] == "PASSED" and reproducible),
        detail=f"overall={out['overall']} report_id={stored.id} "
               f"reproducible={reproducible} verification_recorded=True",
    )

    # --- BAD_NUMERIC (the headline absence assertion) ---------------------
    snap = _make_snapshot(repo)
    hard_failed = not_retried = False
    overall = "?"
    try:
        asyncio.run(generate_report_job(_ctx(repo, StubMode.BAD_NUMERIC), snap.id))
    except ReportHardFailure as exc:
        hard_failed = True
        not_retried = not isinstance(exc, Retry)  # terminal, arq won't re-enqueue
        overall = exc.verification.overall.value
    rows = _count_report_rows(snap.id)
    absent = rows == 0 and repo.get_report_by_snapshot(snap.id) is None
    _record(
        "BAD_NUMERIC", ok=(hard_failed and not_retried and absent),
        detail=f"hard_failed={hard_failed} overall={overall} "
               f"not_retried={not_retried} report_rows_in_db={rows} (ABSENT)",
    )

    # --- BAD_CITATION -----------------------------------------------------
    snap = _make_snapshot(repo)
    out = asyncio.run(generate_report_job(_ctx(repo, StubMode.BAD_CITATION), snap.id))
    stored = repo.get_report_by_snapshot(snap.id)
    sections = stored.content["sections"].values() if stored else []
    prose_flag = any("[UNVERIFIED CITATION]" in s["prose"] for s in sections)
    has_flagged = any(
        c["status"] == "FLAGGED" for s in sections for c in s["claims"]
    )
    _record(
        "BAD_CITATION", ok=(out["overall"] == "PASSED_WITH_FLAGS" and stored is not None
                            and prose_flag and has_flagged),
        detail=f"overall={out['overall']} report_id={stored.id} "
               f"flagged_claim={has_flagged} prose_annotated={prose_flag}",
    )

    # --- INCOMPLETE -------------------------------------------------------
    snap = _make_snapshot(repo)
    out = asyncio.run(generate_report_job(_ctx(repo, StubMode.INCOMPLETE), snap.id))
    stored = repo.get_report_by_snapshot(snap.id)
    missing = stored.verification["missing_sections"] if stored else None
    _record(
        "INCOMPLETE", ok=(out["overall"] == "INCOMPLETE" and stored is not None
                          and missing == ["risks"]),
        detail=f"overall={out['overall']} report_id={stored.id} missing_sections={missing}",
    )

    all_ok = all(ok for _, ok, _ in _results)
    print("\n=== Phase 1A e2e:", "ALL FOUR OUTCOMES PASS ===" if all_ok else "FAILURES ===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
