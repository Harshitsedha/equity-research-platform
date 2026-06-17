"""The composition root — the ONE place adapters are wired to ports.

This is the only module that knows all the concrete implementations at once. The
domain depends on Protocols; here we hand it the real adapters. Swapping an
adapter (e.g. a different document parser or DB) is a change confined to this
file. Nothing imports the composition root except entrypoints (scripts/tests).
"""

from __future__ import annotations

from dataclasses import dataclass

from research_platform.domain.ports.documents import DocumentPort
from research_platform.domain.ports.repository import RepositoryPort
from research_platform.domain.ports.valuation import ValuationPort
from research_platform.ingestion.document_adapter import FileDocumentAdapter
from research_platform.storage.repository import PostgresRepository
from research_platform.valuation.dcf import DCFModel


@dataclass(frozen=True)
class Platform:
    """The wired application: ports, satisfied by concrete adapters."""

    documents: DocumentPort
    repository: RepositoryPort
    valuation: ValuationPort


def build_platform(repository: RepositoryPort | None = None) -> Platform:
    """Wire concrete adapters to the domain's ports.

    ``repository`` can be injected (e.g. tests with a custom session factory);
    otherwise the default PostgresRepository (DATABASE_URL) is used.
    """
    return Platform(
        documents=FileDocumentAdapter(),
        repository=repository or PostgresRepository(),
        valuation=DCFModel(),
    )
