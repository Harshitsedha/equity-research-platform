"""DocumentPort — the interface the domain uses to read external documents.

The domain owns this contract; ``ingestion`` implements it. All file/parse
fragility is quarantined behind this Protocol (ADR-004).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from research_platform.domain.models import ParsedDocument


@runtime_checkable
class DocumentPort(Protocol):
    """Parse an uploaded file into a normalized ``ParsedDocument``."""

    def parse(self, source: str | Path) -> ParsedDocument:
        """Read ``source`` (a local file path) and normalize it.

        Implementations MUST fail loudly on malformed/incomplete input rather
        than silently filling defaults (ADR-005: loud failure on bad data).
        """
        ...
