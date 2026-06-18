"""The composition root — the ONE place adapters are wired to ports.

This is the only module that knows all the concrete implementations at once. The
domain depends on Protocols; here we hand it the real adapters. Swapping an
adapter (e.g. the stub LLM for the real Claude adapter in Part B) is a change
confined to this file. Nothing imports the composition root except entrypoints
(scripts / the worker / tests).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from research_platform.domain.ports.documents import DocumentPort
from research_platform.domain.ports.llm import LLMPort
from research_platform.domain.ports.repository import RepositoryPort
from research_platform.domain.ports.stock_repository import StockRepository
from research_platform.domain.ports.valuation import ValuationPort
from research_platform.ingestion.document_adapter import FileDocumentAdapter
from research_platform.ingestion.llm.stub_adapter import StubLLMAdapter
from research_platform.storage.db import make_session_factory
from research_platform.storage.repository import PostgresRepository
from research_platform.storage.stock_repository import SqlStockRepository
from research_platform.valuation.dcf import DCFModel


def _select_llm() -> LLMPort:
    """Provider-agnostic LLM selection. Defaults to the stub so the default test
    run never touches the network. Set ``LLM_PROVIDER=claude`` to use Claude.
    """
    provider = os.environ.get("LLM_PROVIDER", "stub").lower()
    if provider == "claude":
        from research_platform.ingestion.llm.claude_adapter import ClaudeLLMAdapter

        return ClaudeLLMAdapter()  # reads ANTHROPIC_API_KEY; fails loudly if missing
    return StubLLMAdapter()


@dataclass(frozen=True)
class Platform:
    """The wired application: ports, satisfied by concrete adapters."""

    documents: DocumentPort
    repository: RepositoryPort
    stock_repository: StockRepository
    valuation: ValuationPort
    llm: LLMPort


def build_platform(
    repository: RepositoryPort | None = None,
    stock_repository: StockRepository | None = None,
    llm: LLMPort | None = None,
) -> Platform:
    """Wire concrete adapters to the domain's ports.

    ``repository``, ``stock_repository`` and ``llm`` can be injected (tests, or
    selecting a stub mode); otherwise the defaults (PostgresRepository,
    SqlStockRepository, deterministic StubLLMAdapter in GOOD mode) are used.

    Single connection-config path: when EITHER repository is defaulted, one
    session factory is built here and handed to both, so the storage adapters
    never self-construct a second DB-config/connection path (HARD RULE 3). Both
    repositories are session-per-operation, so the wired Platform is safe to
    treat as an app-lifetime singleton.
    """
    session_factory = (
        make_session_factory()
        if repository is None or stock_repository is None
        else None
    )
    return Platform(
        documents=FileDocumentAdapter(),
        repository=repository or PostgresRepository(session_factory),
        stock_repository=stock_repository or SqlStockRepository(session_factory),
        valuation=DCFModel(),
        llm=llm or _select_llm(),
    )
