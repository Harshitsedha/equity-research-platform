"""Report assembly + the generate->verify->persist pipeline. Pure domain logic.

Prose is rendered deterministically FROM verified claims (simple templating — no
LLM prose in Part A). The full ``VerificationResult`` is attached to every stored
report for auditability. A HARD_FAILED draft is never assembled or stored; the
storage layer independently refuses it too (defence in depth).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from research_platform.domain.models import (
    ClaimStatus,
    Report,
    Snapshot,
    StructuredReportDraft,
    VerificationResult,
)
from research_platform.domain.ports.llm import LLMPort
from research_platform.domain.ports.repository import RepositoryPort
from research_platform.domain.report_spec import REQUIRED_SECTIONS, build_schema_spec
from research_platform.domain.verification import verify_draft
from research_platform.domain.version import CODE_VERSION


class ReportPipelineResult(BaseModel):
    """Outcome of the pipeline: the verdict, and the stored report (if any)."""

    model_config = ConfigDict(frozen=True)

    verification: VerificationResult
    report: Report | None
    stored: bool
    idempotent: bool = False


def _ordered_sections(draft: StructuredReportDraft) -> list[str]:
    order = list(draft.section_order) if draft.section_order else list(REQUIRED_SECTIONS)
    for claim in draft.claims:  # append any sections not already in the order
        if claim.section not in order:
            order.append(claim.section)
    return order


def assemble_report(
    draft: StructuredReportDraft,
    verification: VerificationResult,
    snapshot: Snapshot,
    *,
    kind: str,
    code_version: str = CODE_VERSION,
    model_version: str | None = None,
    prompt_version: str | None = None,
) -> Report:
    """Render an immutable Report from a (storable) verified draft.

    Claims reaching here are VERIFIED or FLAGGED — never FAILED (a hard fail is
    not storable, so it never gets assembled).
    """
    status_by_id = {c.claim_id: c for c in verification.claims}

    sections: dict[str, dict] = {}
    for section in _ordered_sections(draft):
        section_claims = [c for c in draft.claims if c.section == section]
        rendered_claims = []
        prose_parts = []
        for claim in section_claims:
            cv = status_by_id.get(claim.id)
            status = cv.status if cv else ClaimStatus.VERIFIED
            suffix = " [UNVERIFIED CITATION]" if status is ClaimStatus.FLAGGED else ""
            prose_parts.append(f"{claim.statement}{suffix}")
            rendered_claims.append(
                {
                    "id": claim.id,
                    "claim_type": claim.claim_type.value,
                    "statement": claim.statement,
                    "status": status.value,
                    "metric_key": claim.metric_key,
                    "asserted_value": claim.asserted_value,
                    "computed_value": cv.computed_value if cv else None,
                    "citation": claim.citation,
                }
            )
        sections[section] = {"prose": " ".join(prose_parts), "claims": rendered_claims}

    content = {
        "kind": kind,
        "section_order": _ordered_sections(draft),
        "sections": sections,
    }

    return Report(
        stock_id=draft.stock_id,
        snapshot_id=draft.snapshot_id,
        kind=kind,
        content=content,
        verification=verification.model_dump(mode="json"),
        code_version=code_version,
        model_version=model_version,
        prompt_version=prompt_version,
    )


def run_report_pipeline(
    snapshot: Snapshot,
    *,
    llm: LLMPort,
    repo: RepositoryPort,
    kind: str = "initiation",
    code_version: str = CODE_VERSION,
) -> ReportPipelineResult:
    """generate (via port) -> verify -> (if storable) assemble + persist.

    Idempotent via ``snapshot_id``: a report already stored for the snapshot is
    returned as-is rather than duplicated. A HARD_FAILED verdict returns
    ``stored=False`` with no report — the caller (e.g. the arq job) treats this as
    a terminal, loud failure.
    """
    if snapshot.id is None:
        raise ValueError("cannot run report pipeline on an unsaved snapshot")

    spec = build_schema_spec(snapshot)
    draft = llm.generate_report_draft(snapshot.inputs, spec)
    verification = verify_draft(draft, snapshot, code_version=code_version)

    if not verification.is_storable:
        return ReportPipelineResult(
            verification=verification, report=None, stored=False
        )

    existing = repo.get_report_by_snapshot(snapshot.id)
    if existing is not None:
        return ReportPipelineResult(
            verification=verification, report=existing, stored=True, idempotent=True
        )

    # Generic provenance: read the optional version attributes the LLM adapter may
    # expose. The LLMPort only guarantees ``model_name``; vendor adapters (e.g. the
    # Claude adapter) additionally carry model/prompt versions. Stub => None.
    report = assemble_report(
        draft,
        verification,
        snapshot,
        kind=kind,
        code_version=code_version,
        model_version=getattr(llm, "model_version", None),
        prompt_version=getattr(llm, "prompt_version", None),
    )
    saved = repo.save_report(report)
    return ReportPipelineResult(verification=verification, report=saved, stored=True)
