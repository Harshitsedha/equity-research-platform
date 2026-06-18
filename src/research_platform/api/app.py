"""FastAPI application factory.

``create_app`` assembles the read-only HTTP interface (Phase 2b-api). No auth is
wired — single-operator local for now (noted, not built); a later sub-phase owns
authentication. No write routes are registered (HARD RULE 2).
"""

from __future__ import annotations

from fastapi import FastAPI

from research_platform.api.routes.stocks import router as stocks_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Equity Research Platform API",
        version="0.1.0",
        summary="Read-only HTTP interface over the Stock aggregate (Phase 2b-api).",
    )
    app.include_router(stocks_router)
    return app
