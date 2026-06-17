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
from research_platform.domain.ports.valuation import ValuationPort
from research_platform.ingestion.document_adapter import FileDocumentAdapter
from research_platform.ingestion.llm.stub_adapter import StubLLMAdapter
from research_platform.storage.repository import PostgresRepository
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
    valuation: ValuationPort
    llm: LLMPort


def build_platform(
    repository: RepositoryPort | None = None,
    llm: LLMPort | None = None,
) -> Platform:
    """Wire concrete adapters to the domain's ports.

    ``repository`` and ``llm`` can be injected (tests, or selecting a stub mode);
    otherwise the defaults (PostgresRepository, deterministic StubLLMAdapter in
    GOOD mode) are used. Part B swaps the LLM default for the real Claude adapter.
    """
    return Platform(
        documents=FileDocumentAdapter(),
        repository=repository or PostgresRepository(),
        valuation=DCFModel(),
        llm=llm or _select_llm(),
    )
