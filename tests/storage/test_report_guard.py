"""Step 4 GATE — the storage guard. Requires a live PostgreSQL.

Directly attempts to persist a non-storable (HARD_FAILED) Report through the real
PostgresRepository and asserts it is REFUSED with ZERO rows written. This proves
the guard at the persistence boundary, independent of the pipeline/caller.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from research_platform.domain.models import Report
from research_platform.storage import models as orm
from research_platform.storage.repository import NonStorableReportError

pytestmark = pytest.mark.db

_ORPHAN_SNAPSHOT_ID = 987_654_321  # never inserted; guard fires before any FK use


def _hard_failed_report() -> Report:
    return Report(
        stock_id=1,
        snapshot_id=_ORPHAN_SNAPSHOT_ID,
        kind="initiation",
        content={"sections": {}},
        verification={
            "overall": "HARD_FAILED",
            "claims": [],
            "missing_sections": [],
            "code_version": "t",
        },
        code_version="t",
    )


def _report_row_count(session_factory, snapshot_id: int) -> int:
    with session_factory() as session:
        return session.scalar(
            select(func.count())
            .select_from(orm.Report)
            .where(orm.Report.snapshot_id == snapshot_id)
        )


def test_storage_refuses_hard_failed_report(repository, session_factory) -> None:
    assert _report_row_count(session_factory, _ORPHAN_SNAPSHOT_ID) == 0

    with pytest.raises(NonStorableReportError, match="HARD_FAILED"):
        repository.save_report(_hard_failed_report())

    # the point of Part A: the wrong report never reached the database.
    assert _report_row_count(session_factory, _ORPHAN_SNAPSHOT_ID) == 0
