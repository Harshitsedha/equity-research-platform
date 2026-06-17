"""The report contract: required sections and the schema spec handed to the LLM.

``REQUIRED_SECTIONS`` is the structural-completeness checklist enforced by the
verification harness. ``build_schema_spec`` describes — as a plain dict — exactly
what a draft must contain, so an LLM adapter (stub now, Claude in Part B) knows
which sections and which recomputable metrics to populate. Pure domain logic.
"""

from __future__ import annotations

from research_platform.domain.metrics import METRIC_REQUIREMENTS, NUMERIC_METRIC_KEYS
from research_platform.domain.models import Snapshot

#: Core checklist — every required section must carry at least one claim.
REQUIRED_SECTIONS: tuple[str, ...] = ("business", "financials", "valuation", "risks")

#: Default rendering order of sections in the assembled report.
DEFAULT_SECTION_ORDER: tuple[str, ...] = REQUIRED_SECTIONS


def build_schema_spec(snapshot: Snapshot) -> dict:
    """Build the contract dict an ``LLMPort`` is asked to satisfy for a snapshot.

    Carries the identity (so the returned draft can be tied back) and the menu of
    independently-recomputable metrics with their input dependencies.
    """
    return {
        "stock_id": snapshot.stock_id,
        "snapshot_id": snapshot.id,
        "required_sections": list(REQUIRED_SECTIONS),
        "section_order": list(DEFAULT_SECTION_ORDER),
        "numeric_metrics": [
            {"metric_key": key, "requires": list(METRIC_REQUIREMENTS[key])}
            for key in NUMERIC_METRIC_KEYS
        ],
    }
