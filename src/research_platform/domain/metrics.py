"""Deterministic financial-metric recomputation — pure domain logic.

This is the platform's *own* arithmetic, used by the verification harness to
independently recompute any NUMERIC claim an LLM asserts (ADR-010). It lives in
the domain, not the valuation adapter, because the standard of numeric truth is
core business logic and the domain may not import an adapter (HARD RULE 1). It is
pure: stdlib only, no I/O, deterministic, fixed rounding.

Each metric is a function of a snapshot's raw ``inputs`` dict. Missing inputs,
unknown metrics, or division by zero raise ``MetricError`` — the harness treats
an un-recomputable NUMERIC claim as a hard failure (it cannot be trusted).
"""

from __future__ import annotations

from collections.abc import Callable

#: Decimal places every metric is rounded to (matches the valuation module).
PRECISION = 6


class MetricError(ValueError):
    """Raised when a metric cannot be computed from the given inputs."""


# metric_key -> the raw input keys it requires (also advertised in the schema spec)
METRIC_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "ebit_margin": ("ebit", "revenue"),
    "net_margin": ("net_income", "revenue"),
    "roce": ("ebit", "total_assets", "current_liabilities"),
    "roe": ("net_income", "equity"),
    "debt_equity": ("total_debt", "equity"),
    "revenue_growth": ("revenue", "prev_revenue"),
}


def _div(numerator: float, denominator: float) -> float:
    if denominator == 0:
        raise MetricError("division by zero")
    return numerator / denominator


def _ebit_margin(i: dict) -> float:
    return _div(float(i["ebit"]), float(i["revenue"]))


def _net_margin(i: dict) -> float:
    return _div(float(i["net_income"]), float(i["revenue"]))


def _roce(i: dict) -> float:
    capital_employed = float(i["total_assets"]) - float(i["current_liabilities"])
    return _div(float(i["ebit"]), capital_employed)


def _roe(i: dict) -> float:
    return _div(float(i["net_income"]), float(i["equity"]))


def _debt_equity(i: dict) -> float:
    return _div(float(i["total_debt"]), float(i["equity"]))


def _revenue_growth(i: dict) -> float:
    prev = float(i["prev_revenue"])
    return _div(float(i["revenue"]) - prev, prev)


_METRIC_FNS: dict[str, Callable[[dict], float]] = {
    "ebit_margin": _ebit_margin,
    "net_margin": _net_margin,
    "roce": _roce,
    "roe": _roe,
    "debt_equity": _debt_equity,
    "revenue_growth": _revenue_growth,
}

#: All metric keys the harness can independently recompute. Sourced from the
#: dispatch table itself (``_METRIC_FNS`` — what ``compute_metric`` actually keys
#: on) so it is the single source of "what is a derived metric": the 3b drift
#: resolver imports THIS to decide derived-vs-raw, and the set can never drift
#: from the functions that implement it.
NUMERIC_METRIC_KEYS: tuple[str, ...] = tuple(_METRIC_FNS)


def compute_metric(metric_key: str, inputs: dict, *, precision: int = PRECISION) -> float:
    """Independently compute ``metric_key`` from ``inputs``. Pure, deterministic.

    Raises ``MetricError`` for an unknown metric, a missing required input, or a
    non-numeric value / division by zero.
    """
    if metric_key not in _METRIC_FNS:
        raise MetricError(f"unknown metric {metric_key!r}")
    for required in METRIC_REQUIREMENTS[metric_key]:
        if required not in inputs:
            raise MetricError(
                f"metric {metric_key!r} requires missing input {required!r}"
            )
    try:
        value = _METRIC_FNS[metric_key](inputs)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, MetricError):
            raise
        raise MetricError(f"metric {metric_key!r} not computable: {exc}") from exc
    return round(value, precision)
