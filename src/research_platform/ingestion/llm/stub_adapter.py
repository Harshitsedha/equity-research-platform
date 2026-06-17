"""Deterministic stub LLMPort — no network, no randomness (Part A only).

Stands in for the real Claude adapter (Part B) so the whole verify -> assemble ->
persist pipeline can be proven end-to-end. It derives claims directly from the
snapshot inputs using the *same* metric code the harness uses, so GOOD mode is
correct by construction.

Crucially the failure modes are SEPARABLE — each injects exactly one defect — so
every verification branch is proven independently rather than tangled:

  GOOD         -> all numbers correct, all citations resolve   -> PASSED
  BAD_NUMERIC  -> one NUMERIC claim with a wrong value         -> HARD_FAILED
  BAD_CITATION -> one NUMERIC claim, correct value, bad cite   -> PASSED_WITH_FLAGS
  INCOMPLETE   -> omits one required section                   -> INCOMPLETE
"""

from __future__ import annotations

import enum

from research_platform.domain.metrics import compute_metric
from research_platform.domain.models import (
    Claim,
    ClaimType,
    StructuredReportDraft,
)

_DANGLING_CITATION = "nonexistent_input_key"


class StubMode(str, enum.Enum):
    GOOD = "good"
    BAD_NUMERIC = "bad_numeric"
    BAD_CITATION = "bad_citation"
    INCOMPLETE = "incomplete"


class StubLLMAdapter:
    """An ``LLMPort`` implementation that fabricates deterministic drafts."""

    model_name: str = "stub-llm-v1"

    def __init__(self, mode: StubMode = StubMode.GOOD) -> None:
        self.mode = StubMode(mode)

    def generate_report_draft(
        self, snapshot_inputs: dict, schema_spec: dict
    ) -> StructuredReportDraft:
        stock_id = schema_spec["stock_id"]
        snapshot_id = schema_spec["snapshot_id"]
        section_order = list(schema_spec.get("section_order", []))

        claims: list[Claim] = []

        # --- business: a FACTUAL claim grounded in an existing input ---------
        if "revenue" in snapshot_inputs:
            claims.append(
                Claim(
                    id="business-1",
                    claim_type=ClaimType.FACTUAL,
                    statement=f"Reported revenue of {snapshot_inputs['revenue']}.",
                    section="business",
                    citation="revenue",
                )
            )
        else:
            claims.append(
                Claim(
                    id="business-1",
                    claim_type=ClaimType.QUALITATIVE,
                    statement="Business overview.",
                    section="business",
                )
            )

        # --- financials: one NUMERIC claim per recomputable metric ----------
        numeric_claims: list[Claim] = []
        for i, metric in enumerate(schema_spec.get("numeric_metrics", [])):
            key = metric["metric_key"]
            requires = metric["requires"]
            if not all(req in snapshot_inputs for req in requires):
                continue
            value = compute_metric(key, snapshot_inputs)  # correct by construction
            numeric_claims.append(
                Claim(
                    id=f"fin-{i}-{key}",
                    claim_type=ClaimType.NUMERIC,
                    statement=f"{key} is {value}.",
                    section="financials",
                    metric_key=key,
                    asserted_value=value,
                    citation=requires[0],  # an input the metric depends on (exists)
                )
            )

        # --- valuation + risks: qualitative (no snapshot arithmetic) ---------
        valuation_claim = Claim(
            id="val-1",
            claim_type=ClaimType.QUALITATIVE,
            statement="Valuation is assessed via a separate deterministic DCF run.",
            section="valuation",
        )
        risks_claim = Claim(
            id="risk-1",
            claim_type=ClaimType.QUALITATIVE,
            statement="Key risks include execution and demand cyclicality.",
            section="risks",
        )

        # --- apply exactly one defect for the chosen mode -------------------
        if self.mode is StubMode.BAD_NUMERIC and numeric_claims:
            target = numeric_claims[0]
            numeric_claims[0] = target.model_copy(
                update={
                    "asserted_value": (target.asserted_value or 0.0) * 2.0 + 1.0,
                    "statement": f"{target.metric_key} is deliberately wrong.",
                }
            )
        elif self.mode is StubMode.BAD_CITATION and numeric_claims:
            target = numeric_claims[0]  # value stays correct; citation dangles
            numeric_claims[0] = target.model_copy(
                update={"citation": _DANGLING_CITATION}
            )

        claims.extend(numeric_claims)
        claims.append(valuation_claim)
        if self.mode is not StubMode.INCOMPLETE:
            claims.append(risks_claim)  # INCOMPLETE omits the 'risks' section

        return StructuredReportDraft(
            stock_id=stock_id,
            snapshot_id=snapshot_id,
            claims=claims,
            section_order=section_order,
        )
