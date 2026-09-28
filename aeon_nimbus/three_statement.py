"""Integrated three-statement forecast: income statement, balance sheet, cash flow.

The previous engine forecast a P&L and a free-cash-flow line. That cannot show
whether the plan funds itself, what leverage does, or whether the balance sheet
still stands at the end of it. This module builds all three statements from the
operating drivers and the supporting schedules, and ties them together the way
a model on a desk is tied together:

    drivers   -> revenue, cash costs, capex          (sector KPIs)
    schedules -> depreciation, interest, tax, WC     (roll-forwards)
    IS        -> EBITDA -> EBIT -> PBT -> net income
    CF        -> operating, investing, financing     (indirect method)
    BS        -> assets = liabilities + equity       (checked, every year)

The balance check is not decoration. Cash is the plug that absorbs the cash
flow, every other movement is mirrored on both sides, and `balanced` is
asserted per year so a broken model announces itself instead of quietly
producing a valuation.

Groups operating in more than one country are built per country and then
consolidated, so Kenya and Ethiopia (or Nigeria and the rest) carry their own
drivers, margins and capex rather than being averaged into one line.
"""

from __future__ import annotations

import re
from typing import Any

from aeon_nimbus import drivers as dr
from aeon_nimbus import schedules as sch


def _n(x: Any, d: float = 0.0) -> float:
    return float(x) if isinstance(x, (int, float)) else d


def _fy_int(fy: str) -> int | None:
    m = re.search(r"(\d{4})", fy or "")
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------
# opening balance sheet
# --------------------------------------------------------------------------

# Where a bank's book is not reported, it is derived from total assets on these
# ratios. They are deliberately conservative sector norms, and any model built
# on them is marked as derived so a reader never mistakes one for a filed
# figure. Collecting the real balances replaces them automatically.
LOANS_TO_ASSETS = 0.55
DEPOSITS_TO_ASSETS = 0.72


def _ratio_or_pct(x) -> float:
    """Loan-to-deposit arrives as 60.6 from one source and 0.85 from another."""
    if not isinstance(x, (int, float)) or not x:
        return 0.0
    return float(x) / 100.0 if x > 1.5 else float(x)


# A SUBTOTAL is not a balance. Adding "total assets" to the sheet alongside the
# assets it totals counts everything twice, and a total of the equity components
# sits next to the components themselves. These are checked FIRST, so widening
# the needles below cannot start double-counting by accident.
_SUBTOTALS = (
    "total assets", "total liabilities", "total equity and liabilities",
    "total liabilities and equity", "total current", "total non-current",
    "total shareholders", "total capital",
    # equity components: the sheet opens at total equity, which already
    # contains all of them
    "share capital", "share premium", "retained earnings", "other reserves",
    "revaluation reserve", "translation reserve", "non-controlling interest",
    # bare section headers a parser picks up as if they were rows
    "current assets", "non-current assets", "current liabilities",
    "non-current liabilities", "assets", "liabilities", "equity",
)

_BS_MAP = {
    "ppe": ("property and equipment", "property, plant"),
    # singular as well: two filings write "Intangible asset" and the row was
    # being dropped over the letter s
    "intangibles": ("intangible asset", "goodwill"),
    "rou": ("right-of-use", "right of use", "indefeasible rights-of-use"),
    "inventories": ("inventories", "inventory"),
    "receivables": ("trade and other receivables", "receivables"),
    "cash": ("net cash and cash equivalents", "cash and cash equivalents",
             "restricted cash"),
    "payables": ("payables and accrued", "trade and other payables"),
    "equity": ("total equity",),
    # The extraction now returns these, and naming them shrinks the residual to
    # what is genuinely unexplained instead of everything that was never read.
    "investments": ("investment securities", "investments", "investment propert",
                    "investment in associates", "associates and joint",
                    "loans receivable"),
    "other_assets": ("other assets", "other current assets", "contract asset"),
    "other_liabilities": ("other liabilities", "other current liabilities"),
    # "deferred income tax" and "deferred tax asset" were matched and a bare
    # "deferred tax" was not, which is how fifteen rows reached the residual.
    # The liability spelling is caught below so it cannot land on the asset side.
    "deferred_tax_asset": ("deferred income tax", "deferred tax"),
    # Real liabilities that were only ever read from the notes, so a company
    # whose notes were not parsed carried them in the plug instead.
    "provisions_parsed": ("provisions",),
    "leases_parsed": ("lease liabilit",),
    "debt_parsed": ("borrowings", "interest-bearing", "interest bearing"),
}

# Needles that would otherwise be caught by an asset key. Checked before the map,
# so "deferred tax liability" cannot be read as a deferred tax ASSET.
_WRONG_SIDE = {
    "deferred_tax_asset": ("deferred tax liabilit", "deferred income tax liabilit"),
}


# Every component below comes off a parsed statement table. Total assets comes
# off the headline series, which is cross-checked elsewhere against market cap
# and is the figure the platform reports. When the two disagree the series wins,
# because a component that contradicts its own reported total is either in the
# wrong unit or matched to the wrong row, and there is no third possibility.
_ASSET_COMPONENTS = ("ppe", "intangibles", "rou", "inventories", "receivables",
                     "cash", "investments", "other_assets", "deferred_tax_asset")


def reconcile_opening(out: dict[str, float], anchor: float) -> tuple[dict[str, float], str]:
    """Force the parsed balance sheet to agree with reported total assets.

    Three outcomes, and the middle one is the whole point:

      · agrees            -> kept as read
      · off by a power of ten -> rescaled, because one table was in thousands
        and the series was in millions, which is a units bug rather than a
        wrong figure
      · contradicts       -> DISCARDED, and the caller rebuilds the opening from
        the series alone

    The third is the one that matters. Sasol's parsed sheet carried property
    of 158,041,000 against reported total assets of 359,555, and the model
    dutifully forecast a balance sheet of ZAR 370 trillion and a target price
    173,513% above the quote. A residual the reader can see beats a number
    the reader cannot question, so an opening that cannot be reconciled is
    dropped rather than scaled until it looks plausible.

    Returns the (possibly rescaled) components and a note naming what happened.
    """
    if not anchor or anchor <= 0:
        return out, ""

    def _total(d):
        return sum(_n(d.get(k)) for k in _ASSET_COMPONENTS)

    # A negative asset is never a unit problem, it is a row matched to the wrong
    # line, and rescaling would only make it a smaller wrong number.
    if any(_n(out.get(k)) < 0 for k in _ASSET_COMPONENTS):
        bad = [k for k in _ASSET_COMPONENTS if _n(out.get(k)) < 0]
        return ({k: 0.0 for k in out}, f"parsed balance sheet discarded: negative {', '.join(bad)}")

    read, biggest = _total(out), max(_n(out.get(k)) for k in _ASSET_COMPONENTS)
    if not read:
        return out, ""

    # Components summing to LESS than total assets is normal: not every line is
    # read, and the difference shows on the face of the model as a residual.
    # Summing to MORE is arithmetically impossible and must be dealt with.
    if read <= anchor * 1.05:
        return out, ""

    # Pick the decimal factor off the LARGEST component rather than the sum. One
    # row matched to the wrong line inflates the sum and would drag the whole
    # sheet down a decade with it: Sasol's sum wants 1/10,000, while its largest
    # real balance, property at 158,041,000 against total assets of 359,555,
    # plainly wants 1/1,000. No single asset can exceed total assets, so that is
    # the test with a defensible meaning.
    factor = 0.0
    for k in range(1, 9):
        if biggest / (10.0 ** k) <= anchor * 1.05:
            factor = 10.0 ** k
            break
    if not factor:
        return ({k: 0.0 for k in out},
                f"parsed balance sheet discarded: largest component {biggest:,.0f} against "
                f"reported total assets of {anchor:,.0f}, and no decimal factor reconciles them")

    scaled = {key: (v / factor if isinstance(v, (int, float)) and not isinstance(v, bool)
                    else v) for key, v in out.items()}

    # Right scale, still impossible. Some row is matched to the wrong line, and
    # there is no way to tell which from here, so none of it is used. The model
    # then shows an honest residual instead of a confident wrong balance sheet.
    if _total(scaled) > anchor * 1.05:
        return ({k: 0.0 for k in out},
                f"parsed balance sheet discarded: at 1/{int(factor):,} the components still "
                f"total {_total(scaled):,.0f} against reported total assets of {anchor:,.0f}")

    return scaled, (f"parsed balance sheet rescaled by 1/{int(factor):,} to agree with "
                    f"reported total assets")


def _fit_to_anchor(v: float, anchor: float) -> float:
    """One balance, held to the same rule as the rest of the sheet.

    A bank's loan book and deposit base are read after the reconciler above has
    run, so they need the same guard applied on their own. KCB's parsed loan
    book was 866,930,793 against reported total assets of 2,147,206, and the
    forecast it produced carried an impairment charge of 18.4 trillion shillings
    and a target price of MINUS 21,350 a share.

    Returns 0.0 when it cannot be reconciled, which puts the caller back on its
    derived-from-total-assets fallback: a marked estimate rather than a figure
    that looks reported and is not.
    """
    if not v or not anchor or anchor <= 0 or v <= anchor * 1.05:
        return v
    for k in range(1, 9):
        if v / (10.0 ** k) <= anchor * 1.05:
            return v / (10.0 ** k)
    return 0.0


def opening_balance_sheet(rec: dict, fins: list[dict]) -> dict[str, float]:
    """Pull the latest reported balance sheet, falling back to the series."""
    out: dict[str, float] = {}
    st = (rec or {}).get("statements") or {}
    rows, cols = st.get("balance_sheet") or [], st.get("columns") or []
    latest = fins[-1] if fins else {}
    notes = (rec or {}).get("notes") or {}

    def _row(*needles: str, avoid: tuple = ()) -> float:
        for r in rows:
            label = str(r.get("label", "")).strip().lower()
            # A subtotal is not a balance, and counting one alongside the rows
            # it totals puts the same money on the sheet twice.
            if any(label == t or label.startswith(t + " ") or label == t + ":"
                   for t in _SUBTOTALS):
                continue
            if any(a in label for a in avoid):
                continue
            if any(label.startswith(n) or n in label for n in needles):
                for v in (r.get("values") or []):
                    if isinstance(v, (int, float)):
                        return float(v)          # first (latest) populated column
        return 0.0

    for key, needles in _BS_MAP.items():
        out[key] = _row(*needles, avoid=_WRONG_SIDE.get(key, ()))

    # Before anything is derived from these, make them agree with the reported
    # total. Everything downstream trusts this dict, so it is the last place a
    # contradiction can be caught cheaply.
    out, _note = reconcile_opening(out, _n(latest.get("total_assets")))
    if _note:
        out["opening_note"] = _note

    out["debt"] = (abs(_n(notes.get("gross_debt"))) or abs(_n(latest.get("total_debt")))
                   or abs(_n(out.pop("debt_parsed", 0.0))))
    out.pop("debt_parsed", None)
    out["leases"] = (abs(_n(notes.get("lease_liabilities")))
                     or abs(_n(out.pop("leases_parsed", 0.0))))
    out.pop("leases_parsed", None)
    # The REPORTED total equity wins over a row scraped from the statement
    # table. Taking the parsed row whenever it was present, and the reported
    # figure only when it was missing, meant a row matched to the wrong line was
    # never corrected: EABL opened on equity of 314,226 against the 42,287 in
    # its accounts. The series figure is the one the platform publishes and the
    # one every ratio is built from, so it is also the one the model opens at.
    out["equity"] = _n(latest.get("total_equity")) or out.get("equity") or 0.0
    out["total_assets"] = _n(latest.get("total_assets"))

    # Liabilities are held to the room the reported sheet actually leaves. A
    # payables balance larger than total assets is a misparse, and carrying it
    # forces an equal and opposite residual: EABL's was -584,367 against a
    # balance sheet of 9,463, which is why the plug read as 6,175% of it.
    room = out["total_assets"] - out["equity"] - out["debt"] - out["leases"]
    for key in ("payables", "other_liabilities"):
        v = _n(out.get(key))
        if v > 0 and room > 0 and v > room * 1.05:
            fitted = _fit_to_anchor(v, room)
            out[key] = fitted if fitted else 0.0
            if not fitted:
                out["opening_note"] = (out.get("opening_note") or "") + (
                    f"; {key} of {v:,.0f} discarded, the reported sheet leaves "
                    f"room for {room:,.0f}")
    if not out.get("cash"):
        nd = _n(latest.get("net_debt"))
        out["cash"] = max(out["debt"] + out["leases"] - nd, 0.0)

    # A bank's balance sheet is its loan book and its deposits. Neither is a
    # reported line in any record we hold, so both are derived here and marked
    # as derived. Leaving them absent is what produced ten bank models with a
    # zero loan book, a zero deposit base and no impairment charge.
    bank = (rec or {}).get("bank") or {}
    if bank:
        assets = out["total_assets"]
        reported_loans = _fit_to_anchor(
            _row("loans and advances", "gross loans", "net loans")
            or _n(notes.get("gross_loans")), assets)
        out["loans"] = reported_loans or (assets * LOANS_TO_ASSETS)
        out["loans_are_derived"] = not bool(reported_loans)
        # Deposits are the funding residual against two REPORTED anchors, total
        # assets and total equity, rather than loans divided by a ratio. A ratio
        # produces a deposit base that does not reconcile with the reported
        # balance sheet, and the difference then has to be absorbed by a plug.
        reported_deposits = _fit_to_anchor(
            _row("customer deposits", "deposits from customers"), assets)
        out["deposits"] = reported_deposits or max(
            assets - out["equity"] - out["debt"] - out["leases"], 0.0)
        out["deposits_are_derived"] = not bool(reported_deposits)

    # Energy: the decommissioning provision is a real liability and belongs on
    # the sheet rather than only in a schedule that drives nothing.
    out["provisions"] = (abs(_n(notes.get("decommissioning")))
                         or abs(_n(notes.get("provisions")))
                         or abs(_n(out.pop("provisions_parsed", 0.0))))
    out.pop("provisions_parsed", None)

    _tie_to_total_assets(out, bool(bank))
    return out


# Assets the model reads off the opening sheet, and the liabilities against
# them. Kept next to the function that uses them so the two cannot drift.
_OPENING_ASSETS = ("cash", "loans", "ppe", "intangibles", "rou", "investments",
                   "other_assets", "deferred_tax_asset", "receivables", "inventories")
_OPENING_LIABS = ("debt", "leases", "equity", "deposits", "payables",
                  "other_liabilities", "provisions")


def _tie_to_total_assets(out: dict[str, float], is_bank: bool) -> None:
    """Open the sheet at reported total assets, on both sides. In place.

    Only a handful of lines are ever read off a filing, so the identified
    balances fall well short of the reported total. The engine then closes the
    sheet with one residual, and that residual has to carry the entire unread
    remainder: a median of 418% of the balance sheet, larger than the sheet.

    The gap is not unknown, though. It is reported total assets less what was
    identified, which is a subtraction between two figures we hold. Naming it
    on each side turns a plug that dominates the model into a line a reader can
    account for, and leaves the true residual close to zero.

    Nothing is invented. The anchor is reported, the components are reported,
    and the difference is exactly what has not been read yet.
    """
    total = _n(out.get("total_assets"))
    if total <= 0:
        return

    assets = sum(_n(out.get(k)) for k in _OPENING_ASSETS if k != "loans" or is_bank)
    gap_a = total - assets
    if gap_a > 0:
        out["other_assets"] = _n(out.get("other_assets")) + gap_a
        out["other_assets_are_derived"] = True

    liabs = sum(_n(out.get(k)) for k in _OPENING_LIABS if k != "deposits" or is_bank)
    gap_l = total - liabs
    if gap_l > 0:
        out["other_liabilities"] = _n(out.get("other_liabilities")) + gap_l
        out["other_liabilities_are_derived"] = True


# --------------------------------------------------------------------------
# the engine
# --------------------------------------------------------------------------

def build(rec: dict, universe_entry: dict, *, years: int = 5,
          drivers: dict[str, float] | None = None,
          schedule_assumptions: dict[str, float] | None = None,
          sector: str | None = None) -> dict[str, Any]:
    """Build the integrated forecast for one entity (or one country)."""
    fins = sorted([f for f in (rec.get("financials") or []) if f.get("fy")],
                  key=lambda x: x["fy"])
    if not fins:
        return {}
    latest = fins[-1]
    sector = sector or dr.classify(universe_entry or {})
    notes = dict(rec.get("notes") or {})

    # direct costs from the income statement feed the telecom cost split
    st = rec.get("statements") or {}
    if "direct_costs" not in notes:
        for r in (st.get("income_statement") or []):
            if str(r.get("label", "")).lower().startswith("direct cost"):
                vals = [v for v in (r.get("values") or []) if isinstance(v, (int, float))]
                if vals:
                    notes["direct_costs"] = abs(vals[0])
                break

    # Calibrate only when deriving drivers from history. A caller passing an
    # explicit set (a scenario, a country override, a sensitivity bump) means
    # exactly those drivers — re-calibrating would silently undo the change.
    if drivers:
        dv = dict(drivers)
    else:
        dv = dr.defaults(sector, fins, notes, universe_entry)
        dv = dr.calibrate(sector, dv, _n(latest.get("revenue")), _n(latest.get("ebitda")))
    a = dict(sch.default_schedule_assumptions(fins, notes))
    if schedule_assumptions:
        a.update(schedule_assumptions)

    # ---- operating build (drivers) ----
    ops = [dr.build_year(sector, dv, t) for t in range(1, years + 1)]
    revenue = [_n(o.get("revenue")) for o in ops]
    cash_costs = [_n(o.get("cash_costs")) for o in ops]
    capex = [_n(o.get("capex")) for o in ops]
    ebitda = [r - c for r, c in zip(revenue, cash_costs)]

    # ---- schedules ----
    ob = opening_balance_sheet(rec, fins)
    ppe = sch.ppe_schedule(ob["ppe"], capex, a)
    intang = sch.intangible_schedule(ob["intangibles"], capex, a)
    lease = sch.lease_schedule(ob["leases"], capex, a)
    cogs = [c * 0.6 for c in cash_costs]                    # goods-type costs drive DIO/DPO
    wc = sch.working_capital_schedule(
        revenue, cogs, a,
        {"receivables": ob["receivables"], "inventory": ob["inventories"],
         "payables": ob["payables"]})

    # first pass: no incremental borrowing, then draw only if cash goes negative
    debt = sch.debt_schedule(ob["debt"], years, a)
    for _ in range(3):                                        # converge the revolver
        is_rows, cf_rows, bs_rows = _assemble(
            years, revenue, ebitda, capex, ppe, intang, lease, wc, debt, a, ob, ops)
        need = [max(-_n(r["cash"]), 0.0) for r in bs_rows]
        if not any(need):
            break
        prior = [_n(r["drawdown"]) for r in debt]
        debt = sch.debt_schedule(ob["debt"], years, a,
                                 funding_need=[p + n for p, n in zip(prior, need)])
    else:
        # The loop drew again on its last pass and then stopped, so the schedule
        # returned to the caller was one revision ahead of the statements built
        # from it: the workbook read borrowings of 117,205.8 off a schedule that
        # closed at 117,214.8. Assemble once more against the final schedule so
        # the two cannot disagree.
        is_rows, cf_rows, bs_rows = _assemble(
            years, revenue, ebitda, capex, ppe, intang, lease, wc, debt, a, ob, ops)

    base_year = _fy_int(latest.get("fy", "")) or 0
    for i, rows in enumerate(zip(is_rows, cf_rows, bs_rows)):
        for r in rows:
            r["fy"] = f"FY{base_year + i + 1}E" if base_year else f"Y{i + 1}E"

    checks = [{"fy": b["fy"], "assets": b["total_assets"],
               "liabilities_equity": b["total_liabilities_equity"],
               "difference": b["balance_check"], "balanced": abs(b["balance_check"]) < 1.0}
              for b in bs_rows]

    return {
        "sector": sector,
        "drivers": dv,
        "schedule_assumptions": a,
        "opening_balance_sheet": ob,
        "operating": [{"fy": is_rows[i]["fy"], **ops[i]} for i in range(years)],
        "income_statement": is_rows,
        "cash_flow": cf_rows,
        "balance_sheet": bs_rows,
        "schedules": {"ppe": ppe, "intangibles": intang, "leases": lease,
                      "working_capital": wc, "debt": debt},
        "checks": checks,
        "balanced": all(c["balanced"] for c in checks),
    }


def _assemble(years, revenue, ebitda, capex, ppe, intang, lease, wc, debt, a, ob, ops):
    """Assemble IS, CF and BS from the schedules; cash is the plug."""
    is_rows, cf_rows, bs_rows = [], [], []
    cash = _n(ob.get("cash"))
    equity = _n(ob.get("equity"))
    dtl = 0.0
    other_net_assets = None
    tax_rate = _n(a.get("tax_rate"), 0.30)
    deferred_pct = _n(a.get("deferred_tax_pct"), 0.10)
    payout = _n(a.get("payout_ratio"), 0.5)
    minority = _n(a.get("minority_share"), 0.0)
    cash_rate = _n(a.get("interest_on_cash"), 0.05)

    for t in range(years):
        p, ig, ls, w, d = ppe[t], intang[t], lease[t], wc[t], debt[t]
        depn, amort, rou_depn = p["depreciation"], ig["amortisation"], ls["depreciation"]
        da = depn + amort + rou_depn
        ebit = ebitda[t] - da
        int_debt, int_lease = d["interest"], ls["interest"]
        int_income = cash * cash_rate
        pbt = ebit - int_debt - int_lease + int_income
        tax = max(pbt, 0.0) * tax_rate
        deferred = tax * deferred_pct
        ni_total = pbt - tax
        nci = ni_total * minority
        ni_attr = ni_total - nci
        dividend = max(ni_attr, 0.0) * payout

        is_rows.append({
            "revenue": revenue[t], "ebitda": ebitda[t], "ebitda_margin": ebitda[t] / revenue[t] if revenue[t] else 0.0,
            "depreciation": -depn, "amortisation": -amort, "rou_depreciation": -rou_depn,
            "ebit": ebit, "interest_expense": -(int_debt + int_lease),
            "interest_income": int_income, "pbt": pbt, "tax": -tax,
            "profit_after_tax": ni_total, "minority_interest": -nci,
            "net_income": ni_attr,
        })

        cfo = ni_total + da + deferred + w["cash_impact"]
        capex_cash = p["additions"] + ig["additions"]
        cfi = -capex_cash
        cff = d["drawdown"] - d["repayment"] - ls["principal"] - dividend
        net_change = cfo + cfi + cff
        cf_rows.append({
            "net_income": ni_total, "depreciation_amortisation": da,
            "deferred_tax": deferred, "change_in_working_capital": w["cash_impact"],
            "operating_cash_flow": cfo, "capital_expenditure": -capex_cash,
            "investing_cash_flow": cfi, "debt_drawdown": d["drawdown"],
            "debt_repayment": -d["repayment"], "lease_principal": -ls["principal"],
            "dividends_paid": -dividend, "financing_cash_flow": cff,
            "net_change_in_cash": net_change, "opening_cash": cash,
            "closing_cash": cash + net_change,
            "free_cash_flow": cfo + cfi,
        })

        cash = cash + net_change
        equity = equity + ni_total - dividend
        dtl = dtl + deferred

        # A bank's book rolls forward here too, so the Python model and the
        # workbook tell the same story rather than two different ones. Loans and
        # the deposits funding them grow at the SAME rate: letting them grow
        # apart would open a gap every year that only a plug could close.
        g = (1 + _n(a.get("loan_growth"), 0.0)) ** (t + 1)
        loans = _n(ob.get("loans")) * g
        deposits = _n(ob.get("deposits")) * g
        provisions = _n(ob.get("provisions"))

        # Balances with no schedule of their own, carried at their opening level.
        # These were computed and then dropped on the floor: the assembly never
        # wrote them, so the workbook read zero and the reconciling balance below
        # absorbed the entire unread half of the balance sheet. That is what a
        # residual of 418% of the sheet was made of.
        investments = _n(ob.get("investments"))
        other_assets = _n(ob.get("other_assets"))
        dta = _n(ob.get("deferred_tax_asset"))
        other_liabs = _n(ob.get("other_liabilities"))

        assets_core = (p["close"] + ig["close"] + ls["close"] + w["receivables"]
                       + w["inventory"] + cash + loans
                       + investments + other_assets + dta)
        liab_core = (d["close"] + ls["close"] + w["payables"] + dtl + equity
                     + deposits + provisions + other_liabs)
        if other_net_assets is None:
            # one-off reconciling balance so the opening sheet ties; held flat
            other_net_assets = liab_core - assets_core
        total_assets = assets_core + other_net_assets
        bs_rows.append({
            "ppe": p["close"], "intangibles": ig["close"], "right_of_use_assets": ls["close"],
            "receivables": w["receivables"], "inventories": w["inventory"],
            "cash": cash, "other_net_assets": other_net_assets,
            "investments": investments, "other_assets": other_assets,
            "deferred_tax_asset": dta, "other_liabilities": other_liabs,
            "loans": loans, "deposits": deposits, "provisions": provisions,
            "total_assets": total_assets,
            "borrowings": d["close"], "lease_liabilities": ls["close"],
            "payables": w["payables"], "deferred_tax_liability": dtl,
            "equity": equity, "total_liabilities_equity": liab_core,
            "balance_check": total_assets - liab_core,
            "net_debt": d["close"] + ls["close"] - cash,
        })
    return is_rows, cf_rows, bs_rows


# --------------------------------------------------------------------------
# multi-country groups
# --------------------------------------------------------------------------

def build_by_country(rec: dict, universe_entry: dict, *, years: int = 5,
                     countries: dict[str, dict] | None = None) -> dict[str, Any]:
    """Build each country separately and consolidate.

    `countries` maps a country name to its share of the group and any driver
    overrides, e.g. Ethiopia growing faster off a smaller base with heavier
    capex and a later break-even. When nothing is supplied the group is built
    as a single entity, so single-country companies are unaffected.
    """
    if not countries:
        return {"consolidated": build(rec, universe_entry, years=years), "countries": {}}

    per: dict[str, Any] = {}
    for name, spec in countries.items():
        share = _n(spec.get("revenue_share"), 0.0)
        sub = dict(rec)
        sub["financials"] = [
            {**f, **{k: _n(f.get(k)) * share for k in
                     ("revenue", "ebitda", "ebit", "net_income", "capex",
                      "total_assets", "total_equity")}}
            for f in (rec.get("financials") or [])
        ]
        per[name] = build(sub, universe_entry, years=years,
                          drivers=spec.get("drivers"),
                          schedule_assumptions=spec.get("schedule_assumptions"),
                          sector=spec.get("sector"))

    keys_is = ("revenue", "ebitda", "ebit", "pbt", "net_income")
    keys_cf = ("operating_cash_flow", "free_cash_flow", "capital_expenditure")
    keys_bs = ("total_assets", "equity", "net_debt", "cash")
    n = min((len(v.get("income_statement", [])) for v in per.values()), default=0)
    cons_is, cons_cf, cons_bs = [], [], []
    for i in range(n):
        fy = next(iter(per.values()))["income_statement"][i]["fy"]
        cons_is.append({"fy": fy, **{k: sum(_n(v["income_statement"][i].get(k)) for v in per.values())
                                     for k in keys_is}})
        cons_cf.append({"fy": fy, **{k: sum(_n(v["cash_flow"][i].get(k)) for v in per.values())
                                     for k in keys_cf}})
        cons_bs.append({"fy": fy, **{k: sum(_n(v["balance_sheet"][i].get(k)) for v in per.values())
                                     for k in keys_bs}})
    return {
        "countries": per,
        "consolidated": {"income_statement": cons_is, "cash_flow": cons_cf,
                         "balance_sheet": cons_bs,
                         "balanced": all(v.get("balanced") for v in per.values())},
    }
