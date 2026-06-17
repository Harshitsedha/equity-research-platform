"""Phase 1B demo — ONE real Claude generation through the verification harness.

Opt-in: requires ANTHROPIC_API_KEY (never hardcode/commit it). No database needed.
Shows the real model's structured claims, the per-claim verification verdict, and
which (if any) were hard-failed or flagged — i.e. whether Claude asserted numbers
the harness catches.

The key is read from the shell environment or a ``.env`` file at the repo root
(loaded here, at the entry point — never inside the domain or adapter modules).

Run:  uv run python scripts/phase1b_demo.py   (ANTHROPIC_API_KEY in .env or env)
"""

from __future__ import annotations

import datetime as dt
import os
import sys

from dotenv import load_dotenv

# Entry-point .env loading: pick up ANTHROPIC_API_KEY (and LLM_PROVIDER, etc.) from
# the repo-root .env. Done here, before the key is read — not in any library module.
load_dotenv()

from research_platform.domain.ledger import freeze_snapshot
from research_platform.domain.models import ClaimStatus, SnapshotKind
from research_platform.domain.report_spec import build_schema_spec
from research_platform.domain.verification import verify_draft
from research_platform.ingestion.llm.claude_adapter import ClaudeLLMAdapter

INPUTS = {
    "revenue": 1500.0, "prev_revenue": 1200.0, "ebit": 360.0, "net_income": 240.0,
    "total_assets": 2000.0, "current_liabilities": 500.0, "total_debt": 300.0,
    "equity": 1000.0,
}


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set — this demo needs a real key. Aborting.")
        return 2

    adapter = ClaudeLLMAdapter()
    print(f"model={adapter.model_version}  prompt={adapter.prompt_version}\n")

    # Stamp model + prompt version into the snapshot's source_versions — Phase 0's
    # existing reproducibility machinery (blueprint 1.4: "when LLM output is
    # involved, model + prompt version"). No domain change required.
    snapshot = freeze_snapshot(
        stock_id=1,
        as_of=dt.date(2026, 3, 31),
        kind=SnapshotKind.annual,
        inputs=INPUTS,
        source_versions={
            "manual_upload": "1.0",
            "llm_model": adapter.model_version,
            "prompt_version": adapter.prompt_version,
        },
    )
    snapshot = snapshot.model_copy(update={"id": 1})  # give it an id for the spec

    print("Calling Claude (one generation)...\n")
    draft = adapter.generate_report_draft(snapshot.inputs, build_schema_spec(snapshot))
    result = verify_draft(draft, snapshot)

    by_id = {c.claim_id: c for c in result.claims}
    print(f"--- {len(draft.claims)} claims through the harness ---")
    for claim in draft.claims:
        cv = by_id[claim.id]
        mark = {"VERIFIED": "OK ", "FAILED": "XX ", "FLAGGED": "!! "}[cv.status.value]
        extra = ""
        if claim.claim_type.value == "NUMERIC":
            extra = f" [{claim.metric_key}={claim.asserted_value} computed={cv.computed_value}]"
        print(f"  {mark}[{cv.status.value:<8}] ({claim.section}) {claim.statement}{extra}")
        if cv.status is not ClaimStatus.VERIFIED:
            print(f"        reason: {cv.detail}")

    hard = [c.claim_id for c in result.claims if c.status is ClaimStatus.FAILED]
    flagged = [c.claim_id for c in result.claims if c.status is ClaimStatus.FLAGGED]
    print(f"\nOVERALL: {result.overall.value}")
    print(f"  hard-failed claims: {hard or 'none'}")
    print(f"  flagged claims:     {flagged or 'none'}")
    print(f"  missing sections:   {result.missing_sections or 'none'}")
    print(f"  storable (would persist): {result.is_storable}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
