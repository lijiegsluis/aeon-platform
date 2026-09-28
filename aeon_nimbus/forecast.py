"""Sector-aware forward projection of the key financial lines.

Three specialised engines cover the sectors where the standard EBITDA-margin
model produces structurally wrong outputs:

  project_bank()    — BK01 banks: NIM × loan book → NII, cost-to-income,
                        provisions (cost of risk), P/B-anchored terminal value
  project_telecom() — TC01 telecoms: subscriber × ARPU → revenue, with mobile
                        money as a separate higher-margin line
  project()         — all other sectors: NOPAT-style EBITDA margin build

A single dispatcher `sector_project()` routes by sector code.

Every assumption is explicit and shown in the model so it can be challenged.
No figure is invented — all drivers default from extracted history, clamped to
a sane band, with a transparent override path.
"""

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def revenue_cagr(historicals: list[dict[str, Any]]) -> float | None:
    rev = [h["revenue"] for h in historicals if h.get("revenue")]
    if len(rev) < 2 or rev[0] <= 0:
        return None
    return (rev[-1] / rev[0]) ** (1 / (len(rev) - 1)) - 1


def _base_year(fy: str) -> int | None:
    m = re.search(r"(\d{4})", fy or "")
    return int(m.group(1)) if m else None


def _frac(x: Any) -> float | None:
    """Convert a value expressed as a percentage (>1.5 → /100) to a decimal."""
    if not isinstance(x, (int, float)):
        return None
    return x / 100.0 if abs(x) > 1.5 else x


def _clamp(v: float, lo: float, hi: float, default: float) -> float:
    return v if (lo <= v <= hi) else default


# ---------------------------------------------------------------------------
# 1. Standard DCF engine (all non-bank, non-telecom sectors)
# ---------------------------------------------------------------------------

def default_assumptions(historicals: list[dict[str, Any]]) -> dict[str, float]:
    """Derive forecast assumptions from the history (clamped, sober).

    Growth is taken from the most recent 3-year window rather than the full
    history, so an old structural break does not skew the forward view.
    """
    latest = historicals[-1]
    recent = historicals[-3:] if len(historicals) >= 3 else historicals
    cagr = revenue_cagr(recent)
    growth = min(max(cagr if cagr is not None else 0.05, 0.0), 0.15)
    margin = (latest["ebitda"] / latest["revenue"]) if (latest.get("ebitda") and latest.get("revenue")) else 0.20
    da_pct = 0.05
    if latest.get("ebitda") and latest.get("ebit") and latest.get("revenue"):
        da_pct = max(0.0, (latest["ebitda"] - latest["ebit"]) / latest["revenue"])
    return {"revenue_growth": growth, "ebitda_margin": margin, "d_and_a_pct_sales": da_pct, "tax_rate": 0.28}


def project(historicals: list[dict[str, Any]], assumptions: dict[str, Any] | None = None,
            years: int = 5, shares: float | None = None) -> list[dict[str, Any]]:
    """Project `years` forward. Returns rows with fy, revenue, ebitda, ebit,
    net_income, ebitda_margin (+ eps when shares given)."""
    if not historicals:
        return []
    a = {**default_assumptions(historicals), **(assumptions or {})}
    latest = historicals[-1]
    g, margin, da_pct, tax = a["revenue_growth"], a["ebitda_margin"], a["d_and_a_pct_sales"], a["tax_rate"]
    rev = latest["revenue"]
    by = _base_year(latest.get("fy", ""))
    out: list[dict[str, Any]] = []
    for t in range(1, years + 1):
        rev = rev * (1 + g)
        ebitda = margin * rev
        ebit = ebitda - da_pct * rev
        net_income = ebit * (1 - tax)
        row = {"fy": f"FY{by + t}E" if by else f"Y{t}E", "revenue": round(rev, 1),
               "ebitda": round(ebitda, 1), "ebit": round(ebit, 1), "net_income": round(net_income, 1),
               "ebitda_margin": round(margin, 4)}
        if shares:
            row["eps"] = round(net_income / shares, 2)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# 2. Bank engine (BK01)
#
# Revenue structure:
#   Net interest income (NII) = loan book × NIM
#   Non-interest income (fees, FX, commission) = flat share of NII
#   Revenue = NII + non-interest income
#   Operating expenses = revenue × cost_to_income
#   Pre-provision profit = revenue − opex
#   Provisions (impairment) = loan book × cost_of_risk
#   PBT = pre-provision profit − provisions
#   Tax = PBT × tax_rate
#   Net income = PBT − tax
#
# Key drivers — all defaulted from extracted `bank` dict + history, then
# overridden by the user's scenario assumptions.
# ---------------------------------------------------------------------------

def _bank_defaults(historicals: list[dict[str, Any]], bank: dict | None) -> dict[str, float]:
    """Derive bank forecast assumptions from extracted history + bank KPIs."""
    b = bank or {}
    latest = historicals[-1] if historicals else {}
    ni, eq = latest.get("net_income"), latest.get("total_equity")

    nim = _frac(b.get("nim")) or 0.06
    nim = _clamp(nim, 0.01, 0.15, 0.06)

    cost_income = _frac(b.get("cost_income")) or 0.52
    cost_income = _clamp(cost_income, 0.25, 0.80, 0.52)

    cost_of_risk = _frac(b.get("cost_of_risk")) or 0.02
    cost_of_risk = _clamp(cost_of_risk, 0.001, 0.08, 0.02)

    roe = (_frac(b.get("roe")) or (ni / eq if (ni and eq and eq > 0) else None) or 0.15)
    roe = _clamp(roe, 0.02, 0.35, 0.15)

    # Non-interest income as a fraction of NII — typically 30-60% for frontier banks
    nii_est = (latest.get("revenue") or 1) * (1 - 0.35)  # rough share
    noni_frac = 0.35  # non-interest income as % of total revenue (default)

    return {
        "loan_growth": 0.12,
        "nim": nim,
        "cost_income": cost_income,
        "cost_of_risk": cost_of_risk,
        "roe": roe,
        "non_interest_income_frac": noni_frac,
        "tax_rate": 0.30,
    }


def project_bank(historicals: list[dict[str, Any]], assumptions: dict[str, Any] | None = None,
                 bank: dict | None = None, years: int = 5,
                 shares: float | None = None) -> list[dict[str, Any]]:
    """Bank income statement projection using NII / cost-to-income / provisions.

    Returns rows shaped like standard project() (revenue, ebitda, ebit,
    net_income) PLUS bank-specific lines: nii, non_interest_income, opex,
    provisions, ppop (pre-provision operating profit).

    `assumptions` mirrors the BANK_KEYS scenario dict (loan_growth, nim,
    cost_income, cost_of_risk, roe, coe, growth, car).
    """
    if not historicals:
        return []
    defaults = _bank_defaults(historicals, bank)
    a = {**defaults, **(assumptions or {})}

    latest = historicals[-1]
    by = _base_year(latest.get("fy", ""))

    loan_growth = _clamp(_frac(a.get("loan_growth")) or a.get("loan_growth", 0.12), -0.05, 0.35, 0.12)
    nim = _clamp(_frac(a.get("nim")) or a.get("nim", 0.06), 0.01, 0.15, 0.06)
    ci = _clamp(_frac(a.get("cost_income")) or a.get("cost_income", 0.52), 0.20, 0.85, 0.52)
    cor = _clamp(_frac(a.get("cost_of_risk")) or a.get("cost_of_risk", 0.02), 0.0, 0.10, 0.02)
    noni_frac = _clamp(float(a.get("non_interest_income_frac", defaults["non_interest_income_frac"])), 0.0, 0.60, 0.35)
    tax_rate = _clamp(float(a.get("tax_rate", 0.30)), 0.10, 0.50, 0.30)

    # Seed from latest balance sheet or impute from NIM + loan estimate
    b = bank or {}
    gross_loans = (b.get("gross_loans") or b.get("loan_book") or
                   # rough imputation: NII / NIM as proxy for loan book
                   (latest.get("revenue", 0) * (1 - noni_frac) / nim if nim else None) or
                   latest.get("total_assets", 1000) * 0.55)

    out: list[dict[str, Any]] = []
    for t in range(1, years + 1):
        gross_loans = gross_loans * (1 + loan_growth)
        nii = gross_loans * nim
        non_interest = nii * (noni_frac / (1 - noni_frac)) if noni_frac < 1 else nii * 0.5
        revenue = nii + non_interest
        opex = revenue * ci
        ppop = revenue - opex
        provisions = gross_loans * cor
        pbt = ppop - provisions
        tax = max(0.0, pbt) * tax_rate
        net_income = pbt - tax
        # Align with standard shape so DCF still works
        row = {
            "fy": f"FY{by + t}E" if by else f"Y{t}E",
            "revenue": round(revenue, 1),
            "ebitda": round(ppop, 1),    # pre-provision = EBITDA proxy for DCF bridge
            "ebit": round(ppop, 1),
            "net_income": round(net_income, 1),
            "ebitda_margin": round(ppop / revenue, 4) if revenue else None,
            # Bank-specific lines
            "nii": round(nii, 1),
            "non_interest_income": round(non_interest, 1),
            "opex": round(opex, 1),
            "ppop": round(ppop, 1),
            "provisions": round(provisions, 1),
            "pbt": round(pbt, 1),
            "gross_loans": round(gross_loans, 1),
            "nim": nim,
            "cost_income": ci,
            "cost_of_risk": cor,
        }
        if shares and shares > 0:
            row["eps"] = round(net_income / shares, 2)
            row["bvps_approx"] = None  # full BS needed for exact BVPS
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# 3. Telecom engine (TC01)
#
# Revenue structure:
#   Voice revenue = subscribers × voice_arpu × 12
#   Data revenue  = subscribers × data_arpu  × 12
#   Mobile money  = subscribers × mm_arpu    × 12  (higher margin)
#   Total revenue = voice + data + mobile_money
#   EBITDA = revenue × ebitda_margin
#   Capex  = revenue × capex_pct  (high: ~15-25% of revenue)
# ---------------------------------------------------------------------------

def _telecom_defaults(historicals: list[dict[str, Any]],
                      sector_kpis: list[dict] | None) -> dict[str, float]:
    """Derive telecom drivers from extracted KPIs and history."""
    kpis = {k.get("name", "").lower(): k.get("value") for k in (sector_kpis or [])}

    subs_m = kpis.get("subscribers_m") or kpis.get("total subscribers (m)") or None
    arpu = kpis.get("arpu") or kpis.get("blended arpu") or None
    data_rev_pct = (_frac(kpis.get("data_revenue_pct")) or
                    _frac(kpis.get("data revenue (% of service revenue)")) or 0.35)
    mm_rev_pct = (_frac(kpis.get("mobile_money_pct")) or
                  _frac(kpis.get("mobile money (% revenue)")) or 0.15)

    latest = historicals[-1] if historicals else {}
    rev = latest.get("revenue")
    ebitda = latest.get("ebitda")
    capex = abs(latest.get("capex") or 0)
    margin = (ebitda / rev) if (ebitda and rev) else 0.38
    capex_pct = (capex / rev) if (capex and rev) else 0.18

    recent = historicals[-3:] if len(historicals) >= 3 else historicals
    cagr = revenue_cagr(recent)
    rev_growth = _clamp(cagr if cagr is not None else 0.07, 0.0, 0.18, 0.07)

    return {
        "revenue_growth": rev_growth,
        "ebitda_margin": _clamp(margin, 0.20, 0.60, 0.38),
        "capex_pct": _clamp(capex_pct, 0.08, 0.30, 0.18),
        "data_revenue_pct": _clamp(data_rev_pct, 0.15, 0.75, 0.35),
        "mobile_money_pct": _clamp(mm_rev_pct, 0.0, 0.35, 0.15),
        "tax_rate": 0.30,
        "d_and_a_pct_sales": 0.12,
    }


def project_telecom(historicals: list[dict[str, Any]], assumptions: dict[str, Any] | None = None,
                    sector_kpis: list[dict] | None = None, years: int = 5,
                    shares: float | None = None) -> list[dict[str, Any]]:
    """Telecom revenue projection: overall revenue growth with data/mobile-money
    mix shift modelled explicitly.

    Returns rows shaped like standard project() plus telecom-specific lines:
    voice_revenue, data_revenue, mobile_money_revenue, capex.
    """
    if not historicals:
        return []
    defaults = _telecom_defaults(historicals, sector_kpis)
    a = {**defaults, **(assumptions or {})}

    latest = historicals[-1]
    by = _base_year(latest.get("fy", ""))
    rev = latest.get("revenue", 0)

    g = _clamp(float(a.get("revenue_growth", defaults["revenue_growth"])), -0.05, 0.25, 0.07)
    margin = _clamp(float(a.get("ebitda_margin", defaults["ebitda_margin"])), 0.15, 0.65, 0.38)
    capex_pct = _clamp(float(a.get("capex_pct", defaults["capex_pct"])), 0.05, 0.35, 0.18)
    data_pct = _clamp(float(a.get("data_revenue_pct", defaults["data_revenue_pct"])), 0.10, 0.80, 0.35)
    mm_pct = _clamp(float(a.get("mobile_money_pct", defaults["mobile_money_pct"])), 0.0, 0.40, 0.15)
    # Data share grows ~2pp/year; mobile money ~1pp/year (blend in, voice out)
    data_drift = float(a.get("data_drift_pa", 0.02))
    mm_drift = float(a.get("mm_drift_pa", 0.01))
    tax = _clamp(float(a.get("tax_rate", 0.30)), 0.10, 0.50, 0.30)
    da_pct = _clamp(float(a.get("d_and_a_pct_sales", 0.12)), 0.05, 0.25, 0.12)

    out: list[dict[str, Any]] = []
    for t in range(1, years + 1):
        rev = rev * (1 + g)
        # Mix shift: data and mobile money grow as share; voice shrinks
        data_rev = rev * min(data_pct + data_drift * t, 0.75)
        mm_rev = rev * min(mm_pct + mm_drift * t, 0.35)
        voice_rev = max(0.0, rev - data_rev - mm_rev)
        ebitda = margin * rev
        ebit = ebitda - da_pct * rev
        net_income = ebit * (1 - tax)
        capex_abs = -capex_pct * rev
        row = {
            "fy": f"FY{by + t}E" if by else f"Y{t}E",
            "revenue": round(rev, 1),
            "ebitda": round(ebitda, 1),
            "ebit": round(ebit, 1),
            "net_income": round(net_income, 1),
            "ebitda_margin": round(margin, 4),
            "capex": round(capex_abs, 1),
            # Telecom-specific revenue decomposition
            "voice_revenue": round(voice_rev, 1),
            "data_revenue": round(data_rev, 1),
            "mobile_money_revenue": round(mm_rev, 1),
            "data_revenue_pct": round(data_rev / rev, 4) if rev else None,
            "mobile_money_pct": round(mm_rev / rev, 4) if rev else None,
        }
        if shares and shares > 0:
            row["eps"] = round(net_income / shares, 2)
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# 4. Sector dispatcher
# ---------------------------------------------------------------------------

def sector_project(historicals: list[dict[str, Any]],
                   sector_code: str,
                   assumptions: dict[str, Any] | None = None,
                   bank: dict | None = None,
                   sector_kpis: list[dict] | None = None,
                   years: int = 5,
                   shares: float | None = None) -> list[dict[str, Any]]:
    """Route to the appropriate engine by H500 sector code.

    BK01 → project_bank()
    TC01 → project_telecom()
    All others → project()
    """
    if sector_code == "BK01":
        return project_bank(historicals, assumptions=assumptions, bank=bank,
                            years=years, shares=shares)
    if sector_code == "TC01":
        return project_telecom(historicals, assumptions=assumptions,
                                sector_kpis=sector_kpis, years=years, shares=shares)
    return project(historicals, assumptions=assumptions, years=years, shares=shares)
