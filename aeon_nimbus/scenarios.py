"""DCF / bank assumption scenarios (Bull / Base / Bear).

The dynamic assumption sets that drive the model. *Base* is derived from the real
history; *Bull* and *Bear* shift the key levers by transparent deltas. Every set
is fully editable — in the Excel scenario matrix (a `=CHOOSE(Scenario, …)`
selector) or the platform Assumptions studio — and the model switches between them
without touching a formula. Shared by `excel_model` and the live app so the
workbook and the dashboard always agree.
"""

from __future__ import annotations

from typing import Any

from aeon_nimbus import forecast as fc
from aeon_nimbus.config import WACC_BY_COUNTRY, BANK_COE_BY_COUNTRY
from aeon_nimbus.country_risk import implied_wacc

ORDER = ["Bull", "Base", "Bear"]

# (key, label, format-kind) — drives the Assumptions matrix + the studio UI.
DCF_KEYS = [
    ("revenue_growth", "Revenue growth", "pct"),
    ("ebitda_margin", "EBITDA margin", "pct"),
    ("d_and_a_pct_sales", "D&A % of sales", "pct"),
    ("capex_pct_sales", "Capex % of sales", "pct"),
    ("working_capital_pct_sales", "Working capital % of Δrev", "pct"),
    ("tax_rate", "Tax rate", "pct"),
    ("wacc", "WACC", "pct"),
    ("terminal_growth", "Terminal growth", "pct"),
    ("exit_multiple", "Exit EV/EBITDA", "mult"),
]
BANK_KEYS = [
    ("loan_growth", "Loan growth", "pct"),
    ("nim", "Net interest margin", "pct"),
    ("cost_income", "Cost-to-income", "pct"),
    ("cost_of_risk", "Cost of risk", "pct"),
    ("roe", "Sustainable ROE", "pct"),
    ("coe", "Cost of equity", "pct"),
    ("growth", "Long-term growth", "pct"),
    ("car", "Capital adequacy", "pct"),
]
# scenario key -> Excel named range consumed by the model
DCF_NAMED = {
    "revenue_growth": "Rev_Growth", "ebitda_margin": "EBITDA_Margin", "d_and_a_pct_sales": "DA_Pct",
    "capex_pct_sales": "Capex_Pct", "working_capital_pct_sales": "WC_Pct", "tax_rate": "Tax_Rate",
    "wacc": "WACC", "terminal_growth": "Terminal_Growth_Rate", "exit_multiple": "Exit_Multiple",
}
BANK_NAMED = {
    "loan_growth": "Bank_LoanGrowth", "nim": "Bank_NIM", "cost_income": "Bank_CostIncome",
    "cost_of_risk": "Bank_CoR", "roe": "Bank_ROE", "coe": "Bank_COE", "growth": "Bank_Growth",
    "car": "Bank_CAR",
}


def keys_for(kind: str):
    return BANK_KEYS if kind == "bank" else DCF_KEYS


def named_for(kind: str):
    return BANK_NAMED if kind == "bank" else DCF_NAMED


def _f(x: Any, d: float) -> float:
    return float(x) if isinstance(x, (int, float)) else d


def _frac(x):
    return x / 100.0 if (x is not None and abs(x) > 1.5) else x


def _sane(x, lo, hi, default):
    """Keep an extracted ratio inside a plausible band; fall back to a sensible
    default when the source value is out of range (bad data must not surface an
    absurd driver like a 90% cost of risk)."""
    v = _frac(x)
    return v if (isinstance(v, (int, float)) and lo <= v <= hi) else default


def _base_dcf(fins: list, country: str | None) -> dict:
    a = fc.default_assumptions(fins) if fins else {}
    latest = fins[-1] if fins else {}
    rev, capex = latest.get("revenue"), latest.get("capex")
    capex_pct = abs(capex / rev) if (capex and rev) else 0.08
    return {
        "revenue_growth": round(min(max(_f(a.get("revenue_growth"), 0.06), -0.05), 0.15), 4),
        "ebitda_margin": round(_f(a.get("ebitda_margin"), 0.25), 4),
        "d_and_a_pct_sales": round(_f(a.get("d_and_a_pct_sales"), 0.08), 4),
        "capex_pct_sales": round(min(max(capex_pct, 0.02), 0.30), 4),
        "working_capital_pct_sales": 0.03,
        "tax_rate": round(_f(a.get("tax_rate"), 0.30), 4),
        "wacc": round(WACC_BY_COUNTRY.get(country) or implied_wacc(country), 4),
        "terminal_growth": 0.03,
        "exit_multiple": 6.0,
    }


def _base_bank(fins: list, bank: dict | None, country: str | None) -> dict:
    b = bank or {}
    latest = fins[-1] if fins else {}
    ni, eq = latest.get("net_income"), latest.get("total_equity")
    roe_raw = _frac(b.get("roe")) or ((ni / eq) if (ni and eq) else 0.15)
    return {
        "loan_growth": 0.12,
        "nim": round(_sane(b.get("nim"), 0.01, 0.15, 0.06), 4),
        "cost_income": round(_sane(b.get("cost_income"), 0.25, 0.80, 0.52), 4),
        "cost_of_risk": round(_sane(b.get("cost_of_risk"), 0.001, 0.06, 0.02), 4),
        "roe": round(_sane(roe_raw, 0.02, 0.20, 0.15), 4),
        "coe": round(BANK_COE_BY_COUNTRY.get(country) or implied_wacc(country, is_bank=True), 4),
        "growth": 0.06,
        "car": round(_sane(b.get("car"), 0.08, 0.35, 0.18), 4),
    }


def _shift_dcf(base: dict, kind: str) -> dict:
    s = dict(base)
    d = 1 if kind == "bull" else -1
    s["revenue_growth"] = round(min(max(base["revenue_growth"] + d * 0.03, -0.05), 0.20), 4)
    s["ebitda_margin"] = round(min(max(base["ebitda_margin"] + d * 0.02, 0.05), 0.60), 4)
    s["wacc"] = round(max(base["wacc"] - d * 0.02, 0.05), 4)
    s["terminal_growth"] = round(min(max(base["terminal_growth"] + d * 0.01, 0.0), 0.05), 4)
    s["exit_multiple"] = round(max(base["exit_multiple"] + d * 1.0, 3.0), 2)
    return s


def _shift_bank(base: dict, kind: str) -> dict:
    s = dict(base)
    d = 1 if kind == "bull" else -1
    s["loan_growth"] = round(max(base["loan_growth"] + d * 0.04, 0.0), 4)
    s["nim"] = round(max(base["nim"] + d * 0.005, 0.01), 4)
    s["cost_income"] = round(max(base["cost_income"] - d * 0.03, 0.30), 4)
    s["cost_of_risk"] = round(max(base["cost_of_risk"] - d * 0.005, 0.002), 4)
    s["roe"] = round(min(max(base["roe"] + d * 0.03, 0.05), 0.20), 4)
    s["coe"] = round(max(base["coe"] - d * 0.02, 0.08), 4)
    return s


def default_scenarios(fins: list, *, country: str | None = None,
                      is_bank: bool = False, bank: dict | None = None) -> dict:
    """Build the default Bull/Base/Bear sets from a company's history."""
    if is_bank:
        base = _base_bank(fins, bank, country)
        sets = {"Bull": _shift_bank(base, "bull"), "Base": base, "Bear": _shift_bank(base, "bear")}
        kind = "bank"
    else:
        base = _base_dcf(fins, country)
        sets = {"Bull": _shift_dcf(base, "bull"), "Base": base, "Bear": _shift_dcf(base, "bear")}
        kind = "dcf"
    return {"selected": "Base", "order": list(ORDER), "kind": kind, "sets": sets}


def normalise(scenarios: dict | None, fins: list, *, country=None, is_bank=False, bank=None) -> dict:
    """Return a valid scenarios dict, filling any gaps from the defaults so a
    partial/edited payload from the UI is always safe to model with."""
    default = default_scenarios(fins, country=country, is_bank=is_bank, bank=bank)
    if not scenarios or "sets" not in scenarios:
        return default
    out = {"selected": scenarios.get("selected", "Base"), "order": scenarios.get("order", default["order"]),
           "kind": default["kind"], "sets": {}}
    if out["selected"] not in out["order"]:
        out["selected"] = "Base"
    for name in out["order"]:
        base = default["sets"].get(name, default["sets"]["Base"])
        given = (scenarios.get("sets") or {}).get(name, {})
        out["sets"][name] = {k: _f(given.get(k), base[k]) for k in base}
    return out


def active_assumptions(scenarios: dict) -> dict:
    """The assumption set for the currently-selected scenario."""
    return scenarios["sets"].get(scenarios.get("selected", "Base"), next(iter(scenarios["sets"].values())))
