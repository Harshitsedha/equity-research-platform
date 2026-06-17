"""FileDocumentAdapter — a DocumentPort over local JSON/CSV financial files.

This is the only fragile "parse the messy world" code in Phase 0, deliberately
quarantined here (ADR-004). It fails loudly on missing/invalid data rather than
filling silent defaults (ADR-005). No network, no NSE — local files only.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

from research_platform.domain.models import ParsedDocument, SnapshotKind

#: The normalized financial line items every downstream model can rely on.
FINANCIAL_KEYS: tuple[str, ...] = (
    "revenue",
    "ebit_margin",
    "tax_rate",
    "depreciation",
    "capex",
    "change_in_nwc",
    "net_debt",
    "shares_outstanding",
)


class DocumentParseError(ValueError):
    """Raised when an input file is malformed or incomplete."""


def _normalize_inputs(raw: dict, *, source: str) -> dict:
    inputs: dict[str, float] = {}
    for key in FINANCIAL_KEYS:
        if key not in raw or raw[key] in (None, ""):
            raise DocumentParseError(
                f"{source}: missing required financial field {key!r}"
            )
        try:
            inputs[key] = float(raw[key])
        except (TypeError, ValueError) as exc:
            raise DocumentParseError(
                f"{source}: financial field {key!r} is not numeric: {raw[key]!r}"
            ) from exc
    return inputs


def _parse_kind(value: str, *, source: str) -> SnapshotKind:
    try:
        return SnapshotKind(value)
    except ValueError as exc:
        valid = ", ".join(k.value for k in SnapshotKind)
        raise DocumentParseError(
            f"{source}: invalid kind {value!r} (expected one of: {valid})"
        ) from exc


def _parse_date(value: str, *, source: str) -> dt.date:
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise DocumentParseError(
            f"{source}: as_of {value!r} is not an ISO date (YYYY-MM-DD)"
        ) from exc


def _require(raw: dict, key: str, *, source: str) -> str:
    if not raw.get(key):
        raise DocumentParseError(f"{source}: missing required field {key!r}")
    return str(raw[key])


class FileDocumentAdapter:
    """DocumentPort implementation for local ``.json`` / ``.csv`` files."""

    def parse(self, source: str | Path) -> ParsedDocument:
        path = Path(source)
        if not path.exists():
            raise DocumentParseError(f"file not found: {path}")
        suffix = path.suffix.lower()
        if suffix == ".json":
            return self._parse_json(path)
        if suffix == ".csv":
            return self._parse_csv(path)
        raise DocumentParseError(
            f"unsupported file type {suffix!r} (expected .json or .csv)"
        )

    # --- JSON: structured {identity..., financials:{...}} ------------------
    def _parse_json(self, path: Path) -> ParsedDocument:
        src = str(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DocumentParseError(f"{src}: invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise DocumentParseError(f"{src}: top level must be a JSON object")

        financials = data.get("financials")
        if not isinstance(financials, dict):
            raise DocumentParseError(f"{src}: missing 'financials' object")

        return ParsedDocument(
            ticker=_require(data, "ticker", source=src),
            name=_require(data, "name", source=src),
            exchange=str(data.get("exchange", "NSE")),
            sector=data.get("sector"),
            profile=data.get("profile") or {},
            as_of=_parse_date(_require(data, "as_of", source=src), source=src),
            kind=_parse_kind(_require(data, "kind", source=src), source=src),
            inputs=_normalize_inputs(financials, source=src),
            source_versions=data.get("source_versions") or {},
        )

    # --- CSV: flat key,value table ----------------------------------------
    def _parse_csv(self, path: Path) -> ParsedDocument:
        src = str(path)
        flat: dict[str, str] = {}
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            for row_no, row in enumerate(reader, start=1):
                cells = [c.strip() for c in row if c.strip() != ""]
                if not cells:
                    continue
                if cells[0].lower() in ("key", "field"):  # optional header
                    continue
                if len(cells) < 2:
                    raise DocumentParseError(
                        f"{src}: row {row_no} is not a key,value pair: {row!r}"
                    )
                flat[cells[0]] = cells[1]

        raw_source_versions = flat.get("source_versions")
        try:
            source_versions = (
                json.loads(raw_source_versions) if raw_source_versions else {}
            )
        except json.JSONDecodeError as exc:
            raise DocumentParseError(
                f"{src}: 'source_versions' must be a JSON object: {exc}"
            ) from exc

        return ParsedDocument(
            ticker=_require(flat, "ticker", source=src),
            name=_require(flat, "name", source=src),
            exchange=flat.get("exchange") or "NSE",
            sector=flat.get("sector") or None,
            as_of=_parse_date(_require(flat, "as_of", source=src), source=src),
            kind=_parse_kind(_require(flat, "kind", source=src), source=src),
            inputs=_normalize_inputs(flat, source=src),
            source_versions=source_versions,
        )
