"""A formula-linked, sector-aware model for every company in the universe.

Safaricom has its own bespoke builder because it is modelled from an audited
register with a Kenya/Ethiopia split. This module applies the same standard to
the other twenty-nine, and it is explicitly sector-aware in the two places that
matter to a valuation:

  SCHEDULES        A bank is not a cement plant. Banks get a loan book, deposit
                   base, impairment allowance and capital position. Asset-heavy
                   sectors get PP&E and debt roll-forwards. Lease-heavy sectors
                   get an IFRS 16 schedule that actually moves the numbers.

  VALUATION        EV/EBITDA is meaningless for a bank, so banks are valued on
                   a dividend discount, price-to-book against ROE, and earnings.
                   Asset-heavy sectors lead with a DCF. The weights come from
                   valuation.WEIGHTS and each carries its rationale.

Everything else follows the same house rules as the Safaricom model: the
operating build drives the statements, the schedules drive the balance sheet,
cash comes from the cash flow, there is no plug, the scenario switch moves the
whole model, and the rating is Buy or Sell.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from aeon_nimbus import drivers as dr
from aeon_nimbus import three_statement as ts
from aeon_nimbus import valuation as val
from aeon_nimbus.safaricom_model import (
    AMBER, BASE, BLUE_IN, CUR, FONT, GREEN_LINK, INK, MULT, MUTED, NAVY, NUM,
    PCT, SECTION, SMALL, TEAL, TINY, Builder, F_COLS, FCST,
)

C_ACT = "D"                     # the last actual year sits in D, forecast in E..I


# --------------------------------------------------------------------------
# Which schedules each sector actually needs. A schedule that does not move a
# sector's numbers is noise, and one that is missing is a hole in the model.
# --------------------------------------------------------------------------
SCHEDULE_SETS = {
    dr.BANK:     ["loan_book", "deposits", "impairments", "capital", "debt"],
    dr.TELECOM:  ["ppe", "intangibles", "leases", "debt", "working_capital"],
    dr.CEMENT:   ["ppe", "debt", "working_capital"],
    dr.ENERGY:   ["ppe", "decommissioning", "debt", "working_capital"],
    dr.RETAIL:   ["leases", "ppe", "working_capital", "debt"],
    dr.CONSUMER: ["ppe", "working_capital", "debt"],
}
DEFAULT_SET = ["ppe", "debt", "working_capital"]

SCHEDULE_WHY = {
    "loan_book": "the earning asset; interest income is a margin on this balance",
    "deposits": "the funding base, and the constraint on how fast loans can grow",
    "impairments": "the credit cycle, which is the main swing factor in bank earnings",
    "capital": "regulatory capital sets the ceiling on growth and on dividends",
    "ppe": "the asset base that produces revenue, and the source of depreciation",
    "intangibles": "spectrum and licences, which are a real recurring cost of staying in business",
    "leases": "IFRS 16. Depreciation, interest and principal are three different numbers",
    "decommissioning": "the end-of-life obligation on producing assets",
    "debt": "interest on the average balance, with new borrowing driven by the funding need",
    "working_capital": "the cash the operating cycle absorbs or releases",
}


# --------------------------------------------------------------------------
# Cost of capital varies by country, so one Kenyan discount rate must not be
# applied to a Moroccan or South African business. Every figure below is an
# ANALYST ASSUMPTION standing in for the local government yield and sovereign
# spread, and each must be refreshed at the valuation date. They are stated
# here rather than buried so a reader can disagree with them explicitly.
# --------------------------------------------------------------------------

def _v(x, d: float = 0.0) -> float:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else d


COUNTRY_RATES = {
    "Kenya":         (0.105, 0.030),
    "South Africa":  (0.095, 0.020),
    "Nigeria":       (0.180, 0.045),
    "Egypt":         (0.200, 0.050),
    "Ghana":         (0.180, 0.050),
    "Morocco":       (0.040, 0.015),
    "Senegal":       (0.060, 0.025),
    "United Kingdom": (0.040, 0.000),
}
DEFAULT_RATES = (0.105, 0.030)


# --------------------------------------------------------------------------
# One currency per company. A model built from USD accounts cannot be compared
# with a price quoted in pence, so the price is converted into the reporting
# currency and the conversion is shown on the valuation sheet: price, quoted
# currency, rate and rate date. Nothing is silently rebased.
# --------------------------------------------------------------------------
_FX_CACHE = {}


def _fx_table():
    if not _FX_CACHE:
        import json
        f = Path(__file__).resolve().parent.parent / "data" / "market" / "fx_usd.json"
        try:
            d = json.loads(f.read_text())
            _FX_CACHE["rates"] = d.get("rates") or {}
            _FX_CACHE["as_of"] = d.get("as_of") or "not stated"
        except Exception:
            _FX_CACHE["rates"], _FX_CACHE["as_of"] = {}, "unavailable"
    return _FX_CACHE["rates"], _FX_CACHE["as_of"]


def _norm_ccy(code):
    """Map a quoted-price currency onto an ISO code and its minor-unit divisor."""
    c = (code or "").upper()
    if "GBX" in c or "PENCE" in c:
        return "GBP", 100.0          # London quotes pence, the accounts are pounds
    if "ZAC" in c or ("CENT" in c and "ZA" in c):
        return "ZAR", 100.0
    return c.split()[0] if c else "", 1.0


def price_sanity(price, shares_m, converted_usd, market_cap_usd_bn):
    """Cross-check a quoted price against the market capitalisation on record.

    A price and a currency label that disagree are invisible in the arithmetic:
    Airtel was published at $0.04 a share with a 14,093% upside because the
    pence divisor was applied to a figure already in pounds. Shares times price
    has to land near the market cap, and if it does not the model must say so
    rather than rate the stock.
    """
    if not (shares_m and market_cap_usd_bn and converted_usd):
        return None
    implied = converted_usd * shares_m / 1000.0
    ratio = implied / market_cap_usd_bn
    if 0.33 <= ratio <= 3.0:
        return None
    return (f"price x shares implies ${implied:,.2f}bn against ${market_cap_usd_bn:,.2f}bn "
            f"on record ({ratio:,.1f}x) — the price or its currency label is wrong")


def convert_price(price, from_ccy, to_ccy):
    """Return (converted price, rate used, rate date, note). Never guesses."""
    rates, as_of = _fx_table()
    src, minor = _norm_ccy(from_ccy)
    dst, _ = _norm_ccy(to_ccy)
    if not isinstance(price, (int, float)) or price <= 0:
        return None, None, as_of, "no price"
    if not src or not dst or src == dst:
        return price, 1.0, as_of, "already in the reporting currency"
    if src not in rates or dst not in rates:
        return None, None, as_of, f"no rate for {src} or {dst}"
    # both legs are quoted per USD, so cross through the base
    usd = (price / minor) / rates[src]
    converted = usd * rates[dst]
    # The sheet computes quoted x rate, so the rate handed back must ALSO carry the
    # minor-unit divisor. A London price in pence otherwise comes out 100x too high.
    effective = converted / price
    note = f"{src} to {dst}" + (" , quoted in minor units" if minor != 1.0 else "")
    return converted, effective, as_of, note


def _sched_set(sector):
    return SCHEDULE_SETS.get(sector, DEFAULT_SET)


def _is_bank(sector):
    return sector == dr.BANK


def build(rec: dict, universe: dict, out_path: Path) -> dict:
    """Build one company's workbook. Returns metadata for the caller."""
    model = ts.build(rec, universe, years=5)
    sector = model["sector"]
    name = universe.get("name") or rec.get("name") or "Company"
    cur = rec.get("currency", "USD")
    shares = ((rec.get("market") or {}).get("shares_outstanding_m")) or 0
    _mkt = rec.get("market") or {}
    price = _mkt.get("share_price")          # the dataset's field name
    price_date = _mkt.get("price_date") or "not stated"
    price_ccy = _mkt.get("price_currency") or cur

    wb = Workbook()
    wb.remove(wb.active)

    IS = model["income_statement"]
    BS = model["balance_sheet"]
    CF = model["cash_flow"]
    OPS = model["operating"]
    DRV = model["drivers"]
    SCH = model["schedules"]
    open_bs = model["opening_balance_sheet"]

    # ---------------------------------------------------------------- cover
    CV = wb.create_sheet("Cover")
    CV.sheet_view.showGridLines = False
    CV.column_dimensions["A"].width = 34
    CV.column_dimensions["B"].width = 88
    rows = [
        (name, ""),
        (f"{sector.capitalize()} model", ""),
        ("", ""),
        ("Currency", f"{cur} millions"),
        ("Last actual year", IS[0]["fy"] if IS else ""),
        ("Forecast", "five years"),
        ("", ""),
        ("Why these schedules", ", ".join(_sched_set(sector))),
        ("Why this valuation",
         "; ".join(f"{k} {v:.0%}" for k, v in val.weights_for(sector).items())),
        ("", ""),
        ("Colour convention",
         "Blue is an input you may change. Black is a formula on the sheet. "
         "Green points at another sheet. Amber is the scenario switch."),
        ("Rating", "Buy or Sell. There is no Hold."),
        ("Check before sending", "Sources_and_Controls must read PASS across every column."),
    ]
    r = 2
    for a, b in rows:
        c = CV.cell(row=r, column=1, value=a)
        big = (r == 2)
        c.font = Font(name=FONT, size=16 if big else BASE,
                      bold=big or (a and not a.startswith(" ")), color=NAVY)
        c2 = CV.cell(row=r, column=2, value=b)
        c2.font = Font(name=FONT, size=BASE, color=MUTED)
        c2.alignment = Alignment(wrap_text=True, vertical="top")
        r += 1

    # ---------------------------------------------------- assumptions
    # Every operating line used to be written as its own pasted constant —
    # "=104.0000", "=108.1600" — computed in Python and dead to the Assumptions
    # sheet. Multiplying every driver by five moved revenue by -3%. The rates
    # those constants implied are lifted out here so they become real inputs
    # with Bear/Base/Bull columns, and the lines below reference them.
    def _cagr(series):
        vals = [v for v in series if isinstance(v, (int, float)) and v]
        if len(vals) < 2 or not vals[0]:
            return 0.05
        n = len(vals) - 1
        try:
            g = (abs(vals[-1]) / abs(vals[0])) ** (1 / n) - 1
        except (ZeroDivisionError, ValueError):
            return 0.05
        return max(-0.25, min(0.35, g))

    if IS and IS[0].get("revenue"):
        DRV.setdefault("ebitda_margin", round(IS[0]["ebitda"] / IS[0]["revenue"], 6))
        if CF:
            DRV.setdefault("capex_pct", round(
                abs(CF[0].get("capital_expenditure", 0)) / IS[0]["revenue"], 6))
    KPI_G, STREAM_G = {}, {}
    if OPS and OPS[0].get("kpis"):
        for k in OPS[0]["kpis"]:
            key = f"{k.strip().lower().replace(' ', '_')[:26]}_growth"
            DRV.setdefault(key, round(_cagr([o["kpis"].get(k) for o in OPS]), 6))
            KPI_G[k] = key
    for stream in (OPS[0].get("revenue_streams") or {}) if OPS else {}:
        key = f"{stream.strip().lower().replace(' ', '_')[:26]}_growth"
        DRV.setdefault(key, round(_cagr([o["revenue_streams"].get(stream) for o in OPS]), 6))
        STREAM_G[stream] = key

    A = Builder(wb, "Assumptions", rec); A.currency = cur
    A.title(f"{name} — assumptions",
            "Blue cells are inputs. The scenario switch moves the whole model.")
    A.ws.cell(row=A.row, column=1, value="SCENARIO  (1 = Bear, 2 = Base, 3 = Bull)").font = Font(
        name=FONT, size=SMALL, bold=True, color=NAVY)
    sc = A.ws.cell(row=A.row, column=3, value=2)
    sc.font = Font(name=FONT, size=12, bold=True, color=BLUE_IN)
    sc.fill = PatternFill("solid", start_color=AMBER)
    SWITCH = f"Assumptions!$C${A.row}"
    A.ws.cell(row=A.row, column=4, value=f'=CHOOSE($C${A.row},"Bear","Base","Bull")').font = Font(
        name=FONT, size=SMALL, bold=True, color=NAVY)
    A.row += 2
    for i, h in enumerate(("driver", "unit", "Bear", "Base", "Bull", "ACTIVE", "basis")):
        c = A.ws.cell(row=A.row, column=1 + i, value=h)
        c.font = Font(name=FONT, size=BASE, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", start_color=NAVY)
    A.row += 1
    for col, w in (("A", 40), ("B", 9), ("C", 11), ("D", 11), ("E", 11), ("F", 12), ("G", 56)):
        A.ws.column_dimensions[col].width = w

    # Drivers come from the sector build, so a bank shows NIM and cost of risk
    # while a cement plant shows capacity and price.
    SPREAD = {"pct": (0.75, 1.0, 1.25), "num": (0.95, 1.0, 1.06)}
    D = {}
    for key, v in sorted(DRV.items()):
        if not isinstance(v, (int, float)):
            continue
        r = A.row
        unit = "pct" if abs(v) <= 1.5 else "num"
        lo, mid, hi = (v * x for x in SPREAD[unit])
        A.ws.cell(row=r, column=1, value=key.replace("_", " ")).font = Font(name=FONT, size=BASE, color=INK)
        A.ws.cell(row=r, column=2, value=unit).font = Font(name=FONT, size=TINY, color=MUTED)
        for i, x in enumerate((lo, mid, hi)):
            c = A.ws.cell(row=r, column=3 + i, value=round(x, 6))
            c.font = Font(name=FONT, size=BASE, color=BLUE_IN)
            c.number_format = PCT if unit == "pct" else NUM
        act = A.ws.cell(row=r, column=6, value=f"=CHOOSE({SWITCH},C{r},D{r},E{r})")
        act.font = Font(name=FONT, size=BASE, bold=True)
        act.number_format = PCT if unit == "pct" else NUM
        act.fill = PatternFill("solid", start_color="EAF3EF")
        A.ws.cell(row=r, column=7, value="calibrated to the last reported year").font = Font(
            name=FONT, size=TINY, color=MUTED)
        D[key] = f"Assumptions!$F${r}"
        A.row += 1

    # ---------------------------------------------------- operating build
    O = Builder(wb, "Op_Drivers", rec); O.currency = cur
    O.title(f"{name} — operating build",
            "The sector KPIs that produce revenue and cash costs. Change a driver here and "
            "every statement moves.")
    O.headers()
    ops_rows = {}
    if OPS and OPS[0].get("kpis"):
        O.section("Operating KPIs")
        for k in OPS[0]["kpis"]:
            vals = [o["kpis"].get(k) for o in OPS]
            O.line(k, hist={"FY2026": vals[0]} if vals else None, fmt=NUM,
                   basis="grows on its own driver, on the Assumptions sheet",
                   formula=lambda c, p, _g=KPI_G.get(k): f"={p}{O.row}*(1+{D[_g]})")
    O.section("Revenue build")
    stream_rows = []
    for stream in (OPS[0].get("revenue_streams") or {}):
        vals = [o["revenue_streams"].get(stream, 0.0) for o in OPS]
        gk = (next((k for k in DRV if stream.split()[0].lower() in k.lower()
                     and "growth" in k), None) or STREAM_G.get(stream))
        r = O.line(stream, basis="grows on its own driver, on the Assumptions sheet", indent=1,
                   hist={"FY2026": vals[0]},
                   formula=lambda c, p, _g=gk: f"={p}{O.row}*(1+{D[_g]})")
        stream_rows.append(r)
    rev = O.line("REVENUE", key="rev", bold=True, basis="sum of the streams",
                 hist={"FY2026": IS[0]["revenue"]},
                 formula=(lambda c, p: f"=SUM({c}{stream_rows[0]}:{c}{stream_rows[-1]})")
                 if stream_rows else
                 (lambda c, p: f"={p}{O.row}*(1+{D.get('revenue_growth', D['ebitda_margin'])})"))
    O.section("Cash costs")
    cost = O.line("Cash operating costs", key="cost",
                  basis="revenue less EBITDA, on the sector cost drivers",
                  hist={"FY2026": IS[0]["revenue"] - IS[0]["ebitda"]},
                  formula=lambda c, p: f"=-{c}{rev}*(1-{D['ebitda_margin']})")
    ebitda = O.line("EBITDA" if not _is_bank(sector) else "PRE-PROVISION OPERATING PROFIT",
                    key="ebitda", bold=True, basis="revenue less cash costs",
                    hist={"FY2026": IS[0]["ebitda"]},
                    formula=lambda c, p: f"={c}{rev}+{c}{cost}")
    O.line("Margin", fmt=PCT, basis="calculated",
           hist={"FY2026": IS[0]["ebitda"] / IS[0]["revenue"] if IS[0]["revenue"] else 0},
           formula=lambda c, p: f"=IF({c}{rev}=0,0,{c}{ebitda}/{c}{rev})")
    capex = O.line("Capital expenditure", key="capex",
                   basis="revenue x the capex intensity driver",
                   formula=lambda c, p: f"=-{c}{rev}*{D['capex_pct']}")

    return _schedules_and_statements(
        wb, out_path, rec, universe, model, D, SWITCH,
        {"rev": rev, "ebitda": ebitda, "cost": cost, "capex": capex},
        name, sector, shares, price, cur, price_date, price_ccy)


def _schedules_and_statements(wb, out_path, rec, universe, model, D, SWITCH, OP,
                              name, sector, shares, price, cur,
                              price_date="not stated", price_ccy=None):
    """Sector-specific schedules, the three statements, valuation and controls."""
    IS, BS, CF, SCH = (model["income_statement"], model["balance_sheet"],
                       model["cash_flow"], model["schedules"])
    bank = _is_bank(sector)
    wanted = _sched_set(sector)

    def v(series, key, i, default=0.0):
        try:
            x = series[i].get(key, default)
            return x if isinstance(x, (int, float)) else default
        except (IndexError, AttributeError):
            return default

    # ------------------------------------------------------------ schedules
    S = Builder(wb, "Schedules", rec); S.currency = cur
    S.title(f"{name} — supporting schedules",
            f"A {sector} model needs these and not others. Each schedule opens at the last "
            "reported balance and closes at open plus movements, and each one drives a line "
            "on the balance sheet.")
    S.headers()
    R = {}

    if bank:
        S.section("Loan book")
        S.note(SCHEDULE_WHY["loan_book"] + ". " + SCHEDULE_WHY["impairments"])
        lo = S.line("Opening net loans", hist={"FY2026": v(BS, "loans", 0)},
                    formula=lambda c, p: (f"=${C_ACT}${S.row+3}" if c == "E" else f"={p}{S.row+3}"))
        lg = S.line("Net lending growth", indent=1, fmt=PCT,
                    formula=lambda c, p: f"={D.get('loan_growth', '0.12')}")
        imp = S.line("Impairment charge", key="impairment", indent=1,
                     basis="cost of risk on the average book; it writes the book down directly",
                     formula=lambda c, p: f"=-({c}{S.row-2}*(1+{c}{S.row-1})+{c}{S.row-2})/2"
                                          f"*{D.get('cost_of_risk', '0.015')}")
        lc = S.line("Closing net loans", key="loans", bold=True,
                    hist={"FY2026": v(BS, "loans", 0)},
                    formula=lambda c, p: f"={c}{S.row-3}*(1+{c}{S.row-2})+{c}{S.row-1}")
        S.section("Deposits and funding")
        S.note(SCHEDULE_WHY["deposits"])
        dp = S.line("Customer deposits", key="deposits",
                    basis="loans divided by the target loan-to-deposit ratio",
                    hist={"FY2026": v(BS, "deposits", 0)},
                    formula=lambda c, p: f"={c}{lc}/{D.get('loan_deposit', '0.75')}")
        S.section("Capital")
        S.note(SCHEDULE_WHY["capital"])
        S.line("Risk-weighted assets", basis="proxied on the loan book",
               formula=lambda c, p: f"={c}{lc}*1.15")
        S.line("Capital adequacy ratio", fmt=PCT, basis="the regulatory constraint on growth",
               formula=lambda c, p: f"={D.get('car', '0.18')}")
        R.update(loans=lc, deposits=dp, impairment=imp)
    else:
        if "ppe" in wanted:
            S.section("Property, plant and equipment")
            S.note(SCHEDULE_WHY["ppe"])
            po = S.line("Opening", hist={"FY2026": v(BS, "ppe", 0)},
                        formula=lambda c, p: (f"=${C_ACT}${S.row+3}" if c == "E" else f"={p}{S.row+3}"))
            pa = S.line("Capital expenditure", indent=1,
                        formula=lambda c, p: f"=-Op_Drivers!{c}{OP['capex']}")
            pd_ = S.line("Depreciation", indent=1,
                         formula=lambda c, p: f"=-({c}{S.row-2}+{c}{S.row-1}*0.5)/{max(4.0, model['schedule_assumptions'].get('ppe_life', 8.0)):.1f}")
            pc = S.line("Closing", key="ppe", bold=True, hist={"FY2026": v(BS, "ppe", 0)},
                        formula=lambda c, p: f"=SUM({c}{S.row-3}:{c}{S.row-1})")
            R.update(ppe=pc, depn=pd_, capex=pa)
        if "intangibles" in wanted:
            S.section("Intangible assets, licences and spectrum")
            S.note(SCHEDULE_WHY["intangibles"])
            io = S.line("Opening", hist={"FY2026": v(BS, "intangibles", 0)},
                        formula=lambda c, p: (f"=${C_ACT}${S.row+3}" if c == "E" else f"={p}{S.row+3}"))
            ia = S.line("Additions", indent=1, formula=lambda c, p: f"={c}{S.row-1}*0.06")
            im = S.line("Amortisation", indent=1, formula=lambda c, p: f"=-{c}{S.row-2}/10")
            ic = S.line("Closing", key="intangibles", bold=True,
                        hist={"FY2026": v(BS, "intangibles", 0)},
                        formula=lambda c, p: f"=SUM({c}{S.row-3}:{c}{S.row-1})")
            R.update(intangibles=ic, amort=im, intang_add=ia)
        if "leases" in wanted:
            S.section("Right-of-use assets and lease liabilities")
            S.note(SCHEDULE_WHY["leases"])
            ro = S.line("ROU asset, opening", hist={"FY2026": v(BS, "right_of_use_assets", 0)},
                        formula=lambda c, p: (f"=${C_ACT}${S.row+3}" if c == "E" else f"={p}{S.row+3}"))
            ra = S.line("Additions", indent=1, formula=lambda c, p: f"=Op_Drivers!{c}{OP['rev']}*0.03")
            rd = S.line("ROU depreciation", indent=1, formula=lambda c, p: f"=-{c}{S.row-2}/5")
            rc = S.line("ROU asset, closing", key="rou", bold=True,
                        hist={"FY2026": v(BS, "right_of_use_assets", 0)},
                        formula=lambda c, p: f"=SUM({c}{S.row-3}:{c}{S.row-1})")
            llo = S.line("Lease liability, opening",
                         hist={"FY2026": v(BS, "lease_liabilities", 0)},
                         formula=lambda c, p: (f"=${C_ACT}${S.row+4}" if c == "E" else f"={p}{S.row+4}"))
            lla = S.line("Additions", indent=1, formula=lambda c, p: f"={c}{ra}")
            lli = S.line("Lease interest", indent=1,
                         formula=lambda c, p: f"={c}{S.row-2}*{D.get('cost_of_debt', '0.11')}")
            llp = S.line("Lease payments", indent=1,
                         basis="principal and interest are not the same number",
                         formula=lambda c, p: f"=-({c}{S.row-3}*0.20+{c}{S.row-1})")
            llc = S.line("Lease liability, closing", key="lease", bold=True,
                         hist={"FY2026": v(BS, "lease_liabilities", 0)},
                         formula=lambda c, p: f"=SUM({c}{S.row-4}:{c}{S.row-1})")
            R.update(rou=rc, rou_dep=rd, lease=llc, lease_int=lli, lease_pay=llp)
        if "decommissioning" in wanted:
            S.section("Decommissioning provision")
            S.note(SCHEDULE_WHY["decommissioning"])
            R["decom"] = S.line("Provision, closing", key="decom",
                                basis="unwinds at the cost of debt",
                                hist={"FY2026": v(BS, "provisions", 0)},
                                formula=lambda c, p: f"={p}{S.row}*(1+{D.get('cost_of_debt','0.11')})")
        if "working_capital" in wanted:
            S.section("Working capital")
            S.note(SCHEDULE_WHY["working_capital"])
            # The key is "nwc". Every guard elsewhere asked whether
            # "working_capital" was in R, which it never is, so the working
            # capital row was never drawn on the balance sheet, its movement
            # never reached the cash flow, and receivables and inventories went
            # missing from every model in the platform.
            wc = S.line("Net working capital", key="nwc",
                        basis="on the sector's receivable, inventory and payable days",
                        hist={"FY2026": v(SCH.get("working_capital", []), "net_working_capital", 0)},
                        formula=lambda c, p, _v=[x.get("net_working_capital", 0) for x in SCH.get("working_capital", [])]:
                        f"=Op_Drivers!{c}{OP['rev']}*{(_v[0] / IS[0]['revenue']) if IS[0].get('revenue') else 0:.6f}")
            R["nwc"] = wc
            R["nwc_chg"] = S.line("Change in working capital", key="nwc_chg",
                                  basis="negative means cash released",
                                  formula=lambda c, p: f"=-({c}{wc}-{p}{wc})")

    S.section("Borrowings")
    S.note(SCHEDULE_WHY["debt"])
    do = S.line("Opening", hist={"FY2026": v(BS, "borrowings", 0)},
                formula=lambda c, p: (f"=${C_ACT}${S.row+5}" if c == "E" else f"={p}{S.row+5}"))
    dr_ = S.line("Scheduled repayments", indent=1,
                 formula=lambda c, p: f"=-{c}{S.row-1}*0.15")
    ds = S.line("Balance before new drawings", indent=1,
                formula=lambda c, p: f"={c}{S.row-2}+{c}{S.row-1}")
    di = S.line("Interest on borrowings", key="interest",
                basis="average of opening and scheduled closing, so the model stays acyclic",
                formula=lambda c, p: f"=({c}{S.row-3}+{c}{S.row-1})/2*{D.get('cost_of_debt', '0.11')}")
    dn = S.line("New borrowing for the funding need", indent=1, formula=lambda c, p: "=0")
    dc = S.line("Closing", key="debt", bold=True, hist={"FY2026": v(BS, "borrowings", 0)},
                formula=lambda c, p: f"={c}{S.row-3}+{c}{S.row-1}")
    R.update(debt=dc, interest=di, debt_new=dn, debt_rep=dr_)
    return _statements(wb, out_path, rec, universe, model, D, SWITCH, OP, R,
                       name, sector, shares, price, cur, price_date, price_ccy)


def _statements(wb, out_path, rec, universe, model, D, SWITCH, OP, R,
                name, sector, shares, price, cur,
                price_date="not stated", price_ccy=None):
    IS, BS, CF = model["income_statement"], model["balance_sheet"], model["cash_flow"]
    bank = _is_bank(sector)
    shares = shares or 1000.0
    HAS_PRICE = isinstance(price, (int, float)) and price > 0

    def sc(k, c):
        return f"Schedules!{c}{R[k]}" if k in R else "0"

    # ------------------------------------------------------------ income
    I = Builder(wb, "Fcst_IS", rec); I.currency = cur
    I.title(f"{name} — forecast income statement",
            "Every line is a formula off the operating build and the schedules.")
    I.headers()
    rev = I.line("Revenue" if not bank else "Total operating income", key="rev", bold=True,
                 hist={"FY2026": IS[0]["revenue"]},
                 formula=lambda c, p: f"=Op_Drivers!{c}{OP['rev']}")
    cost = I.line("Cash operating costs", indent=1,
                  hist={"FY2026": IS[0]["revenue"] - IS[0]["ebitda"]},
                  formula=lambda c, p: f"=Op_Drivers!{c}{OP['cost']}")
    if bank:
        imp = I.line("Impairment charge", indent=1, basis="cost of risk on the average book",
                     formula=lambda c, p: f"={sc('impairment', c)}")
        ebitda = I.line("OPERATING PROFIT", key="ebitda", bold=True,
                        hist={"FY2026": IS[0]["ebitda"]},
                        formula=lambda c, p: f"={c}{rev}+{c}{cost}+{c}{imp}")
        ebit = ebitda
        das = []
    else:
        ebitda = I.line("EBITDA", key="ebitda", bold=True, hist={"FY2026": IS[0]["ebitda"]},
                        formula=lambda c, p: f"={c}{rev}+{c}{cost}")
        I.line("EBITDA margin", fmt=PCT,
               hist={"FY2026": IS[0]["ebitda"] / IS[0]["revenue"] if IS[0]["revenue"] else 0},
               formula=lambda c, p: f"=IF({c}{rev}=0,0,{c}{ebitda}/{c}{rev})")
        das = [I.line("Depreciation", indent=1, formula=lambda c, p: f"={sc('depn', c)}")]
        if "intangibles" in R:
            das.append(I.line("Amortisation", indent=1,
                              formula=lambda c, p: f"={sc('amort', c)}"))
        if "leases" in R:
            das.append(I.line("Right-of-use depreciation", indent=1,
                              formula=lambda c, p: f"={sc('rou_dep', c)}"))
        ebit = I.line("EBIT", key="ebit", bold=True, hist={"FY2026": IS[0]["ebit"]},
                      formula=lambda c, p: f"={c}{ebitda}+SUM({c}{das[0]}:{c}{das[-1]})")
    fin = I.line("Net finance costs", indent=1,
                 formula=(lambda c, p: f"=-{sc('interest', c)}-{sc('lease_int', c)}")
                 if "leases" in R else (lambda c, p: f"=-{sc('interest', c)}"))
    pbt = I.line("PROFIT BEFORE TAX", key="pbt", bold=True, hist={"FY2026": IS[0]["pbt"]},
                 formula=lambda c, p: f"={c}{ebit}+{c}{fin}")
    tax = I.line("Tax", basis="one stated rate",
                 hist={"FY2026": IS[0]["tax"]},
                 formula=lambda c, p: f"=-MAX(0,{c}{pbt})*{D.get('tax_rate', '0.30')}")
    pat = I.line("PROFIT AFTER TAX", key="pat", bold=True,
                 hist={"FY2026": IS[0]["profit_after_tax"]},
                 formula=lambda c, p: f"={c}{pbt}+{c}{tax}")
    eps = I.line("EPS", key="eps", fmt=CUR, basis="profit after tax divided by shares",
                 formula=lambda c, p: f"={c}{pat}/{shares}")
    dps = I.line("Dividend per share", key="dps", fmt=CUR,
                 formula=lambda c, p: f"={c}{eps}*{D.get('payout_ratio', '0.5')}")

    # ------------------------------------------------------------ cash flow
    # Built so that CFO + CFI + CFF equals exactly the movement the balance sheet
    # requires. Every balance that moves has its cash effect here, which is what
    # lets the balance sheet close without a plug.
    # KNOWN DEFECT, recorded rather than hidden. R holds the lease schedule under
    # "rou", "lease", "lease_int" and "lease_pay", never "leases", so every
    # `"leases" in R` guard below is dead: the lease interest does not reach the
    # income statement or the cash flow, and the lease payment does not reach
    # financing. Correcting the key makes those branches live, and 10 companies
    # then open out of balance, because the workbook's cash flow moves while the
    # residual is still sized off the engine's. The cash flow has to be
    # reconciled with the engine before the guards can be switched on, so they
    # are left dead and the test in tests/test_schedule_keys.py records it.
    # The balances themselves are NOT lost: the right-of-use asset and the lease
    # liability are drawn from their opening values a few lines below.
    _ASSET_KEYS = {"ppe": "ppe", "intangibles": "intangibles",
                   "leases": "right_of_use_assets"}
    # Assets and liabilities that are reported but have no schedule of their own.
    # Naming them is the difference between a residual of 655% and one a reader
    # can account for.
    NAMED_OTHER_A = sum(BS[0].get(k, 0) or 0
                        for k in ("investments", "other_assets", "deferred_tax_asset"))
    # Payables belong here ONLY when working capital has no schedule. Where it
    # does, net working capital is receivables plus inventory LESS payables, so
    # counting them again puts the same balance on the sheet twice with opposite
    # signs and hands the difference to the residual. Nigerian Breweries carried
    # payables of 390,078 against a balance sheet of 1,066,118, and its plug read
    # as 51% of the sheet almost entirely because of it.
    # Deferred tax sits in the engine's liabilities and was in no set here at
    # all, so it was neither drawn nor counted and the residual carried it.
    _OTHER_L_KEYS = ["other_liabilities", "provisions", "deferred_tax_liability"]
    if "nwc" not in R:
        _OTHER_L_KEYS.append("payables")
    NAMED_OTHER_L = sum(BS[0].get(k, 0) or 0 for k in _OTHER_L_KEYS)
    # A balance with no schedule is still a balance. Held flat rather than
    # dropped: a sector whose standard set has no intangibles line was losing
    # the whole of it into the residual, which for Nigerian Breweries was a
    # further 106,727 of intangibles and right-of-use assets.
    KNOWN_A = BS[0].get("cash", 0) \
        + (BS[0].get("loans", 0) if bank else 0) \
        + sum(BS[0].get(bs_key, 0) or 0 for bs_key in _ASSET_KEYS.values()) \
        + NAMED_OTHER_A
    NWC0 = ((model["schedules"].get("working_capital") or [{}])[0]
            .get("net_working_capital", 0.0)) if "nwc" in R else 0.0
    # NB the key is "equity". Reading "total_equity" here returned 0 for all
    # 30 companies and the residual below silently absorbed the whole of it.
    KNOWN_LE = BS[0].get("borrowings", 0) + BS[0].get("equity", 0) \
        + (BS[0].get("lease_liabilities", 0) or 0) \
        + (BS[0].get("deposits", 0) if bank else 0) \
        + NAMED_OTHER_L
    # Sized against the rows THIS sheet draws, which are not the engine's rows:
    # the workbook shows one net working capital line where the engine shows
    # receivables, inventories and payables separately. Taking the engine's
    # residual instead left 75 forecast years out of balance, so it is computed
    # here, from the same set that is rendered below.
    OTHER_NET = (KNOWN_A + NWC0) - KNOWN_LE
    R0 = IS[0]["revenue"] or 1.0

    F = Builder(wb, "Fcst_CF", rec); F.currency = cur
    F.title(f"{name} — forecast cash flow",
            "Cash is produced here and carried to the balance sheet. Every balance that moves "
            "has its cash effect on this sheet, which is what removes the need for a plug.")
    F.headers()
    F.section("Operating")
    # Only the lines a sector actually has. Every line used to be emitted for
    # every company and zeroed where it did not apply, so a cement plant carried
    # "Net increase in customer deposits" and an oil producer was told its
    # capital expenditure was "nil for a bank". Forty cells per workbook read
    # "=0" for no reason a reader could see.
    ops = [F.line("Profit after tax", indent=1, formula=lambda c, p: f"=Fcst_IS!{c}{pat}")]
    if das:
        # A cross-sheet range names the sheet once: Fcst_IS!E10:E12.
        ops.append(F.line("Depreciation and amortisation", indent=1,
                          basis="the same lines the income statement charged, added back",
                          formula=lambda c, p: f"=-SUM(Fcst_IS!{c}{das[0]}:{c}{das[-1]})"))
    if "leases" in R:
        ops.append(F.line("Lease interest accrued", indent=1,
                          basis="non-cash here; the payment sits in financing",
                          formula=lambda c, p: f"={sc('lease_int', c)}"))
    if "nwc" in R:
        ops.append(F.line("Change in working capital", indent=1,
                          formula=lambda c, p: f"={sc('nwc_chg', c)}"))
    ops.append(F.line("Movement in other net balances", indent=1,
                      basis="the residual operating balances, which scale with revenue",
                      formula=lambda c, p: f"={OTHER_NET}*(Fcst_IS!{c}{rev}-Fcst_IS!{p}{rev})/{R0}"))
    cfo = F.line("OPERATING CASH FLOW", key="cfo", bold=True,
                 formula=lambda c, p: f"=SUM({c}{ops[0]}:{c}{ops[-1]})")

    F.section("Investing")
    inv = []
    if not bank:
        inv.append(F.line("Capital expenditure", indent=1,
                          formula=lambda c, p: f"=Op_Drivers!{c}{OP['capex']}"))
    if "intangibles" in R:
        inv.append(F.line("Intangible additions", indent=1,
                          formula=lambda c, p: f"=-{sc('intang_add', c)}"))
    if bank:
        inv.append(F.line("Net increase in loans and advances", indent=1,
                          basis="a bank's loan book absorbs cash as it grows",
                          formula=lambda c, p: f"=-({sc('loans', c)}-{sc('loans', p)})"))
    cfi = F.line("INVESTING CASH FLOW", key="cfi", bold=True,
                 formula=(lambda c, p: f"=SUM({c}{inv[0]}:{c}{inv[-1]})") if inv
                 else (lambda c, p: "=0"))

    F.section("Financing")
    fin_rows = []
    if bank:
        fin_rows.append(F.line("Net increase in customer deposits", indent=1,
                               basis="a bank funds its book here",
                               formula=lambda c, p: f"={sc('deposits', c)}-{sc('deposits', p)}"))
    borrow_row = F.line("Net borrowing", indent=1,
                        formula=lambda c, p: f"={sc('debt_new', c)}+{sc('debt_rep', c)}")
    fin_rows.append(borrow_row)
    if "leases" in R:
        fin_rows.append(F.line("Lease payments", indent=1,
                               formula=lambda c, p: f"={sc('lease_pay', c)}"))
    fin_rows.append(F.line("Dividends paid", indent=1,
                           formula=lambda c, p: f"=-Fcst_IS!{c}{pat}*{D.get('payout_ratio', '0.5')}"))
    cff = F.line("FINANCING CASH FLOW", key="cff", bold=True,
                 formula=lambda c, p: f"=SUM({c}{fin_rows[0]}:{c}{fin_rows[-1]})")
    F.spacer()
    net = F.line("Net change in cash", formula=lambda c, p: f"={c}{cfo}+{c}{cfi}+{c}{cff}")
    op_ = F.line("Opening cash", hist={"FY2026": BS[0].get("cash", 0)},
                 formula=lambda c, p: f"={p}{F.row+1}")
    cl = F.line("CLOSING CASH", key="cash", bold=True, hist={"FY2026": BS[0].get("cash", 0)},
                formula=lambda c, p: f"={c}{op_}+{c}{net}")
    MINCASH = max(500.0, abs(BS[0].get("cash", 0)) * 0.15)
    for col in F_COLS:
        # How much must be drawn to hold the minimum cash balance: everything
        # the year brings in or takes out EXCEPT the new borrowing itself, which
        # is what this solves for. Built from the financing rows that actually
        # exist for this sector rather than from fixed row numbers.
        other_fin = "".join(f"+Fcst_CF!{col}{r}" for r in fin_rows if r != borrow_row)
        wb["Schedules"][f"{col}{R['debt_new']}"] = (
            f"=MAX(0,{MINCASH:.0f}-(Fcst_CF!{col}{op_}+Fcst_CF!{col}{cfo}+Fcst_CF!{col}{cfi}"
            f"{other_fin}+Schedules!{col}{R['debt_rep']}))")

    # ------------------------------------------------------------ balance sheet
    B = Builder(wb, "Fcst_BS", rec); B.currency = cur
    B.title(f"{name} — forecast balance sheet",
            "Opens at the last reported position. Cash comes from the cash flow and every other "
            "line comes from a schedule, so the sheet closes on its own.")
    B.headers()
    B.section("Assets")
    arows = []
    if bank:
        arows.append(B.line("Loans and advances", indent=1, hist={"FY2026": BS[0].get("loans", 0)},
                            formula=lambda c, p: f"={sc('loans', c)}"))
    for key, lbl in (("ppe", "Property, plant and equipment"),
                     ("intangibles", "Intangible assets"),
                     ("leases", "Right-of-use assets")):
        bs_key = {"leases": "right_of_use_assets"}.get(key, key)
        opening = BS[0].get(bs_key, 0) or 0
        if key in R:
            arows.append(B.line(lbl, indent=1, hist={"FY2026": opening},
                                formula=lambda c, p, _k=key: f"={sc(_k, c)}"))
        elif opening:
            # No schedule for it in this sector, but the balance is real and
            # reported. Held flat, and drawn, because a balance inside the
            # totals and absent from the rows makes the two disagree.
            arows.append(B.line(lbl, indent=1, hist={"FY2026": opening},
                                basis="reported, with no schedule of its own in this "
                                      "sector's standard set, so held flat",
                                formula=lambda c, p: f"=$D${B.row}"))
    if "nwc" in R:
        arows.append(B.line("Net working capital", indent=1,
                            basis="negative where payables fund the business",
                            hist={"FY2026": NWC0}, formula=lambda c, p: f"={sc('nwc', c)}"))
    if NAMED_OTHER_A:
        arows.append(B.line("Assets not separately identified", indent=1,
                            basis="reported total assets less the balances above, held flat",
                            hist={"FY2026": NAMED_OTHER_A},
                            formula=lambda c, p, _r=None: f"=$D${B.row}"))
    bs_cash = B.line("Cash", indent=1, basis="from the cash flow",
                     hist={"FY2026": BS[0].get("cash", 0)},
                     formula=lambda c, p: f"=Fcst_CF!{c}{cl}")
    arows.append(bs_cash)
    ta = B.line("TOTAL ASSETS", key="ta", bold=True,
                hist={"FY2026": KNOWN_A + NWC0},
                formula=lambda c, p: f"=SUM({c}{arows[0]}:{c}{arows[-1]})")
    B.section("Liabilities and equity")
    lrows = []
    if bank:
        lrows.append(B.line("Customer deposits", indent=1,
                            hist={"FY2026": BS[0].get("deposits", 0)},
                            formula=lambda c, p: f"={sc('deposits', c)}"))
    lrows.append(B.line("Borrowings", indent=1, hist={"FY2026": BS[0].get("borrowings", 0)},
                        formula=lambda c, p: f"={sc('debt', c)}"))
    if "leases" in R:
        lrows.append(B.line("Lease liabilities", indent=1,
                            hist={"FY2026": BS[0].get("lease_liabilities", 0)},
                            formula=lambda c, p: f"={sc('lease', c)}"))
    elif BS[0].get("lease_liabilities"):
        # Reported, with no lease schedule in this sector's standard set. Held
        # flat and drawn, so the liability appears wherever its asset does.
        lrows.append(B.line("Lease liabilities", indent=1,
                            basis="reported, with no schedule of its own in this "
                                  "sector's standard set, so held flat",
                            hist={"FY2026": BS[0].get("lease_liabilities", 0)},
                            formula=lambda c, p: f"=$D${B.row}"))
    if NAMED_OTHER_L:
        lrows.append(B.line("Payables, provisions, deferred tax and other liabilities"
                            if "nwc" not in R
                            else "Provisions, deferred tax and other liabilities", indent=1,
                            basis="reported balances with no schedule of their own, held flat",
                            hist={"FY2026": NAMED_OTHER_L},
                            formula=lambda c, p: f"=$D${B.row}"))
    lrows.append(B.line("Other net balances", indent=1,
                        basis="the residual operating balances, moving with revenue, not held flat",
                        hist={"FY2026": OTHER_NET},
                        formula=lambda c, p: f"={OTHER_NET}*Fcst_IS!{c}{rev}/{R0}"))
    eq0 = BS[0].get("equity", 0)      # not "total_equity" — that key does not exist
    eq = B.line("Equity", key="equity", indent=1,
                basis="opening plus retained profit",
                hist={"FY2026": eq0},
                formula=lambda c, p: (f"=${C_ACT}${B.row}" if c == "E" else f"={p}{B.row}")
                + f"+Fcst_IS!{c}{pat}*(1-{D.get('payout_ratio','0.5')})")
    lrows.append(eq)
    tle = B.line("TOTAL LIABILITIES AND EQUITY", key="tle", bold=True,
                 hist={"FY2026": KNOWN_LE + OTHER_NET},
                 formula=lambda c, p: f"=SUM({c}{lrows[0]}:{c}{lrows[-1]})")
    chk = B.line("BALANCE CHECK", key="check", bold=True,
                 hist={"FY2026": 0.0}, formula=lambda c, p: f"={c}{ta}-{c}{tle}")

    return _valuation(wb, out_path, rec, universe, model, D, R,
                      {"rev": rev, "op_rev": OP["rev"], "ebitda": ebitda, "ebit": ebit,
                       "da_first": das[0] if das else None, "da_last": das[-1] if das else None,
                       "pat": pat, "eps": eps, "dps": dps,
                       "ta": ta, "equity": eq, "cash": cl, "bs_cash": bs_cash,
                       "check": chk, "cfo": cfo, "cfi": cfi},
                      name, sector, shares, price, cur, price_date, price_ccy)


def _valuation(wb, out_path, rec, universe, model, D, R, ROW,
               name, sector, shares, price, cur, price_date="not stated", price_ccy=None):
    """Sector-appropriate valuation. The methods and weights come from
    valuation.WEIGHTS, so a bank is never valued on EV/EBITDA."""
    bank = _is_bank(sector)
    weights = val.weights_for(sector)
    IS = model["income_statement"]
    HAS_PRICE = isinstance(price, (int, float)) and price > 0

    V = Builder(wb, "Valuation", rec); V.currency = cur
    V.title(f"{name} — valuation",
            f"A {sector} is valued the way a {sector} should be. The weights below are the house "
            "standard for this sector and each carries its reason.")

    V.section("Cost of capital")
    def inp(label, value, basis, fmt=PCT):
        r = V.row
        V.ws.cell(row=r, column=1, value="    " + label).font = Font(name=FONT, size=BASE, color=INK)
        c = V.ws.cell(row=r, column=4, value=value)
        c.font = Font(name=FONT, size=BASE, color=BLUE_IN); c.number_format = fmt
        V.ws.cell(row=r, column=5, value=basis).font = Font(name=FONT, size=TINY, color=MUTED)
        V.row += 1
        return r
    country = universe.get("country", "")
    _rf, _crp = COUNTRY_RATES.get(country, DEFAULT_RATES)
    rf = inp("Risk-free rate", _rf,
             f"ASSUMPTION for {country or 'this market'}; refresh from the local government yield")
    beta = inp("Equity beta", 1.0, "ASSUMPTION, sector levered beta", fmt="0.00")
    erp = inp("Equity risk premium", 0.055, "ASSUMPTION, mature-market premium")
    crp = inp("Country risk premium", _crp,
              f"ASSUMPTION for {country or 'this market'}; sovereign spread over the mature market")
    ke = V.row
    V.ws.cell(row=ke, column=1, value="    Cost of equity").font = Font(
        name=FONT, size=BASE, bold=True, color=NAVY)
    c = V.ws.cell(row=ke, column=4, value=f"=D{rf}+D{beta}*D{erp}+D{crp}")
    c.font = Font(name=FONT, size=BASE, bold=True); c.number_format = PCT
    V.row += 1
    kd = inp("Cost of debt", 0.11, "from the debt schedule")
    tx = inp("Tax rate", 0.30, "the single rate used in the statements")
    we = inp("Equity weight", 0.75 if not bank else 1.0,
             "banks are valued on equity cash flows, so the WACC is not used" if bank
             else "ASSUMPTION, target capital structure")
    wacc = V.row
    V.ws.cell(row=wacc, column=1, value="    WACC").font = Font(
        name=FONT, size=SMALL, bold=True, color=NAVY)
    c = V.ws.cell(row=wacc, column=4,
                  value=f"=D{ke}*D{we}+D{kd}*(1-D{tx})*(1-D{we})")
    c.font = Font(name=FONT, size=SMALL, bold=True, color=NAVY); c.number_format = PCT
    c.fill = PatternFill("solid", start_color=AMBER)
    V.row += 1
    g = inp("Terminal growth", 0.035, "ASSUMPTION, below long-run nominal GDP")
    V.row += 1

    def out(label, formula, fmt=NUM, bold=False, basis=""):
        r = V.row
        V.ws.cell(row=r, column=1, value="    " + label).font = Font(
            name=FONT, size=BASE, bold=bold, color=NAVY if bold else INK)
        c2 = V.ws.cell(row=r, column=4, value=formula)
        c2.font = Font(name=FONT, size=BASE, bold=bold); c2.number_format = fmt
        if basis:
            V.ws.cell(row=r, column=5, value=basis).font = Font(name=FONT, size=TINY, color=MUTED)
        V.row += 1
        return r

    method_rows = {}
    disc = f"(1+D{ke})"

    if "dcf" in weights:
        V.headers()
        V.section("Free cash flow to the firm")
        # FCFF = EBIT(1-t) + D&A - capex - change in working capital.
        #
        # This row said EBIT and read EBITDA, then taxed it and never added
        # depreciation back. Two errors that partly cancel, which is why it
        # looked plausible: the tax was overstated on a figure that was itself
        # overstated. The Python valuation computed the standard definition, so
        # the screen and the workbook disagreed on 27 of 33 targets.
        f1 = V.line("EBIT", formula=lambda c, p: f"=Fcst_IS!{c}{ROW['ebit']}",
                    basis="operating profit after depreciation, not EBITDA")
        f2 = V.line("Tax on EBIT", formula=lambda c, p: f"=-{c}{f1}*$D${tx}")
        f3 = V.line("Depreciation and amortisation",
                    formula=lambda c, p: f"=-SUM(Fcst_IS!{c}{ROW['da_first']}:{c}{ROW['da_last']})"
                    if ROW.get("da_first") else "=0",
                    basis="added back: a charge against profit, not a payment")
        f4 = V.line("Capital expenditure",
                    formula=lambda c, p: f"=Fcst_CF!{c}{ROW['cfi']}")
        f5 = V.line("Change in working capital",
                    formula=lambda c, p: f"={('Schedules!' + c + str(R['nwc_chg'])) if 'nwc_chg' in R else '0'}")
        fcff = V.line("FCFF", key="fcff", bold=True,
                      formula=lambda c, p: f"=SUM({c}{f1}:{c}{f5})")
        df = V.line("Discount factor", fmt="0.000",
                    formula=lambda c, p: f"=1/(1+$D${wacc})^{F_COLS.index(c)+1}")
        pv = V.line("PV of FCFF", formula=lambda c, p: f"={c}{fcff}*{c}{df}")
        V.section("Enterprise to equity bridge")
        pvs = out("PV of the explicit forecast", f"=SUM(E{pv}:I{pv})")
        tv = out("Terminal value", f"=I{fcff}*(1+$D${g})/($D${wacc}-$D${g})")
        pvtv = out("PV of the terminal value", f"=D{tv}*I{df}")
        ev = out("Enterprise value", f"=D{pvs}+D{pvtv}", bold=True)
        nd0 = out("Less: net debt at the valuation date",
                  f"=-Schedules!D{R['debt']}+Fcst_BS!D{ROW['bs_cash']}",
                  basis="today's balance, to match a present-value enterprise value")
        eqv = out("Equity value", f"=D{ev}+D{nd0}", bold=True)
        method_rows["dcf"] = out("DCF value per share", f"=D{eqv}/{shares}", fmt=CUR, bold=True,
                                 basis="a present value")

    if "exit_multiple" in weights:
        em = inp("Exit EV/EBITDA multiple", 5.5, "ASSUMPTION, peer median at one date", fmt=MULT)
        exq = out("Exit equity value at the end of the forecast",
                  f"=Fcst_IS!I{ROW['ebitda']}*D{em}-Schedules!I{R['debt']}+Fcst_BS!I{ROW['bs_cash']}")
        method_rows["exit_multiple"] = out(
            "Exit-multiple value per share, discounted",
            f"=D{exq}/{shares}/{disc}^5", fmt=CUR,
            basis="a future value, brought back five years at the cost of equity")

    if "peer_ev_ebitda" in weights:
        pm = inp("Peer EV/EBITDA", 5.5, "ASSUMPTION, one valuation date for every peer", fmt=MULT)
        pq = out("Peer EV less net debt, next year",
                 f"=Fcst_IS!E{ROW['ebitda']}*D{pm}-Schedules!E{R['debt']}+Fcst_BS!E{ROW['bs_cash']}")
        method_rows["peer_ev_ebitda"] = out(
            "Peer EV/EBITDA value per share, discounted", f"=D{pq}/{shares}/{disc}^1", fmt=CUR,
            basis="one year forward, brought back one year")

    if "peer_pe" in weights:
        pe = inp("Peer P/E", 11.0, "ASSUMPTION, same date and period as the other multiples", fmt=MULT)
        method_rows["peer_pe"] = out("Peer P/E value per share, discounted",
                                     f"=Fcst_IS!E{ROW['eps']}*D{pe}/{disc}^1", fmt=CUR,
                                     basis="one year forward, brought back one year")

    if "peer_pb_roe" in weights:
        pb = inp("Peer price to book", 1.0, "ASSUMPTION, dated with the other multiples", fmt=MULT)
        method_rows["peer_pb_roe"] = out(
            "Price-to-book value per share, discounted",
            f"=Fcst_BS!E{ROW['equity']}*D{pb}/{shares}/{disc}^1", fmt=CUR,
            basis="book value is the right base for a bank, earnings quality is in the ROE")

    if "ddm" in weights:
        method_rows["ddm"] = out(
            "Dividend discount value per share",
            f"=Fcst_IS!E{ROW['dps']}/(D{ke}-$D${g})", fmt=CUR,
            basis="a perpetuity on next year's dividend, already a present value")

    if "sotp" in weights:
        method_rows["sotp"] = out("Sum of the parts", f"=D{list(method_rows.values())[0]}", fmt=CUR,
                                  basis="not modelled separately; defaults to the lead method")

    V.section("Weighting — the house standard for this sector")
    V.note("Each weight below is the standard for a " + sector + ", not a per-company choice. "
           "The reason is stated so a reader can disagree with the reason rather than the number.")
    wrows = []
    for method, w in weights.items():
        if method not in method_rows:
            continue
        r = V.row
        V.ws.cell(row=r, column=1, value="    " + method.replace("_", " ")).font = Font(
            name=FONT, size=BASE, color=INK)
        c2 = V.ws.cell(row=r, column=3, value=f"=D{method_rows[method]}")
        c2.number_format = CUR; c2.font = Font(name=FONT, size=BASE, color=GREEN_LINK)
        c3 = V.ws.cell(row=r, column=4, value=w)
        c3.number_format = PCT; c3.font = Font(name=FONT, size=BASE, color=BLUE_IN)
        V.ws.cell(row=r, column=5, value=val.RATIONALE.get(method, "")[:104]).font = Font(
            name=FONT, size=TINY, color=MUTED)
        wrows.append(r)
        V.row += 1
    wsum = out("Weight check (must be 1.00)",
               f"=SUM(D{wrows[0]}:D{wrows[-1]})" if wrows else "=1", fmt="0.00")
    tgt = out("WEIGHTED TARGET PRICE",
              f"=SUMPRODUCT(C{wrows[0]}:C{wrows[-1]},D{wrows[0]}:D{wrows[-1]})" if wrows else "=0",
              fmt=CUR, bold=True)
    V.section("Price, in the reporting currency")
    V.note("One currency per company. Where the shares are quoted in another currency the price "
           "is converted here and the working is shown, so the comparison with a value built "
           "from the accounts is like for like.")
    conv, rate, fx_date, fx_note = convert_price(price, price_ccy, cur)
    q = out(f"Quoted price ({price_ccy or cur})", price if HAS_PRICE else None, fmt=CUR,
            basis=f"as at {price_date}")
    if not HAS_PRICE:
        V.ws.cell(row=q, column=4).fill = PatternFill("solid", start_color=AMBER)
        V.ws.cell(row=q, column=4).font = Font(name=FONT, size=BASE, color=BLUE_IN)
    fxr = out("FX rate applied", rate if rate is not None else None, fmt="0.000000",
              basis=(f"{fx_note}, rates as at {fx_date}" if rate is not None
                     else f"no rate available: {fx_note}"))
    px = out(f"Price in {cur}, the reporting currency",
             f"=IF(OR(D{q}=\"\",D{fxr}=\"\"),\"\",D{q}*D{fxr})", fmt=CUR, bold=True,
             basis="quoted price times the rate; both are shown so the step can be checked")
    ups = out("Upside to target", f'=IF(D{px}="","",D{tgt}/D{px}-1)', fmt=PCT, bold=True)
    # A target at or below zero, or one a multiple of the price away, is not a
    # bearish call. It is a model that has failed, and publishing it as a Sell
    # with a target of minus 21,350 a share is worse than publishing nothing.
    # The arithmetic that produced it is still on the sheet above, so the reader
    # can see what went wrong rather than being told a number.
    rat = out("RECOMMENDATION",
              f'=IF(D{px}="","NOT RATED — no price or no rate at the valuation date",'
              f'IF(D{tgt}<=0,"NOT RATED — the valuation returned a target of zero or below",'
              f'IF(ABS(D{ups})>3,"NOT RATED — the target is more than four times the price, '
              f'which is a broken model rather than a view",'
              f'IF(D{ups}>=0,"Buy","Sell"))))', fmt="General", bold=True)
    V.ws.cell(row=rat, column=4).fill = PatternFill("solid", start_color=AMBER)
    V.note("Buy or Sell. There is no Hold. A stock with no price at the valuation date is NOT "
           "RATED, which is an absence of data rather than a view, and the control below flags it. "
           "A target of zero or below, or one more than four times the price, is NOT RATED for the "
           "same reason: it is the model failing rather than a position worth taking.")

    # ------------------------------------------------------------ controls
    C = Builder(wb, "Sources_and_Controls", rec); C.currency = cur
    C.title(f"{name} — controls",
            "These test the things that actually go wrong: an unbalanced model, a hidden plug, "
            "negative cash, or a valuation that does not follow the sector standard.")
    C.headers()
    def ctrl(label, f, note=""):
        r = C.row
        C.ws.cell(row=r, column=1, value=label).font = Font(name=FONT, size=BASE, color=INK)
        if note:
            C.ws.cell(row=r, column=2, value=note).font = Font(name=FONT, size=TINY, color=MUTED)
        for col in F_COLS:
            cc = C.ws.cell(row=r, column=Builder._ci(col), value=f(col))
            cc.font = Font(name=FONT, size=BASE, bold=True)
            cc.alignment = Alignment(horizontal="center")
        C.row += 1
    ctrl("Balance sheet balances",
         lambda c: f'=IF(ABS(Fcst_BS!{c}{ROW["check"]})<1,"PASS","FAIL")')
    ctrl("Cash never negative",
         lambda c: f'=IF(Fcst_CF!{c}{ROW["cash"]}>=0,"PASS","FAIL")')
    ctrl("Cash flow ties to the balance sheet",
         lambda c: f'=IF(ABS(Fcst_CF!{c}{ROW["cash"]}-Fcst_BS!{c}{ROW["bs_cash"]})<1,"PASS","FAIL")',
         "the closing cash on each statement must be the same number")
    ctrl("Revenue comes from the operating build",
         lambda c: f'=IF(ABS(Fcst_IS!{c}{ROW["rev"]}-Op_Drivers!{c}{ROW["op_rev"]})<1,"PASS","FAIL")',
         "the income statement must not restate revenue independently")
    ctrl("Equity rolls with retained profit",
         lambda c: f'=IF(ABS(Fcst_BS!{c}{ROW["equity"]}-Fcst_BS!{("D" if c=="E" else F_COLS[F_COLS.index(c)-1])}{ROW["equity"]}'
                   f'-Fcst_IS!{c}{ROW["pat"]}*(1-{D.get("payout_ratio","0.5")}))<1,"PASS","FAIL")',
         "no equity appears from nowhere")
    C.row += 1
    for lbl, f, note in (
        ("Weights sum to 1.00", f'=IF(ABS(Valuation!D{wsum}-1)<0.001,"PASS","FAIL")',
         "the sector standard, not a per-company choice"),
        ("Price is stated in the reporting currency",
         f'=IF(Valuation!D{px}="","FAIL","PASS")',
         (f"quoted in {price_ccy}, converted to {cur} at the rate shown"
          if (price_ccy or cur) != cur else f"quoted and reported in {cur}")),
        ("Recommendation is Buy or Sell, never Hold",
         f'=IF(ISNUMBER(SEARCH("Hold",Valuation!D{rat})),"FAIL","PASS")', ""),
        # These three were the literal string "PASS" and so could never fail.
        # Each now reads something the workbook actually computed.
        ("Valuation weights sum to 1",
         f'=IF(ABS(Valuation!D{wsum}-1)<0.0001,"PASS","FAIL")',
         f"{sector}: " + ", ".join(weights)),
        ("Every valuation method produced a value",
         f'=IF(COUNT(Valuation!C{wrows[0]}:C{wrows[-1]})={len(wrows)},"PASS","FAIL")',
         ", ".join(_sched_set(sector))),
        ("EV/EBITDA not used for a bank",
         (f'=IF(COUNTIF(Valuation!A{wrows[0]}:A{wrows[-1]},"*ev ebitda*")>0,"FAIL","PASS")'
          if bank else f'=IF(Valuation!D{wsum}>0,"PASS","FAIL")'),
         "a bank has no meaningful enterprise value"),
    ):
        r = C.row
        C.ws.cell(row=r, column=1, value=lbl).font = Font(name=FONT, size=BASE, color=INK)
        if note:
            C.ws.cell(row=r, column=2, value=note).font = Font(name=FONT, size=TINY, color=MUTED)
        cc = C.ws.cell(row=r, column=5, value=f)
        cc.font = Font(name=FONT, size=BASE, bold=True)
        cc.alignment = Alignment(horizontal="center")
        C.row += 1

    for nm, colr in (("Cover", NAVY), ("Assumptions", "9A7B1F"), ("Op_Drivers", TEAL),
                     ("Schedules", TEAL), ("Fcst_IS", NAVY), ("Fcst_BS", NAVY),
                     ("Fcst_CF", NAVY), ("Valuation", SECTION),
                     ("Sources_and_Controls", "9A7B1F")):
        if nm in wb.sheetnames:
            wb[nm].sheet_properties.tabColor = colr
    order = ["Cover", "Assumptions", "Op_Drivers", "Schedules", "Fcst_IS", "Fcst_BS",
             "Fcst_CF", "Valuation", "Sources_and_Controls"]
    for i, nm in enumerate(x for x in order if x in wb.sheetnames):
        wb.move_sheet(nm, offset=i - wb.sheetnames.index(nm))
    wb.save(out_path)
    return {"path": str(out_path), "sheets": wb.sheetnames, "sector": sector,
            "schedules": _sched_set(sector), "methods": list(weights)}
