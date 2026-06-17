"""arq worker settings — the composition root for the async path.

This wires concrete adapters into the job ``ctx`` (so the job itself stays
adapter-free, depending only on the domain). Run with:  ``arq research_platform.app.worker.WorkerSettings``

Note (ADR-007 Amendment 2026-06-17): Redis is the job broker ONLY; Postgres
remains the system of record / ledger / cache / idempotency store. The job logic
is proven in tests by direct invocation (no Redis needed); running the live
worker needs Redis at ``REDIS_URL`` (docker-compose maps it to host port 6380).
"""

from __future__ import annotations

import os

from arq.connections import RedisSettings
from dotenv import load_dotenv

from research_platform.app.composition import build_platform
from research_platform.jobs.report_jobs import MAX_TRIES, generate_report_job

# Entry-point .env loading: the worker reads REDIS_URL and (when LLM_PROVIDER=claude)
# ANTHROPIC_API_KEY. Loaded here at the worker entry, never in a library/domain module.
# No default test imports this module, so the no-key test path is unaffected.
load_dotenv()


async def startup(ctx: dict) -> None:
    platform = build_platform()
    ctx["repo"] = platform.repository
    ctx["llm"] = platform.llm
    ctx["report_kind"] = "initiation"


async def shutdown(ctx: dict) -> None:  # pragma: no cover - nothing to tear down
    return None


#: Broker URL; docker-compose maps Redis to host port 6380 (avoids colliding
#: with any Redis already on 6379). Override with REDIS_URL.
DEFAULT_REDIS_URL = "redis://localhost:6380"


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(os.environ.get("REDIS_URL", DEFAULT_REDIS_URL))


class WorkerSettings:
    """arq discovers this class to run the worker."""

    functions = [generate_report_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings()
    max_tries = MAX_TRIES
