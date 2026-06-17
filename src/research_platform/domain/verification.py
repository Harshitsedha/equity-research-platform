"""The verification harness — THE HEART (ADR-010). Pure domain logic.

Given a ``StructuredReportDraft`` and the ``Snapshot`` it references, run three
independent checks and return a ``VerificationResult``:

(a) NUMERIC      — recompute each NUMERIC claim's metric from the snapshot's raw
                   inputs and compare to the asserted value within tolerance.
                   A mismatch (or an un-recomputable metric) => claim FAILED.
(b) CITATION     — FACTUAL/NUMERIC claims must cite a key/path that actually
                   exists in the snapshot inputs. Missing/dangling => FLAGGED.
                   (No semantic support-checking — deferred per ADR-010.)
(c) COMPLETENESS — every REQUIRED_SECTION must carry >= 1 claim; else INCOMPLETE.

Failure policy (mixed, enforced exactly):
  * ANY numeric mismatch  => HARD_FAILED  (report NOT stored).
  * citation flags / missing sections => stored, but surfaced for human judgment.
Numeric correctness is non-negotiable; the rest is flagged, not fatal.
"""

from __future__ import annotations

import math

from research_platform.domain.metrics import MetricError, compute_metric
from research_platform.domain.models import (
    Claim,
    ClaimStatus,
    ClaimType,
    ClaimVerification,
    OverallStatus,
    Snapshot,
    StructuredReportDraft,
    VerificationResult,
)
from research_platform.domain.report_spec import REQUIRED_SECTIONS
from research_platform.domain.version import CODE_VERSION

#: Numeric agreement tolerance. The platform and a faithful adapter use the same
#: rounding, so this is tight; it only absorbs float representation noise.
_REL_TOL = 1e-6
_ABS_TOL = 1e-9


def _values_match(asserted: float, computed: float) -> bool:
    return math.isclose(asserted, computed, rel_tol=_REL_TOL, abs_tol=_ABS_TOL)


def is_storable_verification(verification: dict) -> bool:
    """Single source of truth for storability, usable from a serialized result.

    The storage layer calls this to enforce the FLAG-AND-STORE / HARD-FAIL policy
    at the persistence boundary (it sees a JSONB dict, not a VerificationResult).
    A draft is storable iff its overall status is not ``HARD_FAILED``.
    """
    return verification.get("overall") != OverallStatus.HARD_FAILED.value


def citation_exists(inputs: dict, citation: str | None) -> bool:
    """True iff ``citation`` (a dotted key path) resolves within ``inputs``.

    Existence only — NOT semantic support. ``"a.b"`` resolves nested dicts.
    """
    if not citation:
        return False
    node: object = inputs
    for part in citation.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return False
    return True


def _verify_numeric(claim: Claim, inputs: dict) -> ClaimVerification:
    # (a) independent recomputation — the non-negotiable check.
    if claim.metric_key is None or claim.asserted_value is None:
        return ClaimVerification(
            claim_id=claim.id,
            claim_type=claim.claim_type,
            status=ClaimStatus.FAILED,
            detail="NUMERIC claim missing metric_key or asserted_value",
            asserted_value=claim.asserted_value,
            citation=claim.citation,
        )
    try:
        computed = compute_metric(claim.metric_key, inputs)
    except MetricError as exc:
        return ClaimVerification(
            claim_id=claim.id,
            claim_type=claim.claim_type,
            status=ClaimStatus.FAILED,
            detail=f"cannot recompute {claim.metric_key!r}: {exc}",
            asserted_value=claim.asserted_value,
            citation=claim.citation,
        )

    if not _values_match(claim.asserted_value, computed):
        return ClaimVerification(
            claim_id=claim.id,
            claim_type=claim.claim_type,
            status=ClaimStatus.FAILED,
            detail=(
                f"numeric mismatch for {claim.metric_key!r}: "
                f"asserted {claim.asserted_value}, computed {computed}"
            ),
            asserted_value=claim.asserted_value,
            computed_value=computed,
            citation=claim.citation,
        )

    # (b) value is correct; citation must still ground it in an existing input.
    if not citation_exists(inputs, claim.citation):
        return ClaimVerification(
            claim_id=claim.id,
            claim_type=claim.claim_type,
            status=ClaimStatus.FLAGGED,
            detail=(
                f"value verified, but citation {claim.citation!r} is "
                "missing or does not exist in snapshot inputs"
            ),
            asserted_value=claim.asserted_value,
            computed_value=computed,
            citation=claim.citation,
        )

    return ClaimVerification(
        claim_id=claim.id,
        claim_type=claim.claim_type,
        status=ClaimStatus.VERIFIED,
        detail=f"value matches recomputed {claim.metric_key} = {computed}",
        asserted_value=claim.asserted_value,
        computed_value=computed,
        citation=claim.citation,
    )


def _verify_factual(claim: Claim, inputs: dict) -> ClaimVerification:
    # (b) citation-existence only.
    if citation_exists(inputs, claim.citation):
        status, detail = ClaimStatus.VERIFIED, f"citation {claim.citation!r} exists"
    else:
        status, detail = (
            ClaimStatus.FLAGGED,
            f"citation {claim.citation!r} is missing or dangling",
        )
    return ClaimVerification(
        claim_id=claim.id,
        claim_type=claim.claim_type,
        status=status,
        detail=detail,
        citation=claim.citation,
    )


def _verify_claim(claim: Claim, inputs: dict) -> ClaimVerification:
    if claim.claim_type is ClaimType.NUMERIC:
        return _verify_numeric(claim, inputs)
    if claim.claim_type is ClaimType.FACTUAL:
        return _verify_factual(claim, inputs)
    # QUALITATIVE: nothing to recompute or cite.
    return ClaimVerification(
        claim_id=claim.id,
        claim_type=claim.claim_type,
        status=ClaimStatus.VERIFIED,
        detail="qualitative claim — no numeric or citation check applies",
        citation=claim.citation,
    )


def _missing_sections(draft: StructuredReportDraft) -> list[str]:
    present = {claim.section for claim in draft.claims}
    return [section for section in REQUIRED_SECTIONS if section not in present]


def _decide_overall(
    *, any_numeric_failed: bool, any_flagged: bool, missing: list[str]
) -> OverallStatus:
    if any_numeric_failed:
        return OverallStatus.HARD_FAILED  # wrong is wrong — not stored
    if missing:
        return OverallStatus.INCOMPLETE   # stored, flagged for completion
    if any_flagged:
        return OverallStatus.PASSED_WITH_FLAGS  # stored, citations surfaced
    return OverallStatus.PASSED


def verify_draft(
    draft: StructuredReportDraft,
    snapshot: Snapshot,
    *,
    code_version: str = CODE_VERSION,
) -> VerificationResult:
    """Run all three checks over a draft and apply the mixed failure policy."""
    inputs = snapshot.inputs
    claim_results = [_verify_claim(claim, inputs) for claim in draft.claims]

    any_numeric_failed = any(r.status is ClaimStatus.FAILED for r in claim_results)
    any_flagged = any(r.status is ClaimStatus.FLAGGED for r in claim_results)
    missing = _missing_sections(draft)

    overall = _decide_overall(
        any_numeric_failed=any_numeric_failed,
        any_flagged=any_flagged,
        missing=missing,
    )
    return VerificationResult(
        overall=overall,
        claims=claim_results,
        missing_sections=missing,
        code_version=code_version,
    )
