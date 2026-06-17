"""Real Claude LLM adapter — implements the SAME LLMPort the stub satisfies.

Architectural test (Part B): this swaps in for the stub with NO change to the
domain, harness, pipeline, or storage. It depends only on domain models (inward)
and the Anthropic SDK; all network/parse fragility is quarantined here (ADR-004).

The model is treated as fallible: it emits structured *claims*, never trusted
prose or final arithmetic, and its output flows through the existing verification
harness unchanged (ADR-010). Malformed model output raises an adapter-level error
that never leaks domain concerns.

Parsing is a pure function (:func:`parse_claude_draft`) separated from the API
call so it can be unit-tested with fixture payloads — no network in tests.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable

import structlog

from research_platform.domain.models import Claim, StructuredReportDraft

log = structlog.get_logger()

#: Default model (per Anthropic guidance; override via constructor or env).
DEFAULT_MODEL = "claude-opus-4-8"

#: Version of the prompt template below — stamped for reproducibility.
PROMPT_VERSION = "report-claims-v1"


class ClaudeAdapterError(RuntimeError):
    """Base error for the Claude adapter (ingestion concern, never domain)."""


class ClaudeConfigError(ClaudeAdapterError):
    """Raised at construction when configuration (e.g. API key) is missing."""


class ClaudeParseError(ClaudeAdapterError):
    """Raised when the model's output is malformed / fails strict validation."""


# The JSON shape we ask the model to return (also enforced via output_config).
_DRAFT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "claim_type": {
                        "type": "string",
                        "enum": ["NUMERIC", "FACTUAL", "QUALITATIVE"],
                    },
                    "statement": {"type": "string"},
                    "section": {"type": "string"},
                    "metric_key": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "asserted_value": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                    "citation": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": [
                    "id", "claim_type", "statement", "section",
                    "metric_key", "asserted_value", "citation",
                ],
            },
        },
        "section_order": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["claims", "section_order"],
}

_SYSTEM_PROMPT = (
    "You are an equity-research analyst that emits STRUCTURED, CHECKABLE CLAIMS — "
    "never free prose, never final numbers presented as conclusions. Every claim "
    "you make will be independently verified against the source financial data by "
    "a separate system before it is trusted, so:\n"
    "- For NUMERIC claims, set claim_type=NUMERIC, pick a metric_key from the "
    "provided menu, compute asserted_value from the given inputs, and set citation "
    "to one of the input keys the metric depends on. Do NOT invent metrics or "
    "numbers you cannot derive from the inputs.\n"
    "- For FACTUAL claims, set claim_type=FACTUAL and set citation to an input key "
    "that grounds the fact.\n"
    "- For judgement/qualitative statements, set claim_type=QUALITATIVE (no metric, "
    "no citation needed).\n"
    "- Cover every required section with at least one claim.\n"
    "Return ONLY the JSON object matching the requested schema."
)


def _build_user_prompt(snapshot_inputs: dict, schema_spec: dict) -> str:
    return (
        "Snapshot financial inputs (JSON):\n"
        f"{json.dumps(snapshot_inputs, sort_keys=True, indent=2)}\n\n"
        "Required sections (each needs >= 1 claim):\n"
        f"{json.dumps(schema_spec.get('required_sections', []))}\n\n"
        "Recomputable NUMERIC metrics you may assert "
        "(metric_key -> required input keys):\n"
        f"{json.dumps(schema_spec.get('numeric_metrics', []), indent=2)}\n\n"
        "Produce a StructuredReportDraft of claims as JSON."
    )


def parse_claude_draft(raw_text: str, schema_spec: dict) -> StructuredReportDraft:
    """Strictly parse the model's JSON text into a StructuredReportDraft.

    Pure and deterministic — no network. Identity (stock_id/snapshot_id) comes from
    ``schema_spec``, not the model. Any structural problem raises ClaudeParseError.
    """
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ClaudeParseError(f"model output is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ClaudeParseError("model output must be a JSON object")
    raw_claims = payload.get("claims")
    if not isinstance(raw_claims, list) or not raw_claims:
        raise ClaudeParseError("model output is missing a non-empty 'claims' array")

    claims: list[Claim] = []
    for i, raw in enumerate(raw_claims):
        if not isinstance(raw, dict):
            raise ClaudeParseError(f"claim #{i} is not an object")
        try:
            claims.append(Claim.model_validate(raw))
        except Exception as exc:  # pydantic ValidationError -> adapter error
            raise ClaudeParseError(f"claim #{i} failed validation: {exc}") from exc

    section_order = payload.get("section_order")
    if not isinstance(section_order, list):
        section_order = list(schema_spec.get("section_order", []))

    try:
        stock_id = int(schema_spec["stock_id"])
        snapshot_id = int(schema_spec["snapshot_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ClaudeParseError(f"schema_spec missing stock/snapshot id: {exc}") from exc

    return StructuredReportDraft(
        stock_id=stock_id,
        snapshot_id=snapshot_id,
        claims=claims,
        section_order=[str(s) for s in section_order],
    )


class ClaudeLLMAdapter:
    """LLMPort backed by the real Anthropic API.

    ``complete_fn`` is an injectable seam (raw-text completion) used by tests to
    exercise the full parse/pipeline path with fixture output and no network. In
    production it is None and the real API client is built (failing loudly if the
    API key is missing).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        prompt_version: str = PROMPT_VERSION,
        complete_fn: Callable[[dict, dict], str] | None = None,
    ) -> None:
        self.model = model
        self.model_version = model
        self.prompt_version = prompt_version
        # Provenance recorded via the port's model_name (and snapshot source_versions).
        self.model_name = f"claude:{model}:{prompt_version}"
        self._complete_fn = complete_fn

        if complete_fn is None:
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise ClaudeConfigError(
                    "ANTHROPIC_API_KEY is not set — the Claude adapter needs an API "
                    "key (set the env var; never hardcode or commit it)."
                )
            import anthropic  # lazy: only when the real adapter is constructed

            self._client = anthropic.Anthropic(api_key=key)
        else:
            self._client = None

    # --- LLMPort ----------------------------------------------------------
    def generate_report_draft(
        self, snapshot_inputs: dict, schema_spec: dict
    ) -> StructuredReportDraft:
        completer = self._complete_fn or self._call_api
        raw_text = completer(snapshot_inputs, schema_spec)
        draft = parse_claude_draft(raw_text, schema_spec)
        log.info(
            "claude.draft_parsed",
            model=self.model,
            prompt_version=self.prompt_version,
            claims=len(draft.claims),
        )
        return draft

    # --- real API call (not exercised in the default test run) ------------
    def _call_api(self, snapshot_inputs: dict, schema_spec: dict) -> str:
        user_prompt = _build_user_prompt(snapshot_inputs, schema_spec)
        with self._client.messages.stream(  # type: ignore[union-attr]
            model=self.model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            output_config={"format": {"type": "json_schema", "schema": _DRAFT_SCHEMA}},
        ) as stream:
            message = stream.get_final_message()

        if message.stop_reason == "refusal":
            raise ClaudeAdapterError("model refused to generate a report draft")
        for block in message.content:
            if block.type == "text":
                return block.text
        raise ClaudeAdapterError("model response contained no text block")
