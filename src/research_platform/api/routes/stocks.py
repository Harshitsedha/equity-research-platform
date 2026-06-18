"""Read-only Stock endpoints (HARD RULE 2: GET only — no write routes exist).

Stocks are addressed by ISIN in the URL; the surrogate int PK never appears in a
path or a response (HARD RULE 4). Snapshots are int-addressed by design (they are
append-only ledger leaves the domain already references by int).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from research_platform.api.dependencies import (
    get_snapshot_repository,
    get_stock_repository,
)
from research_platform.api.schemas import (
    SnapshotLeafResponse,
    StockResponse,
    StockSummaryResponse,
)
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
