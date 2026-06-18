"""SqlThesisRepository — persists the immutable Thesis aggregate-member (Phase 3a).

Writes a thesis and all its assumptions in ONE transaction. The frozen anchor
facts are guarded at persist time (ADR-015, amendment 2): a thesis can never be
stored with an ``anchor_value_per_share`` / ``anchor_snapshot_id`` inconsistent
with the ``valuation_run`` it cites. That integrity check lives HERE (the repo can
read the run); anchor *ownership* (the run's snapshot belongs to this stock) is
enforced one layer up, in the domain ``Stock.record_thesis``.

``load_thesis_history`` is the single projection of a stock's thesis history,
ordered by ``(recorded_at, id)`` — the same order the domain keeps on insert. It
is shared: ``SqlStockRepository._to_domain`` calls it so a loaded aggregate
exposes ``thesis_history`` / ``active_thesis`` without duplicating the mapping.
"""

from __future__ import annotations

import uuid as uuid_lib

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from research_platform.domain.thesis import Thesis, ThesisAssumption, ToleranceBand
from research_platform.storage import models as orm
from research_platform.storage.db import make_session_factory


class ThesisAnchorMismatchError(RuntimeError):
    """Raised when a thesis's frozen anchor facts disagree with the cited run.

    The integrity guard for ADR-015's freeze: a stored anchor value/snapshot must
    match the ``valuation_run`` it cites, so a frozen anchor can never be
    persisted inconsistent with its provenance.
    """


class UnknownStockError(RuntimeError):
    """Raised when recording a thesis for a stock uuid that does not exist."""


def _to_domain_thesis(session: Session, row: orm.Thesis) -> Thesis:
    assumption_rows = session.scalars(
        select(orm.ThesisAssumption)
        .where(orm.ThesisAssumption.thesis_id == row.id)
        .order_by(orm.ThesisAssumption.id)
    ).all()
    assumptions = [
        ThesisAssumption(
            name=a.name,
            metric_key=a.metric_key,
            recorded_value=a.recorded_value,
            band=ToleranceBand(lower=a.lower, upper=a.upper),
        )
        for a in assumption_rows
    ]
    return Thesis(
        id=row.uuid,
        recorded_at=row.recorded_at,
        anchor_valuation_run_id=row.anchor_valuation_run_id,
        anchor_snapshot_id=row.anchor_snapshot_id,
        anchor_value_per_share=row.anchor_value_per_share,
        analyst_target=row.analyst_target,
        override_rationale=row.override_rationale,
        assumptions=assumptions,
        summary=row.summary,
        bull=row.bull,
        bear=row.bear,
        created_at=row.created_at,
    )


def load_thesis_history(session: Session, stock_id: int) -> list[Thesis]:
    """Project a stock's append-only thesis history, ordered ``(recorded_at, id)``.

    The SAME order the domain keeps on insert, so the active thesis (the last
    element) is identical in memory and after a round-trip.
    """
    rows = session.scalars(
        select(orm.Thesis)
        .where(orm.Thesis.stock_id == stock_id)
        .order_by(orm.Thesis.recorded_at, orm.Thesis.id)
    ).all()
    return [_to_domain_thesis(session, row) for row in rows]


class SqlThesisRepository:
    """Concrete thesis persistence. Construct with a session factory (or default)."""

    def __init__(self, session_factory: sessionmaker[Session] | None = None) -> None:
        self._session_factory = session_factory or make_session_factory()

    def record(self, stock_id: uuid_lib.UUID, thesis: Thesis) -> Thesis:
        """Persist ``thesis`` (and its assumptions) for the stock identified by
        ``stock_id`` (the aggregate UUID), in one transaction.

        Asserts the frozen anchor facts against the cited run BEFORE writing
        (ADR-015): the run must exist, and its ``snapshot_id`` and
        ``result["value_per_share"]`` must equal the thesis's recorded anchor
        facts — else ``ThesisAnchorMismatchError`` and nothing is written.
        """
        with self._session_factory() as session:
            stock_row = session.scalar(
                select(orm.Stock).where(orm.Stock.uuid == stock_id)
            )
            if stock_row is None:
                raise UnknownStockError(f"no stock with uuid {stock_id}")

            run = session.get(orm.ValuationRun, thesis.anchor_valuation_run_id)
            if run is None:
                raise ThesisAnchorMismatchError(
                    f"anchor valuation_run {thesis.anchor_valuation_run_id} "
                    "does not exist"
                )
            run_value = run.result.get("value_per_share")
            if run_value != thesis.anchor_value_per_share:
                raise ThesisAnchorMismatchError(
                    "frozen anchor_value_per_share "
                    f"{thesis.anchor_value_per_share!r} does not match the cited "
                    f"run's output {run_value!r} "
                    f"(run {thesis.anchor_valuation_run_id})"
                )
            if run.snapshot_id != thesis.anchor_snapshot_id:
                raise ThesisAnchorMismatchError(
                    f"frozen anchor_snapshot_id {thesis.anchor_snapshot_id} does "
                    f"not match the cited run's snapshot {run.snapshot_id} "
                    f"(run {thesis.anchor_valuation_run_id})"
                )

            row = orm.Thesis(
                uuid=thesis.id,
                stock_id=stock_row.id,
                anchor_valuation_run_id=thesis.anchor_valuation_run_id,
                anchor_snapshot_id=thesis.anchor_snapshot_id,
                anchor_value_per_share=thesis.anchor_value_per_share,
                analyst_target=thesis.analyst_target,
                override_rationale=thesis.override_rationale,
                recorded_at=thesis.recorded_at,
                summary=thesis.summary,
                bull=thesis.bull,
                bear=thesis.bear,
            )
            session.add(row)
            session.flush()  # assign the thesis int PK for the assumption FKs

            for a in thesis.assumptions:
                session.add(
                    orm.ThesisAssumption(
                        thesis_id=row.id,
                        name=a.name,
                        metric_key=a.metric_key,
                        recorded_value=a.recorded_value,
                        lower=a.band.lower,
                        upper=a.band.upper,
                    )
                )

            session.commit()
            session.refresh(row)
            return _to_domain_thesis(session, row)
