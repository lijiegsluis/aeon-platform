"""Hamilcar model-standard sheets for the workbook.

Adds the tabs the house standard requires on top of the historical statements:

    Op_Drivers      sector KPIs that build revenue, cost and capex (blue inputs)
    Fcst_IS/BS/CF   the integrated three-statement forecast, with a balance check
    Schedules       PPE, intangibles, leases, debt and working-capital roll-forwards
    Scenarios       named scenarios with the drivers each one moves
    Valuation       every method, its standard weight and the reason for it,
                    a two-way sensitivity grid and the football field

Figures come from the Python engine, which is the single source of truth for the
platform, the note and the workbook. Subtotals, margins and the balance check are
written as live Excel formulas so a reader can audit the arithmetic in place
rather than take it on trust.
"""

from __future__ import annotations

from typing import Any

from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from aeon_nimbus import drivers as dr
from aeon_nimbus import scenarios as sc
from aeon_nimbus import three_statement as ts
from aeon_nimbus import valuation as val

NAVY, GREEN, SOFT, YELLOW = "FF123142", "FF1F7A5C", "FFEFF3F3", "FFFFF3B0"
MONEY = '#,##0;(#,##0);"-"'
PCT = '0.0%'
MULT = '0.0"x"'
NUM = '#,##0.00'
_HDR = Font(color="FFFFFFFF", bold=True, size=12)
_SEC = Font(bold=True, color="FFFFFFFF", size=10)
_LBL = Font(bold=True, color="FF123142")
_INPUT = Font(color="FF0000FF")          # blue: an assumption you may change
_CALC = Font(color="FF000000")           # black: calculated
_NOTE = Font(italic=True, size=9, color="FF5C6B72")


def _sheet(wb, name: str, tab: str = NAVY):
    ws = wb.create_sheet(name)
    ws.sheet_properties.tabColor = tab
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 44
    return ws


def _title(ws, text: str, span: int = 10):
    ws.cell(row=1, column=1, value=text).font = _HDR
    for c in range(1, span + 1):
        ws.cell(row=1, column=c).fill = PatternFill("solid", fgColor=NAVY)


def _band(ws, row: int, text: str, span: int = 10):
    ws.cell(row=row, column=1, value=text).font = _SEC
    for c in range(1, span + 1):
        ws.cell(row=row, column=c).fill = PatternFill("solid", fgColor=GREEN)


def _years(ws, row: int, years: list[str], c0: int = 2):
    for i, fy in enumerate(years):
        c = ws.cell(row=row, column=c0 + i, value=fy)
        c.font = _LBL
        c.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(c0 + i)].width = 14


def _row(ws, row: int, label: str, values: list, *, fmt=MONEY, font=_CALC,
         c0: int = 2, bold: bool = False):
    lc = ws.cell(row=row, column=1, value=label)
    lc.font = Font(bold=True, color="FF123142") if bold else Font(color="FF17262E")
    for i, v in enumerate(values):
        cell = ws.cell(row=row, column=c0 + i, value=v)
        cell.number_format = fmt
        cell.font = Font(bold=True, color=font.color.rgb) if bold else font
    return row + 1


def add_model_sheets(wb, company: dict, universe_entry: dict, *, years: int = 5,
                     price: float | None = None, peers: dict | None = None,
                     wacc: float = 0.16, terminal_growth: float = 0.03,
                     countries: dict | None = None) -> dict[str, Any]:
    """Append the Hamilcar-standard sheets. Returns the model + valuation."""
    model = ts.build(company, universe_entry, years=years)
    if not model:
        return {}
    sector = model["sector"]
    market = company.get("market") or {}
    shares = market.get("shares_outstanding_m") or 0.0
    price = price or market.get("share_price")
    fy = [r["fy"] for r in model["income_statement"]]

    _drivers_sheet(wb, model, sector, fy)
    _statements_sheets(wb, model, fy)
    _schedules_sheet(wb, model, fy)
    scen_rows = _scenarios_sheet(wb, company, universe_entry, model, shares, wacc, terminal_growth)
    valuation = _valuation_sheet(wb, model, sector, shares, price, wacc, terminal_growth,
                                 peers, company)
    if countries:
        _country_sheet(wb, company, universe_entry, countries, years)
    return {"model": model, "valuation": valuation, "scenarios": scen_rows}


# --------------------------------------------------------------------------

def _drivers_sheet(wb, model, sector, fy):
    ws = _sheet(wb, "Op_Drivers", GREEN)
    _title(ws, "  OPERATING DRIVERS — the KPIs that build the forecast", 10)
    ws.cell(row=2, column=1, value="Blue cells are the assumptions. Revenue, cost and capex "
                                   "are built from these, not from a single growth rate.").font = _NOTE
    r = 4
    _band(ws, r, f"DRIVER SET ({sector})"); r += 1
    ws.cell(row=r, column=1, value="Driver").font = _LBL
    ws.cell(row=r, column=2, value="Value").font = _LBL
    ws.cell(row=r, column=3, value="Unit").font = _LBL
    r += 1
    fmt = {"pct": PCT, "money": NUM, "num": NUM, "mult": MULT}
    for key, label, kind in dr.driver_keys(sector):
        if key not in model["drivers"]:
            continue
        ws.cell(row=r, column=1, value=label).font = Font(color="FF17262E")
        c = ws.cell(row=r, column=2, value=model["drivers"][key])
        c.font = _INPUT
        c.number_format = fmt.get(kind, NUM)
        ws.cell(row=r, column=3, value=kind).font = _NOTE
        r += 1

    r += 1
    _band(ws, r, "OPERATING BUILD — revenue by stream"); r += 1
    _years(ws, r, fy); r += 1
    streams = list((model["operating"][0].get("revenue_streams") or {}).keys())
    first = r
    for s in streams:
        r = _row(ws, r, s, [o["revenue_streams"].get(s, 0.0) for o in model["operating"]])
    if streams:
        vals = [f"=SUM({get_column_letter(2+i)}{first}:{get_column_letter(2+i)}{r-1})"
                for i in range(len(fy))]
        r = _row(ws, r, "Total revenue", vals, bold=True)
    r += 1
    _band(ws, r, "OPERATING BUILD — cash costs"); r += 1
    _years(ws, r, fy); r += 1
    cost_keys = list((model["operating"][0].get("cost_lines") or {}).keys())
    cfirst = r
    for k in cost_keys:
        r = _row(ws, r, k, [-o["cost_lines"].get(k, 0.0) for o in model["operating"]])
    if cost_keys:
        r = _row(ws, r, "Total cash costs",
                 [f"=SUM({get_column_letter(2+i)}{cfirst}:{get_column_letter(2+i)}{r-1})"
                  for i in range(len(fy))], bold=True)
    r += 1
    _band(ws, r, "IMPLIED OPERATING KPIs"); r += 1
    _years(ws, r, fy); r += 1
    for k in (model["operating"][0].get("kpis") or {}):
        r = _row(ws, r, k, [o["kpis"].get(k, 0.0) for o in model["operating"]], fmt=NUM)
    r = _row(ws, r, "Capex", [-o["capex"] for o in model["operating"]])


def _statements_sheets(wb, model, fy):
    # ---- income statement ----
    ws = _sheet(wb, "Fcst_IS")
    _title(ws, "  FORECAST INCOME STATEMENT", 10)
    r = 3
    _years(ws, r, fy); r += 1
    I = model["income_statement"]
    for label, key, fmt, bold in [
        ("Revenue", "revenue", MONEY, True), ("EBITDA", "ebitda", MONEY, True),
        ("EBITDA margin", "ebitda_margin", PCT, False),
        ("Depreciation", "depreciation", MONEY, False),
        ("Amortisation", "amortisation", MONEY, False),
        ("Right-of-use depreciation", "rou_depreciation", MONEY, False),
        ("EBIT", "ebit", MONEY, True),
        ("Interest expense", "interest_expense", MONEY, False),
        ("Interest income", "interest_income", MONEY, False),
        ("Profit before tax", "pbt", MONEY, True),
        ("Tax", "tax", MONEY, False),
        ("Profit after tax", "profit_after_tax", MONEY, False),
        ("Minority interest", "minority_interest", MONEY, False),
        ("Net income attributable", "net_income", MONEY, True)]:
        r = _row(ws, r, label, [x[key] for x in I], fmt=fmt, bold=bold)

    # ---- balance sheet ----
    ws = _sheet(wb, "Fcst_BS")
    _title(ws, "  FORECAST BALANCE SHEET", 10)
    ws.cell(row=2, column=1, value="The balance check is a live formula: assets less "
                                   "liabilities and equity must be nil.").font = _NOTE
    r = 3
    _years(ws, r, fy); r += 1
    B = model["balance_sheet"]
    _band(ws, r, "ASSETS"); r += 1
    a_first = r
    # Every balance the engine holds gets a line. A key computed and then not
    # drawn does not vanish: it stays inside the totals, and the visible rows
    # then fail to add up to them.
    for label, key in [("Property, plant and equipment", "ppe"), ("Intangible assets", "intangibles"),
                       ("Right-of-use assets", "right_of_use_assets"),
                       ("Loans and advances to customers", "loans"), ("Receivables", "receivables"),
                       ("Inventories", "inventories"), ("Investment securities", "investments"),
                       ("Assets not separately identified", "other_assets"),
                       ("Deferred tax asset", "deferred_tax_asset"), ("Cash", "cash"),
                       ("Other net assets", "other_net_assets")]:
        if any(x.get(key) for x in B):
            r = _row(ws, r, label, [x.get(key, 0) for x in B])
    ta_row = r
    r = _row(ws, r, "Total assets",
             [f"=SUM({get_column_letter(2+i)}{a_first}:{get_column_letter(2+i)}{r-1})"
              for i in range(len(fy))], bold=True)
    r += 1
    _band(ws, r, "LIABILITIES AND EQUITY"); r += 1
    l_first = r
    for label, key in [("Customer deposits", "deposits"), ("Borrowings", "borrowings"),
                       ("Lease liabilities", "lease_liabilities"), ("Payables", "payables"),
                       ("Provisions", "provisions"),
                       ("Liabilities not separately identified", "other_liabilities"),
                       ("Deferred tax liability", "deferred_tax_liability"),
                       ("Equity", "equity")]:
        if key == "equity" or any(x.get(key) for x in B):
            r = _row(ws, r, label, [x.get(key, 0) for x in B])
    tl_row = r
    r = _row(ws, r, "Total liabilities and equity",
             [f"=SUM({get_column_letter(2+i)}{l_first}:{get_column_letter(2+i)}{r-1})"
              for i in range(len(fy))], bold=True)
    r += 1
    r = _row(ws, r, "Balance check (must be nil)",
             [f"={get_column_letter(2+i)}{ta_row}-{get_column_letter(2+i)}{tl_row}"
              for i in range(len(fy))], bold=True)
    for i in range(len(fy)):
        ws.cell(row=r - 1, column=2 + i).fill = PatternFill("solid", fgColor=YELLOW)
    r += 1
    r = _row(ws, r, "Net debt (incl. leases)", [x["net_debt"] for x in B], bold=True)

    # ---- cash flow ----
    ws = _sheet(wb, "Fcst_CF")
    _title(ws, "  FORECAST CASH FLOW", 10)
    r = 3
    _years(ws, r, fy); r += 1
    C = model["cash_flow"]
    for label, key, bold in [
        ("Net income", "net_income", False),
        ("Depreciation and amortisation", "depreciation_amortisation", False),
        ("Deferred tax", "deferred_tax", False),
        ("Change in working capital", "change_in_working_capital", False),
        ("Operating cash flow", "operating_cash_flow", True),
        ("Capital expenditure", "capital_expenditure", False),
        ("Investing cash flow", "investing_cash_flow", True),
        ("Debt drawdown", "debt_drawdown", False),
        ("Debt repayment", "debt_repayment", False),
        ("Lease principal", "lease_principal", False),
        ("Dividends paid", "dividends_paid", False),
        ("Financing cash flow", "financing_cash_flow", True),
        ("Net change in cash", "net_change_in_cash", True),
        ("Closing cash", "closing_cash", True),
        ("Free cash flow", "free_cash_flow", True)]:
        r = _row(ws, r, label, [x[key] for x in C], bold=bold)


def _schedules_sheet(wb, model, fy):
    ws = _sheet(wb, "Schedules")
    _title(ws, "  SUPPORTING SCHEDULES — the roll-forwards behind the statements", 10)
    ws.cell(row=2, column=1, value="Each balance opens, moves and closes. The statements read "
                                   "from these, so depreciation and interest are earned, "
                                   "not assumed as a percentage of revenue.").font = _NOTE
    r = 4
    S = model["schedules"]
    for title, key, lines in [
        ("PROPERTY, PLANT AND EQUIPMENT", "ppe",
         [("Opening balance", "open"), ("Additions (capex)", "additions"),
          ("Depreciation", "depreciation"), ("Closing balance", "close")]),
        ("INTANGIBLE ASSETS", "intangibles",
         [("Opening balance", "open"), ("Additions", "additions"),
          ("Amortisation", "amortisation"), ("Closing balance", "close")]),
        ("LEASES (IFRS 16)", "leases",
         [("Opening liability", "open"), ("Additions", "additions"),
          ("Interest accretion", "interest"), ("Principal paid", "principal"),
          ("Closing liability", "close")]),
        ("DEBT", "debt",
         [("Opening balance", "open"), ("Drawdown", "drawdown"), ("Repayment", "repayment"),
          ("Interest on average balance", "interest"), ("Closing balance", "close")]),
        ("WORKING CAPITAL", "working_capital",
         [("Receivables", "receivables"), ("Inventories", "inventory"),
          ("Payables", "payables"), ("Net working capital", "net_working_capital"),
          ("Cash impact of the movement", "cash_impact")]),
    ]:
        _band(ws, r, title); r += 1
        _years(ws, r, fy); r += 1
        rows = S.get(key) or []
        for label, k in lines:
            sign = -1 if k in ("depreciation", "amortisation", "repayment", "principal",
                               "payables") else 1
            r = _row(ws, r, label, [sign * (x.get(k) or 0.0) for x in rows],
                     bold=k in ("close", "net_working_capital"))
        r += 1

    _band(ws, r, "SCHEDULE ASSUMPTIONS"); r += 1
    a = model["schedule_assumptions"]
    from aeon_nimbus import schedules as _s
    for key, label, kind in _s.SCHEDULE_KEYS:
        if key not in a:
            continue
        ws.cell(row=r, column=1, value=label).font = Font(color="FF17262E")
        c = ws.cell(row=r, column=2, value=a[key])
        c.font = _INPUT
        c.number_format = PCT if kind == "pct" else (MULT if kind == "mult" else NUM)
        r += 1


def _scenarios_sheet(wb, company, universe_entry, model, shares, wacc, growth):
    ws = _sheet(wb, "Scenarios")
    _title(ws, "  SCENARIOS — named states of the world, not bull/base/bear", 10)
    ws.cell(row=2, column=1, value="Each scenario moves the specific operating drivers its "
                                   "thesis implies, so the mechanism can be argued with.").font = _NOTE
    r = 4
    ws.cell(row=r, column=1, value="Scenario").font = _LBL
    for i, h in enumerate(["Drivers moved", "FY-final revenue", "FY-final EBITDA margin",
                           "DCF value/share", "Thesis"]):
        ws.cell(row=r, column=2 + i, value=h).font = _LBL
        ws.column_dimensions[get_column_letter(2 + i)].width = 26 if i in (0, 4) else 18
    ws.column_dimensions["F"].width = 90
    r += 1
    out = []
    for s in sc.named_scenarios(model["sector"]):
        dv = sc.apply_scenario(model["drivers"], s)
        m = ts.build(company, universe_entry, years=len(model["income_statement"]), drivers=dv)
        nd = (m.get("balance_sheet") or [{}])[0].get("net_debt", 0.0)
        v = val.dcf_from_model(m, wacc=wacc, terminal_growth=growth, shares=shares, net_debt=nd)
        last = m["income_statement"][-1]
        ws.cell(row=r, column=1, value=s["name"]).font = Font(bold=True, color="FF123142")
        ws.cell(row=r, column=2, value=", ".join(s["deltas"].keys()) or "base case").font = _NOTE
        ws.cell(row=r, column=3, value=last["revenue"]).number_format = MONEY
        ws.cell(row=r, column=4, value=last["ebitda_margin"]).number_format = PCT
        c = ws.cell(row=r, column=5, value=v.get("value_per_share"))
        c.number_format = NUM
        c.font = Font(bold=True)
        ws.cell(row=r, column=6, value=s["thesis"]).alignment = Alignment(wrap_text=True, vertical="top")
        out.append({"name": s["name"], "value_per_share": v.get("value_per_share"),
                    "revenue": last["revenue"], "margin": last["ebitda_margin"]})
        r += 1
    return out


def _valuation_sheet(wb, model, sector, shares, price, wacc, growth, peers, company):
    ws = _sheet(wb, "Val_Weighted")
    _title(ws, "  WEIGHTED VALUATION — methods, standard weights, sensitivity, football field", 12)
    div = abs((company.get("financials") or [{}])[-1].get("dividends_paid") or 0.0)
    out = val.run_weighted(model, sector=sector, shares=shares, price=price, wacc=wacc,
                           terminal_growth=growth, peers=peers or {},
                           dividend_total=div or None, cost_of_equity=wacc + 0.01)
    r = 3
    _band(ws, r, "METHOD, VALUE, STANDARD WEIGHT AND WHY", 12); r += 1
    for i, h in enumerate(["Method", "Value/share", "Weight", "Contribution", "Rationale"]):
        ws.cell(row=r, column=1 + i, value=h).font = _LBL
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 10
    ws.column_dimensions["D"].width = 13
    ws.column_dimensions["E"].width = 96
    r += 1
    for m in out["summary"]["methods"]:
        ws.cell(row=r, column=1, value=m["method"]).font = Font(color="FF17262E")
        ws.cell(row=r, column=2, value=m["value_per_share"]).number_format = NUM
        ws.cell(row=r, column=3, value=m["weight"]).number_format = PCT
        ws.cell(row=r, column=4, value=m["contribution"]).number_format = NUM
        ws.cell(row=r, column=5, value=m["rationale"]).alignment = Alignment(wrap_text=True, vertical="top")
        r += 1
    ws.cell(row=r, column=1, value="Weighted target price").font = Font(bold=True, color="FF123142")
    tc = ws.cell(row=r, column=2, value=out["summary"]["target"])
    tc.number_format = NUM
    tc.font = Font(bold=True)
    ws.cell(row=r, column=3, value="=SUM(C%d:C%d)" % (r - len(out["summary"]["methods"]), r - 1)).number_format = PCT
    r += 2

    _band(ws, r, "RATING", 12); r += 1
    ws.cell(row=r, column=1, value="Current price").font = _LBL
    ws.cell(row=r, column=2, value=price).number_format = NUM
    r += 1
    ws.cell(row=r, column=1, value="Upside to target").font = _LBL
    ws.cell(row=r, column=2, value=out["rating"]["upside"]).number_format = PCT
    r += 1
    ws.cell(row=r, column=1, value="Rating (Buy or Sell — no Hold)").font = _LBL
    rc = ws.cell(row=r, column=2, value='=IF(B%d="","NR",IF(B%d>=0,"Buy","Sell"))' % (r - 1, r - 1))
    rc.font = Font(bold=True, size=12)
    r += 2

    _band(ws, r, "SENSITIVITY — value per share: WACC (rows) x terminal growth (columns)", 12); r += 1
    sens = out["sensitivity"]
    for i, g in enumerate(sens["growths"]):
        c = ws.cell(row=r, column=2 + i, value=g)
        c.number_format = PCT
        c.font = _LBL
    r += 1
    for row in sens["grid"]:
        c = ws.cell(row=r, column=1, value=row["wacc"])
        c.number_format = PCT
        c.font = _LBL
        for i, v in enumerate(row["values"]):
            cell = ws.cell(row=r, column=2 + i, value=v)
            cell.number_format = NUM
            if abs(v - (out["summary"]["target"] or 0)) < 0.005:
                cell.fill = PatternFill("solid", fgColor=YELLOW)
        r += 1
    r += 1

    _band(ws, r, "FOOTBALL FIELD — range by method", 12); r += 1
    for i, h in enumerate(["Method", "Low", "Mid", "High"]):
        ws.cell(row=r, column=1 + i, value=h).font = _LBL
    r += 1
    for b in out["football_field"]:
        ws.cell(row=r, column=1, value=b["label"]).font = Font(color="FF17262E")
        for i, k in enumerate(("low", "mid", "high")):
            if b.get(k) is not None:
                ws.cell(row=r, column=2 + i, value=b[k]).number_format = NUM
        r += 1
    return out


def _country_sheet(wb, company, universe_entry, countries, years):
    ws = _sheet(wb, "By_Country")
    _title(ws, "  FORECAST BY COUNTRY — built separately, then consolidated", 10)
    ws.cell(row=2, column=1, value="A group with different economics per market is modelled "
                                   "per market: its own drivers, margins and capex.").font = _NOTE
    out = ts.build_by_country(company, universe_entry, years=years, countries=countries)
    r = 4
    for name, m in (out.get("countries") or {}).items():
        _band(ws, r, name.upper()); r += 1
        fy = [x["fy"] for x in m["income_statement"]]
        _years(ws, r, fy); r += 1
        for label, key in [("Revenue", "revenue"), ("EBITDA", "ebitda"), ("EBIT", "ebit"),
                           ("Net income", "net_income")]:
            r = _row(ws, r, label, [x[key] for x in m["income_statement"]],
                     bold=key == "revenue")
        r = _row(ws, r, "Balance check",
                 [c["difference"] for c in m["checks"]], fmt=NUM)
        r += 1
    cons = out.get("consolidated") or {}
    if cons.get("income_statement"):
        _band(ws, r, "CONSOLIDATED GROUP"); r += 1
        fy = [x["fy"] for x in cons["income_statement"]]
        _years(ws, r, fy); r += 1
        for label, key in [("Revenue", "revenue"), ("EBITDA", "ebitda"), ("Net income", "net_income")]:
            r = _row(ws, r, label, [x.get(key, 0.0) for x in cons["income_statement"]],
                     bold=key == "revenue")
