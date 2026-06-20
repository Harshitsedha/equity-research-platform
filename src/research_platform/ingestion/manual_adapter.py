"""ManualAssistedAdapter — the first IngestionSourceAdapter (Phase 0, ADR-017).

Manual-assisted is the source-of-record entry: a human transcribes the 8 raw
fundamentals (+ period) from an NSE filing into a structured key->value form and
pastes/uploads it. NO XBRL, NO PDF parsing (later adapters) and — per ADR-012 —
ZERO external network, which is exactly why it is the first adapter: it exercises
the whole port + pipeline + promote gate without any network surface.

This adapter is deliberately THIN: it wraps the submitted form into a neutral
``RawExtraction`` verbatim. All mapping/validation is the shared pipeline.

THE FORM CONTRACT — absence is unrepresentable as zero (ADR-017 decision 3):
``fields`` maps a field label to either a value (str/number, where ``0`` means a
real zero) or ``None`` (the explicit "unknown / not in the filing" state). The two
states are structurally distinct, so a missing fundamental can never be smuggled in
as ``0``. The adapter converts present values to strings and preserves ``None``.
"""

from __future__ import annotations

from research_platform.domain.ingestion import (
    ClaimedPeriod,
    RawExtraction,
    SourceKind,
)


class ManualFormError(ValueError):
    """Raised when the submitted form is structurally malformed (not a data issue).

    A *data* problem (missing/absent/wrong fundamental) is a validation finding
    downstream, NOT an adapter error. This is only for a form that isn't a form.
    """


class ManualAssistedAdapter:
    """Wrap a structured manual-entry form into a ``RawExtraction``. Pure, no I/O."""

    def read(self, payload: dict) -> RawExtraction:
        """Wrap ``payload`` verbatim. Expected shape::

            {
              "source_doc_ref": "<filing url / 'manual'>",
              "period": {"as_of": "2025-03-31", "kind": "annual"},
              "fields": {"revenue": "1200000", "ebit": "252000", "market_share": None},
            }

        ``fields`` values: a string/number (``0`` is a real zero) or ``None`` (the
        distinct absent state). Period strings are NOT parsed here (a malformed
        period is a validation finding, not an adapter crash).
        """
        if not isinstance(payload, dict):
            raise ManualFormError("manual form payload must be an object")

        source_doc_ref = payload.get("source_doc_ref")
        if not source_doc_ref:
            raise ManualFormError("manual form requires a 'source_doc_ref'")

        fields = payload.get("fields")
        if not isinstance(fields, dict):
            raise ManualFormError("manual form requires a 'fields' object")

        period_obj = payload.get("period") or {}
        if not isinstance(period_obj, dict):
            raise ManualFormError("'period' must be an object when present")

        # Preserve the absence-vs-zero distinction: None stays None; everything else
        # becomes a string (so "0" and 0 both mean a real zero, never absence).
        raw_fields: dict[str, str | None] = {
            str(key): (None if value is None else str(value))
            for key, value in fields.items()
        }

        return RawExtraction(
            source_kind=SourceKind.manual_paste,
            source_doc_ref=str(source_doc_ref),
            raw_payload=payload,
            raw_fields=raw_fields,
            claimed_period=ClaimedPeriod(
                as_of=(str(period_obj["as_of"]) if period_obj.get("as_of") else None),
                kind=(str(period_obj["kind"]) if period_obj.get("kind") else None),
            ),
        )
