"""Valuation engine: compute Bull / Base / Bear DCF (and bank P/B) for every company.

Reads financials from Company.extracted, derives scenario assumptions via scenarios.py,
runs the DCF/bank valuation, and writes a structured `valuation` block back to
Company.extracted.  This is the ground truth for the Valuation tab and Report rating box.

Output schema (stored in extracted['valuation']):
{
  "as_of": "2026-09-24",
  "model": "dcf" | "bank",
  "currency": "KES",
  "shares_m": 40065.5,
  "scenarios": {
    "Bull": {"value_per_share": 42.0, "wacc": 0.115, "revenue_growth": 0.12, ...},
    "Base": {"value_per_share": 34.0, "wacc": 0.135, ...},
    "Bear": {"value_per_share": 24.0, "wacc": 0.155, ...},
  },
  "base": {                        # mirrors scenarios.Base for quick access
    "value_per_share": 34.0,
    "wacc": 0.135,
    "terminal_growth": 0.03,
    "upside_pct": 0.0,             # vs current stored share price
    "stance": "Hold",
  },
  "rating": {
    "stance": "Hold",
    "target_price": 34.0,
    "current_price": 34.05,
    "upside": -0.001,
    "bull_price": 42.0,
    "bear_price": 24.0,
  },
  "wacc_build": {"wacc": 0.135, "risk_free": 0.14, "erp": 0.065, "country_premium": 0.03},
  "sensitivity": {"wacc_range": [...], "tg_range": [...], "grid": [[...]]},
}
"""
from __future__ import annotations

import copy
import datetime
import math
from typing import Any

from aeon_nimbus import scenarios as sc
from aeon_nimbus import analytics
from aeon_nimbus.valuation import blended_dcf, rating_from_upside
from aeon_nimbus.country_risk import implied_wacc
from aeon_nimbus.config import WACC_BY_COUNTRY, BANK_COE_BY_COUNTRY


# ── Helpers ───────────────────────────────────────────────────────────────────

def _f(x, d=0.0):
    return float(x) if isinstance(x, (int, float)) and not math.isnan(x) else d


def _financials_dict(fins: list[dict], bank_data: dict | None = None,
                     market: dict | None = None) -> dict[str, float]:
    """Build the analytics.run_dcf input financials dict from extracted data."""
    if not fins:
        return {}
    latest = fins[-1]
    shares = None
    if market:
        shares = market.get("shares_outstanding_m")  # already in millions
    if shares is None:
        shares = 1_000.0  # fallback to avoid div/zero; value_per_share will be wrong

    _td = latest.get("total_debt") or 0.0
    _cash = latest.get("cash") or 0.0
    nd = latest.get("net_debt")
    if nd is None:
        nd = (_td - _cash) if (_td or _cash) else 0.0
    revenue = latest.get("revenue", 0.0)
    ebitda = latest.get("ebitda")
    if ebitda is None:
        ebitda = latest.get("operating_cash_flow") or (revenue * 0.25)

    return {
        "revenue": _f(revenue),
        "ebitda": _f(ebitda),
        "ebit": _f(latest.get("ebit") or ebitda * 0.6),
        "net_income": _f(latest.get("net_income", 0.0)),
        "capex": _f(latest.get("capex", 0.0)),
        "depreciation": _f(latest.get("depreciation") or ebitda - _f(latest.get("ebit", 0)) or ebitda * 0.3),
        "net_debt": _f(nd),
        "total_equity": _f(latest.get("total_equity", 0.0)),
        "free_cash_flow": _f(latest.get("free_cash_flow") or ((_f(latest.get("operating_cash_flow")) - _f(latest.get("capex"))) if latest.get("operating_cash_flow") else 0.0)),
        "shares_outstanding": _f(shares),  # in millions, same unit as revenue/equity
        "tax_rate": _f(latest.get("tax_rate") or 0.30),
    }


def _bank_p_b_value(assumptions: dict, equity: float, shares: float) -> float | None:
    """Gordon-growth implied P/B → value per share.  Equity in same unit as shares."""
    roe = _f(assumptions.get("roe"), 0.15)
    coe = _f(assumptions.get("coe"), 0.16)
    g = _f(assumptions.get("growth"), 0.06)
    if coe <= g or shares == 0 or equity == 0:
        return None
    pb = (roe - g) / (coe - g)
    pb = max(0.1, min(pb, 5.0))  # clamp to sane range
    return round(pb * equity / shares, 4)


def _run_scenario(fins: list, assump: dict, fin_dict: dict, is_bank: bool,
                  bank_data: dict | None) -> dict:
    """Run a single scenario and return {value_per_share, wacc, ...}."""
    if is_bank:
        equity = fin_dict.get("total_equity", 0.0)
        shares = fin_dict.get("shares_outstanding", 1.0)  # both in millions
        vps = _bank_p_b_value(assump, equity, shares)  # equity/shares = book value per share
        return {**assump, "value_per_share": vps, "model": "bank"}
    # DCF
    a = {
        "revenue_growth": assump["revenue_growth"],
        "ebitda_margin": assump["ebitda_margin"],
        "capex_pct_revenue": assump["capex_pct_sales"],
        "da_pct_revenue": assump.get("d_and_a_pct_sales", 0.08),
        "tax_rate": assump["tax_rate"],
        "wacc": assump["wacc"],
        "terminal_growth": assump["terminal_growth"],
        "terminal_multiple": assump.get("exit_multiple", 6.0),
        "projection_years": 5,
        "working_capital_pct_revenue": assump.get("working_capital_pct_sales", 0.03),
    }
    try:
        result = blended_dcf(fin_dict, a)
        vps = result.get("value_per_share")
        if vps is not None:
            vps = round(float(vps), 4)
        return {**assump, "value_per_share": vps, "model": "dcf"}
    except Exception:
        return {**assump, "value_per_share": None, "model": "dcf"}


def compute_valuation(co_extracted: dict, co_universe: dict) -> dict | None:
    """
    Compute Bull/Base/Bear valuation for a company.
    Returns the valuation dict (to store in extracted['valuation']) or None if
    insufficient data.
    """
    fins = co_extracted.get("financials") or []
    # Normalise fiscal year key to 'fy' (string)
    for f in fins:
        if "fy" not in f:
            raw = f.get("fiscal_year") or f.get("fy_label") or f.get("period_end")
            if raw is not None:
                f["fy"] = str(raw)
    # Ensure chronological order (oldest first) for CAGR / forecast calculations
    if fins and fins[0].get("fy", "") > fins[-1].get("fy", ""):
        fins = list(reversed(fins))
    if not fins:
        return None

    _bank_raw = co_extracted.get("bank")
    bank_data = _bank_raw if isinstance(_bank_raw, dict) else {}
    market = co_extracted.get("market") or {}
    country = co_universe.get("country") or ""
    vm = co_universe.get("valuation_model") or ""
    is_bank = bool(vm in ("bank", "bank_dcf", "bank_pb") or
                   (_bank_raw is True) or
                   (bank_data and bank_data.get("nim")))

    fin_dict = _financials_dict(fins, bank_data, market)
    if not fin_dict.get("revenue"):
        return None

    scens = sc.default_scenarios(fins, country=country, is_bank=is_bank, bank=bank_data or None)
    sets = scens["sets"]

    results: dict[str, dict] = {}
    for name, assump in sets.items():
        results[name] = _run_scenario(fins, assump, fin_dict, is_bank, bank_data)

    base_vps = (results.get("Base") or {}).get("value_per_share")
    bull_vps = (results.get("Bull") or {}).get("value_per_share")
    bear_vps = (results.get("Bear") or {}).get("value_per_share")

    current_price = market.get("share_price") or market.get("price")
    upside = ((base_vps / current_price) - 1) if (base_vps and current_price and current_price > 0) else None
    stance = rating_from_upside(upside)

    base_wacc = (results.get("Base") or {}).get("wacc") or (results.get("Base") or {}).get("coe")
    terminal_g = (results.get("Base") or {}).get("terminal_growth") or (results.get("Base") or {}).get("growth")

    # Sensitivity grid: WACC ± 200bp × terminal growth ± 1%
    sensitivity_grid: list[list] = []
    if not is_bank and base_wacc and terminal_g is not None:
        wacc_deltas = [-0.02, -0.01, 0.0, 0.01, 0.02]
        tg_values = [max(0.0, terminal_g - 0.01), terminal_g, min(0.06, terminal_g + 0.01)]
        base_assump = dict(sets["Base"])
        for dw in wacc_deltas:
            row = []
            for tg in tg_values:
                a2 = {**base_assump, "wacc": base_wacc + dw, "terminal_growth": tg}
                r = _run_scenario(fins, a2, fin_dict, False, None)
                vps = r.get("value_per_share")
                row.append(round(vps, 2) if vps is not None else None)
            sensitivity_grid.append(row)

    val = {
        "as_of": datetime.date.today().isoformat(),
        "model": "bank" if is_bank else "dcf",
        "currency": co_extracted.get("currency") or co_universe.get("currency") or "",
        "shares_m": (fin_dict.get("shares_outstanding") or 0) / 1e6,
        "scenarios": results,
        "base": {
            "value_per_share": base_vps,
            "wacc": base_wacc,
            "terminal_growth": terminal_g,
            "upside_pct": round(upside, 4) if upside is not None else None,
            "stance": stance,
        },
        "rating": {
            "stance": stance,
            "target_price": round(base_vps, 2) if base_vps else None,
            "current_price": current_price,
            "upside": round(upside, 4) if upside is not None else None,
            "bull_price": round(bull_vps, 2) if bull_vps else None,
            "bear_price": round(bear_vps, 2) if bear_vps else None,
        },
    }
    if sensitivity_grid:
        base_wacc_row = (results.get("Base") or {}).get("wacc")
        tg = terminal_g or 0.03
        val["sensitivity"] = {
            "wacc_range": [round(base_wacc + dw, 4) for dw in [-0.02, -0.01, 0.0, 0.01, 0.02]],
            "tg_range": [round(max(0, tg - 0.01), 4), round(tg, 4), round(min(0.06, tg + 0.01), 4)],
            "grid": sensitivity_grid,
        }
    return val
