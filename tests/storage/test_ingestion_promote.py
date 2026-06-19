"""DB-backed ingestion promote-gate tests (Phase 0, ADR-017).

Requires a live PostgreSQL (the real 0007 migration applies the ingestion_draft +
snapshot_source DDL and the snapshot_source immutability trigger). Proves the one
irreversible boundary: a clean draft promotes into the immutable ledger; a draft
that is WRONG (ADR-013 shadow, tampered payload) writes NOTHING; absence is never
zero-filled; the promoted rows are immutable while the draft stays mutable; source
provenance is stamped and reproducible; a restatement supersedes via a new snapshot.

The end-to-end honest-gap test is the headline: a partially-ingested snapshot must
make the REAL drift resolver report KEY_ABSENT / MISSING_INPUT, never a false zero.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import ProgrammingError

from research_platform.domain.drift import (
    DriftStatus,
    UnresolvedReason,
    compute_thesis_drift,
)
from research_platform.domain.ingestion import PromotionBlocked, raw_payload_hash
from research_platform.domain.ledger import freeze_snapshot
from research_platform.domain.stock import Stock
from research_platform.domain.thesis import Thesis, ThesisAssumption, ToleranceBand
from research_platform.ingestion.manual_adapter import ManualAssistedAdapter
from research_platform.storage import models as orm
from research_platform.storage.ingestion_repository import SqlIngestionRepository
from research_platform.storage.stock_repository import SqlStockRepository
from tests.support.isins import synthetic_isin

pytestmark = pytest.mark.db


# --- helpers ----------------------------------------------------------------
def _register_stock(session_factory, tag: str) -> str:
    isin = synthetic_isin(tag)
    SqlStockRepository(session_factory).save(
        Stock(isin=isin, ticker=f"ING_{tag[:8]}", name="Ingest Co")
    )
    return isin


def _form(fields: dict, *, as_of="2025-03-31", kind="annual", ref="filing://demo") -> dict:
    return {"source_doc_ref": ref, "period": {"as_of": as_of, "kind": kind}, "fields": fields}


def _draft(session_factory, isin: str, fields: dict, **form_kw):
    ing = SqlIngestionRepository(session_factory)
    ext = ManualAssistedAdapter().read(_form(fields, **form_kw))
    draft_id, report = ing.create_draft(ext, ingested_by="analyst", proposed_isin=isin)
    return ing, draft_id, report


def _stock_int_id(session_factory, isin: str) -> int:
    with session_factory() as session:
        return session.scalar(select(orm.Stock.id).where(orm.Stock.isin == isin))


def _snapshot_count(session_factory, isin: str) -> int:
    sid = _stock_int_id(session_factory, isin)
    with session_factory() as session:
        return session.scalar(
            select(func.count()).select_from(orm.Snapshot).where(orm.Snapshot.stock_id == sid)
        )


# === the headline: honest absence promotes AND the real resolver sees the gap =
def test_honest_absence_promotes_and_drift_sees_real_gap(session_factory) -> None:
    isin = _register_stock(session_factory, "ingest-honest")
    # revenue + ebit + net_income present; equity / others ABSENT (omitted, not 0).
    ing, draft_id, report = _draft(
        session_factory, isin,
        {"revenue": "1200000", "ebit": "252000", "net_income": "200000"},
    )
    assert report.promotable is True

    snap = ing.promote(draft_id)

    # The frozen inputs carry ONLY what was supplied — absence is omission, not zero.
    assert snap.inputs == {"revenue": 1_200_000.0, "ebit": 252_000.0, "net_income": 200_000.0}
    assert "equity" not in snap.inputs
    assert snap.inputs.get("equity") is None  # never a fabricated 0

    # The REAL drift resolver must report the honest gaps, not false "no drift".
    thesis = Thesis(
        recorded_at=dt.datetime(2024, 6, 1, tzinfo=dt.timezone.utc),
        anchor_valuation_run_id=1, anchor_snapshot_id=1, anchor_value_per_share=100.0,
        assumptions=[
            ThesisAssumption(name="margin", metric_key="ebit_margin", recorded_value=0.25,
                             band=ToleranceBand(lower=0.22, upper=None)),  # derived, computable
            ThesisAssumption(name="roe", metric_key="roe", recorded_value=0.20,
                             band=ToleranceBand(lower=0.10, upper=None)),  # derived, equity absent
            ThesisAssumption(name="share", metric_key="market_share", recorded_value=0.30,
                             band=ToleranceBand(lower=0.25, upper=None)),  # raw, absent
        ],
    )
    drifts = {d.name: d for d in compute_thesis_drift(thesis, snap.inputs).assumption_drifts}
    assert drifts["margin"].status is DriftStatus.CROSSED          # 0.21 < 0.22, resolved
    assert drifts["roe"].status is DriftStatus.UNRESOLVED
    assert drifts["roe"].unresolved_reason is UnresolvedReason.MISSING_INPUT
    assert drifts["share"].status is DriftStatus.UNRESOLVED
    assert drifts["share"].unresolved_reason is UnresolvedReason.KEY_ABSENT


# === the gate writes NOTHING on a violation ==================================
def test_adr013_derived_shadow_blocks_and_writes_nothing(session_factory) -> None:
    isin = _register_stock(session_factory, "ingest-adr013")
    ing, draft_id, _ = _draft(session_factory, isin, {"revenue": "100"})
    # A human "correction" forces a derived metric in as a raw input.
    ing.update_draft(
        draft_id, present_values={"revenue": "100", "ebit_margin": "0.21"},
        as_of="2025-03-31", kind="annual",
    )
    with pytest.raises(PromotionBlocked, match="derived_key_as_raw"):
        ing.promote(draft_id)
    # Nothing reached the ledger, and the draft was NOT flipped to promoted —
    # the whole txn rolled back at the gate.
    assert _snapshot_count(session_factory, isin) == 0
    assert ing.get_draft(draft_id).status == "draft"
    assert ing.get_draft(draft_id).promoted_snapshot_id is None


def test_reproducibility_gate_blocks_tampered_payload(session_factory) -> None:
    isin = _register_stock(session_factory, "ingest-tamper")
    ing, draft_id, _ = _draft(session_factory, isin, {"revenue": "100", "ebit": "20"})
    # Tamper the retained raw payload (the draft is mutable) so its hash no longer
    # matches the stored anchor — the reproducibility gate must refuse to promote.
    with session_factory() as session:
        row = session.get(orm.IngestionDraft, draft_id)
        row.raw_payload = {"tampered": True}
        session.commit()
    with pytest.raises(PromotionBlocked, match="reproducible"):
        ing.promote(draft_id)
    assert _snapshot_count(session_factory, isin) == 0


def test_promote_is_one_way_idempotent(session_factory) -> None:
    isin = _register_stock(session_factory, "ingest-oneway")
    ing, draft_id, _ = _draft(session_factory, isin, {"revenue": "100"})
    ing.promote(draft_id)
    with pytest.raises(PromotionBlocked, match="one-way"):
        ing.promote(draft_id)  # already promoted — refused, no second snapshot
    assert _snapshot_count(session_factory, isin) == 1


# === immutability post-promote; draft mutable pre-promote ====================
def test_promoted_snapshot_and_source_are_immutable(session_factory) -> None:
    isin = _register_stock(session_factory, "ingest-immut")
    ing, draft_id, _ = _draft(session_factory, isin, {"revenue": "100"})
    snap = ing.promote(draft_id)
    with pytest.raises(ProgrammingError, match="immutable"):
        with session_factory() as session:
            session.execute(
                text("UPDATE snapshot SET code_version = 'x' WHERE id = :id"),
                {"id": snap.id},
            )
            session.commit()
    with pytest.raises(ProgrammingError, match="immutable"):
        with session_factory() as session:
            session.execute(
                text("UPDATE snapshot_source SET ingested_by = 'x' WHERE snapshot_id = :id"),
                {"id": snap.id},
            )
            session.commit()


def test_ingestion_draft_is_mutable_pre_promote(session_factory) -> None:
    isin = _register_stock(session_factory, "ingest-mutable")
    ing, draft_id, _ = _draft(session_factory, isin, {"revenue": "100"})
    # A correction mutates the staging row freely (no immutability trigger).
    report = ing.update_draft(
        draft_id, present_values={"revenue": "110", "ebit": "22"},
        as_of="2025-03-31", kind="annual",
    )
    assert report.proposed_inputs == {"revenue": 110.0, "ebit": 22.0}
    assert ing.get_draft(draft_id).proposed_inputs == {"revenue": 110.0, "ebit": 22.0}


# === provenance stamped + reproducible from the payload hash ==================
def test_source_provenance_stamped_and_reproducible(session_factory) -> None:
    isin = _register_stock(session_factory, "ingest-prov")
    ing, draft_id, _ = _draft(session_factory, isin, {"revenue": "100", "ebit": "20"})
    snap = ing.promote(draft_id)
    with session_factory() as session:
        src = session.scalar(
            select(orm.SnapshotSource).where(orm.SnapshotSource.snapshot_id == snap.id)
        )
        draft = session.get(orm.IngestionDraft, draft_id)
        assert src.source_kind == "manual_paste"
        assert src.source_doc_ref == "filing://demo"
        assert src.ingested_by == "analyst"
        assert src.ingestion_code_version  # stamped, non-empty
        # the reproducibility anchor re-derives from the retained payload ...
        assert src.raw_payload_hash == raw_payload_hash(draft.raw_payload)
    # ... and the inputs re-freeze to the same content_hash (deterministic).
    refrozen = freeze_snapshot(
        stock_id=snap.stock_id, as_of=snap.as_of, kind=snap.kind,
        inputs=snap.inputs, code_version=snap.code_version,
    )
    assert refrozen.content_hash == snap.content_hash


# === restatement: new superseding snapshot, chain recorded ===================
def test_restatement_supersedes_via_new_snapshot(session_factory) -> None:
    isin = _register_stock(session_factory, "ingest-restate")
    ing1, d1, _ = _draft(session_factory, isin, {"revenue": "100"})
    snap1 = ing1.promote(d1)
    # A restatement for the SAME (stock, as_of, kind) with a corrected figure.
    ing2, d2, _ = _draft(session_factory, isin, {"revenue": "115"})
    snap2 = ing2.promote(d2)

    assert snap2.id > snap1.id
    sid = _stock_int_id(session_factory, isin)
    with session_factory() as session:
        src2 = session.scalar(
            select(orm.SnapshotSource).where(orm.SnapshotSource.snapshot_id == snap2.id)
        )
        assert src2.supersedes_snapshot_id == snap1.id
        # "latest for the period" is the max-id query — the restatement.
        latest = session.scalar(
            select(func.max(orm.Snapshot.id)).where(
                orm.Snapshot.stock_id == sid,
                orm.Snapshot.as_of == snap2.as_of,
                orm.Snapshot.kind == snap2.kind,
            )
        )
        assert latest == snap2.id
        # the superseded snapshot persists, immutable.
        assert session.get(orm.Snapshot, snap1.id) is not None
