"""Phase 0 end-to-end: ingest -> freeze -> store -> reload -> value -> reproduce.

Proves the spine on the thinnest path (Blueprint 3.3 #6). Requires a migrated
PostgreSQL (``make up && make migrate``). Run with ``make e2e`` or
``uv run python scripts/phase0_e2e.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import structlog

from research_platform.app.composition import build_platform
from research_platform.domain.ledger import freeze_from_document
from research_platform.domain.models import Stock, ValuationRun
from research_platform.domain.version import CODE_VERSION

log = structlog.get_logger()

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_FILE = ROOT / "data" / "sample_infy.json"
ASSUMPTIONS_FILE = ROOT / "data" / "sample_assumptions.json"


def main() -> int:
    platform = build_platform()
    repo = platform.repository
    assumptions = json.loads(ASSUMPTIONS_FILE.read_text(encoding="utf-8"))

    # 1. INGEST: file -> normalized document (DocumentPort, the only fragile path)
    doc = platform.documents.parse(SAMPLE_FILE)
    log.info("ingested", ticker=doc.ticker, as_of=str(doc.as_of), kind=doc.kind.value)

    # 2. Upsert the persistent Stock anchor.
    stock = repo.get_stock_by_ticker(doc.ticker)
    if stock is None:
        stock = repo.save_stock(
            Stock(
                ticker=doc.ticker,
                name=doc.name,
                exchange=doc.exchange,
                sector=doc.sector,
                profile=doc.profile,
            )
        )
    assert stock.id is not None
    log.info("stock ready", stock_id=stock.id, ticker=stock.ticker)

    # 3. FREEZE: pure domain logic stamps content_hash + code_version.
    snapshot = freeze_from_document(doc, stock_id=stock.id)
    log.info("froze snapshot", content_hash=snapshot.content_hash[:12], code_version=snapshot.code_version)

    # 4. STORE the immutable snapshot.
    stored = repo.save_snapshot(snapshot)
    assert stored.id is not None

    # 5. RELOAD from the DB and confirm integrity survived the round-trip.
    reloaded = repo.get_snapshot(stored.id)
    assert reloaded is not None, "snapshot vanished after store"
    assert reloaded.content_hash == snapshot.content_hash, "content hash drifted"
    assert reloaded.code_version == CODE_VERSION, "code version stamp lost"
    log.info("reloaded snapshot", snapshot_id=reloaded.id)

    # 6. VALUE against the reloaded snapshot (ValuationPort).
    result = platform.valuation.run(reloaded.inputs, assumptions)
    stored_run = repo.save_valuation_run(
        ValuationRun(
            stock_id=stock.id,
            snapshot_id=reloaded.id,
            model_name=platform.valuation.model_name,
            assumptions=assumptions,
            result=result,
            code_version=CODE_VERSION,
        )
    )
    assert stored_run.id is not None
    log.info("stored valuation run", run_id=stored_run.id, value_per_share=result["value_per_share"])

    # 7. REPRODUCIBILITY: re-derive purely from the stored snapshot + run, and
    #    assert the result is identical with version stamps intact.
    persisted_run = repo.get_valuation_run(stored_run.id)
    assert persisted_run is not None
    source_snapshot = repo.get_snapshot(persisted_run.snapshot_id)
    assert source_snapshot is not None
    rederived = platform.valuation.run(
        source_snapshot.inputs, persisted_run.assumptions
    )

    assert rederived == persisted_run.result, (
        "REPRODUCIBILITY FAILURE: re-derived result differs from stored result"
    )
    assert persisted_run.code_version == CODE_VERSION, "run code version stamp lost"
    assert source_snapshot.code_version == CODE_VERSION, "snapshot stamp lost"

    log.info(
        "REPRODUCIBLE",
        run_id=persisted_run.id,
        value_per_share=rederived["value_per_share"],
        code_version=persisted_run.code_version,
    )
    print("\n=== Phase 0 e2e: PASS — result re-derived identically from the ledger ===")
    print(json.dumps(rederived, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
