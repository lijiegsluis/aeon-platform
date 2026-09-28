"""Supporting schedules — the roll-forwards that drive the forecast.

A research-grade model does not apply a percentage to revenue and call it
depreciation. Each balance has its own schedule that opens, moves and closes,
and the three statements read from those schedules:

    PPE           open + capex - depreciation                      = close
    Intangibles   open + additions - amortisation                  = close
    Leases        open + additions + interest - payments           = close
    Debt          open + drawdowns - repayments                    = close
    Working cap.  receivables (DSO), inventory (DIO), payables (DPO)
    Equity        open + profit - dividends + other                = close
    Tax           current charge, cash paid, deferred movement

Every schedule returns one row per forecast year, and every row states its own
opening and closing balance, so the workbook can show the movement rather than
a single number the reader has to trust.
"""

from __future__ import annotations

from typing import Any


def _n(x: Any, d: float = 0.0) -> float:
    return float(x) if isinstance(x, (int, float)) else d


# --------------------------------------------------------------------------
# assumptions for the schedules, derived from the reported history
# --------------------------------------------------------------------------

SCHEDULE_KEYS: list[tuple[str, str, str]] = [
    ("ppe_life", "PPE depreciation life (years)", "num"),
    ("intangible_life", "Intangible amortisation life (years)", "num"),
    ("intangible_additions_pct", "Intangible additions % of capex", "pct"),
    ("lease_additions_pct", "Lease additions % of capex", "pct"),
    ("lease_rate", "Lease interest rate", "pct"),
    ("lease_term", "Average lease term (years)", "num"),
    ("cost_of_debt", "Cost of debt", "pct"),
    ("debt_repayment_pct", "Debt repaid per year", "pct"),
    ("target_nd_ebitda", "Target net debt / EBITDA", "mult"),
    ("interest_on_cash", "Interest earned on cash", "pct"),
    ("dso", "Receivable days (DSO)", "num"),
    ("dio", "Inventory days (DIO)", "num"),
    ("dpo", "Payable days (DPO)", "num"),
    ("payout_ratio", "Dividend payout ratio", "pct"),
    ("tax_rate", "Effective tax rate", "pct"),
    ("deferred_tax_pct", "Deferred share of tax charge", "pct"),
    ("minority_share", "Minority share of subsidiary profit", "pct"),
]


def default_schedule_assumptions(fins: list[dict], notes: dict | None = None) -> dict[str, float]:
    """Infer schedule assumptions from the audited history.

    Lives come from the actual depreciation charge against the actual asset
    base, days from the actual working-capital balances, the cost of debt from
    interest paid over average borrowings. Nothing here is a house default when
    the accounts can answer the question.
    """
    notes = notes or {}
    latest = fins[-1] if fins else {}
    prior = fins[-2] if len(fins) > 1 else latest
    rev = _n(latest.get("revenue"))
    ebitda = _n(latest.get("ebitda"))
    ebit = _n(latest.get("ebit"))
    da = _n(notes.get("depreciation_amortisation")) or max(ebitda - ebit, 0.0)
    ppe = _n(latest.get("ppe")) or _n(notes.get("ppe"))
    intang = _n(latest.get("intangibles")) or _n(notes.get("intangibles"))
    leases = _n(notes.get("lease_liabilities"))
    debt = _n(notes.get("gross_debt")) or _n(latest.get("total_debt"))
    interest = abs(_n(notes.get("interest_expense")))
    recv = _n(latest.get("receivables"))
    inv = _n(latest.get("inventories"))
    pay = abs(_n(latest.get("payables")))
    div = abs(_n(latest.get("dividends_paid")))
    ni = _n(latest.get("net_income"))

    # split D&A between tangible and intangible in proportion to the asset base
    asset_base = (ppe + intang) or 1.0
    ppe_da = da * (ppe / asset_base) if da else 0.0
    int_da = da - ppe_da
    ppe_life = (ppe / ppe_da) if (ppe and ppe_da) else 8.0
    int_life = (intang / int_da) if (intang and int_da) else 10.0

    return {
        "ppe_life": round(min(max(ppe_life, 4.0), 25.0), 1),
        "intangible_life": round(min(max(int_life, 3.0), 20.0), 1),
        "intangible_additions_pct": 0.10,
        "lease_additions_pct": 0.08,
        "lease_rate": 0.09,
        "lease_term": 8.0,
        "cost_of_debt": round(min(max(interest / debt if debt else 0.12, 0.03), 0.25), 4),
        "debt_repayment_pct": 0.15,
        "target_nd_ebitda": 1.0,
        "interest_on_cash": 0.05,
        "dso": round(min(max((recv / rev * 365) if (recv and rev) else 45.0, 5.0), 180.0), 1),
        "dio": round(min(max((inv / rev * 365) if (inv and rev) else 20.0, 1.0), 180.0), 1),
        "dpo": round(min(max((pay / rev * 365) if (pay and rev) else 70.0, 5.0), 260.0), 1),
        "payout_ratio": round(min(max((div / ni) if (ni and div) else 0.50, 0.0), 1.0), 4),
        "tax_rate": 0.30,
        "deferred_tax_pct": 0.10,
        "minority_share": 0.0,
    }


# --------------------------------------------------------------------------
# the schedules
# --------------------------------------------------------------------------

def ppe_schedule(open_ppe: float, capex: list[float], a: dict) -> list[dict]:
    """Open + capex - depreciation = close, depreciating the average balance."""
    life = max(_n(a.get("ppe_life"), 8.0), 1.0)
    rows, bal = [], _n(open_ppe)
    for t, cx in enumerate(capex, start=1):
        add = cx * (1 - _n(a.get("intangible_additions_pct"), 0.0))
        depn = ((bal + add) / 2 + bal / 2) / life          # average-balance basis
        depn = min(depn, bal + add)
        close = bal + add - depn
        rows.append({"t": t, "open": bal, "additions": add,
                     "depreciation": depn, "close": close})
        bal = close
    return rows


def intangible_schedule(open_intang: float, capex: list[float], a: dict) -> list[dict]:
    life = max(_n(a.get("intangible_life"), 10.0), 1.0)
    rows, bal = [], _n(open_intang)
    for t, cx in enumerate(capex, start=1):
        add = cx * _n(a.get("intangible_additions_pct"), 0.10)
        amort = min((bal + add / 2) / life, bal + add)
        close = bal + add - amort
        rows.append({"t": t, "open": bal, "additions": add,
                     "amortisation": amort, "close": close})
        bal = close
    return rows


def lease_schedule(open_lease: float, capex: list[float], a: dict) -> list[dict]:
    """IFRS 16: new right-of-use assets accrete interest and amortise through
    the lease payment, so both the P&L charge and the cash payment are visible."""
    rate = _n(a.get("lease_rate"), 0.09)
    term = max(_n(a.get("lease_term"), 8.0), 1.0)
    rows, bal = [], _n(open_lease)
    for t, cx in enumerate(capex, start=1):
        add = cx * _n(a.get("lease_additions_pct"), 0.08)
        interest = bal * rate
        principal = min((bal + add) / term, bal + add)
        close = bal + add + interest - (principal + interest)
        rows.append({"t": t, "open": bal, "additions": add, "interest": interest,
                     "principal": principal, "payment": principal + interest,
                     "depreciation": principal, "close": close})
        bal = close
    return rows


def debt_schedule(open_debt: float, years: int, a: dict,
                  funding_need: list[float] | None = None) -> list[dict]:
    """Scheduled amortisation, plus any drawdown the cash flow requires.

    Interest is charged on the average balance, which is what the accounts do
    and what a percentage-of-revenue proxy gets wrong when leverage changes.
    """
    rate = _n(a.get("cost_of_debt"), 0.12)
    repay_pct = _n(a.get("debt_repayment_pct"), 0.15)
    rows, bal = [], _n(open_debt)
    for t in range(1, years + 1):
        need = _n((funding_need or [0.0] * years)[t - 1]) if funding_need else 0.0
        repay = bal * repay_pct
        draw = max(need, 0.0)
        close = max(bal - repay + draw, 0.0)
        interest = (bal + close) / 2 * rate
        rows.append({"t": t, "open": bal, "drawdown": draw, "repayment": repay,
                     "interest": interest, "close": close})
        bal = close
    return rows


def working_capital_schedule(revenue: list[float], cogs: list[float], a: dict,
                             open_wc: dict | None = None) -> list[dict]:
    """Receivables, inventory and payables from days, with the cash movement.

    A negative net working capital is a funding source, which matters for
    prepaid businesses: growth releases cash rather than consuming it.
    """
    dso, dio, dpo = _n(a.get("dso"), 45.0), _n(a.get("dio"), 20.0), _n(a.get("dpo"), 70.0)
    prior = None
    if open_wc:
        prior = {"receivables": _n(open_wc.get("receivables")),
                 "inventory": _n(open_wc.get("inventory")),
                 "payables": _n(open_wc.get("payables"))}
        prior["net"] = prior["receivables"] + prior["inventory"] - prior["payables"]
    rows = []
    for t, (rev, cs) in enumerate(zip(revenue, cogs), start=1):
        recv = rev * dso / 365
        inv = abs(cs) * dio / 365
        pay = abs(cs) * dpo / 365
        net = recv + inv - pay
        change = (net - prior["net"]) if prior else 0.0
        rows.append({"t": t, "receivables": recv, "inventory": inv, "payables": pay,
                     "net_working_capital": net, "change_in_wc": change,
                     "cash_impact": -change})
        prior = {"receivables": recv, "inventory": inv, "payables": pay, "net": net}
    return rows


def dividend_schedule(net_income: list[float], a: dict) -> list[dict]:
    payout = _n(a.get("payout_ratio"), 0.5)
    return [{"t": t, "net_income": ni, "payout_ratio": payout,
             "dividend": max(ni, 0.0) * payout, "retained": ni - max(ni, 0.0) * payout}
            for t, ni in enumerate(net_income, start=1)]


def tax_schedule(pbt: list[float], a: dict) -> list[dict]:
    rate = _n(a.get("tax_rate"), 0.30)
    deferred_pct = _n(a.get("deferred_tax_pct"), 0.10)
    rows = []
    for t, p in enumerate(pbt, start=1):
        charge = max(p, 0.0) * rate
        deferred = charge * deferred_pct
        rows.append({"t": t, "pbt": p, "tax_charge": charge,
                     "deferred_tax": deferred, "cash_tax": charge - deferred})
    return rows


def equity_schedule(open_equity: float, net_income: list[float],
                    dividends: list[float], minorities: list[float] | None = None) -> list[dict]:
    rows, bal = [], _n(open_equity)
    for t, (ni, dv) in enumerate(zip(net_income, dividends), start=1):
        mi = _n((minorities or [0.0] * len(net_income))[t - 1])
        close = bal + ni - dv + mi
        rows.append({"t": t, "open": bal, "profit": ni, "dividends": -dv,
                     "minority": mi, "close": close})
        bal = close
    return rows
