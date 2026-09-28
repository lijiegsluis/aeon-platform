"""Excel model compiler — the keystone of the ER automation system.

Turns a company's approved, source-linked dataset into a fully LINKED .xlsx
model: editable Bull/Base/Bear assumptions drive a merged financial-statement
build, an FCFF DCF and a scenario summary via NATIVE EXCEL FORMULAS and absolute
named ranges — not a static export. Historical figures are the reported actuals
(blue inputs, source-linked); every forecast/valuation cell is a black formula;
green cells are pulled from another sheet.

Consolidated into a small set of tabs with a navy/green header aesthetic:
Cover · Assumptions · Financials · Valuation · Comps & KPIs · Overview ·
Sources & Controls (+ Bank_Detail for banks). Charts are embedded in-place.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.chart.data_source import StrRef
from openpyxl.chart.series import SeriesLabel
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

from aeon_nimbus import controls as ctrl
from aeon_nimbus import forecast as fc
from aeon_nimbus import scenarios as scn
from aeon_nimbus.platform_data import value_per_share, bank_value_per_share
from aeon_nimbus.sector import config_for as _sector_config

NAVY = "FF123142"
GREEN = "FF5AA469"      # Letshego-style year-header green
TEAL = "FF0C7A6B"
GOLD = "FF9A7B1F"
YELLOW = "FFFFF3B0"
SOFT = "FFEFF3F3"
MONEY = '#,##0;(#,##0);"-"'
PCT = '0.0%'
MULT = '0.0"x"'
_HDR = Font(color="FFFFFFFF", bold=True, size=12, name="Calibri")
_YHDR = Font(color="FFFFFFFF", bold=True, size=11, name="Calibri")
_LBL = Font(bold=True, color="FF123142")
_SEC = Font(bold=True, color="FFFFFFFF", size=10)
_SRC = Font(italic=True, size=9, color="FF5C6B72")
_BODY = Font(color="FF17262E")
_NOTE = Font(italic=True, size=10, color="FF5C6B72")
_INPUT = Font(color="FF0000FF")           # blue: hardcoded input / actual
_FORMULA = Font(color="FF000000")          # black: calculated here
_LINK = Font(color="FF008000")             # green: pulled from another sheet
_INPUT_B = Font(bold=True, color="FF0000FF")
_CENTER = Alignment(horizontal="center")


def _sheet(wb: Workbook, name: str, tab: str = NAVY) -> Any:
    ws = wb.create_sheet(name)
    ws.sheet_properties.tabColor = tab
    ws.sheet_view.showGridLines = False
    return ws


def _fill(ws, row: int, span: int, colour: str) -> None:
    for c in range(1, span + 1):
        ws.cell(row=row, column=c).fill = PatternFill("solid", fgColor=colour)


def _title(ws, text: str, span: int = 12) -> None:
    """Navy company/title band on row 1."""
    ws.cell(row=1, column=1, value=text).font = _HDR
    _fill(ws, 1, span, NAVY)


def _years_band(ws, years: list[str], first_col: int, span: int, right_label: str = "") -> None:
    """Green year-header band on row 2 (Letshego style)."""
    _fill(ws, 2, span, GREEN)
    for j, y in enumerate(years):
        c = ws.cell(row=2, column=first_col + j, value=y)
        c.font = _YHDR
        c.alignment = _CENTER
    if right_label:
        ws.cell(row=2, column=1, value=right_label).font = _YHDR


def _section(ws, row: int, text: str, span: int) -> None:
    ws.cell(row=row, column=1, value=text).font = _SEC
    _fill(ws, row, span, TEAL)


def _kv(ws, row: int, key: str, value: Any, fmt: str | None = None, vfont: Font | None = None) -> None:
    ws.cell(row=row, column=1, value=key).font = _LBL
    cell = ws.cell(row=row, column=2, value=value)
    if fmt:
        cell.number_format = fmt
    if vfont:
        cell.font = vfont


def _name(wb: Workbook, name: str, sheet: str, cell: str) -> None:
    # Absolute ($B$3): a relative defined name is resolved by Excel relative to the
    # cell that uses it, so named inputs would point at the wrong (blank) cells.
    abs_cell = re.sub(r"([A-Za-z]+)(\d+)", r"$\1$\2", cell)
    wb.defined_names.add(DefinedName(name, attr_text=f"'{sheet}'!{abs_cell}"))


def _legend(chart, sheet: str, label_rows: list[int]) -> None:
    """Name each chart series from its row-label cell (col A) for a clean legend."""
    for i, lr in enumerate(label_rows):
        if i < len(chart.series):
            chart.series[i].tx = SeriesLabel(strRef=StrRef(f"'{sheet}'!$A${lr}"))


def _finalize(wb: Workbook, freeze: dict[str, str]) -> None:
    """Readable view on open: comfortable zoom, value columns wide enough that no
    figure shows as '####', frozen headers, Cover selected."""
    for ws in wb.worksheets:
        ws.sheet_view.zoomScale = 120
        ws.sheet_view.zoomScaleNormal = 120
        if (ws.column_dimensions["A"].width or 0) < 30:
            ws.column_dimensions["A"].width = 32
        for ci in range(2, (ws.max_column or 1) + 1):
            col = get_column_letter(ci)
            if (ws.column_dimensions[col].width or 0) < 13:
                ws.column_dimensions[col].width = 13
        fp = freeze.get(ws.title)
        if fp:
            ws.freeze_panes = fp
    wb.active = 0


def compile_model(company: dict, universe: dict, out_path: str | Path,
                  model_version: str = "v1", valuation_date: str | None = None,
                  scenarios: dict | None = None, comps: list | None = None) -> dict[str, Any]:
    """Compile a linked Excel model. Returns metadata incl. controls + DQ score.

    `comps` is an optional list of peer trading multiples (name, ev_ebitda, pe, pb,
    roe, ebitda_margin, div_yield) — see platform_data.comps_for — to populate the
    comparables table with real peer numbers rather than a bare name list."""
    company = {**company, "_peers": universe.get("peers", [])}
    fins = sorted(company.get("financials", []), key=lambda f: f.get("fy", ""))
    name = company.get("name", "Company")
    tkr = universe.get("ticker", "NA")
    cur = company.get("currency", "USD")
    unit = company.get("unit", "millions")
    is_bank = company.get("bank") is not None
    market = company.get("market", {}) or {}

    # --- Single normalised scenario set (same as platform dashboard + studio) ---
    # Use scn.normalise() so any stored/edited scenarios from the studio are
    # honoured, not silently replaced by defaults. This is the single source
    # of truth shared by the platform, the Excel, and the report.
    _norm_dcf = scn.normalise(
        scenarios if (scenarios and scenarios.get("kind") == "dcf") else company.get("scenarios"),
        fins, country=universe.get("country"), is_bank=False,
    )
    _norm_bank = scn.normalise(
        scenarios if (scenarios and scenarios.get("kind") == "bank") else company.get("scenarios"),
        fins, country=universe.get("country"), is_bank=True,
        bank=company.get("bank") if isinstance(company.get("bank"), dict) else None,
    ) if is_bank else None

    # Python-computed base DCF value — written to Cover as a reconciliation reference.
    # If the Excel formula-computed Valuation!B15 differs materially, something is wrong.
    _base_assumptions = (_norm_bank["sets"][_norm_bank["selected"]] if is_bank
                         else _norm_dcf["sets"][_norm_dcf["selected"]])
    _py_value = (bank_value_per_share(fins, market, _base_assumptions) if is_bank
                 else value_per_share(fins, market, _base_assumptions))

    # --- Sector config for IS structure ---
    _scfg = _sector_config(universe.get("sector", ""), universe.get("sub_sector"), is_bank=is_bank)

    hist_years = [f["fy"] for f in fins]
    n = len(hist_years)
    # Sector-aware forecast uses the same base assumption set
    fcast = fc.sector_project(
        fins, _scfg.code,
        assumptions=_base_assumptions,
        bank=company.get("bank") if isinstance(company.get("bank"), dict) else None,
        sector_kpis=company.get("sector_kpis"),
        years=5,
    ) if fins else []
    fcast_years = [r["fy"] for r in fcast]
    all_years = hist_years + fcast_years
    ny = len(all_years)
    dq = ctrl.data_quality_score(company)
    controls = ctrl.run_controls(company)
    model_id = f"{tkr}-{model_version}"
    valuation_date = valuation_date or market.get("price_date") or "2026-07-02"
    C0 = 3                       # first year column (C)
    LC = get_column_letter(C0 + n - 1)   # last historical column letter
    span = max(C0 + ny, 8)

    wb = Workbook()
    wb.remove(wb.active)

    # ==================================================================
    # 1) COVER  (+ how-to-read guide folded in)
    # ==================================================================
    cv = _sheet(wb, "Cover")
    cv.column_dimensions["A"].width = 30
    cv.column_dimensions["B"].width = 62
    _title(cv, f"  AEON NIMBUS — {name} ({tkr})", 6)
    meta = [
        ("Company", name), ("Ticker", tkr), ("Exchange", universe.get("exchange")),
        ("Country", universe.get("country")), ("Sector", universe.get("sector")),
        ("Sub-sector", universe.get("sub_sector", "—")), ("Reporting", f"{cur} {unit}, {universe.get('accounting_standard', 'IFRS')}"),
        ("Fiscal year-end", universe.get("fiscal_year_end", "December")),
        ("Model ID / version", f"{model_id}"), ("Generation date", date.today().isoformat()),
        ("Valuation date", valuation_date), ("Data-quality score", f"{dq['score']} / 100  (band {dq['band']})"),
        ("Export status", "ALLOWED" if dq["export_allowed"] else "BLOCKED — critical control"),
        ("Sector guide", f"{_scfg.code} — {_scfg.label} ({_scfg.valuation_kind.upper()})"),
        ("Active scenario", _norm_bank.get("selected", "Base") if is_bank else _norm_dcf.get("selected", "Base")),
        ("Python model value/share", round(_py_value, 2) if _py_value else "n/a"),
        ("Reconciliation", "Value/share above must match Valuation!B15 ±2% — discrepancy = stale scenario."),
    ]
    for i, (k, v) in enumerate(meta, start=3):
        _kv(cv, i, k, v)
    r = 3 + len(meta) + 1
    _section(cv, r, "  HOW TO READ THIS MODEL", 6); r += 2
    cv.cell(row=r, column=1, value="Colour legend").font = _LBL
    cv.cell(row=r, column=2, value="Blue = input you can edit / a reported actual    ·    Black = calculated formula    ·    Green = pulled from another sheet").font = _BODY
    r += 2
    cv.cell(row=r, column=1, value="Calculation flow").font = _LBL; r += 1
    for step in ["1  Assumptions — editable Bull/Base/Bear drivers; the selector (B3) picks the Live column via CHOOSE.",
                 "2  Financials — reported 5-yr actuals (blue) + the forecast build (P&L, margins, FCFF, balance sheet).",
                 "3  Valuation — discount FCFF at WACC, add terminal value, − net debt, ÷ shares = value per share; scenario football field below.",
                 "4  Comps & KPIs · Overview · Sources & Controls — peers, operating metrics, narrative, and the audit trail."]:
        cv.cell(row=r, column=2, value=step).font = _BODY; r += 1
    r += 1
    cv.cell(row=r, column=1, value="The DCF in one line").font = _LBL
    cv.cell(row=r, column=2, value="Revenue → ×margin → EBITDA → −D&A → EBIT → −tax → NOPAT → +D&A −capex −ΔWC → FCFF → ÷(1+WACC)^t → +terminal value → EV → −net debt → ÷shares = value per share.").font = _BODY

    # ==================================================================
    # 2) ASSUMPTIONS & SCENARIOS
    # Uses the normalised scenario set (_norm_dcf / _norm_bank) already built
    # above — the same set the platform dashboard and studio use.
    # ==================================================================
    dcf_scen = _norm_dcf
    asm = _sheet(wb, "Assumptions", TEAL)
    for col, w in zip("ABCDE", (34, 14, 12, 12, 12)):
        asm.column_dimensions[col].width = w
    _title(asm, "  ASSUMPTIONS & SCENARIOS — flip the selector to switch the model", 6)
    _fill(asm, 2, 6, GREEN)
    asm.cell(row=3, column=1, value="Active scenario").font = _LBL
    sel = asm.cell(row=3, column=2, value=dcf_scen["order"].index(dcf_scen["selected"]) + 1)
    sel.fill = PatternFill("solid", fgColor=YELLOW); sel.font = _INPUT_B; sel.alignment = _CENTER
    dv = DataValidation(type="list", formula1='"1,2,3"', allow_blank=False)
    asm.add_data_validation(dv); dv.add(sel)
    _name(wb, "Scenario", "Assumptions", "B3")
    asm.cell(row=3, column=3, value='=CHOOSE(B3,"Bull","Base","Bear")').font = Font(bold=True, italic=True, color="FF0C7A6B")
    asm.cell(row=3, column=5, value="1=Bull 2=Base 3=Bear").font = _SRC
    for j, h in enumerate(["Driver", "Live", "Bull", "Base", "Bear"]):
        asm.cell(row=5, column=1 + j, value=h).font = _LBL
    asm.cell(row=5, column=6, value="Rationale / basis").font = _LBL
    asm.column_dimensions["F"].width = 62
    _RATIONALE = {
        "Revenue growth": "Historical revenue CAGR (clamped to a sane band); tie to the demand drivers in the report.",
        "EBITDA margin": "Held near the latest reported margin; review cost pressures (Ethiopia, FX, energy, excise).",
        "D&A % of sales": "Latest depreciation & amortisation as a % of sales — reflects capex / network intensity.",
        "Capex % of sales": "Latest capex as a % of sales; separate growth vs maintenance where the filing allows.",
        "Working capital % of Δrev": "Working-capital investment per unit of revenue growth (telecoms often run negative WC).",
        "Tax rate": "Statutory / effective rate from the reported accounts (see effective tax rate on Financials).",
        "WACC": "CAPM in local currency: risk-free + β × equity-risk-premium + country risk + FX premium.",
        "Terminal growth": "Long-run nominal growth proxy (≈ GDP + inflation for the country); kept below WACC.",
        "Exit EV/EBITDA": "Cross-check terminal value against a peer exit multiple (memo, not the headline TV).",
    }
    _fmt = {"pct": PCT, "mult": MULT, "num": "0.00"}
    rr = 6
    for key, label, kind in scn.DCF_KEYS:
        f = _fmt.get(kind, MONEY)
        asm.cell(row=rr, column=1, value=label).font = _BODY
        for jj, nm in enumerate(dcf_scen["order"]):
            c = asm.cell(row=rr, column=3 + jj, value=dcf_scen["sets"][nm].get(key)); c.number_format = f; c.font = _INPUT
        live = asm.cell(row=rr, column=2, value=f"=CHOOSE(Scenario,C{rr},D{rr},E{rr})"); live.number_format = f; live.font = _FORMULA
        asm.cell(row=rr, column=6, value=_RATIONALE.get(label, "")).font = _NOTE
        if key in scn.DCF_NAMED:
            _name(wb, scn.DCF_NAMED[key], "Assumptions", f"B{rr}")
        rr += 1
    rr += 1
    asm.cell(row=rr, column=1, value="Shares outstanding (m)").font = _LBL
    asm.cell(row=rr, column=2, value=market.get("shares_outstanding_m")).font = _INPUT
    _name(wb, "Shares_Out", "Assumptions", f"B{rr}"); rr += 1
    asm.cell(row=rr, column=1, value="Current share price").font = _LBL
    cpc = asm.cell(row=rr, column=2, value=market.get("share_price")); cpc.number_format = MONEY; cpc.font = _INPUT
    _name(wb, "Current_Price", "Assumptions", f"B{rr}"); rr += 1
    if is_bank:
        bank_scen = _norm_bank
        rr += 1
        _section(asm, rr, "  BANK DRIVERS — scenario-switched (feed the bank valuation)", 5); rr += 1
        for j, h in enumerate(["Driver", "Live", "Bull", "Base", "Bear"]):
            asm.cell(row=rr, column=1 + j, value=h).font = _LBL
        rr += 1
        for key, label, kind in scn.BANK_KEYS:
            f = _fmt.get(kind, MONEY)
            asm.cell(row=rr, column=1, value=label).font = _BODY
            for jj, nm in enumerate(bank_scen["order"]):
                c = asm.cell(row=rr, column=3 + jj, value=bank_scen["sets"][nm].get(key)); c.number_format = f; c.font = _INPUT
            live = asm.cell(row=rr, column=2, value=f"=CHOOSE(Scenario,C{rr},D{rr},E{rr})"); live.number_format = f; live.font = _FORMULA
            _name(wb, scn.BANK_NAMED[key], "Assumptions", f"B{rr}")
            rr += 1
    asm.cell(row=rr + 1, column=1, value="Blue = editable input · Black = formula · Live = CHOOSE(selector).").font = _SRC

    # ==================================================================
    # 3) FINANCIALS  (merged: reported actuals + forecast, IS / FCFF / BS / CF)
    # ==================================================================
    fin = _sheet(wb, "Financials")
    fin.column_dimensions["A"].width = 32
    _title(fin, f"  {name} ({tkr}) — FINANCIALS   ({cur} {unit})", span)
    _years_band(fin, all_years, C0, span, right_label=f"  {cur} {unit}")

    # row map — full income-statement bridge, FCFF build, balance sheet, cash flow
    REV, DC, ECL, OOE, EBITDA, EBM, DA, EBIT = 4, 5, 6, 7, 8, 9, 10, 11
    NFIN, ASSOC, HYP, PBT, TAX, ETR, NI, NM = 12, 13, 14, 15, 16, 17, 18, 19
    NOPAT, ADDDA, CAPEXf, DWC, FCFF = 22, 23, 24, 25, 26
    # balance sheet — detail (from the audited statements block) + model totals
    PPE, INTA, ROU, ONCA, TNCA = 29, 30, 31, 32, 33
    INV, RECV, CASH, OCA, TCA = 34, 35, 36, 37, 38
    TA = 39
    SCP, RES, TE = 40, 41, 42
    TD, OTHL, TL = 43, 44, 45
    NWC, ND, NDE = 46, 47, 48
    OCF, CAPEXh, FCFh, DIV = 51, 52, 53, 54

    def hist(row, key, fmt=MONEY):
        for j, ff in enumerate(fins):
            v = ff.get(key)
            if v is not None:
                c = fin.cell(row=row, column=C0 + j, value=v); c.number_format = fmt; c.font = _INPUT

    _stb = company.get("statements") or {}
    _stcols = _stb.get("columns", [])
    _bsmap = {r.get("label", "").strip(): (r.get("values") or []) for r in _stb.get("balance_sheet", [])}
    _finyrs = [f.get("fy") for f in fins]

    def bs_hist(row, *labels):
        """Pull balance-sheet detail from the audited statements block (summing the
        given labels), mapping newest-first statement columns to this tab's actual
        columns. Populates only for years the statements block covers (else blank —
        companies without a statements block just show the model totals)."""
        for j, fy in enumerate(_finyrs):
            if fy not in _stcols:
                continue
            k = _stcols.index(fy)
            tot = None
            for lab in labels:
                vs = _bsmap.get(lab, [])
                v = vs[k] if k < len(vs) else None
                if isinstance(v, (int, float)):
                    tot = (tot or 0) + v
            if tot is not None:
                c = fin.cell(row=row, column=C0 + j, value=round(tot, 1)); c.number_format = MONEY; c.font = _INPUT

    def fore(row, formula, fmt=MONEY):
        for j in range(5):
            col = get_column_letter(C0 + n + j)
            prev = get_column_letter(C0 + n + j - 1)
            c = fin.cell(row=row, column=C0 + n + j, value=formula(col, prev)); c.number_format = fmt; c.font = _FORMULA

    def ratio(row, num, den, fmt=PCT):
        for j in range(ny):
            col = get_column_letter(C0 + j)
            fin.cell(row=row, column=C0 + j, value=f'=IF({col}{den}=0,"",{col}{num}/{col}{den})').number_format = fmt

    def label(row, txt, bold=False, note=None, italic=False):
        c = fin.cell(row=row, column=1, value=("   " + txt) if italic else txt)
        c.font = _LBL if bold else (_NOTE if italic else _BODY)
        if note:
            fin.cell(row=row, column=C0 + ny + 1, value=note).font = _NOTE

    def calc(row, cols_iter, fx):
        for j in cols_iter:
            col = get_column_letter(C0 + j); prev = get_column_letter(C0 + j - 1)
            c = fin.cell(row=row, column=C0 + j, value=fx(col, prev)); c.number_format = MONEY; c.font = _FORMULA

    _section(fin, 3, "  Income statement" + (" — bank (NII / provisions structure)" if is_bank else ""), span)
    label(REV, "Revenue" if not is_bank else "Total income (NII + non-interest income)", bold=True,
          note="actual (blue) | forecast: prior × (1 + revenue growth)")
    hist(REV, "revenue"); fore(REV, lambda c, p: f"={p}{REV}*(1+Rev_Growth)")
    # Bank-specific income lines (reported actuals only; forecast uses Bank_NIM × loan book)
    if is_bank:
        b_data = company.get("bank") or {}
        nii_val = b_data.get("net_interest_income")
        label(DC, "Net interest income (NII)", italic=True, note="reported; forecast: loan book × NIM")
        for j, ff in enumerate(fins):
            v = nii_val if j == len(fins) - 1 else None
            if isinstance(v, (int, float)):
                c = fin.cell(row=DC, column=C0 + j, value=v); c.number_format = MONEY; c.font = _INPUT
        fore(DC, lambda c, p: f"={p}{DC}*(1+Bank_LoanGrowth)*Bank_NIM/Bank_NIM")  # grows with loan_growth
    else:
        label(DC, "Direct costs", italic=True, note="reported"); hist(DC, "direct_costs")
    label(ECL, "Expected credit losses / Provisions", italic=True,
          note="reported; forecast (bank): loan book × cost of risk"); hist(ECL, "ecl")
    label(OOE, "Other operating expenses", italic=True, note="reported"); hist(OOE, "other_opex")
    label(EBITDA, "EBITDA", bold=True, note="actual: revenue − costs (reported) | forecast: revenue × EBITDA margin")
    hist(EBITDA, "ebitda"); fore(EBITDA, lambda c, p: f"={c}{REV}*EBITDA_Margin")
    label(EBM, "EBITDA margin", italic=True, note="EBITDA ÷ revenue"); ratio(EBM, EBITDA, REV)
    label(DA, "Depreciation & amortisation", note="actual: EBIT − EBITDA | forecast: − revenue × D&A%")
    calc(DA, range(n), lambda c, p: f"={c}{EBIT}-{c}{EBITDA}")
    fore(DA, lambda c, p: f"=-{c}{REV}*DA_Pct")
    label(EBIT, "EBIT", bold=True, note="EBITDA + D&A"); hist(EBIT, "ebit"); fore(EBIT, lambda c, p: f"={c}{EBITDA}+{c}{DA}")
    label(NFIN, "Net finance income/(costs)", note="actual reported | forecast: − prior net debt × cost of debt (11%)")
    hist(NFIN, "net_finance"); fore(NFIN, lambda c, p: f"=-MAX(0,{p}{ND})*0.11")
    label(ASSOC, "Associates, JV & fair-value items", italic=True, note="reported; nil in forecast"); hist(ASSOC, "assoc_jv")
    label(HYP, "Hyperinflationary monetary gain", italic=True, note="reported (IAS 29); nil in forecast"); hist(HYP, "hyperinflation")
    label(PBT, "Profit before tax", bold=True, note="actual reported | forecast: EBIT + net finance")
    hist(PBT, "pbt"); fore(PBT, lambda c, p: f"={c}{EBIT}+{c}{NFIN}")
    label(TAX, "Tax", note="actual reported | forecast: − max(PBT,0) × tax rate")
    hist(TAX, "tax"); fore(TAX, lambda c, p: f"=-MAX(0,{c}{PBT})*Tax_Rate")
    label(ETR, "Effective tax rate", italic=True, note="− tax ÷ PBT")
    for j in range(ny):
        col = get_column_letter(C0 + j)
        fin.cell(row=ETR, column=C0 + j, value=f'=IF({col}{PBT}=0,"",-{col}{TAX}/{col}{PBT})').number_format = PCT
    label(NI, "Net income", bold=True, note="PBT + tax"); hist(NI, "net_income"); fore(NI, lambda c, p: f"={c}{PBT}+{c}{TAX}")
    label(NM, "Net margin", italic=True, note="net income ÷ revenue"); ratio(NM, NI, REV)

    _section(fin, 21, "  Free cash flow to firm (FCFF) — unlevered", span)
    label(NOPAT, "NOPAT (EBIT after tax)", note="actual: EBIT × (1 − effective tax) | forecast: EBIT × (1 − tax rate)")
    calc(NOPAT, range(n), lambda c, p: f"=IF({c}{PBT}=0,{c}{EBIT}*(1-Tax_Rate),{c}{EBIT}*(1+{c}{TAX}/{c}{PBT}))")
    fore(NOPAT, lambda c, p: f"={c}{EBIT}-MAX(0,{c}{EBIT})*Tax_Rate")
    label(ADDDA, "add: D&A", note="add back non-cash D&A"); calc(ADDDA, range(ny), lambda c, p: f"=-{c}{DA}")
    label(CAPEXf, "less: Capex", note="actual: reported capex | forecast: − revenue × capex%")
    calc(CAPEXf, range(n), lambda c, p: f"=-{c}{CAPEXh}"); fore(CAPEXf, lambda c, p: f"=-{c}{REV}*Capex_Pct")
    label(DWC, "less: Δ working capital", note="actual: −(NWC − prior NWC) | forecast: −(ΔRevenue × WC%)")
    calc(DWC, range(1, n), lambda c, p: f"=-({c}{NWC}-{p}{NWC})"); fore(DWC, lambda c, p: f"=-({c}{REV}-{p}{REV})*WC_Pct")
    label(FCFF, "FCFF", bold=True, note="NOPAT + D&A − capex − ΔWC")
    calc(FCFF, range(ny), lambda c, p: f"={c}{NOPAT}+{c}{ADDDA}+{c}{CAPEXf}+{c}{DWC}")

    _section(fin, 28, "  Balance sheet", span)
    label(PPE, "Property & equipment", italic=True); bs_hist(PPE, "Property and equipment")
    label(INTA, "Intangible assets", italic=True); bs_hist(INTA, "Intangible assets")
    label(ROU, "Right-of-use assets", italic=True); bs_hist(ROU, "Right-of-use assets")
    label(ONCA, "Other non-current assets", italic=True, note="total non-current − PP&E − intangibles − RoU")
    calc(ONCA, range(n), lambda c, p: f'=IF({c}{TNCA}="","",{c}{TNCA}-{c}{PPE}-{c}{INTA}-{c}{ROU})')
    label(TNCA, "Total non-current assets", bold=True); bs_hist(TNCA, "Total non-current assets")
    label(INV, "Inventories", italic=True); bs_hist(INV, "Inventories")
    label(RECV, "Trade & other receivables", italic=True); bs_hist(RECV, "Trade and other receivables")
    label(CASH, "Cash & equivalents"); hist(CASH, "cash")
    label(OCA, "Other current assets", italic=True, note="total current − inventories − receivables − cash")
    calc(OCA, range(n), lambda c, p: f'=IF({c}{TCA}="","",{c}{TCA}-{c}{INV}-{c}{RECV}-{c}{CASH})')
    label(TCA, "Total current assets", bold=True); bs_hist(TCA, "Total current assets")
    label(TA, "Total assets", bold=True); hist(TA, "total_assets")
    label(SCP, "Share capital & premium", italic=True); bs_hist(SCP, "Share capital", "Share premium")
    label(RES, "Retained earnings & reserves", italic=True); bs_hist(RES, "Retained earnings", "Other reserves")
    label(TE, "Total equity", bold=True, note="actual reported | forecast: prior equity + net income"); hist(TE, "total_equity"); fore(TE, lambda c, p: f"={p}{TE}+{c}{NI}")
    label(TD, "Total debt (borrowings + leases)"); hist(TD, "total_debt")
    label(OTHL, "Other liabilities", italic=True, note="total liabilities − debt (payables, provisions, tax, contract liabilities)")
    calc(OTHL, range(n), lambda c, p: f'=IF({c}{TL}="","",{c}{TL}-{c}{TD})')
    label(TL, "Total liabilities", bold=True); bs_hist(TL, "Total liabilities")
    label(NWC, "Net working capital", italic=True, note="receivables + inventory − current payables"); hist(NWC, "nwc")
    label(ND, "Net debt", bold=True, note="actual reported | forecast: prior net debt − FCFF"); hist(ND, "net_debt"); fore(ND, lambda c, p: f"={p}{ND}-{c}{FCFF}")
    label(NDE, "Net debt / EBITDA", italic=True, note="leverage")
    for j in range(ny):
        col = get_column_letter(C0 + j)
        fin.cell(row=NDE, column=C0 + j, value=f'=IF({col}{EBITDA}=0,"",{col}{ND}/{col}{EBITDA})').number_format = MULT

    _section(fin, 50, "  Cash flow (reported)", span)
    label(OCF, "Operating cash flow"); hist(OCF, "operating_cash_flow")
    label(CAPEXh, "Capex"); hist(CAPEXh, "capex")
    label(FCFh, "Free cash flow (levered)", note="operating cash flow − capex"); hist(FCFh, "free_cash_flow")
    label(DIV, "Dividends paid"); hist(DIV, "dividends_paid")
    fin.cell(row=56, column=1, value="Actuals reported/source-linked. Balance-sheet detail (italic) is pulled from the audited Financial_Statements tab; 'Other' lines are the reported subtotal less the itemised lines (so each subtotal foots). Companies without a full statements block show the model totals only. The forecast models the FCFF/DCF drivers, not a full 3-statement balance sheet.").font = _SRC
    fin.cell(row=2, column=C0 + ny + 1, value="How it's calculated").font = _SRC
    fin.column_dimensions[get_column_letter(C0 + ny + 1)].width = 46

    # charts on Financials — revenue/EBITDA, margins, FCFF & net debt
    cats = Reference(fin, min_col=C0, min_row=2, max_col=C0 + ny - 1, max_row=2)
    ch1 = BarChart(); ch1.type = "col"; ch1.title = "Revenue & EBITDA"; ch1.height, ch1.width = 7.5, 15
    ch1.add_data(Reference(fin, min_col=C0, min_row=REV, max_col=C0 + ny - 1, max_row=REV), from_rows=True)
    ch1.add_data(Reference(fin, min_col=C0, min_row=EBITDA, max_col=C0 + ny - 1, max_row=EBITDA), from_rows=True)
    ch1.set_categories(cats); _legend(ch1, "Financials", [REV, EBITDA]); fin.add_chart(ch1, "A58")
    ch2 = LineChart(); ch2.title = "Margins (EBITDA & net)"; ch2.height, ch2.width = 7.5, 15
    ch2.add_data(Reference(fin, min_col=C0, min_row=EBM, max_col=C0 + ny - 1, max_row=EBM), from_rows=True)
    ch2.add_data(Reference(fin, min_col=C0, min_row=NM, max_col=C0 + ny - 1, max_row=NM), from_rows=True)
    ch2.set_categories(cats); _legend(ch2, "Financials", [EBM, NM]); fin.add_chart(ch2, "J58")
    ch3 = BarChart(); ch3.type = "col"; ch3.title = "FCFF & net debt"; ch3.height, ch3.width = 7.5, 15
    ch3.add_data(Reference(fin, min_col=C0, min_row=FCFF, max_col=C0 + ny - 1, max_row=FCFF), from_rows=True)
    ch3.add_data(Reference(fin, min_col=C0, min_row=ND, max_col=C0 + ny - 1, max_row=ND), from_rows=True)
    ch3.set_categories(cats); _legend(ch3, "Financials", [FCFF, ND]); fin.add_chart(ch3, "A76")

    # Full reported statements (line-item detail, as filed) when the dataset has them
    _statements_tab(wb, company, universe)

    # ==================================================================
    # 4) VALUATION  (DCF + scenario summary + football field, or bank P/B)
    # ==================================================================
    vl = _sheet(wb, "Valuation", GOLD)
    vl.column_dimensions["A"].width = 34
    _title(vl, f"  {name} ({tkr}) — VALUATION", span)
    _fill(vl, 2, span, GREEN)
    fcf_cols = [get_column_letter(C0 + n + j) for j in range(5)]
    vl.cell(row=3, column=1, value="Forecast year").font = _LBL
    for j, y in enumerate(fcast_years):
        vl.cell(row=3, column=2 + j, value=y).font = _LBL
    vl.cell(row=4, column=1, value="FCFF").font = _LBL
    for j in range(5):
        c = vl.cell(row=4, column=2 + j, value=f"=Financials!{fcf_cols[j]}{FCFF}"); c.number_format = MONEY; c.font = _LINK
    vl.cell(row=5, column=1, value="Discount factor").font = _LBL
    for j in range(5):
        vl.cell(row=5, column=2 + j, value=f"=1/(1+WACC)^{j + 1}").number_format = "0.000"
    vl.cell(row=6, column=1, value="PV of FCFF").font = _LBL
    for j in range(5):
        cc = get_column_letter(2 + j)
        vl.cell(row=6, column=2 + j, value=f"={cc}4*{cc}5").number_format = MONEY
    r = 8
    steps = [
        ("Sum PV of FCFF", "=SUM(B6:F6)", MONEY, "sum of the five PV of FCFF"),
        ("Terminal value — perpetuity (Gordon)", "=F4*(1+Terminal_Growth_Rate)/(WACC-Terminal_Growth_Rate)", MONEY, "final FCFF × (1+g) ÷ (WACC − g)"),
        ("Terminal value — exit multiple (memo)", f"=Exit_Multiple*Financials!{fcf_cols[4]}{EBITDA}", MONEY, "exit multiple × final-year EBITDA (memo)"),
        ("PV of terminal value", "=B9/(1+WACC)^5", MONEY, "perpetuity TV ÷ (1 + WACC)^5"),
        ("Enterprise value", "=B8+B11", MONEY, "sum PV of FCFF + PV of terminal value"),
        ("Less: net debt", f"=-Financials!{LC}{ND}", MONEY, "− latest reported net debt"),
        ("Equity value", "=B12+B13", MONEY, "enterprise value − net debt"),
        ("Value per share", "=B14/Shares_Out", MONEY, "equity value ÷ shares outstanding"),
        ("Current price", "=Current_Price", MONEY, "current price (Assumptions)"),
        ("Upside / (downside)", "=B15/Current_Price-1", PCT, "value per share ÷ price − 1"),
    ]
    for k, (lab, formula, fmt, note) in enumerate(steps):
        rown = r + k
        vl.cell(row=rown, column=1, value=lab).font = _LBL
        cc = vl.cell(row=rown, column=2, value=formula); cc.number_format = fmt
        cc.font = _LINK if formula.startswith("=Financials") or formula.startswith("=-Financials") else _FORMULA
        vl.cell(row=rown, column=4, value=note).font = _NOTE
    _name(wb, "Value_Per_Share", "Valuation", "B15")
    vl.column_dimensions["D"].width = 46
    vl.cell(row=r + len(steps) + 1, column=1,
            value=("Banks: DCF is a reference only — the headline is the justified-P/B valuation below." if is_bank
                   else "Headline uses a Gordon-growth perpetuity terminal (matches the platform DCF); the exit-multiple TV is a memo cross-check.")).font = _SRC

    # scenario football field (DCF for all three) below the DCF
    ss0 = 22
    _section(vl, ss0 - 1, "  Scenario summary — value per share by scenario", span)
    vl.cell(row=ss0, column=1, value="Driver / output").font = _LBL
    for j, nm in enumerate(["Bull", "Base", "Bear"]):
        vl.cell(row=ss0, column=2 + j, value=nm).font = _LBL
    ci = {"B": 2, "C": 3, "D": 4}
    srccol = {"B": "C", "C": "D", "D": "E"}
    rev0 = f"Financials!{LC}{REV}"
    ndref = f"Financials!{LC}{ND}"
    base = ss0 + 1
    lab_map = {base: "Prior-year revenue", base + 1: "Revenue growth", base + 2: "EBITDA margin", base + 3: "D&A % sales",
               base + 4: "Capex % sales", base + 5: "WC % Δrev", base + 6: "Tax rate", base + 7: "WACC", base + 8: "Terminal growth",
               base + 9: "Revenue Y1–Y5", base + 15: "FCFF Y1–Y5", base + 21: "PV of FCFF Y1–Y5",
               base + 27: "Sum PV of FCFF", base + 28: "Terminal value", base + 29: "PV of terminal value",
               base + 30: "Enterprise value", base + 31: "Less: net debt", base + 32: "Equity value",
               base + 33: "Value per share", base + 34: "Upside / (downside)"}
    for rw, lab in lab_map.items():
        vl.cell(row=rw, column=1, value=lab).font = _LBL if rw in (base + 27, base + 30, base + 32, base + 33, base + 34) else _BODY
    for blk in (base + 10, base + 16, base + 22):
        for k in range(5):
            vl.cell(row=blk + k, column=1, value=f"    Y{k + 1}").font = Font(color="FF5C6B72", size=10)
    for col in "BCD":
        a = srccol[col]
        vl.cell(row=base, column=ci[col], value=f"={rev0}").number_format = MONEY
        for off, arow in enumerate((6, 7, 8, 9, 10, 11, 12, 13), start=1):
            vl.cell(row=base + off, column=ci[col], value=f"=Assumptions!{a}{arow}").number_format = (MULT if arow == 14 else PCT)
        rev_rows = [base + 10 + k for k in range(5)]
        prev_rows = [base] + rev_rows[:-1]
        gcol = f"{col}{base + 1}"  # revenue growth cell
        for k in range(5):
            vl.cell(row=rev_rows[k], column=ci[col], value=f"={col}{prev_rows[k]}*(1+{gcol})").number_format = MONEY
        fcff_rows = [base + 16 + k for k in range(5)]
        for k in range(5):
            rvc = f"{col}{rev_rows[k]}"
            ebit = f"{rvc}*({col}{base + 2}-{col}{base + 3})"
            vl.cell(row=fcff_rows[k], column=ci[col],
                    value=f"=({ebit})-MAX(0,{ebit})*{col}{base + 6}+{rvc}*{col}{base + 3}-{rvc}*{col}{base + 4}-({rvc}-{col}{prev_rows[k]})*{col}{base + 5}").number_format = MONEY
        pv_rows = [base + 22 + k for k in range(5)]
        for k in range(5):
            vl.cell(row=pv_rows[k], column=ci[col], value=f"={col}{fcff_rows[k]}/(1+{col}{base + 7})^{k + 1}").number_format = MONEY
        wcol, tcol = f"{col}{base + 7}", f"{col}{base + 8}"
        vl.cell(row=base + 27, column=ci[col], value=f"=SUM({col}{pv_rows[0]}:{col}{pv_rows[4]})").number_format = MONEY
        vl.cell(row=base + 28, column=ci[col], value=f"={col}{fcff_rows[4]}*(1+{tcol})/({wcol}-{tcol})").number_format = MONEY
        vl.cell(row=base + 29, column=ci[col], value=f"={col}{base + 28}/(1+{wcol})^5").number_format = MONEY
        vl.cell(row=base + 30, column=ci[col], value=f"={col}{base + 27}+{col}{base + 29}").number_format = MONEY
        vl.cell(row=base + 31, column=ci[col], value=f"=-{ndref}").number_format = MONEY
        vl.cell(row=base + 32, column=ci[col], value=f"={col}{base + 30}+{col}{base + 31}").number_format = MONEY
        vc = vl.cell(row=base + 33, column=ci[col], value=f"={col}{base + 32}/Shares_Out"); vc.number_format = MONEY; vc.font = Font(bold=True)
        vl.cell(row=base + 34, column=ci[col], value=f"={col}{base + 33}/Current_Price-1").number_format = PCT
    ff = BarChart(); ff.type = "col"; ff.title = "Value per share by scenario"; ff.height, ff.width = 7, 12
    ff.add_data(Reference(vl, min_col=2, min_row=base + 33, max_col=4, max_row=base + 33), from_rows=True)
    ff.set_categories(Reference(vl, min_col=2, min_row=ss0, max_col=4, max_row=ss0))
    vl.add_chart(ff, "F22")

    if is_bank:
        _bank_valuation(wb, vl, base + 37, company, fins, market, span)

    # ==================================================================
    # 5) COMPS & KPIs
    # ==================================================================
    cp = _sheet(wb, "Comps_and_KPIs", TEAL)
    for col, w in zip("ABCDEFG", (34, 14, 12, 12, 12, 14, 12)):
        cp.column_dimensions[col].width = w
    _title(cp, f"  {name} ({tkr}) — COMPARABLES & OPERATING KPIs", span)
    _fill(cp, 2, span, GREEN)
    mc = "Shares_Out*Current_Price"
    evx = f"({mc}+Financials!{LC}{ND})"

    # (a) the subject's own trading multiples — computed live from the model
    _section(cp, 3, f"  Trading multiples — {name} (computed live from the model)", span)
    trow = [
        ("Market cap", f"={mc}", MONEY), ("Enterprise value (EV)", f"={evx}", MONEY),
        ("EV / EBITDA", f'=IF(Financials!{LC}{EBITDA}=0,"",{evx}/Financials!{LC}{EBITDA})', MULT),
        ("P / E", f'=IF(Financials!{LC}{NI}>0,{mc}/Financials!{LC}{NI},"")', MULT),
        ("P / B", f'=IF(Financials!{LC}{TE}=0,"",{mc}/Financials!{LC}{TE})', MULT),
        ("FCF yield", f'=IF(({mc})=0,"",Financials!{LC}{FCFF}/({mc}))', PCT),
        ("Dividend yield", f'=IF(({mc})=0,"",ABS(Financials!{LC}{DIV})/({mc}))', PCT),
        ("ROE", f'=IF(Financials!{LC}{TE}=0,"",Financials!{LC}{NI}/Financials!{LC}{TE})', PCT),
        ("EBITDA margin", f'=IF(Financials!{LC}{REV}=0,"",Financials!{LC}{EBITDA}/Financials!{LC}{REV})', PCT),
        ("Net margin", f'=IF(Financials!{LC}{REV}=0,"",Financials!{LC}{NI}/Financials!{LC}{REV})', PCT),
    ]
    r = 4
    for lab, formula, fmt in trow:
        cp.cell(row=r, column=1, value=lab).font = _LBL
        c = cp.cell(row=r, column=2, value=formula); c.number_format = fmt; c.font = _LINK
        r += 1
    r += 1

    # (b) comparable-companies table — subject (live) + peers (real multiples) + median
    _section(cp, r, "  Comparable companies", span); r += 1
    for j, h in enumerate(["Company", "EV/EBITDA", "P/E", "P/B", "ROE", "EBITDA margin", "Div yield"]):
        cp.cell(row=r, column=1 + j, value=h).font = _LBL
    r += 1
    cp.cell(row=r, column=1, value=f"{name} (this model)").font = Font(bold=True, color="FF123142")
    for j, formula, fmt in [
        (2, f'=IF(Financials!{LC}{EBITDA}=0,"",{evx}/Financials!{LC}{EBITDA})', MULT),
        (3, f'=IF(Financials!{LC}{NI}>0,{mc}/Financials!{LC}{NI},"")', MULT),
        (4, f'=IF(Financials!{LC}{TE}=0,"",{mc}/Financials!{LC}{TE})', MULT),
        (5, f'=IF(Financials!{LC}{TE}=0,"",Financials!{LC}{NI}/Financials!{LC}{TE})', PCT),
        (6, f'=IF(Financials!{LC}{REV}=0,"",Financials!{LC}{EBITDA}/Financials!{LC}{REV})', PCT),
        (7, f'=IF(({mc})=0,"",ABS(Financials!{LC}{DIV})/({mc}))', PCT)]:
        cc = cp.cell(row=r, column=j, value=formula); cc.number_format = fmt; cc.font = _LINK
    r += 1
    peer_start = r
    peer_list = comps if comps is not None else [{"name": (p.get("name") if isinstance(p, dict) else p)}
                                                 for p in universe.get("peers", [])]
    any_mult = False
    for p in peer_list:
        cp.cell(row=r, column=1, value=p.get("name"))
        for j, key, fmt in [(2, "ev_ebitda", MULT), (3, "pe", MULT), (4, "pb", MULT),
                            (5, "roe", PCT), (6, "ebitda_margin", PCT), (7, "div_yield", PCT)]:
            v = p.get(key)
            if isinstance(v, (int, float)):
                any_mult = True
                cc = cp.cell(row=r, column=j, value=v); cc.number_format = fmt; cc.font = _INPUT
        r += 1
    peer_end = r - 1
    if any_mult:
        cp.cell(row=r, column=1, value="Peer median").font = Font(italic=True, bold=True, color="FF5C6B72")
        median_row = r
        for j in range(2, 8):
            cl = get_column_letter(j)
            fmt = MULT if j <= 4 else PCT
            cp.cell(row=r, column=j, value=f'=IFERROR(MEDIAN({cl}{peer_start}:{cl}{peer_end}),"")').number_format = fmt
        r += 2
        # comparable valuation — implied value per share (formula-driven, H500 method set)
        _section(cp, r, "  Comparable valuation — implied value per share", span); r += 1
        cv_start = r
        for lab, formula, note in [
            ("EV/EBITDA (peer median)",
             f'=IFERROR((B{median_row}*Financials!{LC}{EBITDA}-Financials!{LC}{ND})/Shares_Out,"")',
             "median EV/EBITDA × latest EBITDA − net debt, ÷ shares"),
            ("P/E (peer median)",
             f'=IFERROR(IF(Financials!{LC}{NI}>0,(C{median_row}*Financials!{LC}{NI})/Shares_Out,""),"")',
             "median P/E × latest net income ÷ shares"),
            ("DCF (base) — memo", "=Value_Per_Share", "headline DCF value (Valuation tab)"),
            ("Current price", "=Current_Price", "market price (Assumptions)"),
        ]:
            cp.cell(row=r, column=1, value=lab).font = _LBL
            cc = cp.cell(row=r, column=2, value=formula); cc.number_format = MONEY; cc.font = _LINK
            cp.cell(row=r, column=4, value=note).font = _NOTE
            r += 1
        evb_r, pe_r, dcf_r = cv_start, cv_start + 1, cv_start + 2
        r += 1
        # hybrid / blended valuation — editable method weights + a blended value per share
        _section(cp, r, "  Hybrid / blended valuation (edit the weights)", span); r += 1
        wt = {}
        for lab, dflt, vref in [("Weight: DCF (base)", 50, dcf_r), ("Weight: EV/EBITDA (peers)", 30, evb_r),
                                ("Weight: P/E (peers)", 20, pe_r)]:
            cp.cell(row=r, column=1, value=lab).font = _LBL
            wc = cp.cell(row=r, column=2, value=dflt); wc.number_format = '0"%"'; wc.font = _INPUT_B
            wt[vref] = r
            r += 1
        wsum = "+".join(f"B{wt[x]}" for x in (dcf_r, evb_r, pe_r))
        blend = "+".join(f"B{wt[x]}*B{x}" for x in (dcf_r, evb_r, pe_r))
        cp.cell(row=r, column=1, value="Blended value per share").font = _LBL
        bc = cp.cell(row=r, column=2, value=f'=IFERROR(({blend})/({wsum}),"")'); bc.number_format = MONEY; bc.font = _FORMULA
        cp.cell(row=r, column=4, value="Σ(weight × method value) ÷ Σ(weights) — weights (blue) are editable").font = _NOTE
        r += 1
        cp.cell(row=r, column=1, value="Blended upside / (downside)").font = _LBL
        uc = cp.cell(row=r, column=2, value=f'=IFERROR(B{r - 1}/Current_Price-1,"")'); uc.number_format = PCT; uc.font = _FORMULA
        r += 2
    cp.cell(row=r + 1, column=1, value=(
        "Subject multiples are computed live from the model (green); peer multiples (blue) are this platform's own computed figures for peers in coverage."
        if any_mult else
        "Peer multiples populate when the peer is in coverage or from a market-data feed; the subject's live multiples are above.")).font = _SRC
    r += 3

    # (c) operating KPIs
    _section(cp, r, "  Operating KPIs (reported)", span); r += 1
    for j, h in enumerate(["KPI", "Value", "Unit", "Period"]):
        cp.cell(row=r, column=1 + j, value=h).font = _LBL
    r += 1
    for k in company.get("sector_kpis", []):
        cp.cell(row=r, column=1, value=k.get("name"))
        cp.cell(row=r, column=2, value=k.get("value"))
        cp.cell(row=r, column=3, value=k.get("unit"))
        cp.cell(row=r, column=4, value=k.get("period"))
        r += 1

    # ==================================================================
    # 6) OVERVIEW  (company + investment thesis, + bank detail for banks)
    # ==================================================================
    ov = _sheet(wb, "Overview", NAVY)
    ov.column_dimensions["A"].width = 30
    ov.column_dimensions["B"].width = 96
    _title(ov, f"  {name} ({tkr}) — OVERVIEW & INVESTMENT THESIS", 4)
    _fill(ov, 2, 4, GREEN)
    _section(ov, 3, "  Investment thesis — model-driven, active scenario", 4)
    ov.cell(row=4, column=1, value="Value per share (active scenario)").font = _LBL
    ov.cell(row=4, column=2, value=("=Bank_Fair_Value" if is_bank else "=Value_Per_Share")).number_format = MONEY
    ov.cell(row=4, column=2).font = _LINK
    ov.cell(row=5, column=1, value="Current price").font = _LBL
    ov.cell(row=5, column=2, value="=Current_Price").number_format = MONEY
    ov.cell(row=6, column=1, value="Upside / (downside)").font = _LBL
    ov.cell(row=6, column=2, value='=IF(B5,B4/B5-1,"")').number_format = PCT
    ov.cell(row=7, column=1, value="Indicative stance").font = _LBL
    ov.cell(row=7, column=2, value='=IF(B6="","NR",IF(B6>=0.2,"Buy",IF(B6>=-0.1,"Hold","Reduce")))').font = Font(bold=True)
    ov.cell(row=8, column=1, value="Active scenario").font = _LBL
    ov.cell(row=8, column=2, value='=CHOOSE(Scenario,"Bull","Base","Bear")')
    r = 10
    _section(ov, r, "  Company", 4); r += 1
    for k, v in [("Business", universe.get("sub_sector") or "—"),
                 ("Sector / country", f"{universe.get('sector')} · {universe.get('country')}"),
                 ("Exchange", universe.get("exchange"))]:
        ov.cell(row=r, column=1, value=k).font = _LBL
        ov.cell(row=r, column=2, value=v).font = _BODY
        r += 1
    segs = universe.get("segments") or (company.get("notes", {}) or {}).get("segments") or []
    if segs:
        ov.cell(row=r, column=1, value="Segments").font = _LBL
        ov.cell(row=r, column=2, value=", ".join((s.get("name") if isinstance(s, dict) else str(s)) for s in segs)).font = _BODY
        r += 1
    peers = universe.get("peers") or []
    if peers:
        ov.cell(row=r, column=1, value="Peers").font = _LBL
        ov.cell(row=r, column=2, value=", ".join((p.get("name") if isinstance(p, dict) else str(p)) for p in peers)).font = _BODY
        r += 1
    risks = universe.get("risks") or []
    if risks:
        r += 1
        _section(ov, r, "  Key risks", 4); r += 1
        for rk in risks[:6]:
            ov.cell(row=r, column=2, value="• " + (rk.get("description") if isinstance(rk, dict) else str(rk))).font = _BODY
            r += 1

    # ==================================================================
    # 7) SOURCES & CONTROLS  (sources + controls + control log + change log)
    # ==================================================================
    sc = _sheet(wb, "Sources_and_Controls", "FFB0352C" if not dq["export_allowed"] else TEAL)
    for col, w in zip("ABCDEF", (30, 16, 16, 46, 14, 16)):
        sc.column_dimensions[col].width = w
    _title(sc, f"  {name} ({tkr}) — SOURCES & CONTROLS", 6)
    _fill(sc, 2, 6, GREEN)
    _section(sc, 3, "  Data quality & export gate", 6)
    for i, (k, v) in enumerate([
        ("Data-quality score", f"{dq['score']} / 100 ({dq['band']})"), ("Critical failures", dq["critical_failures"]),
        ("Warnings", dq["warnings"]), ("Export status", "ALLOWED" if dq["export_allowed"] else "BLOCKED"),
        ("Model ID / version", model_id), ("Generation date", date.today().isoformat())], start=4):
        _kv(sc, i, k, v)
    r = 11
    _section(sc, r, "  Control checks", 6); r += 1
    for j, h in enumerate(["Check", "Severity", "Status", "Detail"]):
        sc.cell(row=r, column=1 + j, value=h).font = _LBL
    r += 1
    for c in controls:
        sc.cell(row=r, column=1, value=c["description"])
        sc.cell(row=r, column=2, value=c["severity"])
        st = sc.cell(row=r, column=3, value=c["status"].upper())
        st.font = Font(bold=True, color="FF197243" if c["status"] == "pass" else ("FFB0352C" if c["status"] == "fail" else "FFA96C00"))
        sc.cell(row=r, column=4, value=f"expected {c['expected']} | actual {c['actual']}")
        r += 1
    r += 1
    _section(sc, r, "  Sources — every historical figure traces here", 6); r += 1
    for j, h in enumerate(["Fiscal year", "Statement", "Source", "Confidence"]):
        sc.cell(row=r, column=1 + j, value=h).font = _LBL
    r += 1
    for ff in fins:
        sc.cell(row=r, column=1, value=ff.get("fy"))
        sc.cell(row=r, column=2, value="Income / balance / cash flow")
        sc.cell(row=r, column=3, value=(ff.get("source", "") or "")[:120])
        sc.cell(row=r, column=4, value=ff.get("confidence")).number_format = "0.00"
        r += 1

    freeze = {"Financials": "C3", "Financial_Statements": "C3", "Valuation": "B3",
              "Comps_and_KPIs": "A3", "Sources_and_Controls": "A3"}
    if is_bank:
        _bank_detail(wb, company, fins, market, span, freeze)
    _finalize(wb, freeze)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return {"path": str(out), "model_id": model_id, "version": model_version,
            "data_quality": dq, "controls": controls, "sheets": wb.sheetnames,
            "export_allowed": dq["export_allowed"]}


def _bank_valuation(wb, vl, r0, company, fins, market, span) -> None:
    """Justified-P/B bank valuation block on the Valuation sheet (scenario-switched)."""
    latest = fins[-1] if fins else {}
    eq, sh = latest.get("total_equity"), market.get("shares_outstanding_m")
    _section(vl, r0, "  Bank valuation — justified price / book (headline for banks)", span)
    rows = [
        ("Sustainable ROE (capped 20%)", "=MIN(Bank_ROE,0.20)", PCT),
        ("Cost of equity", "=Bank_COE", PCT),
        ("Sustainable growth", "=Bank_Growth", PCT),
        ("Book value per share", (eq / sh if (eq and sh) else None), MONEY),
        ("Justified P/B  =(ROE-g)/(COE-g)", f"=(B{r0+1}-B{r0+3})/(B{r0+2}-B{r0+3})", MULT),
        ("Fair value per share", f"=B{r0+5}*B{r0+4}", MONEY),
        ("Current price", "=Current_Price", MONEY),
        ("Upside / (downside)", f'=IF(B{r0+7},B{r0+6}/B{r0+7}-1,"")', PCT),
    ]
    for k, (lab, val, fmt) in enumerate(rows, start=1):
        vl.cell(row=r0 + k, column=1, value=lab).font = _LBL
        c = vl.cell(row=r0 + k, column=2, value=val); c.number_format = fmt
        if isinstance(val, (int, float)):
            c.font = _INPUT
    _name(wb, "Bank_BVPS", "Valuation", f"B{r0+4}")
    _name(wb, "Justified_PB", "Valuation", f"B{r0+5}")
    _name(wb, "Bank_Fair_Value", "Valuation", f"B{r0+6}")


def _bank_detail(wb, company, fins, market, span, freeze) -> None:
    """Bank operating schedules (loan book / deposits / margins / capital)."""
    b = company.get("bank") or {}
    kpis = company.get("sector_kpis", [])

    def frac(x):
        return x / 100.0 if (x is not None and abs(x) > 1.5) else x

    def kpi(sub):
        for k in kpis:
            if sub.lower() in (k.get("name", "").lower()):
                return k.get("value")
        return None

    bd = _sheet(wb, "Bank_Detail", TEAL)
    bd.column_dimensions["A"].width = 34
    _title(bd, f"  {company.get('name')} — BANK SCHEDULES", 4)
    _fill(bd, 2, 4, GREEN)
    blocks = [
        ("  Loan book", [("Gross loans", kpi("gross loan"), MONEY), ("Net loans & advances", kpi("net loan"), MONEY),
                         ("Gross NPL ratio", frac(b.get("npl_ratio")), PCT), ("Cost of risk", frac(b.get("cost_of_risk")), PCT)]),
        ("  Deposits & funding", [("Customer deposits", kpi("deposit"), MONEY), ("Loan-to-deposit ratio", frac(b.get("loan_deposit")), PCT),
                                  ("Net interest income", b.get("net_interest_income"), MONEY)]),
        ("  Margins & efficiency", [("Net interest margin", frac(b.get("nim")), PCT), ("Cost-to-income ratio", frac(b.get("cost_income")), PCT),
                                    ("Return on assets", frac(b.get("roa")), PCT)]),
        ("  Capital & returns", [("Capital adequacy ratio", frac(b.get("car")), PCT), ("Return on equity", frac(b.get("roe")), PCT)]),
    ]
    r = 3
    for title, rows in blocks:
        _section(bd, r, title, 4); r += 1
        for lab, val, fmt in rows:
            _kv(bd, r, lab, val, fmt, vfont=_INPUT if isinstance(val, (int, float)) else None)
            r += 1
        r += 1
    freeze["Bank_Detail"] = "A3"


def _statements_tab(wb, company: dict, universe: dict) -> bool:
    """Reproduce the reported audited financial statements (full line-item detail)
    exactly as filed — Statement of Comprehensive Income / Financial Position /
    Cash Flows — when the dataset carries a `statements` block. Returns True if a
    tab was added."""
    st = company.get("statements")
    if not st:
        return False
    name = company.get("name", "Company")
    tkr = universe.get("ticker", "NA")
    cols = st.get("columns", [])
    span = 2 + len(cols) + 1
    fs = _sheet(wb, "Financial_Statements", NAVY)
    fs.column_dimensions["A"].width = 54
    fs.column_dimensions["B"].width = 11
    for j in range(len(cols)):
        fs.column_dimensions[get_column_letter(3 + j)].width = 15
    _title(fs, f"  {name} ({tkr}) — REPORTED FINANCIAL STATEMENTS   ({st.get('basis', '')})", span)
    _fill(fs, 2, span, GREEN)
    nc = fs.cell(row=2, column=2, value="Note (FY25)"); nc.font = _YHDR
    nc.comment = Comment("Note references are from the FY2025 audited filing. Earlier years' "
                         "filings occasionally number the same note differently.", "Aeon Nimbus Research")
    for j, y in enumerate(cols):
        c = fs.cell(row=2, column=3 + j, value=y); c.font = _YHDR; c.alignment = _CENTER
    top = Border(top=Side(style="thin", color="FF9AA7AC"))
    state = {"r": 3}

    def render(title, rows):
        _section(fs, state["r"], "  " + title, span); state["r"] += 1
        for row in rows:
            r = state["r"]
            lab, note, vals, style = row["label"], row.get("note"), row.get("values"), row.get("style", "")
            if style == "H":
                fs.cell(row=r, column=1, value=lab).font = Font(bold=True, italic=True, color="FF5C6B72")
                state["r"] += 1
                continue
            fs.cell(row=r, column=1, value=lab).font = _LBL if style == "B" else _BODY
            if note:
                fs.cell(row=r, column=2, value=note).font = Font(size=9, color="FF9AA7AC")
            for j, v in enumerate(vals or []):
                c = fs.cell(row=r, column=3 + j, value=(v if v is not None else "–"))
                c.number_format = MONEY
                c.font = Font(bold=True, color="FF123142") if style == "B" else Font(color="FF0000FF")
                if style == "B":
                    c.border = top
            state["r"] += 1
        state["r"] += 1

    render("Statement of comprehensive income", st.get("income_statement", []))
    render("Statement of financial position", st.get("balance_sheet", []))
    render("Statement of cash flows", st.get("cash_flow", []))
    note = st.get("note", "")
    if note:
        fs.cell(row=state["r"], column=1, value=note).font = _SRC
        state["r"] += 1
    fs.cell(row=state["r"] + 1, column=1,
            value="Reported/audited figures (blue), reproduced as filed. Blank (–) = line not separately disclosed that year (never zero-filled). Source: " + st.get("source", "")).font = _SRC
    return True
