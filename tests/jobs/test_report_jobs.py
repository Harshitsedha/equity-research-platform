"""Step 5 — the arq report job. No network, no DB, no Redis.

The job coroutine is invoked directly; arq's retry loop is emulated by re-calling
on ``Retry`` with an incrementing ``job_try``. Proves: hard-fail is terminal and
NOT re-enqueued, transient errors are retried then succeed (and exhaust
terminally), and re-runs are idempotent.
"""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest
from arq import Retry

from research_platform.domain.models import Snapshot, SnapshotKind
from research_platform.jobs.report_jobs import (
    MAX_TRIES,
    ReportHardFailure,
    ReportJobError,
    TransientJobError,
    generate_report_job,
)
from research_platform.ingestion.llm.stub_adapter import StubLLMAdapter, StubMode
from tests.support.fakes import InMemoryRepository

INPUTS = {
    "revenue": 1500.0, "prev_revenue": 1200.0, "ebit": 360.0, "net_income": 240.0,
    "total_assets": 2000.0, "current_liabilities": 500.0, "total_debt": 300.0,
    "equity": 1000.0,
}


class FlakyLLM:
    """Wraps a delegate LLM, raising a transient error its first ``fail_times`` calls."""

    model_name = "flaky-llm"

    def __init__(self, fail_times: int, delegate) -> None:
        self.calls = 0
        self.fail_times = fail_times
        self.delegate = delegate

    def generate_report_draft(self, snapshot_inputs, schema_spec):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TransientJobError("simulated I/O hiccup")
        return self.delegate.generate_report_draft(snapshot_inputs, schema_spec)


def _setup(llm):
    repo = InMemoryRepository()
    snap = repo.save_snapshot(
        Snapshot(stock_id=1, as_of=dt.date(2026, 3, 31), kind=SnapshotKind.annual,
                 inputs=INPUTS, code_version="t", content_hash="h")
    )
    return {"repo": repo, "llm": llm, "report_kind": "initiation"}, repo, snap


def _run_with_arq_retry_loop(ctx, snapshot_id, max_tries=MAX_TRIES):
    """Emulate arq: re-invoke on Retry with an incrementing job_try; surface terminals."""
    retries = 0
    for attempt in range(1, max_tries + 1):
        ctx["job_try"] = attempt
        try:
            return asyncio.run(generate_report_job(ctx, snapshot_id)), retries
        except Retry:
            retries += 1
            continue
    raise AssertionError("loop ended without a terminal outcome")


# --- happy path ------------------------------------------------------------
def test_job_good_stores_report() -> None:
    ctx, repo, snap = _setup(StubLLMAdapter(StubMode.GOOD))
    result = asyncio.run(generate_report_job({**ctx, "job_try": 1}, snap.id))
    assert result["stored"] and result["overall"] == "PASSED"
    assert repo.get_report_by_snapshot(snap.id) is not None


# --- hard fail is terminal and NOT retried ---------------------------------
def test_job_hard_fail_is_terminal_not_retried() -> None:
    ctx, repo, snap = _setup(StubLLMAdapter(StubMode.BAD_NUMERIC))
    with pytest.raises(ReportHardFailure) as ei:
        asyncio.run(generate_report_job({**ctx, "job_try": 1}, snap.id))
    # structural guarantee: a hard fail is NOT an arq Retry -> arq won't re-enqueue
    assert not isinstance(ei.value, Retry)
    assert not issubclass(ReportHardFailure, Retry)
    # and nothing was written
    assert repo.get_report_by_snapshot(snap.id) is None
    assert repo.reports == {}


def test_job_hard_fail_is_deterministic_on_reinvocation() -> None:
    ctx, repo, snap = _setup(StubLLMAdapter(StubMode.BAD_NUMERIC))
    for _ in range(3):  # re-running never "fixes" a wrong number, never stores
        with pytest.raises(ReportHardFailure):
            asyncio.run(generate_report_job({**ctx, "job_try": 1}, snap.id))
    assert repo.reports == {}


# --- transient retries -----------------------------------------------------
def test_job_transient_is_retried_then_succeeds() -> None:
    # fail twice, succeed on the 3rd attempt (within MAX_TRIES=3)
    ctx, repo, snap = _setup(FlakyLLM(fail_times=2, delegate=StubLLMAdapter(StubMode.GOOD)))
    result, retries = _run_with_arq_retry_loop(ctx, snap.id)
    assert result["stored"] and result["overall"] == "PASSED"
    assert retries == 2  # two Retry deferrals before success
    assert repo.get_report_by_snapshot(snap.id) is not None


def test_job_transient_exhausts_to_terminal() -> None:
    ctx, repo, snap = _setup(FlakyLLM(fail_times=99, delegate=StubLLMAdapter(StubMode.GOOD)))
    with pytest.raises(ReportJobError, match="exhausted"):
        _run_with_arq_retry_loop(ctx, snap.id)
    assert repo.reports == {}  # never stored


# --- idempotency -----------------------------------------------------------
def test_job_is_idempotent_for_same_snapshot() -> None:
    ctx, repo, snap = _setup(StubLLMAdapter(StubMode.GOOD))
    first = asyncio.run(generate_report_job({**ctx, "job_try": 1}, snap.id))
    second = asyncio.run(generate_report_job({**ctx, "job_try": 1}, snap.id))
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert second["report_id"] == first["report_id"]
    assert len(repo.reports) == 1  # no duplicate
