"""Valuation, liquidity, macro, portfolio, and special-situation analytics."""

from __future__ import annotations

import math
from statistics import median
from typing import Any


def calculate_enterprise_value(
    market_cap: float,
    total_debt: float,
    cash: float,
    minorities: float = 0.0,
    associates: float = 0.0,
) -> float:
    """Calculate enterprise value."""

    return market_cap + total_debt + minorities - cash - associates


def calculate_valuation_multiples(financials: dict[str, float]) -> dict[str, float | None]:
    """Calculate P/E, EV/EBITDA, EV/Sales, P/B, dividend yield, and FCF yield."""

    market_cap = financials.get("market_cap")
    ev = financials.get("enterprise_value")
    revenue = financials.get("revenue")
    ebitda = financials.get("ebitda")
    net_income = financials.get("net_income")
    total_equity = financials.get("total_equity")
    dividends = financials.get("dividends_paid")
    free_cash_flow = financials.get("free_cash_flow")
    return {
        "pe": _safe_div(market_cap, net_income),
        "ev_ebitda": _safe_div(ev, ebitda),
        "ev_sales": _safe_div(ev, revenue),
        "price_book": _safe_div(market_cap, total_equity),
        "dividend_yield": _safe_div(abs(dividends or 0), market_cap),
        "fcf_yield": _safe_div(free_cash_flow, market_cap),
        "net_debt_ebitda": _safe_div(financials.get("net_debt"), ebitda),
        "roe": _safe_div(net_income, total_equity),
        "ebitda_margin": _safe_div(ebitda, revenue),
    }


def build_peer_multiples_table(peers: list[dict[str, Any]], year: str) -> list[dict[str, Any]]:
    """Build a peer comparison table."""

    return [peer for peer in peers if str(peer.get("period")) == str(year)]


def calculate_discount_to_peer_median(company_multiple: float, peer_multiples: list[float]) -> float | None:
    """Calculate valuation discount or premium versus peer median."""

    values = [value for value in peer_multiples if value is not None and not math.isnan(value)]
    if not values:
        return None
    peer_median = median(values)
    if peer_median == 0:
        return None
    return (company_multiple / peer_median) - 1


def run_dcf(financials: dict[str, float], assumptions: dict[str, float]) -> dict[str, Any]:
    """Run a DCF with EBIT tax, D&A addback, capex, working capital, and EV bridge."""

    revenue = financials["revenue"]
    years = int(assumptions.get("years", 5))
    growth = assumptions.get("revenue_growth", 0.05)
    ebitda_margin = assumptions.get("ebitda_margin", 0.18)
    d_and_a_pct_sales = assumptions.get("d_and_a_pct_sales", financials.get("d_and_a_pct_sales", 0.04))
    tax_rate = assumptions.get("tax_rate", 0.30)
    capex_pct_sales = assumptions.get("capex_pct_sales", 0.06)
    wc_pct_sales = assumptions.get("working_capital_pct_sales", 0.02)
    wacc = assumptions.get("wacc", 0.15)
    terminal_growth = assumptions.get("terminal_growth", 0.03)
    terminal_multiple = assumptions.get("terminal_multiple")
    net_debt = financials.get("net_debt", 0.0)
    minorities = financials.get("minorities", 0.0)
    associates = financials.get("associates", 0.0)
    non_operating_assets = financials.get("non_operating_assets", 0.0)
    shares = financials.get("shares_outstanding", 1.0)
    if years <= 0:
        raise ValueError("DCF forecast years must be positive.")
    if wacc <= terminal_growth and terminal_multiple is None:
        raise ValueError("DCF WACC must be greater than terminal growth when using a perpetuity terminal value.")

    rows = []
    present_value = 0.0
    current_revenue = revenue
    previous_revenue = revenue
    for year in range(1, years + 1):
        current_revenue *= 1 + growth
        ebitda = current_revenue * ebitda_margin
        depreciation = current_revenue * d_and_a_pct_sales
        ebit = ebitda - depreciation
        tax = max(ebit, 0) * tax_rate
        nopat = ebit - tax
        capex = current_revenue * capex_pct_sales
        change_in_working_capital = (current_revenue - previous_revenue) * wc_pct_sales
        free_cash_flow = nopat + depreciation - capex - change_in_working_capital
        discount_factor = (1 + wacc) ** year
        pv_fcf = free_cash_flow / discount_factor
        present_value += pv_fcf
        previous_revenue = current_revenue
        rows.append(
            {
                "year": year,
                "revenue": current_revenue,
                "ebitda": ebitda,
                "depreciation": depreciation,
                "ebit": ebit,
                "tax": tax,
                "capex": capex,
                "change_in_working_capital": change_in_working_capital,
                "free_cash_flow": free_cash_flow,
                "pv_fcf": pv_fcf,
            }
        )

    terminal_fcf = rows[-1]["free_cash_flow"] * (1 + terminal_growth)
    if terminal_multiple:
        terminal_value = rows[-1]["ebitda"] * terminal_multiple
    else:
        terminal_value = terminal_fcf / max(wacc - terminal_growth, 0.01)
    pv_terminal = terminal_value / ((1 + wacc) ** years)
    enterprise_value = present_value + pv_terminal
    equity_value = enterprise_value - net_debt - minorities + associates + non_operating_assets
    implied_exit_multiple = terminal_value / rows[-1]["ebitda"] if rows[-1]["ebitda"] else None
    terminal_value_share = pv_terminal / enterprise_value if enterprise_value else None
    return {
        "projection": rows,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "value_per_share": equity_value / shares if shares else None,
        "pv_terminal": pv_terminal,
        "terminal_value": terminal_value,
        "terminal_value_share": terminal_value_share,
        "implied_exit_multiple": implied_exit_multiple,
        "shares_outstanding": shares,
        "assumptions": assumptions,
        "ev_bridge": {
            "enterprise_value": enterprise_value,
            "net_debt": net_debt,
            "minorities": minorities,
            "associates": associates,
            "non_operating_assets": non_operating_assets,
            "equity_value": equity_value,
        },
    }


def run_scenario_analysis(financials: dict[str, float], scenarios: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Run base, upside, and downside valuation scenarios."""

    return {name: run_dcf(financials, assumptions) for name, assumptions in scenarios.items()}


def calculate_debt_capacity(financials: dict[str, float], target_net_debt_to_ebitda: float) -> dict[str, float]:
    """Estimate debt capacity under leverage constraints."""

    ebitda = financials.get("ebitda", 0.0)
    current_net_debt = financials.get("net_debt", 0.0)
    target_net_debt = ebitda * target_net_debt_to_ebitda
    return {
        "target_net_debt": target_net_debt,
        "current_net_debt": current_net_debt,
        "additional_capacity": target_net_debt - current_net_debt,
    }


def calculate_average_daily_value_traded(market_data: list[dict[str, float]], lookback_days: int = 90) -> float:
    """Calculate average daily value traded."""

    rows = market_data[-lookback_days:] if lookback_days else market_data
    values = [row.get("close", 0.0) * row.get("volume", 0.0) for row in rows]
    return sum(values) / len(values) if values else 0.0


def estimate_days_to_trade(position_value: float, adv_value: float, participation_rate: float = 0.2) -> float | None:
    """Estimate how many trading days are needed to build or exit a position."""

    usable_daily_liquidity = adv_value * participation_rate
    return _safe_div(position_value, usable_daily_liquidity)


def calculate_liquidity_score(
    average_daily_value: float,
    free_float: float,
    bid_ask_spread: float | None = None,
    trading_frequency: float = 1.0,
) -> float:
    """Score liquidity using value traded, spread, free float, and trading frequency."""

    adv_score = min(average_daily_value / 2_000_000, 1.0)
    float_score = max(0.0, min(free_float, 1.0))
    spread_score = 1.0 if bid_ask_spread is None else max(0.0, min(1.0, 1 - bid_ask_spread / 0.10))
    frequency_score = max(0.0, min(trading_frequency, 1.0))
    return round(100 * (0.40 * adv_score + 0.25 * float_score + 0.20 * spread_score + 0.15 * frequency_score), 1)


def estimate_block_trade_discount(block_size: float, average_daily_value: float, liquidity_score: float) -> float:
    """Estimate possible block discount based on size and liquidity."""

    days = estimate_days_to_trade(block_size, average_daily_value, 0.2) or 0.0
    base_discount = min(days * 0.0025, 0.15)
    liquidity_penalty = max(0.0, (60 - liquidity_score) / 1000)
    return round(base_discount + liquidity_penalty, 4)


def estimate_market_impact_cost(
    position_value: float,
    average_daily_value: float,
    volatility: float,
    bid_ask_spread: float,
    participation_rate: float,
    free_float: float,
    ownership_concentration: float,
) -> float:
    """Estimate all-in market impact cost for a block or staged trade."""

    if average_daily_value <= 0 or participation_rate <= 0:
        return float("inf")
    size_ratio = position_value / average_daily_value
    spread_cost = max(bid_ask_spread, 0.0) / 2
    footprint_cost = volatility * math.sqrt(max(size_ratio, 0.0)) * max(participation_rate, 0.01) * 0.12
    participation_cost = max(participation_rate - 0.15, 0.0) * 0.10
    float_penalty = max(0.30 - free_float, 0.0) * 0.08 + max(ownership_concentration - 0.60, 0.0) * 0.07
    return round(spread_cost + footprint_cost + participation_cost + float_penalty, 4)


def build_block_trade_execution_scenarios(
    position_value: float,
    average_daily_value: float,
    volatility: float,
    bid_ask_spread: float,
    free_float: float,
    ownership_concentration: float,
) -> list[dict[str, float | str]]:
    """Build execution scenarios for frontier-market block-trade review."""

    strategies = [
        ("Open-market low footprint", 0.05, "Patient build, lowest signalling risk"),
        ("Staged accumulation", 0.10, "Balanced footprint for normal market execution"),
        ("Standard block work", 0.20, "Requires broker colour and careful crossing"),
        ("Negotiated block", 0.35, "Potentially faster, but discount and seller concentration matter"),
        ("Exit stress case", 0.15, "Exit under weaker liquidity and wider spread assumptions"),
    ]
    rows: list[dict[str, float | str]] = []
    for name, participation, note in strategies:
        stress_multiplier = 0.65 if name == "Exit stress case" else 1.0
        stressed_adv = average_daily_value * stress_multiplier
        stressed_spread = bid_ask_spread * (1.8 if name == "Exit stress case" else 1.0)
        days = estimate_days_to_trade(position_value, stressed_adv, participation) or float("inf")
        impact = estimate_market_impact_cost(
            position_value,
            stressed_adv,
            volatility,
            stressed_spread,
            participation,
            free_float,
            ownership_concentration,
        )
        if days > 60 or impact > 0.12:
            decision = "watch only"
        elif "Negotiated" in name:
            decision = "tradable with block negotiation"
        elif days <= 20 and impact <= 0.06:
            decision = "tradable now"
        else:
            decision = "tradable with staged execution"
        rows.append(
            {
                "strategy": name,
                "participation_rate": participation,
                "adv_value": round(stressed_adv, 1),
                "days_to_trade": round(days, 1),
                "impact_cost": impact,
                "estimated_discount": round(max(impact, estimate_block_trade_discount(position_value, stressed_adv, 70)), 4),
                "decision": decision,
                "note": note,
            }
        )
    return rows


def build_wacc_from_components(
    risk_free_rate: float,
    beta: float,
    equity_risk_premium: float,
    country_risk_premium: float,
    size_premium: float,
    fx_risk_premium: float,
    pre_tax_cost_of_debt: float,
    tax_rate: float,
    target_debt_weight: float,
) -> dict[str, float]:
    """Build a local-currency WACC from explicit cost-of-capital components."""

    target_debt_weight = max(0.0, min(target_debt_weight, 0.95))
    cost_of_equity = risk_free_rate + beta * equity_risk_premium + country_risk_premium + size_premium + fx_risk_premium
    after_tax_cost_of_debt = pre_tax_cost_of_debt * (1 - tax_rate)
    wacc = cost_of_equity * (1 - target_debt_weight) + after_tax_cost_of_debt * target_debt_weight
    return {
        "risk_free_rate": risk_free_rate,
        "beta": beta,
        "equity_risk_premium": equity_risk_premium,
        "country_risk_premium": country_risk_premium,
        "size_premium": size_premium,
        "fx_risk_premium": fx_risk_premium,
        "pre_tax_cost_of_debt": pre_tax_cost_of_debt,
        "tax_rate": tax_rate,
        "target_debt_weight": target_debt_weight,
        "cost_of_equity": cost_of_equity,
        "after_tax_cost_of_debt": after_tax_cost_of_debt,
        "wacc": wacc,
    }


def flag_block_trade_risks(position_value: float, average_daily_value: float, ownership_concentration: float) -> list[str]:
    """Flag settlement, liquidity, market impact, and ownership concentration risks."""

    flags: list[str] = []
    days = estimate_days_to_trade(position_value, average_daily_value, 0.2)
    if days and days > 20:
        flags.append("Market impact risk: proposed trade is large versus observed liquidity.")
    if average_daily_value < 250_000:
        flags.append("Liquidity risk: average daily value traded is low.")
    if ownership_concentration > 0.60:
        flags.append("Ownership concentration risk: free float may be limited.")
    if not flags:
        flags.append("No major block-trade warning from current liquidity inputs.")
    return flags


def calculate_fx_sensitivity(financials: dict[str, float], fx_move: float = 0.10) -> dict[str, float]:
    """Estimate impact of currency movement on revenue, costs, debt, and valuation."""

    usd_debt = financials.get("foreign_currency_debt", 0.0)
    import_costs = financials.get("import_costs", 0.0)
    export_revenue = financials.get("export_revenue", 0.0)
    return {
        "debt_revaluation_impact": usd_debt * fx_move,
        "import_cost_impact": import_costs * fx_move,
        "export_revenue_offset": export_revenue * fx_move,
        "net_ebitda_impact": export_revenue * fx_move - import_costs * fx_move,
    }


def calculate_country_risk_adjusted_wacc(base_wacc: float, country_risk_premium: float, fx_risk_premium: float) -> float:
    """Add country and FX risk premia to cost of capital."""

    return base_wacc + country_risk_premium + fx_risk_premium


def build_capital_structure(financials: dict[str, float]) -> list[dict[str, float | str]]:
    """Map debt, equity, convertibles, leases, and other claims."""

    return [
        {"claim": "Senior secured debt", "amount": financials.get("senior_debt", 0.0), "ranking": "1"},
        {"claim": "Lease liabilities", "amount": financials.get("lease_liabilities", 0.0), "ranking": "2"},
        {"claim": "Convertible bonds", "amount": financials.get("convertibles", 0.0), "ranking": "3"},
        {"claim": "Equity value", "amount": financials.get("market_cap", 0.0), "ranking": "4"},
    ]


def calculate_recovery_waterfall(claims: list[dict[str, Any]], enterprise_value_scenarios: dict[str, float]) -> dict[str, list[dict[str, Any]]]:
    """Estimate recovery by creditor and equity class."""

    output: dict[str, list[dict[str, Any]]] = {}
    for scenario, enterprise_value in enterprise_value_scenarios.items():
        remaining = enterprise_value
        rows = []
        for claim in sorted(claims, key=lambda item: str(item.get("ranking", ""))):
            amount = float(claim.get("amount") or 0.0)
            recovery = min(amount, max(remaining, 0.0))
            remaining -= recovery
            rows.append(
                {
                    "claim": claim.get("claim"),
                    "amount": amount,
                    "recovery": recovery,
                    "recovery_rate": recovery / amount if amount else None,
                }
            )
        output[scenario] = rows
    return output


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator
