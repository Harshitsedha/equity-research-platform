"""ValuationPort — the interface for deterministic valuation models.

Implementations live in ``valuation`` and MUST be pure functions: identical
``inputs`` + ``assumptions`` always yield an identical ``result`` (ADR-004,
reproducibility). No I/O, no randomness, no clock.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ValuationPort(Protocol):
    #: Stable identifier of the model, stored on the ValuationRun.
    model_name: str

    def run(self, inputs: dict, assumptions: dict) -> dict:
        """Compute a valuation from frozen ``inputs`` and ``assumptions``.

        ``inputs`` come from an immutable Snapshot; ``assumptions`` are the
        named levers. The return value must be deterministic.
        """
        ...
