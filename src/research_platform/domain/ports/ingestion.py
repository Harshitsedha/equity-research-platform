"""IngestionSourceAdapter — the stable ingestion PORT (Phase 0, ADR-017).

The domain owns this contract; ``ingestion`` implements it. An adapter's ONLY job
is to turn a source-specific blob into a neutral ``RawExtraction`` — no canonical
mapping, no validation, no persistence (those are the shared, source-agnostic
pipeline in ``domain.ingestion`` + the gated promote in ``storage``). This is the
seam that lets the future Screener adapter slot in with zero pipeline change: it is
one more ``read`` implementation behind this same Protocol.

This SUPERSEDES the legacy ``DocumentPort`` for fundamentals (ADR-017): that port
raises on absence, writes a derived key, and has no staging/promote gate — it is
marked for retirement, not extended.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from research_platform.domain.ingestion import RawExtraction


@runtime_checkable
class IngestionSourceAdapter(Protocol):
    """Turn a source-specific payload into a neutral ``RawExtraction``."""

    def read(self, payload: dict) -> RawExtraction:
        """Wrap ``payload`` into a ``RawExtraction`` verbatim.

        Implementations MUST NOT interpret, map, or validate — absence stays
        ``None`` (distinct from ``"0"``), values stay strings, the period stays
        raw. Normalization and validation are the shared pipeline's job.
        """
        ...
