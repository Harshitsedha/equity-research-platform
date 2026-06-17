"""LLMPort — the interface the domain uses to obtain a structured report draft.

The domain depends on THIS Protocol, never on a concrete model (ADR-004/010).
The adapter (a deterministic stub in Part A; Claude in Part B) turns snapshot
inputs + a schema spec into a ``StructuredReportDraft`` of checkable claims — it
emits claims, never trusted prose or final arithmetic.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from research_platform.domain.models import StructuredReportDraft


@runtime_checkable
class LLMPort(Protocol):
    #: Stable identifier of the model, recorded for reproducibility.
    model_name: str

    def generate_report_draft(
        self, snapshot_inputs: dict, schema_spec: dict
    ) -> StructuredReportDraft:
        """Produce a draft of structured claims for the given snapshot inputs.

        ``schema_spec`` (see ``domain.report_spec.build_schema_spec``) tells the
        adapter the identity to stamp, the required sections, and the menu of
        recomputable metrics. The result is *unverified* until the harness checks
        it.
        """
        ...
