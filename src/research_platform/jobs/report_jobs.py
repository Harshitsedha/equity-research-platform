"""The async report job — wraps the report pipeline for arq.

This is the first genuinely fallible/slow path. The job:
  * is idempotent via ``snapshot_id`` (the pipeline returns the existing report
    rather than duplicating — backed by the unique constraint on report.snapshot_id);
  * retries TRANSIENT errors with bounded exponential backoff (arq ``Retry``);
  * treats a verification HARD_FAIL as a TERMINAL, LOUD failed-job state — it raises
    a non-``Retry`` exception, so arq does not re-enqueue it. Retrying a wrong number
    is pointless and is made structurally impossible here.

The job depends only on the domain (pipeline + ports) and arq; adapters are
injected via ``ctx`` by the worker's composition root (``app/worker.py``).
"""

from __future__ import annotations

from typing import Any

import structlog
from arq import Retry

from research_platform.domain.models import VerificationResult
from research_platform.domain.ports.llm import LLMPort
from research_platform.domain.ports.repository import RepositoryPort
from research_platform.domain.reporting import run_report_pipeline
from research_platform.domain.version import CODE_VERSION

log = structlog.get_logger()

#: Bounded retry budget for transient failures (inclusive of the first attempt).
MAX_TRIES = 3

#: Exceptions considered transient (worth retrying). Verification failures are
#: explicitly NOT here — they are deterministic and terminal.
TRANSIENT_EXCEPTIONS: tuple[type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
)


class TransientJobError(Exception):
    """A retryable I/O-style hiccup (used to simulate transient faults in Part A)."""


class ReportJobError(RuntimeError):
    """A terminal job failure that is not a verification hard-fail (e.g. bad input)."""


class ReportHardFailure(RuntimeError):
    """Terminal: the draft failed numeric verification. Loud, never retried."""

    def __init__(self, snapshot_id: int, verification: VerificationResult) -> None:
        self.snapshot_id = snapshot_id
        self.verification = verification
        super().__init__(
            f"report generation HARD-FAILED for snapshot {snapshot_id}: "
            f"{verification.overall.value} — not stored, not retried"
        )


def _is_transient(exc: Exception) -> bool:
    return isinstance(exc, (TransientJobError, *TRANSIENT_EXCEPTIONS))


def _handle_transient(ctx: dict, snapshot_id: int, exc: Exception) -> None:
    """Either schedule a bounded retry (arq ``Retry``) or give up terminally."""
    job_try = ctx.get("job_try", 1)
    log.warning(
        "report_job.transient",
        snapshot_id=snapshot_id,
        attempt=job_try,
        max_tries=MAX_TRIES,
        error=str(exc),
    )
    if job_try >= MAX_TRIES:
        raise ReportJobError(
            f"transient failures exhausted after {job_try} attempts: {exc}"
        ) from exc
    # bounded exponential backoff: 1s, 4s, ...
    raise Retry(defer=job_try**2)


async def generate_report_job(ctx: dict, snapshot_id: int) -> dict[str, Any]:
    """arq entrypoint: generate -> verify -> assemble -> persist for one snapshot."""
    repo: RepositoryPort = ctx["repo"]
    llm: LLMPort = ctx["llm"]
    kind: str = ctx.get("report_kind", "initiation")

    try:
        snapshot = repo.get_snapshot(snapshot_id)
        if snapshot is None:
            raise ReportJobError(f"snapshot {snapshot_id} not found")  # terminal
        result = run_report_pipeline(
            snapshot, llm=llm, repo=repo, kind=kind, code_version=CODE_VERSION
        )
    except Retry:
        raise
    except ReportJobError:
        raise
    except Exception as exc:  # noqa: BLE001 - classify then re-raise appropriately
        if _is_transient(exc):
            _handle_transient(ctx, snapshot_id, exc)
        raise  # unknown error -> terminal (do not silently retry)

    if not result.stored:
        # HARD FAIL: terminal + loud. Raising a non-Retry exception means arq will
        # NOT re-enqueue it. Wrong is wrong; retrying cannot help.
        log.error(
            "report_job.hard_fail",
            snapshot_id=snapshot_id,
            overall=result.verification.overall.value,
        )
        raise ReportHardFailure(snapshot_id, result.verification)

    log.info(
        "report_job.done",
        snapshot_id=snapshot_id,
        report_id=result.report.id,
        overall=result.verification.overall.value,
        idempotent=result.idempotent,
    )
    return {
        "snapshot_id": snapshot_id,
        "report_id": result.report.id,
        "overall": result.verification.overall.value,
        "idempotent": result.idempotent,
        "stored": True,
    }
