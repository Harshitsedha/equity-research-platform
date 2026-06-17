"""Reproducibility test (Blueprint 3.3 #5).

Store a ValuationRun, then re-derive purely from its referenced (reloaded)
snapshot and assert an identical result with version stamps intact. Requires a
live PostgreSQL.
"""

from __future__ import annotations

import pytest

from research_platform.app.composition import build_platform
from research_platform.domain.ledger import freeze_from_document
from research_platform.domain.models import Stock, ValuationRun
from research_platform.domain.version import CODE_VERSION
from research_platform.ingestion.document_adapter import FileDocumentAdapter
from tests.conftest import ROOT

pytestmark = pytest.mark.db

ASSUMPTIONS = {
    "growth_rate": 0.10,
    "projection_years": 5,
    "wacc": 0.11,
    "terminal_growth": 0.04,
}


def test_valuation_run_re_derives_identically(repository):
    platform = build_platform(repository=repository)

    doc = FileDocumentAdapter().parse(ROOT / "data" / "sample_infy.json")
    stock = repository.save_stock(
        Stock(ticker=doc.ticker, name=doc.name, exchange=doc.exchange, sector=doc.sector)
    )
    snapshot = repository.save_snapshot(freeze_from_document(doc, stock_id=stock.id))

    # First compute + store.
    result = platform.valuation.run(snapshot.inputs, ASSUMPTIONS)
    run = repository.save_valuation_run(
        ValuationRun(
            stock_id=stock.id,
            snapshot_id=snapshot.id,
            model_name=platform.valuation.model_name,
            assumptions=ASSUMPTIONS,
            result=result,
            code_version=CODE_VERSION,
        )
    )

    # Reload everything from the DB and re-derive.
    persisted_run = repository.get_valuation_run(run.id)
    source_snapshot = repository.get_snapshot(persisted_run.snapshot_id)
    rederived = platform.valuation.run(
        source_snapshot.inputs, persisted_run.assumptions
    )

    assert rederived == persisted_run.result
    # version stamps intact through the round-trip
    assert persisted_run.code_version == CODE_VERSION
    assert source_snapshot.code_version == CODE_VERSION
    # the snapshot's integrity hash is unchanged
    assert source_snapshot.content_hash == snapshot.content_hash


def test_two_independent_runs_match(repository):
    """Determinism across two separate stored runs from the same snapshot."""
    platform = build_platform(repository=repository)
    doc = FileDocumentAdapter().parse(ROOT / "data" / "sample_infy.json")
    stock = repository.save_stock(Stock(ticker="REPRO2", name=doc.name))
    snapshot = repository.save_snapshot(freeze_from_document(doc, stock_id=stock.id))

    r1 = repository.save_valuation_run(
        ValuationRun(
            stock_id=stock.id, snapshot_id=snapshot.id, model_name="dcf",
            assumptions=ASSUMPTIONS,
            result=platform.valuation.run(snapshot.inputs, ASSUMPTIONS),
            code_version=CODE_VERSION,
        )
    )
    r2 = repository.save_valuation_run(
        ValuationRun(
            stock_id=stock.id, snapshot_id=snapshot.id, model_name="dcf",
            assumptions=ASSUMPTIONS,
            result=platform.valuation.run(snapshot.inputs, ASSUMPTIONS),
            code_version=CODE_VERSION,
        )
    )
    assert repository.get_valuation_run(r1.id).result == repository.get_valuation_run(r2.id).result
