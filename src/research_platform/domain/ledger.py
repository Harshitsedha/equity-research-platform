"""The ledger rules — pure functions, no I/O.

This is the realization of ADR-003: freezing inputs into an immutable,
version-stamped, content-addressed ``Snapshot``. Everything here is
deterministic so that the same inputs always produce the same ``content_hash``
and the same snapshot — the precondition for reproducibility.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

from research_platform.domain.models import ParsedDocument, Snapshot, SnapshotKind
from research_platform.domain.version import CODE_VERSION


def canonicalize(inputs: dict) -> str:
    """Serialize a dict to a canonical, stable string for hashing.

    Keys are sorted and whitespace is fixed so that two semantically-identical
    dicts always serialize identically. ``default=str`` lets dates and other
    simple objects hash deterministically by their string form.
    """
    return json.dumps(
        inputs,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def compute_content_hash(inputs: dict) -> str:
    """SHA-256 over the canonical form of ``inputs`` — dedupe + integrity."""
    return hashlib.sha256(canonicalize(inputs).encode("utf-8")).hexdigest()


def freeze_snapshot(
    *,
    stock_id: int,
    as_of: dt.date,
    kind: SnapshotKind,
    inputs: dict,
    source_versions: dict | None = None,
    code_version: str = CODE_VERSION,
) -> Snapshot:
    """Freeze ``inputs`` into an immutable ``Snapshot``.

    Pure: no database, no network, no wall-clock. ``created_at`` is intentionally
    left unset — it is assigned by the database on insert and is metadata, not
    part of the reproducible content. The ``content_hash`` covers ``inputs``
    only, which is exactly what a valuation re-derives from.
    """
    return Snapshot(
        stock_id=stock_id,
        as_of=as_of,
        kind=kind,
        inputs=inputs,
        source_versions=source_versions or {},
        code_version=code_version,
        content_hash=compute_content_hash(inputs),
    )


def freeze_from_document(
    doc: ParsedDocument,
    *,
    stock_id: int,
    code_version: str = CODE_VERSION,
) -> Snapshot:
    """Convenience: freeze a parsed document for an already-persisted stock."""
    return freeze_snapshot(
        stock_id=stock_id,
        as_of=doc.as_of,
        kind=doc.kind,
        inputs=doc.inputs,
        source_versions=doc.source_versions,
        code_version=code_version,
    )
