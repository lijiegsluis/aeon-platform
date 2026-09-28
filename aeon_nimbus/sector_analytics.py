"""Sector-aware valuation: banks are valued as banks (justified P/B, residual
income, dividend discount), never DCF. Non-financials route to the existing
`run_dcf`. A recruiter spots a DCF'd bank instantly, so this split is the single
most credibility-critical part of the engine.
"""

from __future__ import annotations

from typing import Any

from aeon_nimbus import analytics


# --- cost of capital -------------------------------------------------------

def cost_of_equity(risk_free: float, beta: float, equity_risk_premium: float,
                   country_risk_premium: float = 0.0) -> float:
    """CAPM cost of equity plus an explicit country-risk premium (frontier markets)."""
    return risk_free + beta * equity_risk_premium + country_risk_premium


# --- justified price/book (Gordon) -----------------------------------------

def justified_pb(roe: float, cost_of_equity: float, growth: float) -> float:
    """Warranted price/book for a bank: (ROE - g) / (COE - g)."""
    if cost_of_equity <= growth:
        raise ValueError("cost_of_equity must exceed growth for a finite justified P/B")
    return (roe - growth) / (cost_of_equity - growth)


def fair_value_per_share_pb(book_value_per_share: float, justified_pb: float) -> float:
    """Fair value per share = justified P/B x book value per share."""
    return justified_pb * book_value_per_share


# --- residual income / excess return ---------------------------------------

def residual_income_value(book_value_per_share: float, roe_path: list[float],
                          cost_of_equity: float, payout_ratio: float,
                          terminal_growth: float = 0.0) -> float:
    """Value per share = starting book + PV of forecast residual income (excess
    return over the cost of equity) + PV of a terminal residual income.

    BVPS grows by retained earnings: BVPS_t = BVPS_{t-1} x (1 + ROE_t x (1 - payout)).
    Residual income_t = (ROE_t - COE) x BVPS_{t-1}.
    """
    if cost_of_equity <= terminal_growth:
        raise ValueError("cost_of_equity must exceed terminal_growth")
    bvps = book_value_per_share
    pv_ri = 0.0
    last_ri = 0.0
    for t, roe in enumerate(roe_path, start=1):
        ri = (roe - cost_of_equity) * bvps
        pv_ri += ri / (1 + cost_of_equity) ** t
        last_ri = ri
        bvps = bvps * (1 + roe * (1 - payout_ratio))
    terminal_ri = last_ri * (1 + terminal_growth)
    pv_terminal = (terminal_ri / (cost_of_equity - terminal_growth)) / (1 + cost_of_equity) ** len(roe_path)
    return book_value_per_share + pv_ri + pv_terminal


def dividend_discount_value(dividend_next: float, cost_of_equity: float, growth: float) -> float:
    """Gordon growth DDM: D1 / (COE - g)."""
    if cost_of_equity <= growth:
        raise ValueError("cost_of_equity must exceed growth")
    return dividend_next / (cost_of_equity - growth)


# --- bank KPIs -------------------------------------------------------------

def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return a / b


def bank_kpis(f: dict[str, float]) -> dict[str, float | None]:
    """Standard bank analysis ratios from a financial-line dict."""
    return {
        "nim": _safe_div(f.get("net_interest_income"), f.get("average_earning_assets")),
        "cost_to_income": _safe_div(f.get("operating_expenses"), f.get("total_operating_income")),
        "roe": _safe_div(f.get("net_income"), f.get("total_equity")),
        "roa": _safe_div(f.get("net_income"), f.get("total_assets")),
        "npl_ratio": _safe_div(f.get("non_performing_loans"), f.get("gross_loans")),
        "loan_to_deposit": _safe_div(f.get("gross_loans"), f.get("customer_deposits")),
        "cost_of_risk": _safe_div(f.get("loan_impairment_charge"), f.get("gross_loans")),
    }


# --- sector router ---------------------------------------------------------

def value_company(valuation_model: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Route to the correct valuation for the company's sector."""
    if valuation_model == "bank_pb_ddm":
        pb = justified_pb(inputs["roe"], inputs["cost_of_equity"], inputs["growth"])
        return {
            "method": "bank_pb_ddm",
            "justified_pb": pb,
            "fair_value_pb": fair_value_per_share_pb(inputs["book_value_per_share"], pb),
            "residual_income_value": residual_income_value(
                inputs["book_value_per_share"], inputs["roe_path"], inputs["cost_of_equity"],
                inputs["payout_ratio"], inputs.get("terminal_growth", 0.0)),
        }
    if valuation_model == "dcf":
        dcf = analytics.run_dcf(inputs["financials"], inputs["assumptions"])
        return {"method": "dcf", **dcf}
    raise ValueError(f"unknown valuation_model {valuation_model!r}")
