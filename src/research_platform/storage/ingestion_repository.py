"""SqlIngestionRepository — the mutable staging buffer + the gated promote.

The storage half of Phase-0 ingestion (ADR-017). Two responsibilities:

- **Staging (mutable):** ``create_draft`` / ``get_draft`` / ``update_draft`` write
  and re-validate an ``ingestion_draft`` row freely — normalize, human correction,
  re-validation. Nothing here is immutable; nothing reaches the ledger.
- **Promote (the one irreversible boundary):** ``promote`` runs the full gate and,
  only if every assertion holds, writes the immutable ``Snapshot`` (via the pure
  ``freeze_snapshot`` ledger factory) AND its ``snapshot_source`` provenance row in
  ONE transaction, then flips the draft to ``promoted``. Any failed assertion raises
  ``PromotionBlocked`` BEFORE any write, so a blocked promote persists nothing — the
  ingestion analog of ``save_report``'s ``NonStorableReportError`` guard. Promote is
  one-way; irreversibility lives in ``block_mutation()``, not in this code.

The surrogate int ``stock.id`` is resolved and used ONLY inside this adapter
(ADR-013 guardrail); registering a stock is the aggregate's job (SqlStockRepository),
so promote requires the stock to already exist and resolves it by ISIN here.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from research_platform.domain.ingestion import (
    ClaimedPeriod,
    PromotionBlocked,
    RawExtraction,
    SourceKind,
    ValidationReport,
    assert_promotable,
    parse_period,
    prepare,
    raw_payload_hash,
    validate,
)
from research_platform.domain.ledger import freeze_snapshot
from research_platform.domain.models import Snapshot as DomainSnapshot
from research_platform.domain.version import CODE_VERSION
from research_platform.storage import models as orm
from research_platform.storage.db import make_session_factory


class UnknownDraftError(RuntimeError):
    """Raised when a draft id does not exist."""


def _to_domain_snapshot(row: orm.Snapshot) -> DomainSnapshot:
    return DomainSnapshot(
        id=row.id,
        stock_id=row.stock_id,
        as_of=row.as_of,
        kind=row.kind,
        inputs=row.inputs,
        source_versions=row.source_versions or {},
        code_version=row.code_version,
        content_hash=row.content_hash,
        created_at=row.created_at,
    )


class SqlIngestionRepository:
    """Concrete ingestion persistence. Construct with a session factory (or default)."""

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory or make_session_factory()

    # --- staging (MUTABLE) -------------------------------------------------
    def create_draft(
        self,
        extraction: RawExtraction,
        *,
        ingested_by: str,
        proposed_isin: str | None = None,
        proposed_ticker: str | None = None,
        proposed_name: str | None = None,
    ) -> tuple[int, ValidationReport]:
        """Normalize + validate ``extraction`` and persist a mutable draft.

        Returns ``(draft_id, report)``. The draft holds the retained raw payload +
        its hash, the proposed inputs, and the validation results — all mutable until
        promote. Period is parsed best-effort (a malformed period is stored as NULL
        and the promote gate will block it).
        """
        proposal, report = prepare(extraction)
        as_of, kind, _ = parse_period(extraction.claimed_period)
        with self._session_factory() as session:
            row = orm.IngestionDraft(
                stock_id=None,
                proposed_isin=proposed_isin,
                proposed_ticker=proposed_ticker,
                proposed_name=proposed_name,
                as_of=as_of,
                kind=kind.value if kind is not None else None,
                raw_payload=extraction.raw_payload,
                raw_payload_hash=raw_payload_hash(extraction.raw_payload),
                present_values=proposal.present_values,
                proposed_inputs=report.proposed_inputs,
                validation=report.model_dump(mode="json"),
                source_kind=extraction.source_kind.value,
                source_doc_ref=extraction.source_doc_ref,
                ingested_by=ingested_by,
                ingested_at=dt.datetime.now(dt.timezone.utc),
                status="draft",
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.id, report

    def get_draft(self, draft_id: int) -> orm.IngestionDraft | None:
        with self._session_factory() as session:
            return session.get(orm.IngestionDraft, draft_id)

    def update_draft(
        self,
        draft_id: int,
        *,
        present_values: dict[str, str],
        as_of: str | None,
        kind: str | None,
    ) -> ValidationReport:
        """Human correction: overwrite the declared present values/period, re-validate.

        Mutates the draft in place (it is staging, not the ledger). ``present_values``
        is the editable canonical_key -> raw-string surface; re-running the same
        validation keeps the live verdict and the float cache in step.
        """
        period = ClaimedPeriod(as_of=as_of, kind=kind)
        report = validate(present_values, period)
        parsed_as_of, parsed_kind, _ = parse_period(period)
        with self._session_factory() as session:
            row = session.get(orm.IngestionDraft, draft_id)
            if row is None:
                raise UnknownDraftError(f"no ingestion_draft with id {draft_id}")
            row.present_values = present_values
            row.proposed_inputs = report.proposed_inputs
            row.validation = report.model_dump(mode="json")
            row.as_of = parsed_as_of
            row.kind = parsed_kind.value if parsed_kind is not None else None
            session.commit()
            return report

    # --- promote (THE irreversible boundary) -------------------------------
    def promote(
        self, draft_id: int, *, ingestion_code_version: str = CODE_VERSION
    ) -> DomainSnapshot:
        """Promote a validated draft into the immutable ledger. Gate or nothing.

        Asserts, BEFORE any write: (1) no derived-named key (ADR-013), (2) every
        value numeric, (3) period valid, (4) source provenance complete, (5) the
        reproducibility gate — the retained raw payload re-hashes to the stored hash
        AND ``content_hash`` recomputes via ``freeze_snapshot`` inside the txn. On
        success: writes the immutable Snapshot + its snapshot_source row in ONE
        transaction and flips the draft to ``promoted``. One-way: a non-draft draft
        is refused (idempotent-safe).
        """
        with self._session_factory() as session:
            draft = session.get(orm.IngestionDraft, draft_id)
            if draft is None:
                raise UnknownDraftError(f"no ingestion_draft with id {draft_id}")
            if draft.status != "draft":
                raise PromotionBlocked(
                    f"draft {draft_id} is {draft.status!r}, not promotable (one-way)"
                )

            # Pure gate (1-4): forbidden shadow, numeric, period, provenance.
            period = ClaimedPeriod(
                as_of=draft.as_of.isoformat() if draft.as_of else None,
                kind=draft.kind,
            )
            as_of, kind, inputs = assert_promotable(
                present_values=draft.present_values,
                period=period,
                source_kind=SourceKind(draft.source_kind) if draft.source_kind else None,
                source_doc_ref=draft.source_doc_ref,
                raw_payload_hash_value=draft.raw_payload_hash,
                ingested_by=draft.ingested_by,
                ingestion_code_version=ingestion_code_version,
            )

            # (5) Reproducibility gate: the retained payload must re-hash to the
            # stored anchor — a tampered payload blocks the promote.
            if raw_payload_hash(draft.raw_payload) != draft.raw_payload_hash:
                raise PromotionBlocked(
                    f"draft {draft_id}: raw_payload_hash mismatch — payload not reproducible"
                )

            # Resolve the stock int PK (it must already be registered via the aggregate).
            stock_row = None
            if draft.proposed_isin:
                stock_row = session.scalar(
                    select(orm.Stock).where(orm.Stock.isin == draft.proposed_isin)
                )
            elif draft.stock_id is not None:
                stock_row = session.get(orm.Stock, draft.stock_id)
            if stock_row is None:
                raise PromotionBlocked(
                    f"draft {draft_id}: no registered stock "
                    f"(isin={draft.proposed_isin!r}) — register via the aggregate first"
                )

            # Freeze via the pure ledger factory — content_hash recomputed in-txn.
            snap = freeze_snapshot(
                stock_id=stock_row.id,
                as_of=as_of,
                kind=kind,
                inputs=inputs,  # freshly re-validated at the gate, not a stale cache
                source_versions={},
                code_version=ingestion_code_version,
            )
            snap_row = orm.Snapshot(
                stock_id=snap.stock_id,
                as_of=snap.as_of,
                kind=snap.kind,
                inputs=snap.inputs,
                source_versions=snap.source_versions,
                code_version=snap.code_version,
                content_hash=snap.content_hash,
            )
            session.add(snap_row)
            session.flush()  # assign the snapshot int PK

            # Restatement: a prior snapshot for the same (stock, as_of, kind) is
            # superseded by this one (append-only — the old row persists, immutable).
            prior_id = session.scalar(
                select(func.max(orm.Snapshot.id)).where(
                    orm.Snapshot.stock_id == stock_row.id,
                    orm.Snapshot.as_of == as_of,
                    orm.Snapshot.kind == kind,
                    orm.Snapshot.id != snap_row.id,
                )
            )

            session.add(
                orm.SnapshotSource(
                    snapshot_id=snap_row.id,
                    source_kind=draft.source_kind,
                    source_doc_ref=draft.source_doc_ref,
                    raw_payload_hash=draft.raw_payload_hash,
                    ingested_at=draft.ingested_at or dt.datetime.now(dt.timezone.utc),
                    ingested_by=draft.ingested_by,
                    ingestion_code_version=ingestion_code_version,
                    supersedes_snapshot_id=prior_id,
                )
            )

            draft.status = "promoted"
            draft.promoted_snapshot_id = snap_row.id

            session.commit()
            session.refresh(snap_row)
            return _to_domain_snapshot(snap_row)
