"""DCF tests — purity, determinism, and loud failure. No DB, no network.

The DCF lives in the ``valuation`` adapter, but it is a pure function with no I/O,
so it is exercised here alongside the domain to prove the compute path is
decoupled and reproducible.
"""

from __future__ import annotations

import pytest

from research_platform.domain.ports.valuation import ValuationPort
from research_platform.valuation.dcf import DCFModel, value_dcf

INPUTS = {
    "revenue": 1_500_000.0,
    "ebit_margin": 0.24,
    "tax_rate": 0.25,
    "depreciation": 30_000.0,
    "capex": 40_000.0,
    "change_in_nwc": 15_000.0,
    "net_debt": -200_000.0,
    "shares_outstanding": 4_150.0,
}
ASSUMPTIONS = {
    "growth_rate": 0.10,
    "projection_years": 5,
    "wacc": 0.11,
    "terminal_growth": 0.04,
}


def test_dcf_satisfies_port() -> None:
    assert isinstance(DCFModel(), ValuationPort)
    assert DCFModel().model_name == "dcf"


def test_dcf_is_deterministic() -> None:
    assert value_dcf(INPUTS, ASSUMPTIONS) == value_dcf(INPUTS, ASSUMPTIONS)


def test_dcf_produces_sensible_structure() -> None:
    result = value_dcf(INPUTS, ASSUMPTIONS)
    assert result["model"] == "dcf"
    assert len(result["projection"]) == ASSUMPTIONS["projection_years"]
    assert result["value_per_share"] > 0
    # equity = EV - net_debt; net_debt is negative (net cash) so equity > EV
    assert result["equity_value"] > result["enterprise_value"]


def test_dcf_changes_with_assumptions() -> None:
    base = value_dcf(INPUTS, ASSUMPTIONS)
    higher_wacc = value_dcf(INPUTS, {**ASSUMPTIONS, "wacc": 0.15})
    assert higher_wacc["value_per_share"] < base["value_per_share"]


def test_dcf_rejects_diverging_terminal() -> None:
    with pytest.raises(ValueError, match="wacc must exceed terminal_growth"):
        value_dcf(INPUTS, {**ASSUMPTIONS, "terminal_growth": 0.11})


def test_dcf_rejects_missing_input() -> None:
    broken = {k: v for k, v in INPUTS.items() if k != "revenue"}
    with pytest.raises(ValueError, match="missing required DCF input"):
        value_dcf(broken, ASSUMPTIONS)
