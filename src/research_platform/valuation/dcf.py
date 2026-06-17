"""A simple, deterministic discounted-cash-flow model (a ValuationPort).

This is a *pure function* dressed as a small class so it can carry a stable
``model_name``. Identical ``inputs`` + ``assumptions`` always produce an
identical ``result`` — no randomness, no clock, no I/O (ADR-004). Numbers are
rounded to a fixed precision so the JSON-serialized result is stable and
byte-comparable for the reproducibility test.

The model intentionally holds non-revenue line items (depreciation, capex,
change in net working capital) constant across the explicit horizon. It is a
*spine-proving* model, not a research-grade one — realism is a later phase.
"""

from __future__ import annotations

from typing import Any

#: Decimal places every monetary output is rounded to. Fixed for determinism.
_PRECISION = 6


def _round(x: float) -> float:
    return round(x, _PRECISION)


def _require(condition: bool, message: str) -> None:
    """Loud failure on bad inputs (ADR-005) rather than silent garbage."""
    if not condition:
        raise ValueError(message)


def value_dcf(inputs: dict, assumptions: dict) -> dict[str, Any]:
    """Compute a DCF valuation. Pure and deterministic.

    Required ``inputs`` (from a frozen Snapshot):
        revenue, ebit_margin, tax_rate, depreciation, capex,
        change_in_nwc, net_debt, shares_outstanding

    Required ``assumptions`` (the levers):
        growth_rate, projection_years, wacc, terminal_growth
    """
    # --- pull + validate ---------------------------------------------------
    try:
        revenue0 = float(inputs["revenue"])
        ebit_margin = float(inputs["ebit_margin"])
        tax_rate = float(inputs["tax_rate"])
        depreciation = float(inputs["depreciation"])
        capex = float(inputs["capex"])
        change_in_nwc = float(inputs["change_in_nwc"])
        net_debt = float(inputs["net_debt"])
        shares = float(inputs["shares_outstanding"])
    except KeyError as exc:
        raise ValueError(f"missing required DCF input: {exc.args[0]!r}") from exc

    try:
        growth = float(assumptions["growth_rate"])
        years = int(assumptions["projection_years"])
        wacc = float(assumptions["wacc"])
        terminal_growth = float(assumptions["terminal_growth"])
    except KeyError as exc:
        raise ValueError(f"missing required DCF assumption: {exc.args[0]!r}") from exc

    _require(years >= 1, "projection_years must be >= 1")
    _require(shares > 0, "shares_outstanding must be > 0")
    _require(
        wacc > terminal_growth,
        "wacc must exceed terminal_growth (Gordon terminal value diverges otherwise)",
    )

    # --- explicit-horizon free cash flow to firm ---------------------------
    projection: list[dict[str, float]] = []
    pv_explicit = 0.0
    revenue = revenue0
    fcff = 0.0
    for year in range(1, years + 1):
        revenue = revenue * (1.0 + growth)
        ebit = revenue * ebit_margin
        nopat = ebit * (1.0 - tax_rate)
        fcff = nopat + depreciation - capex - change_in_nwc
        discount_factor = 1.0 / ((1.0 + wacc) ** year)
        pv_fcff = fcff * discount_factor
        pv_explicit += pv_fcff
        projection.append(
            {
                "year": year,
                "revenue": _round(revenue),
                "ebit": _round(ebit),
                "nopat": _round(nopat),
                "fcff": _round(fcff),
                "discount_factor": _round(discount_factor),
                "pv_fcff": _round(pv_fcff),
            }
        )

    # --- Gordon-growth terminal value (on the last explicit FCFF) ----------
    terminal_value = fcff * (1.0 + terminal_growth) / (wacc - terminal_growth)
    pv_terminal = terminal_value / ((1.0 + wacc) ** years)

    enterprise_value = pv_explicit + pv_terminal
    equity_value = enterprise_value - net_debt
    value_per_share = equity_value / shares

    return {
        "model": DCFModel.model_name,
        "enterprise_value": _round(enterprise_value),
        "equity_value": _round(equity_value),
        "value_per_share": _round(value_per_share),
        "pv_explicit": _round(pv_explicit),
        "pv_terminal": _round(pv_terminal),
        "terminal_value": _round(terminal_value),
        "projection": projection,
    }


class DCFModel:
    """ValuationPort implementation wrapping :func:`value_dcf`."""

    model_name: str = "dcf"

    def run(self, inputs: dict, assumptions: dict) -> dict:
        return value_dcf(inputs, assumptions)
