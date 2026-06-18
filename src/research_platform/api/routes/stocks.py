"""Read-only Stock endpoints (HARD RULE 2: GET only — no write routes exist).

Stocks are addressed by ISIN in the URL; the surrogate int PK never appears in a
path or a response (HARD RULE 4). Snapshots are int-addressed by design (they are
append-only ledger leaves the domain already references by int).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from research_platform.api.dependencies import (
    get_snapshot_repository,
    get_stock_repository,
)
from research_platform.api.schemas import (
    SnapshotLeafResponse,
    StockResponse,
    StockSummaryResponse,
    ThesisDriftResponse,
)
from research_platform.domain.drift import compute_thesis_drift
from research_platform.domain.ports.repository import RepositoryPort
from research_platform.domain.ports.stock_repository import StockRepository
from research_platform.domain.stock import CoverageStatus

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("", response_model=list[StockSummaryResponse])
def list_stocks(
    status: CoverageStatus | None = None,
    stocks: StockRepository = Depends(get_stock_repository),
) -> list[StockSummaryResponse]:
    """List stocks, optionally filtered by coverage ``status``.

    Invalid ``status`` values are rejected by FastAPI (422) via the enum.
    """
    aggregates = stocks.list()
    if status is not None:
        aggregates = [s for s in aggregates if s.status is status]
    return [StockSummaryResponse.from_aggregate(s) for s in aggregates]


@router.get("/{isin}", response_model=StockResponse)
def get_stock(
    isin: str,
    stocks: StockRepository = Depends(get_stock_repository),
) -> StockResponse:
    """The full aggregate view for one stock; 404 on unknown ISIN."""
    stock = stocks.get_by_isin(isin)
    if stock is None:
        raise HTTPException(status_code=404, detail=f"no stock with isin {isin!r}")
    return StockResponse.from_aggregate(stock)


@router.get("/{isin}/snapshots/{snapshot_id}", response_model=SnapshotLeafResponse)
def get_snapshot_leaf(
    isin: str,
    snapshot_id: int,
    stocks: StockRepository = Depends(get_stock_repository),
    snapshots: RepositoryPort = Depends(get_snapshot_repository),
) -> SnapshotLeafResponse:
    """A single immutable snapshot leaf owned by this stock.

    404 if the ISIN is unknown, OR if the snapshot is not on THIS stock's
    timeline — ownership is enforced via the aggregate, not mere existence, and
    without ever touching the stock's int PK.
    """
    stock = stocks.get_by_isin(isin)
    if stock is None:
        raise HTTPException(status_code=404, detail=f"no stock with isin {isin!r}")

    owned_ids = {ref.snapshot_id for ref in stock.snapshot_refs}
    if snapshot_id not in owned_ids:
        raise HTTPException(
            status_code=404,
            detail=f"snapshot {snapshot_id} not found for isin {isin!r}",
        )

    leaf = snapshots.get_snapshot(snapshot_id)
    if leaf is None:  # defensive: the timeline asserted it exists
        raise HTTPException(
            status_code=404, detail=f"snapshot {snapshot_id} not found"
        )
    return SnapshotLeafResponse.from_snapshot(leaf, isin=isin)


@router.get("/{isin}/drift", response_model=ThesisDriftResponse)
def get_thesis_drift(
    isin: str,
    response: Response,
    stocks: StockRepository = Depends(get_stock_repository),
    snapshots: RepositoryPort = Depends(get_snapshot_repository),
) -> ThesisDriftResponse:
    """Drift of the active thesis against the current snapshot — a COMPUTED view.

    Drift is never stored (ADR-015): this recomputes on every call via the pure
    ``compute_thesis_drift``, so the response is marked ``Cache-Control: no-store``
    — the transport analog of the never-frozen guarantee.

    404 only on an unknown ISIN. The other states are real read results, all 200:
    no active thesis, or a thesis with no current snapshot to resolve against
    (both yield ``drift=null``, distinguished by ``thesis_present``); an empty /
    partial current snapshot yields a full projection with UNRESOLVED assumptions.
    The two-step load mirrors ``get_snapshot_leaf``; orchestration is inline and
    the domain stays pure (no service layer).
    """
    # Drift is recomputed per call and must never be cached (ADR-015 at transport).
    response.headers["Cache-Control"] = "no-store"

    stock = stocks.get_by_isin(isin)
    if stock is None:
        raise HTTPException(status_code=404, detail=f"no stock with isin {isin!r}")

    thesis = stock.active_thesis
    if thesis is None:
        return ThesisDriftResponse.no_thesis(isin)

    # The anchor ("then") snapshot is owned by this stock (HARD RULE 3), so its
    # as_of is on the already-loaded timeline — no extra query.
    anchor_ref = next(
        (r for r in stock.snapshot_refs if r.snapshot_id == thesis.anchor_snapshot_id),
        None,
    )

    current_ref = stock.latest_snapshot_ref
    if current_ref is None:
        return ThesisDriftResponse.no_current_snapshot(isin, thesis, anchor_ref)

    snapshot = snapshots.get_snapshot(current_ref.snapshot_id)
    if snapshot is None:  # defensive: the timeline asserted it exists
        return ThesisDriftResponse.no_current_snapshot(isin, thesis, anchor_ref)

    projection = compute_thesis_drift(thesis, snapshot.inputs)
    return ThesisDriftResponse.from_projection(
        isin, thesis, anchor_ref, current_ref, projection
    )
