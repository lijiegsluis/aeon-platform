"""Full valuation — the MODEL outputs of the Aeon Nimbus ER process.

Builds on the FCFF DCF in `analytics.run_dcf` and adds the pieces the Bamburi
template shows: a blended terminal value (50/50 perpetuity-growth and EV/EBITDA
exit-multiple), a WACC build from cost-of-capital components, a WACC x exit-
multiple sensitivity grid, comparable-multiple valuation, and a football-field
range that a rating and target price are read off.

Pure functions; every figure is derived from inputs, never invented.
"""

from __future__ import annotations

from statistics import median
from typing import Any

from aeon_nimbus import analytics


def blended_dcf(financials: dict[str, float], assumptions: dict[str, Any]) -> dict[str, Any]:
    """FCFF DCF value per share with terminal = 50% perpetuity + 50% exit multiple.

    `assumptions` must include both `terminal_growth` and `terminal_multiple`.
    """
    perp = analytics.run_dcf(financials, {**assumptions, "terminal_multiple": None})
    exitm = analytics.run_dcf(financials, assumptions)
    vps = 0.5 * perp["value_per_share"] + 0.5 * exitm["value_per_share"]
    return {
        "value_per_share": vps,
        "perpetuity_value_per_share": perp["value_per_share"],
        "exit_multiple_value_per_share": exitm["value_per_share"],
        "enterprise_value": 0.5 * perp["enterprise_value"] + 0.5 * exitm["enterprise_value"],
        "projection": perp["projection"],
    }


def wacc_build(risk_free: float, equity_risk_premium: float, beta: float,
               cost_of_debt: float, tax_rate: float, debt_weight: float) -> dict[str, float]:
    """WACC from explicit components (risk-free from a local bond coupon)."""
    return analytics.build_wacc_from_components(
        risk_free_rate=risk_free, beta=beta, equity_risk_premium=equity_risk_premium,
        country_risk_premium=0.0, size_premium=0.0, fx_risk_premium=0.0,
        pre_tax_cost_of_debt=cost_of_debt, tax_rate=tax_rate, target_debt_weight=debt_weight,
    )


def comparable_values(latest: dict[str, float], peer_median: dict[str, float],
                      shares: float, net_debt: float) -> dict[str, float]:
    """Implied value per share from peer median EV/EBITDA and P/E."""
    out: dict[str, float] = {}
    ebitda, ni = latest.get("ebitda"), latest.get("net_income")
    if peer_median.get("ev_ebitda") and ebitda and shares:
        out["ev_ebitda"] = (peer_median["ev_ebitda"] * ebitda - net_debt) / shares
    if peer_median.get("pe") and ni and ni > 0 and shares:
        out["pe"] = peer_median["pe"] * ni / shares
    return out


def peer_medians(peers: list[dict[str, Any]]) -> dict[str, float]:
    """Median EV/EBITDA and P/E across a peer set (ignoring missing values)."""
    def med(key: str) -> float | None:
        vals = [p[key] for p in peers if p.get(key) is not None and p[key] > 0]
        return round(median(vals), 2) if vals else None
    return {"ev_ebitda": med("ev_ebitda"), "pe": med("pe")}


def sensitivity(financials: dict[str, float], assumptions: dict[str, Any],
                wacc_deltas: list[float], exit_multiples: list[float]) -> list[list[float | None]]:
    """value-per-share grid over WACC (rows) x EV/EBITDA exit multiple (cols)."""
    grid: list[list[float | None]] = []
    for dw in wacc_deltas:
        row: list[float | None] = []
        for em in exit_multiples:
            a = {**assumptions, "wacc": assumptions["wacc"] + dw, "terminal_multiple": em}
            try:
                row.append(round(blended_dcf(financials, a)["value_per_share"], 2))
            except (ValueError, ZeroDivisionError):
                row.append(None)
        grid.append(row)
    return grid


def football_field(methods: list[dict[str, Any]], current_price: float) -> dict[str, Any]:
    """Assemble valuation ranges into a football-field structure.

    `methods` is a list of {method, low, high}; the target price is the midpoint
    of the DCF method when present, else the overall midpoint.
    """
    valid = [m for m in methods if m.get("low") is not None and m.get("high") is not None]
    dcf = next((m for m in valid if m["method"].lower().startswith("dcf")), None)
    anchor = dcf or (valid[0] if valid else None)
    target = round((anchor["low"] + anchor["high"]) / 2, 2) if anchor else None
    upside = (target / current_price - 1) if (target and current_price) else None
    return {"current_price": current_price, "methods": valid, "target_price": target, "upside": upside}


def rating_from_upside(upside: float | None) -> str:
    """Aeon Nimbus stance from upside to the target price."""
    if upside is None:
        return "Under review"
    if upside >= 0.20:
        return "Buy"
    if upside >= 0.0:
        return "Accumulate"
    if upside >= -0.15:
        return "Hold"
    return "Reduce"
