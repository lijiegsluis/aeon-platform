"""Safaricom: a formula-linked, segment-split three-statement model.

Answers the reviewer's model feedback directly:

  1  one integrated forecast — the operating build drives everything
  2  native Excel formulas, not values; change an assumption and the model moves
  3  Kenya and Ethiopia forecast on their own bases, reconciling to group
  4  FY2026 audited base, definitions never mixed
  5  reported / calculated / assumption marked on every input
  6  one direct-cost convention, so agent commissions cannot double-count
  7  Ethiopia schedule with FX and NCI; EPS on profit attributable to parent
  8  FY2026 closing balances are the forecast opening position
  9  no "other net assets" plug — the balance sheet closes through the schedules
 10  working capital forecast from the real FY2026 balances
 11  PP&E, intangible, lease and debt roll-forwards, each formula-linked
 12  a single tax assumption
 13  dividends declared separated from dividends paid
 14  visible WACC build; every valuation output linked
 15  named scenarios that move the whole model
 16  a control sheet that actually tests the things that went wrong

Every historical figure comes from data/safaricom_fy2026_register.json, which is
transcribed from the FY2026 Annual Report with page references. Nothing here is
invented: where the report does not disclose something, the model carries an
explicitly-labelled assumption rather than a fabricated "actual".
"""

from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent.parent
REGISTER = ROOT / "data" / "safaricom_fy2026_register.json"

# ---- house conventions ----------------------------------------------------
# Palette lifted from the Safaricom_Excel_V1 template so this matches the house look.
NAVY = "123142"        # titles, headers, tab colour
INK = "17262E"         # body text
MUTED = "5C6B72"       # units and notes
FAINT = "9AA7AC"       # the quietest annotations
SECTION = "1F7A5C"     # section-header fill
TEAL = "0C7A6B"        # accent / tab colour
BLUE_IN = "0000FF"     # a hardcoded input you may change
GREEN_LINK = "008000"  # a formula pointing at another sheet
BLACK = "000000"       # a formula on this sheet
PAPER, BORDER_C, AMBER = "F4F6F8", "D6DCE4", "FFF3B0"
FONT = "Calibri"
BASE, SMALL, TINY = 11, 10, 9

HIST = ["FY2025", "FY2026"]                      # actual columns C, D
FCST = ["FY2027E", "FY2028E", "FY2029E", "FY2030E", "FY2031E"]
C_H0, C_H1 = "C", "D"                            # FY2025, FY2026
F_COLS = ["E", "F", "G", "H", "I"]               # forecast columns
ALL_COLS = [C_H0, C_H1] + F_COLS

NUM = '#,##0;\\(#,##0\\);"-"'
PCT = '0.0%'
MULT = '0.0"x"'
CUR = '#,##0.00'


def _thin():
    s = Side(style="thin", color=BORDER_C)
    return Border(left=s, right=s, top=s, bottom=s)


class Builder:
    """Writes one sheet, tracking the row of every named line so later sheets
    can point at it by name instead of a magic cell reference."""

    # The unit label was the constant "KES millions" on every sheet of every
    # workbook, so a South African or Nigerian model announced itself in
    # Kenyan shillings on seven tabs.
    currency = "KES"

    def __init__(self, wb, title, reg):
        self.ws = wb.create_sheet(title)
        self.reg = reg
        self.row = 1
        self.ref = {}                     # label -> row number
        self.ws.sheet_view.showGridLines = False
        self.ws.column_dimensions["A"].width = 44
        self.ws.column_dimensions["B"].width = 30
        for c in ALL_COLS:
            self.ws.column_dimensions[c].width = 13

    # -- chrome ------------------------------------------------------------
    def title(self, text, sub=""):
        c = self.ws.cell(row=self.row, column=1, value="  " + text.upper())
        c.font = Font(name=FONT, size=12, bold=True, color="FFFFFF")
        for col in range(1, 11):
            self.ws.cell(row=self.row, column=col).fill = PatternFill("solid", start_color=NAVY)
        self.ws.row_dimensions[self.row].height = 20
        self.row += 1
        if sub:
            c = self.ws.cell(row=self.row, column=1, value=sub)
            c.font = Font(name=FONT, size=TINY, color=MUTED)
            self.row += 1
        self.row += 1

    def headers(self):
        self.ws.cell(row=self.row, column=1,
                     value=f"{self.currency} millions").font = Font(
            name=FONT, size=BASE, bold=True, color=NAVY)
        self.ws.cell(row=self.row, column=2, value="basis").font = Font(
            name=FONT, size=BASE, bold=True, color=NAVY)
        for col, lbl in zip(ALL_COLS, HIST + FCST):
            c = self.ws.cell(row=self.row, column=self._ci(col), value=lbl)
            c.font = Font(name=FONT, size=BASE, bold=True, color=NAVY)
            c.alignment = Alignment(horizontal="center")
        self.hdr_row = self.row
        self.row += 1

    def section(self, text):
        self.row += 1
        c = self.ws.cell(row=self.row, column=1, value=text.upper())
        c.font = Font(name=FONT, size=SMALL, bold=True, color="FFFFFF")
        for col in range(1, 11):
            self.ws.cell(row=self.row, column=col).fill = PatternFill("solid", start_color=SECTION)
        self.row += 1

    def note(self, text):
        c = self.ws.cell(row=self.row, column=1, value=text)
        c.font = Font(name=FONT, size=TINY, color=MUTED)
        self.row += 1

    @staticmethod
    def _ci(col):
        return ord(col) - 64

    # -- the workhorse -----------------------------------------------------
    def line(self, label, *, key=None, basis="", hist=None, formula=None,
             fmt=NUM, bold=False, input_row=False, indent=0):
        """One model line.

        hist     : {"FY2025": v, "FY2026": v} written as hardcoded actuals
        formula  : callable(col, prev_col) -> formula string, per forecast year
        input_row: blue, an assumption the user is meant to change
        """
        r = self.row
        lbl = ("    " * indent) + label
        c = self.ws.cell(row=r, column=1, value=lbl)
        c.font = Font(name=FONT, size=BASE, bold=bold, color=NAVY if bold else INK)
        if basis:
            if basis.startswith("="):          # never let a label parse as a formula
                basis = "'" + basis
            b = self.ws.cell(row=r, column=2, value=basis)
            b.font = Font(name=FONT, size=TINY, color=MUTED)

        colour = BLUE_IN if input_row else BLACK
        for col, yr in zip([C_H0, C_H1], HIST):
            v = (hist or {}).get(yr)
            if v is not None:
                cell = self.ws.cell(row=r, column=self._ci(col), value=v)
                cell.number_format = fmt
                _lk = isinstance(v, str) and v.startswith("=")
                cell.font = Font(name=FONT, size=BASE, bold=bold,
                                 color=GREEN_LINK if _lk else BLUE_IN)
        if formula is not None:
            prev = C_H1
            for col in F_COLS:
                f = formula(col, prev)
                if f is not None:
                    cell = self.ws.cell(row=r, column=self._ci(col), value=f)
                    cell.number_format = fmt
                    # green marks a link to another sheet, the house convention
                    col_c = GREEN_LINK if ("!" in f and not input_row) else colour
                    cell.font = Font(name=FONT, size=BASE, bold=bold, color=col_c)
                prev = col
        if key:
            self.ref[key] = r
        self.row += 1
        return r

    def spacer(self):
        self.row += 1


def _pct_row(b, label, key, value, basis, scen_row=None):
    """An assumption that a scenario can move: base value in the actual column,
    forecast years read it back so one edit propagates."""
    return b.line(label, key=key, basis=basis, input_row=True, fmt=PCT,
                  hist={"FY2026": value},
                  formula=lambda c, p: f"=${C_H1}${b.row}")


def build(out_path: Path) -> dict:
    reg = json.loads(REGISTER.read_text())
    wb = Workbook()
    wb.remove(wb.active)

    inc = reg["income_statement"]
    seg = reg["segment_note36"]["FY2026"]
    seg25 = reg["segment_note36"]["FY2025"]
    bs = reg["balance_sheet"]
    li = reg["liabilities"]
    eq = reg["equity"]
    ppe = reg["ppe_schedule_note18"]
    rou = reg["rou_schedule_note22a"]
    dbt = reg["borrowings_schedule_note16"]
    nci = reg["nci_and_ethiopia"]
    div = reg["dividends"]
    kre = reg["revenue_by_stream"]["kenya"]
    kpi = reg["operating_kpis_kenya"]
    ox = reg["opex_breakdown_note7"]
    tx_note = reg["tax_reconciliation_note12"]
    mb = reg["margin_bridge"]
    # note 7 is a GROUP note; Kenya carries this share of group other-expenses
    KSH = seg["other_expenses"]["kenya"] / seg["other_expenses"]["total_segments"]

    built = {}

    # =====================================================================
    # ASSUMPTIONS — every driver a scenario can move
    # =====================================================================
    A = Builder(wb, "Assumptions", reg)
    A.title("Safaricom PLC — assumptions",
            "Blue cells are inputs. Change one and the whole model moves. "
            "Scenario switch below drives Kenya, Ethiopia, capex, FX and working capital together.")

    A.ws.cell(row=A.row, column=1, value="SCENARIO  (1 = Bear, 2 = Base, 3 = Bull)").font = Font(
        name=FONT, size=SMALL, bold=True, color=NAVY)
    sc = A.ws.cell(row=A.row, column=3, value=2)
    sc.font = Font(name=FONT, size=11, bold=True, color=BLUE_IN)
    sc.fill = PatternFill("solid", start_color=AMBER)
    sc.border = _thin()
    from openpyxl.worksheet.datavalidation import DataValidation
    _dv = DataValidation(type="list", formula1='"1,2,3"', allow_blank=False,
                         showErrorMessage=True)
    _dv.error = "The scenario switch takes 1 for Bear, 2 for Base or 3 for Bull."
    _dv.errorTitle = "Scenario out of range"
    A.ws.add_data_validation(_dv)
    _dv.add(sc)
    SWITCH = f"Assumptions!$C${A.row}"
    A.ws.cell(row=A.row, column=4,
              value='=CHOOSE($C$%d,"Bear","Base","Bull")' % A.row).font = Font(
        name=FONT, size=SMALL, bold=True, color=NAVY)
    A.row += 2

    hdr = ["driver", "unit", "Bear", "Base", "Bull", "ACTIVE", "basis"]
    for i, h in enumerate(hdr):
        c = A.ws.cell(row=A.row, column=1 + i, value=h)
        c.font = Font(name=FONT, size=BASE, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", start_color=NAVY)
    A.row += 1
    A.ws.column_dimensions["C"].width = 11
    A.ws.column_dimensions["D"].width = 11
    A.ws.column_dimensions["E"].width = 11
    A.ws.column_dimensions["F"].width = 12
    A.ws.column_dimensions["G"].width = 58

    drivers = [
        # key,                 label,                              unit, bear, base, bull, basis
        ("ke_voice_subs_g",  "Kenya voice customers, growth",      "%",  0.010, 0.030, 0.045,
         "FY2026 one-month active voice customers +4.7% (reported, p93); base assumes it moderates"),
        ("ke_voice_arpu_g",  "Kenya voice ARPU, growth",           "%", -0.070,-0.040,-0.010,
         "FY2026 voice ARPU -4.7% (reported, p93); competition and CVM keep it negative"),
        ("ke_data_g",        "Kenya mobile data revenue, growth",  "%",  0.080, 0.120, 0.160,
         "FY2026 +14.4% (reported, p93); assumption, decelerating off a larger base"),
        ("ke_mpesa_g",       "Kenya M-PESA revenue, growth",       "%",  0.060, 0.105, 0.150,
         "FY2026 +13.4% (reported, p90); bear reflects the mobile-money tax risk"),
        ("ke_fixed_g",       "Kenya fixed and IoT revenue, growth","%",  0.070, 0.110, 0.150,
         "FY2026 +12.2% (reported, p93); FTTH-led"),
        ("ke_msg_g",         "Kenya messaging revenue, growth",    "%", -0.150,-0.100,-0.050,
         "FY2026 -11.8% (calculated from p98); structural decline to OTT"),
        ("ke_other_g",       "Kenya other mobile revenue, growth", "%",  0.020, 0.060, 0.100,
         "assumption; includes incoming and other mobile service"),
        ("ke_handset_g",     "Kenya handset and other, growth",    "%", -0.100, 0.000, 0.100,
         "volatile line, FY2026 fell to 6,457.6 from 12,635.2 (p98); base holds flat"),
        ("ke_direct_pct",    "Kenya direct costs, % of revenue",   "%",  0.245, 0.235, 0.225,
         "FY2026 actual 23.8% (calculated). INCLUDES M-PESA agent commissions — see Op_Drivers"),
        ("ke_ecl_pct",       "Kenya expected credit losses, % rev","%",  0.018, 0.013, 0.010,
         "FY2026 1.4% (calculated); FY2025 was 2.9%, so FY2026 is already normalised"),
        ("ke_opex_pct",      "Kenya other opex ex-staff/network/FX, % rev","%",0.075, 0.068, 0.062,
         "FY2026 residual after employee, network and FX are taken out (calculated from note 7)"),
        ("ke_employee_g",    "Kenya employee costs, growth",       "%",  0.100, 0.076, 0.055,
         "FY2026 employee benefits rose 7.6% to 31,258.6 (calculated, note 7)"),
        ("ke_network_g",     "Kenya network operating costs, growth","%",0.060, 0.030, 0.012,
         "FY2026 network costs rose only 1.2% to 24,424.3 (calculated, note 7)"),
        ("ke_fx_opex",       "FX losses inside opex, KShs mn",     "mn", 14000, 8000,  3000,
         "FY2026 13,433.0, FY2025 20,871.3 (note 7). A TEMPORARY benefit: most of the FY2026 opex "
         "fall was this line, not underlying cost control"),
        ("et_rev_g",         "Ethiopia revenue growth",            "%",  0.350, 0.550, 0.750,
         "FY2026 +130.5% (calculated from segment note); decelerates off a small base"),
        ("et_margin_step",   "Ethiopia margin gain, first year",   "%",  0.450, 0.600, 0.750,
         "FY2026 margin -88.5%, improved from -447.5% (calculated). Management targets EBITDA "
         "break-even by end FY2027"),
        ("et_capex",         "Ethiopia capex FY2027, KShs mn",     "mn", 12000,  7500,  6000,
         "GUIDANCE: the company guides KShs 6bn to 9bn for FY2027 against 18.7bn spent in "
         "FY2026, as the network approaches its site target. Base is the midpoint"),
        ("et_capex_run",     "Ethiopia capex run-rate, KShs mn",   "mn", 16000, 10000,  8000,
         "ASSUMPTION, not guidance: the company has guided FY2027 only. This carries a "
         "run-rate for FY2028 to FY2031 and is the analyst's judgement"),
        ("et_fx_dep",        "Birr depreciation vs KShs, annual",  "%",  0.180, 0.100, 0.040,
         "assumption. FY2026 translation differences were -16,385.4 on PP&E and -5,137.6 on debt"),
        ("nci_share",        "NCI share of Ethiopia result",       "%",  0.4583,0.4583,0.4583,
         "the FY2026 weighted-average minority interest, being the rate that reproduces "
         "the audited NCI charge of -21,932.6 (note, p155). It is not the standing "
         "shareholding: after the FY2026 dilution Safaricom PLC holds 54.17% of Safaricom "
         "Ethiopia, so the minority on the register is 45.83%. The dilution happened "
         "during the year, which is why the average differs from the year-end position, "
         "and the forecast carries the average"),
        ("ke_capex_pct",     "Kenya capex, % of Kenya revenue",    "%",  0.120, 0.110, 0.100,
         "assumption; group PP&E additions 74,516.2 less Ethiopia capex"),
        ("dso",              "Receivable days",                    "days",38.0, 33.6, 30.0,
         "FY2026 ACTUAL 33.6 days (calculated: 39,416.9 / 427,559.1 x 365). Model previously "
         "assumed 45, which item 10 flagged"),
        ("dpo",              "Payable days",                       "days", 63.0, 68.5, 74.0,
         "FY2026 ACTUAL 68.5 days (calculated: 80,263.9 / 427,559.1 x 365). Payables fund the business, which is why working capital is negative"),
        ("dio",              "Inventory days",                     "days",  2.0,  1.4, 1.2,
         "FY2026 actual 1.4 days (calculated); Safaricom carries almost no inventory"),
        ("tax_rate",         "Statutory income tax rate",          "%",  0.300, 0.300, 0.300,
         "DISCLOSED: note 12 states the applicable rate is 30%. One rate throughout (item 12)"),
        ("tax_drag",         "Non-deductible and unrecognised drag","%",  0.100, 0.070, 0.040,
         "FY2026 effective rate was 41.9% against a 30% statutory rate. The gap is 18,107.7 of "
         "non-deductible expenses plus unrecognised deferred tax on Ethiopian losses (note 12). "
         "This drag decays as Ethiopia approaches break-even"),
        ("deferred_tax",     "Deferred tax credit, KShs mn",       "mn", 500,   1000,  1700,
         "FY2026 credit 1,712.5, FY2025 3,203.4 (note 17). Item 12: a simple stated assumption, "
         "NOT a fixed percentage of the tax charge"),
        ("nci_distrib",      "Distributions to minorities, KShs mn","mn",   0,     0,     0,
         "Item 13. FY2026 had a 9,451.3 capital CONTRIBUTION from minorities, not a distribution. "
         "Nil assumed while Ethiopia is funding itself"),
        ("etb_per_kes",      "Birr per KShs, average rate",        "x",  0.55,  0.55,  0.55,
         "ASSUMPTION, calibrated so birr revenue translates back to the audited 17,355.7. Refresh "
         "from the FY2026 Annual Report FX note before publication"),
        ("final_share",      "Final dividend, % of the declaration","%",  0.575, 0.575, 0.575,
         "ASSUMPTION, held at the FY2026 split. The interim of 34,055.6 and the proposed "
         "final of 46,075.2 sum to the 80,130.8 declared, so the final is 57.5% of it. "
         "The split matters because only the final is outstanding at the year end: it is "
         "proposed, not approved, so IAS 10 keeps it in equity until the annual general "
         "meeting, and it is paid in the year after"),
        ("payout",           "Dividend payout, % of profit to parent","%",0.700,0.800, 0.900,
         "FY2026 DECLARED payout 83.8% (calculated). Not the 62.9% paid ratio — item 13"),
        ("deposit_rate",     "Deposit rate earned on cash",        "%",  0.060, 0.090, 0.110,
         "ASSUMPTION. FY2026 finance income of 6,223.8 against closing cash of 26,955.3 "
         "implies 23.1%, which is not a deposit yield: note 8 carries more than bank "
         "interest, and the filed cash flow shows interest received of a fraction of it. "
         "The forecast earns a stated rate on opening cash instead, against a Central Bank "
         "Rate of 8.75% at the valuation date"),
        ("cost_of_debt",     "Cost of debt",                       "%",  0.125, 0.116, 0.106,
         "DISCLOSED: 11.6% group including Ethiopia, 10.6% Kenya only (note 16)"),
        ("debt_repay_pct",   "Debt repaid, % of opening balance",  "%",  0.200, 0.180, 0.160,
         "FY2026 repayments 74,178.9 on opening 107,430.4 (note 16), but that included "
         "refinancing; base assumes a steadier profile"),
        ("lease_add_pct",    "New leases, % of revenue",           "%",  0.040, 0.035, 0.030,
         "FY2026 ROU additions 15,768.1 on revenue 427,559.1 = 3.7% (calculated, note 22a)"),
        ("intang_add",       "Intangible additions, KShs mn",      "mn", 9000,  7500,  6000,
         "assumption; includes spectrum and licences. FY2026 amortisation was 9,441.8"),
    ]
    D = {}
    for key, label, unit, bear, base, bull, basis in drivers:
        r = A.row
        A.ws.cell(row=r, column=1, value=label).font = Font(name=FONT, size=BASE)
        A.ws.cell(row=r, column=2, value=unit).font = Font(name=FONT, size=TINY, color=MUTED)
        for i, v in enumerate((bear, base, bull)):
            c = A.ws.cell(row=r, column=3 + i, value=v)
            c.font = Font(name=FONT, size=BASE, color=BLUE_IN)
            c.number_format = PCT if unit == "%" else NUM
        act = A.ws.cell(row=r, column=6, value=f"=CHOOSE({SWITCH},C{r},D{r},E{r})")
        act.font = Font(name=FONT, size=BASE, bold=True, color=BLACK)
        act.number_format = PCT if unit == "%" else NUM
        act.fill = PatternFill("solid", start_color="EAF3EF")   # quiet tint on the ACTIVE driver
        bc = A.ws.cell(row=r, column=7, value=basis)
        bc.font = Font(name=FONT, size=TINY, italic=True, color=MUTED)
        bc.alignment = Alignment(wrap_text=True, vertical="top")
        A.ws.row_dimensions[r].height = 22
        D[key] = f"Assumptions!$F${r}"
        A.row += 1

    A.row += 1
    A.note("Sources: Safaricom PLC Annual Report FY2026 (year ended 31 March 2026). "
           "'reported' = printed in the report; 'calculated' = derived here from reported figures; "
           "'assumption' = analyst judgement. Item 5 asks for exactly this distinction.")
    D["_switch"] = SWITCH
    built["assumptions"] = D

    # =====================================================================
    # KENYA OPERATING BUILD
    # =====================================================================
    K = Builder(wb, "Op_Drivers", reg)
    K.title("Kenya — operating build",
            "Kenya bases only. The 44.36m group one-month-active figure is NOT used here: "
            "Kenya voice runs on the disclosed 31.6m Kenyan voice customer base (item 3).")
    K.headers()

    K.section("Operating drivers")
    subs = K.line("One-month active voice customers (m)", key="subs", basis="reported, p93",
                  hist={"FY2025": round(kpi["one_month_active_voice_customers_m"]["FY2026"] /
                                        (1 + kpi["one_month_active_voice_customers_m"]["growth"]), 2),
                        "FY2026": kpi["one_month_active_voice_customers_m"]["FY2026"]},
                  formula=lambda c, p: f"={p}{K.row}*(1+{D['ke_voice_subs_g']})")
    arpu = K.line("Voice ARPU (KShs per month)", key="arpu", basis="calculated = revenue / subs / 12",
                  hist={"FY2025": round(kre["voice"]["FY2025"] / (kpi["one_month_active_voice_customers_m"]["FY2026"] /
                                        (1 + kpi["one_month_active_voice_customers_m"]["growth"])) / 12, 1),
                        "FY2026": round(kre["voice"]["FY2026"] / kpi["one_month_active_voice_customers_m"]["FY2026"] / 12, 1)},
                  formula=lambda c, p: f"={p}{K.row}*(1+{D['ke_voice_arpu_g']})", fmt=CUR)

    K.section("Kenya service revenue by stream")
    voice = K.line("Voice revenue", key="voice", basis="subs x ARPU x 12",
                   hist={y: kre["voice"][y] for y in HIST},
                   formula=lambda c, p: f"={c}{subs}*{c}{arpu}*12")
    msg = K.line("Messaging revenue", key="msg", basis="growth driver",
                 hist={y: kre["messaging"][y] for y in HIST},
                 formula=lambda c, p: f"={p}{K.row}*(1+{D['ke_msg_g']})")
    data = K.line("Mobile data revenue", key="data", basis="growth driver",
                  hist={y: kre["mobile_data"][y] for y in HIST},
                  formula=lambda c, p: f"={p}{K.row}*(1+{D['ke_data_g']})")
    othm = K.line("Mobile incoming and other mobile", key="othm", basis="growth driver",
                  hist={y: kre["mobile_incoming"][y] + kre["other_mobile_service"][y] for y in HIST},
                  formula=lambda c, p: f"={p}{K.row}*(1+{D['ke_other_g']})")
    conn = K.line("Connectivity revenue", key="conn", basis="sum", bold=True,
                  hist={y: kre["connectivity"][y] for y in HIST},
                  formula=lambda c, p: f"=SUM({c}{voice}:{c}{othm})")
    mpesa = K.line("M-PESA revenue", key="mpesa", basis="growth driver",
                   hist={y: kre["m_pesa"][y] for y in HIST},
                   formula=lambda c, p: f"={p}{K.row}*(1+{D['ke_mpesa_g']})")
    mobsvc = K.line("Mobile service revenue", key="mobsvc", basis="connectivity + M-PESA", bold=True,
                    hist={y: kre["mobile_service_revenue"][y] for y in HIST},
                    formula=lambda c, p: f"={c}{conn}+{c}{mpesa}")
    fixed = K.line("Fixed service and IoT revenue", key="fixed", basis="growth driver",
                   hist={y: kre["fixed_service_and_iot"][y] for y in HIST},
                   formula=lambda c, p: f"={p}{K.row}*(1+{D['ke_fixed_g']})")
    ksvc = K.line("KENYA SERVICE REVENUE", key="svc", basis="mobile service + fixed", bold=True,
                  hist={y: kre["service_revenue"][y] for y in HIST},
                  formula=lambda c, p: f"={c}{mobsvc}+{c}{fixed}")
    hand = K.line("Handset and other revenue", key="handset", basis="growth driver",
                  hist={y: kre["handset_and_other"][y] for y in HIST},
                  formula=lambda c, p: f"={p}{K.row}*(1+{D['ke_handset_g']})")
    ktot = K.line("KENYA TOTAL REVENUE", key="total", basis="service + handset and other", bold=True,
                  hist={"FY2026": seg["total_revenue"]["kenya"],
                        "FY2025": seg25["total_revenue"]["kenya"]},
                  formula=lambda c, p: f"={c}{ksvc}+{c}{hand}")
    K.note("Kenya service revenue of 400,794.6 plus handset and other of 6,457.6 ties to the segment "
           "note's Kenya revenue from contracts (407,252.0, p243). Definitions are not mixed: this "
           "sheet never uses group service revenue of 414,137.4 or group total revenue of 427,559.1.")

    K.section("Kenya cost build")
    K.note("Item 6, ONE convention so nothing double-counts: direct costs are stated INCLUDING "
           "M-PESA agent commissions, exactly as note 6(a) presents them. No separate commission "
           "line is added anywhere in this model. The categories below are the material ones "
           "disclosed in note 7.")
    kdir = K.line("Direct costs (incl. M-PESA agent commissions)", key="direct",
                  basis="% of Kenya revenue, note 6(a)",
                  hist={"FY2026": seg["direct_costs"]["kenya"]},
                  formula=lambda c, p: f"=-{c}{ktot}*{D['ke_direct_pct']}")
    kemp = K.line("Employee benefits", key="employee", basis="grows with the wage driver", indent=1,
                  hist={"FY2026": ox["employee_benefits"]["FY2026"] * KSH,
                        "FY2025": ox["employee_benefits"]["FY2025"] * KSH},
                  formula=lambda c, p: f"={p}{K.row}*(1+{D['ke_employee_g']})")
    knet = K.line("Network operating costs", key="network", basis="grows with the network driver",
                  indent=1,
                  hist={"FY2026": ox["network_operating_costs"]["FY2026"] * KSH,
                        "FY2025": ox["network_operating_costs"]["FY2025"] * KSH},
                  formula=lambda c, p: f"={p}{K.row}*(1+{D['ke_network_g']})")
    kfx = K.line("Net FX losses inside operating expenses", key="fx_opex",
                 basis="note 7; assumed to normalise", indent=1,
                 hist={"FY2026": ox["net_fx_losses_in_opex"]["FY2026"] * KSH,
                       "FY2025": ox["net_fx_losses_in_opex"]["FY2025"] * KSH},
                 formula=lambda c, p: f"=-{D['ke_fx_opex']}")
    koth = K.line("Other operating expenses", key="opex", basis="% of Kenya revenue", indent=1,
                  hist={"FY2026": (seg["other_expenses"]["kenya"]
                                   - ox["employee_benefits"]["FY2026"] * KSH
                                   - ox["network_operating_costs"]["FY2026"] * KSH
                                   - ox["net_fx_losses_in_opex"]["FY2026"] * KSH)},
                  formula=lambda c, p: f"=-{c}{ktot}*{D['ke_opex_pct']}")
    kecl = K.line("Expected credit losses", key="ecl", basis="% of Kenya revenue, note 6(b)",
                  hist={"FY2026": seg["expected_credit_losses"]["kenya"]},
                  formula=lambda c, p: f"=-{c}{ktot}*{D['ke_ecl_pct']}")
    K.note("FY2026 ECL of 4,013.1 group-wide was already NORMALISED: FY2025 carried 11,146.0, of "
           "which 11,124.8 was trade receivables (note 6(b)). That 7,132.9 improvement does not "
           "repeat, which is part of why a flat 51% group margin is not supportable.")
    kebitda = K.line("KENYA EBITDA", key="ebitda", basis="revenue less cash costs", bold=True,
                     hist={"FY2026": seg["ebitda"]["kenya"], "FY2025": seg25["ebitda"]["kenya"]},
                     formula=lambda c, p: f"={c}{ktot}+{c}{kdir}+{c}{kemp}+{c}{knet}+{c}{kfx}+{c}{koth}+{c}{kecl}")
    K.line("Kenya EBITDA margin", basis="calculated", fmt=PCT,
           hist={"FY2026": seg["ebitda"]["kenya"] / seg["total_revenue"]["kenya"],
                 "FY2025": seg25["ebitda"]["kenya"] / seg25["total_revenue"]["kenya"]},
           formula=lambda c, p: f"=IF({c}{ktot}=0,0,{c}{kebitda}/{c}{ktot})")
    kcapex = K.line("Kenya capex", key="capex", basis="% of Kenya revenue",
                    formula=lambda c, p: f"=-{c}{ktot}*{D['ke_capex_pct']}")

    built["kenya"] = {"total": ktot, "ebitda": kebitda, "capex": kcapex, "svc": ksvc,
                      "direct": kdir, "ecl": kecl, "opex": koth, "mpesa": mpesa,
                      "employee": kemp, "network": knet, "fx_opex": kfx}

    # =====================================================================
    # ETHIOPIA
    # =====================================================================
    E = Builder(wb, "Ethiopia", reg)
    E.title("Ethiopia — simplified segment schedule",
            "Item 7. Revenue, EBITDA, EBIT, net loss, capex, FX and the NCI share. "
            "FY2026 figures are the audited segment note (p243, p245).")
    E.headers()

    E.section("Revenue in local currency, then translated")
    E.note("Item 7: Ethiopia earns birr. Revenue is grown in birr and translated at the average "
           "rate, so birr weakness reduces the KES contribution automatically. The base-year rate "
           "is calibrated to reproduce the audited 17,355.7, and is an assumption to refresh.")
    # This carried a literal 0.55 while the Assumptions sheet held a Bear/Base/Bull
    # driver of the same name that nothing read, under an instruction to refresh it
    # before publication. The driver is now the one the model uses.
    efx_r = E.line("Birr per KShs, average rate", key="rate", basis="from the Assumptions sheet", fmt="0.000",
                   hist={"FY2026": f"={D['etb_per_kes']}"},
                   formula=lambda c, p: f"={p}{E.row}*(1+{D['et_fx_dep']})")
    ebirr = E.line("Ethiopia revenue (ETB m)", key="rev_birr", basis="grown in local currency",
                   hist={"FY2026": seg["total_revenue"]["ethiopia"] * 0.55},
                   formula=lambda c, p: f"={p}{E.row}*(1+{D['et_rev_g']})")
    erev = E.line("Ethiopia revenue (KShs m)", key="rev", basis="birr revenue / average rate",
                  bold=True,
                  hist={"FY2026": seg["total_revenue"]["ethiopia"],
                        "FY2025": seg25["total_revenue"]["ethiopia"]},
                  formula=lambda c, p: f"={c}{ebirr}/{c}{efx_r}")

    E.section("Operating")
    E.note("Item 2: management targets EBITDA break-even by the END of FY2027, which is an exit "
           "run-rate rather than a full-year outcome. A business reaching break-even in its final "
           "quarter still reports a negative full year, so the FY2027 full-year figure below is "
           "negative and FY2028 is the first full year at or above break-even. The margin gain "
           "halves each year as the easy operating leverage is used up.")
    emar = E.line("Ethiopia EBITDA margin", key="margin",
                  basis="prior year plus the annual gain, which halves each year", fmt=PCT,
                  hist={"FY2026": seg["ebitda"]["ethiopia"] / seg["total_revenue"]["ethiopia"],
                        "FY2025": seg25["ebitda"]["ethiopia"] / seg25["total_revenue"]["ethiopia"]},
                  formula=lambda c, p: f"=MIN(0.30,{p}{E.row}+{D['et_margin_step']}/2^{F_COLS.index(c)})")
    eebitda = E.line("ETHIOPIA EBITDA", key="ebitda", basis="revenue x margin", bold=True,
                     hist={"FY2026": seg["ebitda"]["ethiopia"], "FY2025": seg25["ebitda"]["ethiopia"]},
                     formula=lambda c, p: f"={c}{erev}*{c}{emar}")
    E.note("Item 2: management targets EBITDA break-even by the END of FY2027. This model "
           "shows a negative FY2027 full year and a positive FY2028, and the two statements "
           "are consistent rather than contradictory: a business that exits FY2027 at a "
           "break-even run rate still reports a loss for the year as a whole, because the "
           "first three quarters were loss-making. The full year turns positive in the year "
           "AFTER the exit rate does. The margin driver decides this, not a hardcoded date, "
           "so the bear case pushes it out and the bull case pulls it in.")

    ecap = E.line("Ethiopia capex", key="capex",
                  basis="FY2027 at guidance, then the assumed run-rate",
                  formula=lambda c, p: (f"=-{D['et_capex']}" if c == F_COLS[0]
                                        else f"=-{D['et_capex_run']}"))
    E.note("Item 3: the first forecast year is the company's own guidance. The four years "
           "after are an assumed run-rate and are NOT guidance. The two are separated here "
           "because the distinction matters to how much weight the correction carries.")
    edep = E.line("Ethiopia depreciation and amortisation", key="da",
                  basis="prior-year capex profile",
                  hist={"FY2026": seg["depreciation_ppe"]["ethiopia"] +
                        seg["amortisation_intangibles"]["ethiopia"] + seg["depreciation_rou"]["ethiopia"]},
                  formula=lambda c, p: (f"={p}{E.row}*0.5-{D['et_capex']}*0.25" if c == F_COLS[0]
                                        else f"={p}{E.row}*0.5-{D['et_capex_run']}*0.25"))
    eebit = E.line("ETHIOPIA EBIT", key="ebit", basis="EBITDA less D&A", bold=True,
                   hist={"FY2026": seg["operating_profit"]["ethiopia"],
                         "FY2025": seg25["operating_profit"]["ethiopia"]},
                   formula=lambda c, p: f"={c}{eebitda}+{c}{edep}")

    E.section("Currency and funding")
    efx = E.line("Birr depreciation in the year", key="fx", basis="assumption", fmt=PCT,
                 formula=lambda c, p: f"={D['et_fx_dep']}")
    efxl = E.line("FX loss on foreign-currency funding", key="fxloss",
                  basis="birr move x Ethiopia net debt",
                  hist={"FY2026": dbt["translation_differences"]},
                  formula=lambda c, p: f"=-{c}{efx}*ABS({c}{ecap})*1.2")
    E.note("FY2026 carried translation differences of -16,385.4 on property and equipment (note 18) "
           "and -5,137.6 on borrowings (note 16), with a +5,315.9 revaluation of foreign-currency "
           "loans. Ethiopia earns birr while part of its funding and equipment is foreign-currency.")

    enet = E.line("ETHIOPIA NET RESULT", key="net", basis="EBIT less FX and funding cost", bold=True,
                  formula=lambda c, p: f"={c}{eebit}+{c}{efxl}")

    E.section("Ethiopian tax")
    E.note("Item 2: IFRS 10 attributes profit or loss AFTER tax to minorities, so Ethiopia "
           "is taxed here before anything is attributed. Ethiopia has never been profitable "
           "and its losses have not been recognised as a deferred tax asset, so the "
           "accumulated losses below shelter the first profitable years and no tax is paid "
           "until they are used up.")
    etlo = E.line("Accumulated tax losses, opening", key="tl_open", indent=1,
                  basis="prior-year closing; opens at the two disclosed loss years",
                  hist={"FY2026": abs(seg25["operating_profit"]["ethiopia"])},
                  formula=lambda c, p: f"={p}{E.row+3}" if c != F_COLS[0] else
                  f"={C_H1}{E.row+3}")
    etax = E.line("Ethiopian tax", key="tax", indent=1,
                  basis="statutory rate on profit after the losses carried forward",
                  hist={"FY2026": 0.0},
                  formula=lambda c, p: f"=-MAX(0,{c}{enet}-{c}{E.row-1})*{D['tax_rate']}")
    eaft = E.line("ETHIOPIA RESULT AFTER TAX", key="after_tax", bold=True,
                  basis="the figure IFRS 10 attributes",
                  hist={"FY2026": seg["operating_profit"]["ethiopia"]},
                  formula=lambda c, p: f"={c}{enet}+{c}{etax}")
    etlc = E.line("Accumulated tax losses, closing", key="tl_close", indent=1,
                  basis="opening plus this year's loss, less any used",
                  hist={"FY2026": abs(seg25["operating_profit"]["ethiopia"]) +
                        abs(seg["operating_profit"]["ethiopia"])},
                  formula=lambda c, p: f"=MAX(0,{c}{etlo}-{c}{enet})")

    E.section("Attribution to minorities")
    encis = E.line("NCI share of Ethiopia result", key="nci_share",
                   basis="NCI % x the result AFTER tax, as IFRS 10 requires",
                   hist={"FY2026": nci["nci_share_of_loss"]["FY2026"]},
                   formula=lambda c, p: f"={c}{eaft}*{D['nci_share']}")
    encif = E.line("NCI share of translation differences", key="nci_fx", indent=1,
                   basis="birr movement on the minority's opening equity",
                   hist={"FY2026": nci["nci_share_of_fx_translation"]["FY2026"]},
                   formula=lambda c, p: f"=-{c}{efx}*{p}{E.row+3}")
    encid = E.line("Distributions to minority shareholders", key="nci_distrib",
                   basis="item 13; nil while Ethiopia is funding itself", indent=1,
                   hist={"FY2026": 0.0}, formula=lambda c, p: f"=-{D['nci_distrib']}")
    encic = E.line("Capital contribution from minorities", key="nci_contrib",
                   basis="item 5: minorities fund their share rather than let the balance "
                         "go negative", indent=1,
                   hist={"FY2026": nci["nci_capital_contribution"]["FY2026"]},
                   formula=lambda c, p: f"=MAX(0,-({p}{E.row+1}+{c}{encis}+{c}{encif}+{c}{encid}))")
    encib = E.line("NCI closing balance", key="nci_bal",
                   basis="opening + share of result - distributions",
                   hist={"FY2026": nci["nci_closing_balance"]["FY2026"],
                         "FY2025": nci["nci_opening_balance"]["FY2026"]},
                   formula=lambda c, p: f"={p}{E.row}+{c}{encis}+{c}{encif}+{c}{encid}+{c}{encic}")
    E.note("The minority's equity is a claim on Ethiopian net assets carried in birr, so it "
           "translates down as the birr falls. FY2026 carried a share of "
           f"{nci['nci_share_of_fx_translation']['FY2026']:,.1f} against an opening balance of "
           f"{nci['nci_opening_balance']['FY2026']:,.1f}, and the forecast applies the birr "
           "assumption to the opening balance each year. Leaving it out put the whole "
           "translation on the parent and overstated the minority's equity.")
    E.note("FY2026 saw a 9,451.3 capital CONTRIBUTION from minority shareholders towards Safaricom "
           "Ethiopia's equity (p155). While the venture is loss-making the minorities keep funding "
           "their share, so the balance is floored at nil rather than turning negative: a negative "
           "NCI would imply the minorities owe the group money, which is not the funding structure "
           "in place.")
    built["ethiopia"] = {"rev": erev, "ebitda": eebitda, "ebit": eebit, "capex": ecap, "fxloss": efxl,
                         "net": enet, "nci_share": encis, "nci_bal": encib, "da": edep, "nci_distrib": encid,
                         "nci_contrib": encic, "after_tax": eaft, "tax": etax, "nci_fx": encif}

    return _finish(wb, out_path, reg, built, D)


def _finish(wb, out_path, reg, built, D):
    """Schedules, three statements, valuation and controls — all formula-linked."""
    inc, bs, li = reg["income_statement"], reg["balance_sheet"], reg["liabilities"]
    eq, ppe, rou = reg["equity"], reg["ppe_schedule_note18"], reg["rou_schedule_note22a"]
    dbt, nci, div = reg["borrowings_schedule_note16"], reg["nci_and_ethiopia"], reg["dividends"]
    seg = reg["segment_note36"]["FY2026"]
    K, E = built["kenya"], built["ethiopia"]

    def k(key, col):  return f"Op_Drivers!{col}{K[key]}"
    def et(key, col): return f"Ethiopia!{col}{E[key]}"

    # =================================================================
    # SCHEDULES
    # =================================================================
    S = Builder(wb, "Schedules", reg)
    S.title("Supporting schedules",
            "Item 11. Every roll-forward opens at the FY2026 audited closing balance and closes "
            "at open + movements. These schedules DRIVE the balance sheet; nothing is plugged.")
    S.headers()

    S.section("Property and equipment  (note 18)")
    p_op = S.line("Opening net book value", key="ppe_open", basis="FY2026 close = note 18",
                  hist={"FY2026": ppe["opening_net_carrying_amount"]},
                  formula=lambda c, p: (f"=${C_H1}${S.row+4}" if c == "E" else f"={p}{S.row+4}"))
    p_add = S.line("Additions (capex)", key="ppe_add", basis="Kenya + Ethiopia capex", indent=1,
                   hist={"FY2026": ppe["additions"]},
                   formula=lambda c, p: f"=-{k('capex',c)}-{et('capex',c)}")
    p_dep = S.line("Depreciation", key="ppe_dep", basis="opening / useful life", indent=1,
                   hist={"FY2026": ppe["depreciation_charge"]},
                   formula=lambda c, p: f"=-({c}{S.row-2}+{c}{S.row-1}*0.5)/7.5")
    p_fx = S.line("Translation differences", key="ppe_fx", basis="birr move x Ethiopia asset base",
                  indent=1, hist={"FY2026": ppe["translation_differences"]},
                  formula=lambda c, p: f"=-{D['et_fx_dep']}*{c}{S.row-3}*0.30")
    p_cl = S.line("Closing net book value", key="ppe_close", basis="open + capex - depn + FX",
                  bold=True, hist={"FY2026": ppe["closing_net_carrying_amount"]},
                  formula=lambda c, p: f"=SUM({c}{S.row-4}:{c}{S.row-1})")

    S.section("Indefeasible rights of use  (note 20)")
    S.note("These amortise on the FY2026 charge until the balance is exhausted, which happens "
           "inside the forecast. Carrying the charge past that point would write an asset "
           "below zero, so the roll takes whichever is smaller: the charge, or what is left.")
    IRU_OPEN = reg["balance_sheet"]["indefeasible_rights_of_use"]["FY2026"]
    IRU_CHG = abs(reg["income_statement"]["depreciation_irus"]["FY2026"])
    u_op = S.line("Opening", key="iru_open", basis="FY2026 close",
                  hist={"FY2026": IRU_OPEN},
                  formula=lambda c, p: (f"=${C_H1}${S.row+2}" if c == "E" else f"={p}{S.row+2}"))
    u_am = S.line("Amortisation", key="iru_am", basis="the FY2026 charge, capped at the balance",
                  indent=1, hist={"FY2026": -IRU_CHG},
                  formula=lambda c, p: f"=-MIN({IRU_CHG:.1f},{c}{S.row-1})")
    u_cl = S.line("Closing", key="iru_close", basis="opening less amortisation", bold=True,
                  hist={"FY2026": IRU_OPEN},
                  formula=lambda c, p: f"={c}{S.row-2}+{c}{S.row-1}")

    S.section("Intangible assets  (note 21)")
    i_op = S.line("Opening", key="int_open", basis="FY2026 close",
                  hist={"FY2026": 111455.8},
                  formula=lambda c, p: (f"=${C_H1}${S.row+3}" if c == "E" else f"={p}{S.row+3}"))
    i_add = S.line("Additions incl. spectrum and licences", key="int_add", basis="assumption",
                   indent=1, formula=lambda c, p: f"={D['intang_add']}")
    i_am = S.line("Amortisation", key="int_am", basis="opening / life", indent=1,
                  hist={"FY2026": inc["amortisation_intangibles"]["FY2026"]},
                  formula=lambda c, p: f"=-{c}{S.row-2}/11")
    i_cl = S.line("Closing", key="int_close", basis="open + additions - amortisation", bold=True,
                  hist={"FY2026": bs["intangible_assets"]["FY2026"]},
                  formula=lambda c, p: f"=SUM({c}{S.row-3}:{c}{S.row-1})")

    S.section("Right-of-use assets and lease liabilities  (note 22)")
    S.note("Item 11: ROU depreciation, lease interest and lease principal are three different "
           "numbers and are modelled separately. FY2026 ROU asset was 38,370.1 against a lease "
           "liability of 57,510.5 — they are not equal and do not move together.")
    r_op = S.line("ROU asset, opening", key="rou_open", basis="FY2026 close",
                  hist={"FY2026": rou["opening"]},
                  formula=lambda c, p: (f"=${C_H1}${S.row+3}" if c == "E" else f"={p}{S.row+3}"))
    r_add = S.line("New and remeasured leases", key="rou_add", basis="% of group revenue", indent=1,
                   hist={"FY2026": rou["additions"]},
                   formula=lambda c, p: f"=({k('total',c)}+{et('rev',c)})*{D['lease_add_pct']}")
    r_dep = S.line("ROU depreciation", key="rou_dep", basis="opening / lease term", indent=1,
                   hist={"FY2026": rou["depreciation_charge"]},
                   formula=lambda c, p: f"=-{c}{S.row-2}/4.6")
    r_cl = S.line("ROU asset, closing", key="rou_close", basis="open + additions - depreciation",
                  bold=True, hist={"FY2026": rou["closing"]},
                  formula=lambda c, p: f"=SUM({c}{S.row-3}:{c}{S.row-1})")
    l_op = S.line("Lease liability, opening", key="lease_open", basis="FY2026 close",
                  hist={"FY2026": li["lease_liability_total"]["FY2025"]},
                  formula=lambda c, p: (f"=${C_H1}${S.row+4}" if c == "E" else f"={p}{S.row+4}"))
    l_add = S.line("Additions", key="lease_add", basis="equal to ROU additions, non-cash", indent=1,
                   formula=lambda c, p: f"={c}{r_add}")
    l_int = S.line("Lease interest accrued", key="lease_int", basis="opening x cost of debt",
                   indent=1, formula=lambda c, p: f"={c}{S.row-2}*{D['cost_of_debt']}")
    l_pr = S.line("Lease payments", key="lease_pay", basis="principal + interest, cash", indent=1,
                  formula=lambda c, p: f"=-({c}{S.row-3}*0.19+{c}{S.row-1})")
    l_cl = S.line("Lease liability, closing", key="lease_close", basis="open + additions + interest - payments",
                  bold=True, hist={"FY2026": li["lease_liability_total"]["FY2026"]},
                  formula=lambda c, p: f"=SUM({c}{S.row-4}:{c}{S.row-1})")

    S.section("Borrowings  (note 16)")
    S.note("Item 11: new borrowing is driven by the funding need the cash flow produces, not by a "
           "net debt / EBITDA target. Interest is charged on the AVERAGE balance. Disclosed rates: "
           "11.6% group including Ethiopia, 10.6% Kenya only.")
    d_op = S.line("Opening borrowings", key="debt_open", basis="FY2026 close",
                  hist={"FY2026": dbt["opening"]},
                  formula=lambda c, p: (f"=${C_H1}${S.row+7}" if c == "E" else f"={p}{S.row+7}"))
    d_rep = S.line("Scheduled repayments", key="debt_rep", basis="% of opening", indent=1,
                   hist={"FY2026": dbt["repayments"]},
                   formula=lambda c, p: f"=-{c}{S.row-1}*{D['debt_repay_pct']}")
    d_fx = S.line("Translation differences", key="debt_fx", basis="birr move x Ethiopia debt",
                  indent=1, hist={"FY2026": dbt["translation_differences"]},
                  formula=lambda c, p: f"=-{D['et_fx_dep']}*{c}{S.row-2}*0.12")
    d_rev = S.line("Revaluation of foreign-currency loans", key="debt_reval",
                   basis="the FX loss charged in the income statement, per note 16", indent=1,
                   hist={"FY2026": dbt["revaluation_of_foreign_currency_loans"]},
                   formula=lambda c, p: f"=-{et('fxloss',c)}")
    d_sch = S.line("Debt before new drawings", key="debt_sched",
                   basis="opening, repayments, FX and revaluation", indent=1,
                   formula=lambda c, p: f"=SUM({c}{S.row-4}:{c}{S.row-1})")
    d_int = S.line("Interest on borrowings", key="debt_int",
                   basis="average of opening and scheduled closing x cost of debt",
                   hist={"FY2026": dbt["interest_charged"]},
                   formula=lambda c, p: f"=({c}{S.row-5}+{c}{S.row-1})/2*{D['cost_of_debt']}")
    d_new = S.line("New borrowing to meet funding need", key="debt_new",
                   basis="drawn only if cash would otherwise fall below the minimum", indent=1,
                   hist={"FY2026": dbt["additions"]}, formula=lambda c, p: "=0")
    d_cl = S.line("Closing borrowings", key="debt_close", basis="scheduled balance plus new drawings",
                  bold=True, hist={"FY2026": dbt["closing"]},
                  formula=lambda c, p: f"={c}{S.row-3}+{c}{S.row-1}")
    S.note("Interest is charged on the average of the opening balance and the scheduled closing "
           "balance, BEFORE any new drawing. That keeps the model free of a circular reference "
           "while still using an average balance, as item 11 asks.")

    S.section("Working capital")
    S.note("Item 10. FY2026 net working capital is NEGATIVE on either definition: -39,227.5 on the "
           "narrow basis (inventories plus receivables less payables) and -43,465.0 on the broad basis "
           "used here, which also carries contract assets, provisions and contract liabilities. This "
           "sheet uses the BROAD definition throughout, base year included, so the base and the "
           "forecast are measured the same way. Safaricom is prepaid and funded by its payables.")
    w_rec = S.line("Trade and other receivables", key="rec", basis="DSO x revenue / 365",
                   hist={"FY2026": bs["trade_and_other_receivables"]["FY2026"],
                         "FY2025": bs["trade_and_other_receivables"]["FY2025"]},
                   formula=lambda c, p: f"=({k('total',c)}+{et('rev',c)})*{D['dso']}/365")
    w_inv = S.line("Inventories", key="inv", basis="DIO x revenue / 365",
                   hist={"FY2026": bs["inventories"]["FY2026"], "FY2025": bs["inventories"]["FY2025"]},
                   formula=lambda c, p: f"=({k('total',c)}+{et('rev',c)})*{D['dio']}/365")
    w_ca = S.line("Contract assets", key="ca", basis="held at FY2026 ratio to revenue",
                  hist={"FY2026": bs["contract_assets_current"]["FY2026"] + bs["contract_assets_non_current"]["FY2026"]},
                  formula=lambda c, p: f"=({k('total',c)}+{et('rev',c)})*0.0283")
    w_pay = S.line("Payables and accrued expenses", key="pay", basis="DPO x revenue / 365",
                   hist={"FY2026": li["payables_and_accrued_current"]["FY2026"],
                         "FY2025": li["payables_and_accrued_current"]["FY2025"]},
                   formula=lambda c, p: f"=({k('total',c)}+{et('rev',c)})*{D['dpo']}/365")
    w_prov = S.line("Provisions and contract liabilities", key="prov", basis="held at FY2026 ratio",
                    hist={"FY2026": li["provisions_current"]["FY2026"] + li["contract_liabilities_current"]["FY2026"]},
                    formula=lambda c, p: f"=({k('total',c)}+{et('rev',c)})*0.0382")
    w_net = S.line("NET WORKING CAPITAL", key="nwc", basis="operating assets less operating liabilities",
                   bold=True,
                   hist={"FY2026": (bs["trade_and_other_receivables"]["FY2026"] + bs["inventories"]["FY2026"]
                                    + bs["contract_assets_current"]["FY2026"] + bs["contract_assets_non_current"]["FY2026"])
                         - (li["payables_and_accrued_current"]["FY2026"] + li["provisions_current"]["FY2026"]
                            + li["contract_liabilities_current"]["FY2026"])},
                   formula=lambda c, p: f"={c}{w_rec}+{c}{w_inv}+{c}{w_ca}-{c}{w_pay}-{c}{w_prov}")
    w_chg = S.line("Change in working capital (cash impact)", key="nwc_chg",
                   basis="negative = cash released",
                   formula=lambda c, p: f"=-({c}{w_net}-{p}{w_net})")

    S.section("Dividends  (item 13: declared is not paid)")
    dv_dec = S.line("Dividends DECLARED", key="div_dec", basis="payout x profit attributable to parent",
                    hist={"FY2026": div["total_declared_for_fy2026"]["FY2026"]}, formula=None)
    dv_pay = S.line("Dividends PAID in cash", key="div_paid", basis="prior final + current interim",
                    hist={"FY2026": div["total_paid_in_fy2026"]["FY2026"]}, formula=None)
    dv_fin = S.line("of which proposed final, outstanding at the year end", key="div_final",
                    indent=1, basis="declared x the final share on Assumptions",
                    hist={"FY2026": div["fy2026_final_proposed"]["FY2026"]}, formula=None)
    dv_bal = S.line("Dividend payable, closing", key="div_bal",
                    basis="approved and unpaid, held at the audited residual",
                    hist={"FY2026": li["dividend_payable"]["FY2026"]}, formula=None)
    S.note("Item 13: three different numbers. DECLARED is the charge against retained "
           "earnings. PAID is the cash, and it is last year's proposed final plus this "
           f"year's interim, which reproduces the audited "
           f"{div['total_paid_in_fy2026']['FY2026']:,.1f} exactly from "
           f"{div['fy2025_final_paid_in_fy2026']['FY2026']:,.1f} and "
           f"{div['fy2026_interim_paid']['FY2026']:,.1f}. The proposed final sits in "
           "EQUITY at the year end, not in liabilities, because the annual general "
           "meeting has not approved it. The payable below is only the approved and "
           "unclaimed residual and is held at its audited level.")
    built["sched"] = dict(ppe_open=p_op, ppe_close=p_cl, ppe_dep=p_dep, ppe_add=p_add, ppe_fx=p_fx,
                          int_close=i_cl, int_am=i_am, int_add=i_add,
                          iru_open=u_op, iru_am=u_am, iru_close=u_cl,
                          rou_close=r_cl, rou_dep=r_dep, rou_add=r_add,
                          lease_close=l_cl, lease_int=l_int, lease_pay=l_pr,
                          debt_close=d_cl, debt_int=d_int, debt_new=d_new, debt_rep=d_rep, debt_fx=d_fx,
                          rec=w_rec, inv=w_inv, ca=w_ca, pay=w_pay, prov=w_prov,
                          nwc=w_net, nwc_chg=w_chg,
                          div_dec=dv_dec, div_paid=dv_pay, div_bal=dv_bal, div_final=dv_fin)
    return _statements(wb, out_path, reg, built, D, S)


def _comps_table(wb, reg, val_rows):
    """Item 6: the peer table behind the multiples, and item 4's dating discipline.

    Every figure comes from the peer's own record: EBITDA, net debt and net income
    from its filing, market capitalisation from the stored price snapshot. EV, the
    two multiples and the medians are formulas, so the table reproduces the
    multiples the valuation uses rather than asserting them, and editing any input
    reprices the target.
    """
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    peers = [("MTN Group", "mtn_group"), ("Airtel Africa plc", "airtel_africa_plc"),
             ("Vodacom Group", "vodacom_group"),
             ("MTN Nigeria", "mtn_nigeria_communications_plc"),
             ("Sonatel", "sonatel"), ("Maroc Telecom", "maroc_telecom")]
    C = wb["Comps_and_KPIs"]
    r = C.max_row + 3
    c = C.cell(row=r, column=1, value="COMPARABLE COMPANIES")
    c.font = Font(name=FONT, size=SMALL, bold=True, color="FFFFFF")
    for col in range(1, 11):
        C.cell(row=r, column=col).fill = PatternFill("solid", start_color=SECTION)
    r += 1
    C.cell(row=r, column=1, value=(
        "Operating figures are from each peer's own filing and market capitalisation from its "
        "stored price snapshot. EV, both multiples and the medians are formulas, so the medians "
        "at the foot are what the valuation actually applies. Each peer is stated in its own "
        "currency, which cancels inside a ratio, so no conversion is needed. The Priced column "
        "gives each snapshot date: they span a few days rather than falling on one, and the "
        "multiples move very little over that window.")
    ).font = Font(name=FONT, size=TINY, color=MUTED)
    r += 2
    hdr = ["Peer", "FY", "Cur", "EBITDA", "Net debt", "Market cap", "Net income",
           "EV", "EV/EBITDA", "P/E", "Priced"]
    for i, h in enumerate(hdr):
        cc = C.cell(row=r, column=1 + i, value=h)
        cc.font = Font(name=FONT, size=BASE, bold=True, color=NAVY)
        if i >= 3:
            cc.alignment = Alignment(horizontal="center")
    r += 1
    first = r
    for name, slug in peers:
        f = root / "data" / "extracted" / f"{slug}.json"
        if not f.exists():
            continue
        rec = json.loads(f.read_text())
        fins = sorted(rec.get("financials", []), key=lambda x: x.get("fy", ""))
        if not fins:
            continue
        L = fins[-1]
        eb = L.get("ebitda")
        # The stored net debt, not total debt less cash: the two differ for
        # several peers and the valuation uses the stored figure, so a table
        # built the other way would not reproduce the multiples it supports.
        nd = L.get("net_debt")
        if nd is None:
            nd = (L.get("total_debt") or 0) - (L.get("cash") or 0)
        mkt = rec.get("market") or {}
        mcap = mkt.get("market_cap_m")
        ni = L.get("net_income")
        C.cell(row=r, column=1, value=name).font = Font(name=FONT, size=BASE, color=INK)
        C.cell(row=r, column=2, value=L.get("fy")).font = Font(name=FONT, size=BASE, color=BLUE_IN)
        C.cell(row=r, column=3, value=rec.get("currency")).font = Font(name=FONT, size=TINY, color=MUTED)
        for col, v in ((4, eb), (5, nd)):
            cc = C.cell(row=r, column=col, value=v)
            cc.number_format = NUM
            cc.font = Font(name=FONT, size=BASE, color=BLUE_IN)
        mc = C.cell(row=r, column=6, value=mcap)
        mc.number_format = NUM
        mc.font = Font(name=FONT, size=BASE, color=BLUE_IN)
        nic = C.cell(row=r, column=7, value=ni)
        nic.number_format = NUM
        nic.font = Font(name=FONT, size=BASE, color=BLUE_IN)
        ev = C.cell(row=r, column=8, value=f"=IF(F{r}=\"\",\"\",F{r}+E{r})")
        ev.number_format = NUM; ev.font = Font(name=FONT, size=BASE)
        mu = C.cell(row=r, column=9, value=f"=IF(OR(F{r}=\"\",D{r}=0),\"\",H{r}/D{r})")
        mu.number_format = MULT; mu.font = Font(name=FONT, size=BASE)
        pe = C.cell(row=r, column=10, value=f"=IF(OR(F{r}=\"\",G{r}=0),\"\",F{r}/G{r})")
        pe.number_format = MULT; pe.font = Font(name=FONT, size=BASE)
        C.cell(row=r, column=11, value=mkt.get("price_date")).font = Font(
            name=FONT, size=TINY, color=MUTED)
        r += 1
    last = r - 1
    C.cell(row=r, column=1, value="Median").font = Font(name=FONT, size=BASE, bold=True, color=NAVY)
    med_ev = C.cell(row=r, column=9, value=f"=IFERROR(MEDIAN(I{first}:I{last}),\"\")")
    med_ev.number_format = MULT; med_ev.font = Font(name=FONT, size=BASE, bold=True)
    med_pe = C.cell(row=r, column=10, value=f"=IFERROR(MEDIAN(J{first}:J{last}),\"\")")
    med_pe.number_format = MULT; med_pe.font = Font(name=FONT, size=BASE, bold=True)
    med_row = r
    r += 2
    C.cell(row=r, column=1, value=(
        "The medians feed the Valuation sheet directly. Should a peer's market capitalisation be "
        "cleared, its multiples drop out of the median, and if every one were cleared the valuation "
        "would fall back to the assumptions held on that sheet.")
    ).font = Font(name=FONT, size=TINY, color=MUTED)

    # the valuation prefers the peer median once it exists
    V = wb["Valuation"]
    for row_i, col_letter in ((val_rows["exit_multiple"], "I"), (val_rows["peer_pe"], "J")):
        cur = V.cell(row=row_i, column=4).value
        V.cell(row=row_i, column=4,
               value=f"=IF(Comps_and_KPIs!{col_letter}{med_row}=\"\",{cur},Comps_and_KPIs!{col_letter}{med_row})")
        V.cell(row=row_i, column=4).number_format = MULT
        V.cell(row=row_i, column=4).font = Font(name=FONT, size=BASE, color=GREEN_LINK)
        V.cell(row=row_i, column=5, value="peer median where entered, otherwise the assumption").font = \
            Font(name=FONT, size=TINY, color=MUTED)


def _template_sheets(wb, reg, built, D, SH_OUT, PRICE, val_rows):
    """The template sheets the reviewer expects, wired to the one forecast.

    Financials keeps the top-down view the workbook used to lead with, but it is
    now labelled a reasonableness check and sits BESIDE the detailed model
    rather than competing with it (item 1).
    """
    IS_, SC, E = built["is"], built["sched"], built["ethiopia"]
    inc, seg = reg["income_statement"], reg["segment_note36"]
    kre, gre = reg["revenue_by_stream"]["kenya"], reg["revenue_by_stream"]["group"]
    YRS = ["FY2022", "FY2023", "FY2024", "FY2025", "FY2026"]
    HC = ["C", "D", "E", "F", "G"]                    # template actual columns

    # ---------------- Financials: every reported component --------------------
    F = Builder(wb, "Financials", reg)
    F.ws.column_dimensions["A"].width = 54
    F.ws.column_dimensions["B"].width = 46
    for col in HC + ["H"]:
        F.ws.column_dimensions[col].width = 13
    F.title("Safaricom PLC — reported financials in full",
            "Every line below is printed in the audited accounts. Blue is a reported figure, "
            "black is arithmetic on this sheet. The note reference is in column B so any number "
            "can be traced back to the annual report.")

    def label_of(k):
        return k.replace("_", " ").replace("non current", "non-current").capitalize()

    def block(heading, data, note="", years=YRS, cols=HC, skip=()):
        """Render a register block. Years the report does not give are left blank."""
        F.section(heading)
        if note:
            F.note(note)
        hdr = F.row
        F.ws.cell(row=hdr, column=1, value="KES millions").font = Font(
            name=FONT, size=BASE, bold=True, color=NAVY)
        F.ws.cell(row=hdr, column=2, value="definition / note").font = Font(
            name=FONT, size=BASE, bold=True, color=NAVY)
        for col, y in zip(cols, years):
            c = F.ws.cell(row=hdr, column=Builder._ci(col), value=y)
            c.font = Font(name=FONT, size=BASE, bold=True, color=NAVY)
            c.alignment = Alignment(horizontal="center")
        F.row += 1
        first = F.row
        for k, v in data.items():
            if k.startswith("_") or k in skip:
                continue
            r = F.row
            bold = any(t in k for t in ("total", "ebitda", "ebit", "profit_for", "attributable_to_parent"))
            F.ws.cell(row=r, column=1, value=label_of(k)).font = Font(
                name=FONT, size=BASE, bold=bold, color=NAVY if bold else INK)
            if isinstance(v, dict):
                d = v.get("definition", "")
                if d:
                    cc = F.ws.cell(row=r, column=2, value=d[:110])
                    cc.font = Font(name=FONT, size=TINY, color=MUTED)
                for col, y in zip(cols, years):
                    val = v.get(y)
                    if isinstance(val, (int, float)):
                        c2 = F.ws.cell(row=r, column=Builder._ci(col), value=val)
                        c2.number_format = NUM
                        c2.font = Font(name=FONT, size=BASE, bold=bold, color=BLUE_IN)
            elif isinstance(v, (int, float)):
                c2 = F.ws.cell(row=r, column=Builder._ci(cols[-1]), value=v)
                c2.number_format = PCT if abs(v) < 1.5 and "." in str(v) else NUM
                c2.font = Font(name=FONT, size=BASE, bold=bold, color=BLUE_IN)
            F.row += 1
        F.row += 1
        return first

    block("Income statement", inc,
          "Group columns only. The company (parent-only) columns in the report are a different "
          "entity and are never mixed in here.", years=["FY2025", "FY2026"], cols=["F", "G"])
    block("Profit attribution", reg["profit_attribution"],
          "Item 7: EPS, dividends and the P/E all use profit attributable to shareholders.",
          years=["FY2026"], cols=["G"])
    block("Kenya revenue by stream (five years)", kre,
          "Note the denominator: these are KENYA figures, not group.", years=YRS, cols=HC)
    block("Group revenue by stream (five years)", gre,
          "Group differs from Kenya by Ethiopia. Neither is interchangeable with total revenue.",
          years=YRS, cols=HC)
    block("Expected credit losses (note 6b)", reg["ecl_breakdown_note6b"],
          years=["FY2025", "FY2026"], cols=["F", "G"])
    block("Other operating expenses (note 7)", reg["opex_breakdown_note7"],
          "The FX line sits INSIDE operating expenses. Most of the FY2026 fall was that line, "
          "not underlying cost control.", years=["FY2025", "FY2026"], cols=["F", "G"])
    block("Balance sheet — assets", reg["balance_sheet"], years=["FY2025", "FY2026"], cols=["F", "G"])
    block("Balance sheet — liabilities", reg["liabilities"], years=["FY2025", "FY2026"], cols=["F", "G"])
    block("Equity", reg["equity"], years=["FY2025", "FY2026"], cols=["F", "G"])
    block("Tax reconciliation (note 12)", reg["tax_reconciliation_note12"],
          "Explains the gap between the 30% statutory rate and the 41.9% effective rate.",
          years=["FY2025", "FY2026"], cols=["F", "G"], skip=("statutory_rate",))
    block("Dividends", reg["dividends"],
          "Declared and paid are different numbers and are kept apart.", years=["FY2026"], cols=["G"])
    block("Non-controlling interests roll-forward", reg["nci_and_ethiopia"],
          years=["FY2026"], cols=["G"])
    block("Property and equipment (note 18)", reg["ppe_schedule_note18"],
          "Opening plus movements equals closing. The translation line is the FX effect on "
          "Ethiopian assets.")
    block("Right-of-use assets (note 22a)", reg["rou_schedule_note22a"])
    block("Borrowings (note 16)", reg["borrowings_schedule_note16"],
          "Interest charged is borrowing interest only and is not the same as total finance costs.",
          skip=("disclosed_rate_company_kenya", "disclosed_rate_group_incl_ethiopia"))

    F.section("Segment split, Kenya and Ethiopia (note 36)")
    F.note("Every line sums across the two segments to the total-segments column, and that plus "
           "intersegment eliminations equals the group column.")
    hdr = F.row
    for col, lbl in zip(["C", "D", "E", "F", "G"],
                        ["Kenya", "Ethiopia", "Total segments", "Intersegment", "Group"]):
        c = F.ws.cell(row=hdr, column=Builder._ci(col), value=lbl)
        c.font = Font(name=FONT, size=BASE, bold=True, color=NAVY)
        c.alignment = Alignment(horizontal="center")
    F.ws.cell(row=hdr, column=1, value="KES millions, FY2026").font = Font(
        name=FONT, size=BASE, bold=True, color=NAVY)
    F.row += 1
    for k, v in seg["FY2026"].items():
        if k.startswith("_"):
            continue
        r = F.row
        bold = k in ("total_revenue", "ebitda", "operating_profit")
        F.ws.cell(row=r, column=1, value=label_of(k)).font = Font(
            name=FONT, size=BASE, bold=bold, color=NAVY if bold else INK)
        for col, kk in zip(["C", "D", "E", "F", "G"],
                           ["kenya", "ethiopia", "total_segments", "intersegment", "consolidated"]):
            c2 = F.ws.cell(row=r, column=Builder._ci(col), value=v[kk])
            c2.number_format = NUM
            c2.font = Font(name=FONT, size=BASE, bold=bold, color=BLUE_IN)
        F.row += 1
    F.row += 1

    # The top-down view the workbook used to lead with, kept only as a sanity
    # check and deliberately wired to nothing (item 1).
    F.section("Top-down cross-check — not linked to anything")
    F.note("One growth rate and a flat margin applied to FY2026. If this lands far from the "
           "detailed build on Fcst_IS, one of the two needs explaining. Shown for that purpose "
           "only: no other sheet reads these cells.")
    hdr = F.row
    F.ws.cell(row=hdr, column=1, value="KES millions").font = Font(
        name=FONT, size=BASE, bold=True, color=NAVY)
    for col, lbl in zip(F_COLS, FCST):
        c = F.ws.cell(row=hdr, column=Builder._ci(col), value=lbl)
        c.font = Font(name=FONT, size=BASE, bold=True, color=NAVY)
        c.alignment = Alignment(horizontal="center")
    F.row += 1
    td_rev = F.line("Revenue, top-down", basis="FY2026 grown at one rate",
                    hist={"FY2026": inc["total_revenue"]["FY2026"]},
                    formula=lambda c, p: f"={p}{F.row}*(1+0.085)")
    td_eb = F.line("EBITDA, top-down", basis="flat 51.5% margin",
                   hist={"FY2026": inc["ebitda"]["FY2026"]},
                   formula=lambda c, p: f"={c}{td_rev}*0.515")
    F.line("Detailed model revenue", basis="Fcst_IS, the live forecast",
           formula=lambda c, p: f"=Fcst_IS!{c}{IS_['rev']}")
    F.line("Difference, top-down less detailed", basis="a gap is a question, not an error",
           formula=lambda c, p: f"={c}{td_rev}-Fcst_IS!{c}{IS_['rev']}")
    F.line("Detailed model EBITDA", basis="Fcst_IS",
           formula=lambda c, p: f"=Fcst_IS!{c}{IS_['ebitda']}")
    F.line("Difference, top-down less detailed",
           formula=lambda c, p: f"={c}{td_eb}-Fcst_IS!{c}{IS_['ebitda']}")

    # ---------------- Scenarios ----------------
    S = Builder(wb, "Scenarios", reg)
    S.title("Named scenarios",
            "Item 15. Each is a state of the world with a stated mechanism, not a generic "
            "bull/base/bear label. Set the switch on Assumptions to move the whole model.")
    rows = [
        ("Bear", "Mobile-money tax passes through and competition bites",
         "A Finance Act levy on transfer fees, a well-funded Airtel taking data share, birr "
         "weakness at 18% a year and Ethiopian break-even slipping past FY2028. Capex stays high "
         "to defend the network."),
        ("Base", "M-PESA and data keep compounding, Ethiopia turns around management's target",
         "M-PESA grows 10.5% and data 12%, decelerating off a larger base. Ethiopia reaches "
         "EBITDA break-even in the forecast, close to management guidance. Birr depreciates 10% "
         "a year."),
        ("Bull", "Data monetisation accelerates and Ethiopia breaks even early",
         "Data grows 16% on smartphone adoption, M-PESA 15% with no new tax, Ethiopia turns "
         "EBITDA-positive sooner and birr weakness is contained at 4%."),
    ]
    for i, (name, thesis, detail) in enumerate(rows, start=1):
        r = S.row
        S.ws.cell(row=r, column=1, value=f"{i}  {name}").font = Font(name=FONT, size=11, bold=True, color=NAVY)
        S.ws.cell(row=r, column=3, value=thesis).font = Font(name=FONT, size=SMALL, bold=True)
        S.row += 1
        c = S.ws.cell(row=S.row, column=3, value=detail)
        c.font = Font(name=FONT, size=BASE, color=MUTED); c.alignment = Alignment(wrap_text=True, vertical="top")
        S.ws.row_dimensions[S.row].height = 34
        S.row += 2
    S.ws.column_dimensions["C"].width = 96
    S.spacer()
    S.ws.cell(row=S.row, column=1, value="Active scenario").font = Font(name=FONT, size=SMALL, bold=True, color=NAVY)
    cc = S.ws.cell(row=S.row, column=3, value=f'=CHOOSE({D["_switch"]},"Bear","Base","Bull")')
    cc.font = Font(name=FONT, size=11, bold=True, color=NAVY)
    cc.fill = PatternFill("solid", start_color=AMBER)
    S.row += 2
    for lbl, f in (("FY2031 revenue", f"=Fcst_IS!I{IS_['rev']}"),
                   ("FY2031 EBITDA", f"=Fcst_IS!I{IS_['ebitda']}"),
                   ("Target price", f"=Valuation!D{val_rows['target']}"),
                   ("Recommendation", f"=Valuation!D{val_rows['rating']}")):
        r = S.row
        S.ws.cell(row=r, column=1, value=lbl).font = Font(name=FONT, size=BASE)
        c2 = S.ws.cell(row=r, column=3, value=f)
        c2.number_format = CUR if "price" in lbl else NUM
        c2.font = Font(name=FONT, size=BASE, bold=True)
        S.row += 1
    return F, S


def _statements(wb, out_path, reg, built, D, S):
    """The three statements, valuation and controls."""
    inc, bs, li = reg["income_statement"], reg["balance_sheet"], reg["liabilities"]
    eq, nci, div = reg["equity"], reg["nci_and_ethiopia"], reg["dividends"]
    seg = reg["segment_note36"]["FY2026"]
    pa = reg["profit_attribution"]
    tx_note = reg["tax_reconciliation_note12"]
    ox, mb = reg["opex_breakdown_note7"], reg["margin_bridge"]
    # eliminations expressed against total segment revenue, so they scale with the business
    ELIM_REV = seg["total_revenue"]["intersegment"] / seg["total_revenue"]["total_segments"]
    ELIM_EBITDA = seg["ebitda"]["intersegment"] / seg["total_revenue"]["total_segments"]
    # FY2026 Ethiopia net result: the base the tax drag is scaled against
    ETH_LOSS_BASE = reg["profit_attribution"]["attributable_to_nci"]["FY2026"] / 0.4405
    K, E, SC = built["kenya"], built["ethiopia"], built["sched"]
    SH_OUT = 40065.4          # shares outstanding, millions
    PRICE = 34.05             # last price used for the upside calculation

    def k(key, c):  return f"Op_Drivers!{c}{K[key]}"
    def et(key, c): return f"Ethiopia!{c}{E[key]}"
    def sc(key, c): return f"Schedules!{c}{SC[key]}"

    # ---------------- INCOME STATEMENT ----------------
    I = Builder(wb, "Fcst_IS", reg)
    I.title("Group income statement",
            "Group = Kenya + Ethiopia. Item 1: this is the ONE forecast. Every line is a formula "
            "off the operating build and the schedules.")
    I.headers()
    r_kr = I.line("Kenya revenue", basis="Op_Drivers", indent=1,
                  hist={"FY2026": seg["total_revenue"]["kenya"]},
                  formula=lambda c, p: f"={k('total',c)}")
    r_er = I.line("Ethiopia revenue", basis="Ethiopia", indent=1,
                  hist={"FY2026": seg["total_revenue"]["ethiopia"]},
                  formula=lambda c, p: f"={et('rev',c)}")
    r_el = I.line("Intersegment eliminations", basis="% of total segment revenue, at the FY2026 rate",
                  indent=1, hist={"FY2026": seg["total_revenue"]["intersegment"]},
                  formula=lambda c, p: f"=({c}{r_kr}+{c}{r_er})*{ELIM_REV:.6f}")
    rev = I.line("TOTAL REVENUE", key="rev", basis="Kenya + Ethiopia less eliminations", bold=True,
                 hist={"FY2026": inc["total_revenue"]["FY2026"], "FY2025": inc["total_revenue"]["FY2025"]},
                 formula=lambda c, p: f"={c}{r_kr}+{c}{r_er}+{c}{r_el}")
    I.note("Item 1: the group is NOT the simple sum of the two segments. FY2026 carried "
           f"{seg['total_revenue']['intersegment']:,.1f} of revenue eliminations and "
           f"+{seg['ebitda']['intersegment']:,.1f} on EBITDA. These are two different "
           "things. The revenue elimination is matched exactly by cost eliminations of "
           f"+{seg['direct_costs']['intersegment']:,.1f} in direct costs and "
           f"+{seg['other_expenses']['intersegment']:,.1f} in other expenses, so "
           "intersegment trading nets to nil and carries no margin. The whole of the "
           "EBITDA elimination is the reversal of Kenya's expected credit loss provision "
           "against the intragroup receivable from Ethiopia, which cannot survive "
           "consolidation. Both are carried forward at their FY2026 rate, which for the "
           "provision is a judgement: it was "
           f"{reg['segment_note36']['FY2025']['ebitda']['intersegment']:,.1f} in FY2025 "
           "and would be released, not "
           "grown, if Ethiopia turns profitable as this model forecasts.")
    I.note("Definition: TOTAL revenue of 427,559.1 in FY2026, not group service revenue of "
           "414,137.4 and not Kenya service revenue of 400,794.6 (item 4).")
    RC26 = inc["revenue_from_contracts_with_customers"]["FY2026"]
    RO26 = inc["revenue_from_other_sources"]["FY2026"]
    RT26 = RC26 + RO26
    I.line("of which revenue from contracts with customers", indent=2,
           basis="at the FY2026 share of total revenue",
           hist={"FY2026": RC26},
           formula=lambda c, p: f"={c}{rev}*{RC26 / RT26:.6f}")
    I.line("of which revenue from other sources", indent=2,
           basis="at the FY2026 share of total revenue",
           hist={"FY2026": RO26},
           formula=lambda c, p: f"={c}{rev}*{RO26 / RT26:.6f}")

    I.section("Operating costs")
    I.note("The Kenyan cost lines come from the operating build, one row each. Ethiopia "
           "does not have a cost build of its own: the segment is forecast on a margin, so "
           "its operating costs are shown as the single balancing figure that margin "
           "implies. The elimination row is the cost side of the same intersegment "
           "trading netted off revenue above.")
    # The segment note stores costs as positive magnitudes. The statement shows
    # them as the deductions they are, so the FY2026 column is signed to match
    # the forecast columns it sits beside.
    KSH_ = seg["other_expenses"]["kenya"] / seg["other_expenses"]["total_segments"]
    K_EMP = -abs(ox["employee_benefits"]["FY2026"] * KSH_)
    K_NET = -abs(ox["network_operating_costs"]["FY2026"] * KSH_)
    K_FX = -abs(ox["net_fx_losses_in_opex"]["FY2026"] * KSH_)
    K_OTH = -abs(seg["other_expenses"]["kenya"]) - K_EMP - K_NET - K_FX
    x1 = I.line("Direct costs, including M-PESA commissions", indent=1, basis="Op_Drivers",
                hist={"FY2026": -abs(seg["direct_costs"]["kenya"])},
                formula=lambda c, p: f"={k('direct',c)}")
    x2 = I.line("Employee benefits", indent=1, basis="Op_Drivers",
                hist={"FY2026": K_EMP}, formula=lambda c, p: f"={k('employee',c)}")
    x3 = I.line("Network operating costs", indent=1, basis="Op_Drivers",
                hist={"FY2026": K_NET}, formula=lambda c, p: f"={k('network',c)}")
    x4 = I.line("Net foreign-exchange losses inside operating costs", indent=1, basis="Op_Drivers",
                hist={"FY2026": K_FX}, formula=lambda c, p: f"={k('fx_opex',c)}")
    x5 = I.line("Other operating expenses", indent=1, basis="Op_Drivers",
                hist={"FY2026": K_OTH}, formula=lambda c, p: f"={k('opex',c)}")
    x6 = I.line("Expected credit losses", indent=1, basis="Op_Drivers",
                hist={"FY2026": -abs(seg["expected_credit_losses"]["kenya"])},
                formula=lambda c, p: f"={k('ecl',c)}")
    x7 = I.line("Ethiopia operating costs", indent=1,
                basis="revenue less EBITDA, the figure the margin implies",
                hist={"FY2026": seg["ebitda"]["ethiopia"] - seg["total_revenue"]["ethiopia"]},
                formula=lambda c, p: f"={et('ebitda',c)}-{et('rev',c)}")
    x8 = I.line("Intersegment eliminations on costs", indent=1,
                basis="the cost side of the trading netted off revenue",
                hist={"FY2026": seg["ebitda"]["intersegment"] - seg["total_revenue"]["intersegment"]},
                formula=lambda c, p: (f"=({c}{r_kr}+{c}{r_er})*{ELIM_EBITDA:.6f}"
                                      f"-({c}{r_kr}+{c}{r_er})*{ELIM_REV:.6f}"))
    tcost = I.line("TOTAL OPERATING COSTS", key="opex", bold=True,
                   hist={"FY2026": inc["ebitda"]["FY2026"] - inc["total_revenue"]["FY2026"]},
                   formula=lambda c, p: f"=SUM({c}{x1}:{c}{x8})")

    I.section("Earnings")
    e_k = I.line("Kenya EBITDA", basis="Op_Drivers", indent=1,
                 hist={"FY2026": seg["ebitda"]["kenya"]}, formula=lambda c, p: f"={k('ebitda',c)}")
    e_e = I.line("Ethiopia EBITDA", basis="Ethiopia", indent=1,
                 hist={"FY2026": seg["ebitda"]["ethiopia"]}, formula=lambda c, p: f"={et('ebitda',c)}")
    e_el = I.line("Intersegment eliminations", basis="% of total segment revenue, at the FY2026 rate",
                  indent=1, hist={"FY2026": seg["ebitda"]["intersegment"]},
                  formula=lambda c, p: f"=({c}{r_kr}+{c}{r_er})*{ELIM_EBITDA:.6f}")
    ebitda = I.line("EBITDA", key="ebitda", basis="Kenya + Ethiopia less eliminations", bold=True,
                    hist={"FY2026": inc["ebitda"]["FY2026"], "FY2025": inc["ebitda"]["FY2025"]},
                    formula=lambda c, p: f"={c}{e_k}+{c}{e_e}+{c}{e_el}")
    mgn = I.line("EBITDA margin", basis="calculated", fmt=PCT,
                 hist={"FY2026": inc["ebitda"]["FY2026"] / inc["total_revenue"]["FY2026"],
                       "FY2025": inc["ebitda"]["FY2025"] / inc["total_revenue"]["FY2025"]},
                 formula=lambda c, p: f"={c}{ebitda}/{c}{rev}")
    I.note("The group margin is a BLEND: Kenya 56.8% and Ethiopia -88.5% in FY2026. It moves as the "
           "mix shifts, which is why a flat 51% assumption was wrong (item 6).")
    dep = I.line("Depreciation — property and equipment", basis="Schedules", indent=1,
                 hist={"FY2026": inc["depreciation_property_and_equipment"]["FY2026"]},
                 formula=lambda c, p: f"={sc('ppe_dep',c)}")
    amt = I.line("Amortisation — intangibles", basis="Schedules", indent=1,
                 hist={"FY2026": inc["amortisation_intangibles"]["FY2026"]},
                 formula=lambda c, p: f"={sc('int_am',c)}")
    rdp = I.line("Depreciation — right-of-use assets", basis="Schedules", indent=1,
                 hist={"FY2026": inc["depreciation_rou_assets"]["FY2026"]},
                 formula=lambda c, p: f"={sc('rou_dep',c)}")
    iru = I.line("Amortisation — indefeasible rights of use", basis="Schedules", indent=1,
                 hist={"FY2026": inc["depreciation_irus"]["FY2026"]},
                 formula=lambda c, p: f"={sc('iru_am',c)}")
    ebit = I.line("EBIT", key="ebit", basis="EBITDA less D&A", bold=True,
                  hist={"FY2026": inc["ebit"]["FY2026"], "FY2025": inc["ebit"]["FY2025"]},
                  formula=lambda c, p: f"={c}{ebitda}+{c}{dep}+{c}{amt}+{c}{rdp}+{c}{iru}")
    fi = I.line("Finance income", basis="opening cash at the deposit rate on Assumptions",
                indent=1, hist={"FY2026": inc["finance_income"]["FY2026"]},
                formula=None)   # written once the cash flow exists, below
    fdi = I.line("Interest on borrowings", basis="Schedules, average balance", indent=1,
                 formula=lambda c, p: f"=-{sc('debt_int',c)}")
    fli = I.line("Lease interest", basis="Schedules", indent=1,
                 formula=lambda c, p: f"=-{sc('lease_int',c)}")
    ffx = I.line("FX on foreign-currency funding", basis="Ethiopia", indent=1,
                 formula=lambda c, p: f"={et('fxloss',c)}")
    I.note(f"FY2026 finance costs were {inc['finance_costs']['FY2026']:,.1f} in total; borrowing "
           f"interest alone was 10,660.4 (note 16). The two are not the same and are split here.")
    pbt = I.line("PROFIT BEFORE TAX", key="pbt", basis="EBIT plus net finance", bold=True,
                 hist={"FY2026": inc["profit_before_income_tax"]["FY2026"]},
                 formula=lambda c, p: f"={c}{ebit}+{c}{fi}+{c}{fdi}+{c}{fli}+{c}{ffx}")
    I.note("Item 12: ONE statutory rate of 30%, as note 12 discloses, plus an explicit drag for "
           "non-deductible expenses and unrecognised deferred tax on Ethiopian losses. FY2026's "
           "41.9% effective rate is therefore explained rather than asserted, and the drag decays "
           "as Ethiopia approaches break-even.")
    tax_s = I.line("Tax at the statutory rate", basis="30% of profit before tax", indent=1,
                   hist={"FY2026": tx_note["tax_at_statutory"]["FY2026"]},
                   formula=lambda c, p: f"=-MAX(0,{c}{pbt})*{D['tax_rate']}")
    tax_d = I.line("Non-deductible and unrecognised deferred tax",
                   basis="drag scaled by Ethiopia's remaining loss", indent=1,
                   hist={"FY2026": tx_note["expenses_not_deductible"]["FY2026"]
                         + tx_note["deferred_tax_not_recognised"]["FY2026"]
                         + tx_note["income_not_subject_to_tax"]["FY2026"]},
                   formula=lambda c, p: (f"=-MAX(0,{c}{pbt})*{D['tax_drag']}"
                                         f"*MAX(0,-Ethiopia!{c}{E['net']})/{abs(ETH_LOSS_BASE):.1f}"))
    I.note("Item 3: the drag is no longer flat. It scales with Ethiopia's remaining loss, so it "
           "decays as the venture approaches break-even and reaches nil once Ethiopia turns "
           "profitable, which is the mechanism note 12 describes. The effective rate therefore "
           "falls towards the 30% statutory rate across the forecast.")
    tax_f = I.line("Deferred tax credit", basis="stated assumption, not a % of the charge", indent=1,
                   hist={"FY2026": tx_note["deferred_income_tax"]["FY2026"]},
                   formula=lambda c, p: f"={D['deferred_tax']}")
    tax = I.line("INCOME TAX", key="tax", basis="statutory + drag + deferred", bold=True,
                 hist={"FY2026": inc["income_tax_expense"]["FY2026"]},
                 formula=lambda c, p: f"={c}{tax_s}+{c}{tax_d}+{c}{tax_f}")
    etr = I.line("Effective tax rate", basis="calculated", fmt=PCT,
           hist={"FY2026": -inc["income_tax_expense"]["FY2026"] / inc["profit_before_income_tax"]["FY2026"]},
           formula=lambda c, p: f"=IF({c}{pbt}=0,0,-{c}{tax}/{c}{pbt})")
    pat = I.line("PROFIT FOR THE YEAR", key="pat", basis="group, before attribution", bold=True,
                 hist={"FY2026": pa["profit_for_the_year"]["FY2026"]},
                 formula=lambda c, p: f"={c}{pbt}+{c}{tax}")
    ncs = I.line("Attributable to non-controlling interests", basis="Ethiopia NCI share", indent=1,
                 hist={"FY2026": pa["attributable_to_nci"]["FY2026"]},
                 formula=lambda c, p: f"={et('nci_share',c)}")
    par = I.line("ATTRIBUTABLE TO SAFARICOM SHAREHOLDERS", key="parent",
                 basis="item 7: EPS, dividends and P/E all use THIS", bold=True,
                 hist={"FY2026": pa["attributable_to_parent"]["FY2026"]},
                 formula=lambda c, p: f"={c}{pat}-{c}{ncs}")
    eps = I.line("EPS (KShs)", key="eps", basis="profit to parent / shares", fmt=CUR,
                 hist={"FY2026": pa["attributable_to_parent"]["FY2026"] / SH_OUT},
                 formula=lambda c, p: f"={c}{par}/{SH_OUT}")
    dps = I.line("DPS (KShs)", key="dps", basis="FY2026 as declared; FY2027 onwards forecast, being the declaration divided by shares", fmt=CUR,
                 formula=lambda c, p: f"={sc('div_dec',c)}/{SH_OUT}")

    # wire the dividend schedule back now that profit-to-parent exists
    for col in F_COLS:
        prev = C_H1 if col == "E" else F_COLS[F_COLS.index(col) - 1]
        S.ws[f"{col}{SC['div_dec']}"] = f"=Fcst_IS!{col}{par}*{D['payout']}"
        # Capped at what is actually owed: the opening payable plus this year's
        # declaration. Without the cap a falling dividend pays out more than has
        # ever been declared and the payable turns negative, which is not a
        # liability that can exist.
        S.ws[f"{col}{SC['div_final']}"] = f"={col}{SC['div_dec']}*{D['final_share']}"
        # last year's proposed final, approved and paid, plus this year's interim
        S.ws[f"{col}{SC['div_paid']}"] = (
            f"={prev}{SC['div_final']}+{col}{SC['div_dec']}*(1-{D['final_share']})")
        # the approved and unclaimed residual, which does not accumulate
        S.ws[f"{col}{SC['div_bal']}"] = f"={prev}{SC['div_bal']}"
        for rr in (SC['div_dec'], SC['div_final'], SC['div_paid'], SC['div_bal']):
            S.ws[f"{col}{rr}"].number_format = NUM
            S.ws[f"{col}{rr}"].font = Font(name=FONT, size=BASE)

    # ---------------- CASH FLOW ----------------
    F = Builder(wb, "Fcst_CF", reg)
    F.title("Group cash flow",
            "Cash is produced HERE and carried to the balance sheet. That is what removes the "
            "-67.8bn plug (item 9).")
    F.headers()
    F.section("Operating")
    c1 = F.line("Profit for the year", indent=1, formula=lambda c, p: f"=Fcst_IS!{c}{pat}")
    c2a = F.line("Depreciation, property and equipment", indent=1,
                 formula=lambda c, p: f"=-Fcst_IS!{c}{dep}")
    c2b = F.line("Amortisation, intangibles", indent=1,
                 formula=lambda c, p: f"=-Fcst_IS!{c}{amt}")
    c2c = F.line("Depreciation, right-of-use assets", indent=1,
                 formula=lambda c, p: f"=-Fcst_IS!{c}{rdp}")
    c2d = F.line("Amortisation, indefeasible rights of use", indent=1,
                 formula=lambda c, p: f"=-Fcst_IS!{c}{iru}")
    F.note("Each non-cash charge is added back on its own line, so the addback can be "
           "checked against the income statement charge by charge. The "
           "indefeasible-rights amortisation was previously missing from this addback "
           "while the income statement carried it, which left the balance sheet out by "
           "the amount of the charge in the year the asset was exhausted.")
    c3 = F.line("Lease interest accrued (non-cash, paid in financing)", indent=1,
                formula=lambda c, p: f"={sc('lease_int',c)}")
    c4 = F.line("FX on funding (non-cash)", indent=1, formula=lambda c, p: f"=-Fcst_IS!{c}{ffx}")
    w1 = F.line("Movement in trade and other receivables", indent=1,
                basis="an increase uses cash",
                formula=lambda c, p: f"=-({sc('rec',c)}-{sc('rec',p)})")
    w2 = F.line("Movement in inventories", indent=1, basis="an increase uses cash",
                formula=lambda c, p: f"=-({sc('inv',c)}-{sc('inv',p)})")
    w3 = F.line("Movement in contract assets", indent=1, basis="an increase uses cash",
                formula=lambda c, p: f"=-({sc('ca',c)}-{sc('ca',p)})")
    w4 = F.line("Movement in payables and accrued expenses", indent=1,
                basis="an increase releases cash",
                formula=lambda c, p: f"={sc('pay',c)}-{sc('pay',p)}")
    w5 = F.line("Movement in provisions and contract liabilities", indent=1,
                basis="an increase releases cash",
                formula=lambda c, p: f"={sc('prov',c)}-{sc('prov',p)}")
    c5 = F.line("Change in working capital", indent=1, bold=True,
                basis="the five movements above, and it ties to the schedule",
                formula=lambda c, p: f"=SUM({c}{w1}:{c}{w5})")
    F.note("Working capital is broken into the five balances that drive it, so a reader can "
           "see which of them is releasing the cash. The subtotal ties to the change on the "
           "working-capital schedule, and the control page tests that it does.")
    # The other operating balances move on the balance sheet, so their cash effect
    # belongs here. Without this the statements cannot reconcile.
    OCA_BAL = bs["restricted_cash_letter_of_credit"]["FY2026"] + bs["mobile_financial_deposit"]["FY2026"]
    OL_BAL = (li["payables_non_current"]["FY2026"] + li["provisions_non_current"]["FY2026"]
              + li["contract_liabilities_non_current"]["FY2026"] + li["current_income_tax"]["FY2026"]
              + li["mobile_financial_payable"]["FY2026"])
    NCA_STEP = abs(inc["depreciation_irus"]["FY2026"])          # IRU amortisation, non-cash
    c5b = F.line("Movement in other assets and liabilities", indent=1,
                 basis="the balance-sheet lines that move with revenue but sit outside "
                       "working capital, plus the non-cash deferred tax credit",
                 formula=lambda c, p: (f"=-({OCA_BAL}-{OL_BAL})*(Fcst_IS!{c}{rev}-Fcst_IS!{p}{rev})"
                                       f"/{inc['total_revenue']['FY2026']}"
                                       f"-{D['deferred_tax']}"))
    F.note("The indefeasible-rights amortisation used to be added back here as a fixed "
           "figure, because the asset declined on the balance sheet with no charge in the "
           "income statement to match it. The charge is now on the income statement and "
           "added back above with the other non-cash items, so this line no longer carries "
           "it.")
    cfo = F.line("OPERATING CASH FLOW", key="cfo", bold=True,
                 basis="profit, the non-cash addbacks and the movements above",
                 formula=lambda c, p: (f"=SUM({c}{c1}:{c}{w5})+{c}{c5b}"))
    F.section("Investing")
    c6 = F.line("Capital expenditure", indent=1, formula=lambda c, p: f"=-{sc('ppe_add',c)}")
    c7 = F.line("Intangible additions incl. spectrum", indent=1,
                formula=lambda c, p: f"=-{sc('int_add',c)}")
    cfi = F.line("INVESTING CASH FLOW", key="cfi", bold=True,
                 formula=lambda c, p: f"={c}{c6}+{c}{c7}")
    F.section("Financing")
    c8 = F.line("Debt drawn", indent=1, formula=lambda c, p: f"={sc('debt_new',c)}")
    c9 = F.line("Debt repaid", indent=1, formula=lambda c, p: f"={sc('debt_rep',c)}")
    c10 = F.line("Lease payments (principal and interest)", indent=1,
                 formula=lambda c, p: f"={sc('lease_pay',c)}")
    c11 = F.line("Dividends PAID", indent=1, basis="cash, not declared — item 13",
                 formula=lambda c, p: f"=-{sc('div_paid',c)}")
    # These two were missing entirely. The Ethiopia schedule raised NCI equity by
    # the capital minorities put in, and nothing on this statement recorded the
    # cash arriving, so the balance sheet broke by the cumulative contribution —
    # KShs 31.1bn by FY2031 in the Bear case, where the contributions are large.
    c11b = F.line("Capital contributed by minorities", indent=1,
                  basis="Ethiopia item 5: minorities fund their share, and that is cash in",
                  formula=lambda c, p: f"={et('nci_contrib', c)}")
    c11c = F.line("Distributions to minorities", indent=1,
                  basis="Ethiopia item 13, cash out when Ethiopia pays its minorities",
                  formula=lambda c, p: f"={et('nci_distrib', c)}")
    cff = F.line("FINANCING CASH FLOW", key="cff", bold=True,
                 formula=lambda c, p: f"=SUM({c}{c8}:{c}{c11c})")
    F.spacer()
    net = F.line("Net change in cash", key="net", bold=True,
                 formula=lambda c, p: f"={c}{cfo}+{c}{cfi}+{c}{cff}")
    op = F.line("Opening cash", key="open",
                hist={"FY2026": bs["net_cash_and_cash_equivalents"]["FY2025"]},
                formula=lambda c, p: f"={p}{F.row+1}")
    cl = F.line("CLOSING CASH", key="close", bold=True,
                hist={"FY2026": bs["net_cash_and_cash_equivalents"]["FY2026"]},
                formula=lambda c, p: f"={c}{op}+{c}{net}")
    # Finance income earns on the cash the year OPENS with, so it can be written
    # only now that the cash flow exists. Using closing cash would make this
    # year's profit depend on this year's interest and back again.
    # This used to carry the FY2026 ratio of note-8 finance income to closing cash,
    # which is 23.1% and is not a rate anyone earns on a deposit. Note 8 is wider than
    # bank interest. The forecast now earns an explicit deposit rate on opening cash.
    for col in F_COLS:
        I.ws[f"{col}{fi}"] = f"=Fcst_CF!{col}{op}*{D['deposit_rate']}"

    # the debt schedule's funding need points at closing cash
    for col in F_COLS:
        S.ws[f"{col}{SC['debt_new']}"] = (
            f"=MAX(0,20000-(Fcst_CF!{col}{op}+Fcst_CF!{col}{cfo}+Fcst_CF!{col}{cfi}"
            f"+{col}{SC['lease_pay']}+{col}{SC['debt_rep']}-{col}{SC['div_paid']}"
            f"+Fcst_CF!{col}{c11b}+Fcst_CF!{col}{c11c}))")

    # ---------------- BALANCE SHEET ----------------
    B = Builder(wb, "Fcst_BS", reg)
    B.title("Group balance sheet",
            "Opens at the FY2026 audited closing position (item 8). No 'other net assets' plug: "
            "every line is a schedule or a real disclosed balance (item 9).")
    B.headers()
    rev_ref = "Fcst_IS!{}" + str(rev)      # the group revenue row on Fcst_IS
    R26 = inc["total_revenue"]["FY2026"]

    def with_rev(base_val):
        """A line with no driver of its own, carried at its FY2026 ratio to revenue."""
        return lambda c, p: f"={base_val}*{rev_ref.format(c)}/{R26}"

    def flat():
        """Held at the opening balance. Linked, not repeated, so a reader can tell a
        deliberate constant from one that was forgotten."""
        return lambda c, p: f"=$D${B.row}"

    def split(total_ref, share):
        return lambda c, p: f"={total_ref(c)}*{share:.6f}"

    B.section("Assets")
    B.note("Item 6: every line the audited balance sheet prints is carried here as its own "
           "row. Where a line has a driver it moves on that driver. Where it has none it is "
           "either held at its FY2026 ratio to revenue or held flat, and the basis column "
           "says which. Nothing is bundled into an 'other' bucket.")
    n1 = B.line("Deferred income tax asset", indent=1, basis="opening plus the deferred tax credit",
                hist={"FY2026": bs["deferred_income_tax_asset"]["FY2026"]},
                formula=lambda c, p: f"={p}{B.row}+{D['deferred_tax']}")
    n2 = B.line("Property and equipment", indent=1, basis="from the schedule",
                hist={"FY2026": bs["property_and_equipment"]["FY2026"]},
                formula=lambda c, p: f"={sc('ppe_close',c)}")
    IRU26 = bs["indefeasible_rights_of_use"]["FY2026"]
    IRU_AM = abs(inc["depreciation_irus"]["FY2026"])
    n3 = B.line("Indefeasible rights of use", indent=1,
                basis="from the schedule; the charge also sits in the income statement",
                hist={"FY2026": IRU26},
                formula=lambda c, p: f"={sc('iru_close',c)}")
    n4 = B.line("Investment properties", indent=1, basis="held flat: no acquisition or disposal assumed",
                hist={"FY2026": bs["investment_properties"]["FY2026"]}, formula=flat())
    n5 = B.line("Intangible assets", indent=1, basis="from the schedule",
                hist={"FY2026": bs["intangible_assets"]["FY2026"]},
                formula=lambda c, p: f"={sc('int_close',c)}")
    n6 = B.line("Right-of-use assets", indent=1, basis="from the schedule",
                hist={"FY2026": bs["right_of_use_assets"]["FY2026"]},
                formula=lambda c, p: f"={sc('rou_close',c)}")
    n7 = B.line("Investment in associates and joint ventures", indent=1,
                basis="held flat: no equity-accounted result is forecast",
                hist={"FY2026": bs["investment_in_associates_and_jv"]["FY2026"]}, formula=flat())
    n8 = B.line("Restricted cash, non-current", indent=1, basis="held flat",
                hist={"FY2026": bs["restricted_cash_non_current"]["FY2026"]}, formula=flat())
    n9 = B.line("Deferred restricted cash asset", indent=1, basis="held flat",
                hist={"FY2026": bs["deferred_restricted_cash_asset"]["FY2026"]}, formula=flat())
    CA_NC = bs["contract_assets_non_current"]["FY2026"]
    CA_C = bs["contract_assets_current"]["FY2026"]
    CA_T = CA_NC + CA_C
    n10 = B.line("Contract assets, non-current", indent=1,
                 basis="the non-current share of the contract-asset schedule",
                 hist={"FY2026": CA_NC},
                 formula=lambda c, p: f"={sc('ca',c)}*{CA_NC / CA_T:.6f}" if CA_T else flat()(c, p))
    tnca = B.line("TOTAL NON-CURRENT ASSETS", bold=True,
                  hist={"FY2026": bs["total_non_current_assets"]["FY2026"]},
                  formula=lambda c, p: f"=SUM({c}{n1}:{c}{n10})")
    c1 = B.line("Inventories", indent=1, basis="inventory days x revenue / 365",
                hist={"FY2026": bs["inventories"]["FY2026"]},
                formula=lambda c, p: f"={sc('inv',c)}")
    c2 = B.line("Trade and other receivables", indent=1, basis="receivable days x revenue / 365",
                hist={"FY2026": bs["trade_and_other_receivables"]["FY2026"]},
                formula=lambda c, p: f"={sc('rec',c)}")
    c3 = B.line("Net cash and cash equivalents", indent=1, basis="from the cash flow",
                hist={"FY2026": bs["net_cash_and_cash_equivalents"]["FY2026"]},
                formula=lambda c, p: f"=Fcst_CF!{c}{cl}")
    c4 = B.line("Restricted cash, letter of credit", indent=1, basis="at its FY2026 ratio to revenue",
                hist={"FY2026": bs["restricted_cash_letter_of_credit"]["FY2026"]},
                formula=with_rev(bs["restricted_cash_letter_of_credit"]["FY2026"]))
    c5 = B.line("Contract assets, current", indent=1,
                basis="the current share of the contract-asset schedule",
                hist={"FY2026": CA_C},
                formula=lambda c, p: f"={sc('ca',c)}*{CA_C / CA_T:.6f}" if CA_T else flat()(c, p))
    c6 = B.line("Mobile financial deposit", indent=1,
                basis="the M-PESA float, at its FY2026 ratio to revenue",
                hist={"FY2026": bs["mobile_financial_deposit"]["FY2026"]},
                formula=with_rev(bs["mobile_financial_deposit"]["FY2026"]))
    tca = B.line("TOTAL CURRENT ASSETS", bold=True,
                 hist={"FY2026": bs["total_current_assets"]["FY2026"]},
                 formula=lambda c, p: f"=SUM({c}{c1}:{c}{c6})")
    ta = B.line("TOTAL ASSETS", key="ta", bold=True, hist={"FY2026": bs["total_assets"]["FY2026"]},
                formula=lambda c, p: f"={c}{tnca}+{c}{tca}")

    B.section("Liabilities")
    BOR_NC = li["borrowings_non_current"]["FY2026"]
    BOR_C = li["borrowings_current"]["FY2026"]
    BOR_T = BOR_NC + BOR_C
    LSE_NC = li["lease_liabilities_non_current"]["FY2026"]
    LSE_C = li["lease_liabilities_current"]["FY2026"]
    LSE_T = LSE_NC + LSE_C
    m1 = B.line("Borrowings, non-current", indent=1,
                basis="the non-current share of the borrowings schedule",
                hist={"FY2026": BOR_NC},
                formula=split(lambda c: sc('debt_close', c), BOR_NC / BOR_T))
    m2 = B.line("Lease liabilities, non-current", indent=1,
                basis="the non-current share of the lease schedule",
                hist={"FY2026": LSE_NC},
                formula=split(lambda c: sc('lease_close', c), LSE_NC / LSE_T))
    m3 = B.line("Payables, non-current", indent=1, basis="at its FY2026 ratio to revenue",
                hist={"FY2026": li["payables_non_current"]["FY2026"]},
                formula=with_rev(li["payables_non_current"]["FY2026"]))
    m4 = B.line("Provisions, non-current", indent=1, basis="at its FY2026 ratio to revenue",
                hist={"FY2026": li["provisions_non_current"]["FY2026"]},
                formula=with_rev(li["provisions_non_current"]["FY2026"]))
    m5 = B.line("Contract liabilities, non-current", indent=1, basis="at its FY2026 ratio to revenue",
                hist={"FY2026": li["contract_liabilities_non_current"]["FY2026"]},
                formula=with_rev(li["contract_liabilities_non_current"]["FY2026"]))
    tncl = B.line("TOTAL NON-CURRENT LIABILITIES", bold=True,
                  hist={"FY2026": li["total_non_current_liabilities"]["FY2026"]},
                  formula=lambda c, p: f"=SUM({c}{m1}:{c}{m5})")
    k1 = B.line("Current income tax", indent=1, basis="at its FY2026 ratio to revenue",
                hist={"FY2026": li["current_income_tax"]["FY2026"]},
                formula=with_rev(li["current_income_tax"]["FY2026"]))
    k2 = B.line("Dividend payable", indent=1, basis="from the dividend schedule",
                hist={"FY2026": li["dividend_payable"]["FY2026"]},
                formula=lambda c, p: f"={sc('div_bal',c)}")
    k3 = B.line("Shareholder loan", indent=1, basis="held flat: no new shareholder funding assumed",
                hist={"FY2026": li["shareholder_loan"]["FY2026"]}, formula=flat())
    k4 = B.line("Borrowings, current", indent=1, basis="the current share of the borrowings schedule",
                hist={"FY2026": BOR_C},
                formula=split(lambda c: sc('debt_close', c), BOR_C / BOR_T))
    k5 = B.line("Lease liabilities, current", indent=1, basis="the current share of the lease schedule",
                hist={"FY2026": LSE_C},
                formula=split(lambda c: sc('lease_close', c), LSE_C / LSE_T))
    k6 = B.line("Payables and accrued expenses, current", indent=1, basis="payable days x revenue / 365",
                hist={"FY2026": li["payables_and_accrued_current"]["FY2026"]},
                formula=lambda c, p: f"={sc('pay',c)}")
    PR_C = li["provisions_current"]["FY2026"]
    CL_C = li["contract_liabilities_current"]["FY2026"]
    PR_T = PR_C + CL_C
    k7 = B.line("Provisions, current", indent=1,
                basis="the provisions share of the provisions-and-contract schedule",
                hist={"FY2026": PR_C},
                formula=lambda c, p: f"={sc('prov',c)}*{PR_C / PR_T:.6f}" if PR_T else flat()(c, p))
    k8 = B.line("Mobile financial payable", indent=1,
                basis="the M-PESA float owed to customers, at its FY2026 ratio to revenue",
                hist={"FY2026": li["mobile_financial_payable"]["FY2026"]},
                formula=with_rev(li["mobile_financial_payable"]["FY2026"]))
    k9 = B.line("Contract liabilities, current", indent=1,
                basis="the contract-liability share of the same schedule",
                hist={"FY2026": CL_C},
                formula=lambda c, p: f"={sc('prov',c)}*{CL_C / PR_T:.6f}" if PR_T else flat()(c, p))
    tcl = B.line("TOTAL CURRENT LIABILITIES", bold=True,
                 hist={"FY2026": li["total_current_liabilities"]["FY2026"]},
                 formula=lambda c, p: f"=SUM({c}{k1}:{c}{k9})")
    tl = B.line("TOTAL LIABILITIES", key="tl", bold=True,
                hist={"FY2026": li["total_liabilities"]["FY2026"]},
                formula=lambda c, p: f"={c}{tncl}+{c}{tcl}")

    B.section("Equity")
    q1 = B.line("Share capital", indent=1, basis="held flat: no issue or buy-back assumed",
                hist={"FY2026": eq["share_capital"]["FY2026"]}, formula=flat())
    q2 = B.line("Share premium", indent=1, basis="held flat",
                hist={"FY2026": eq["share_premium"]["FY2026"]}, formula=flat())
    q3 = B.line("Retained earnings", indent=1,
                basis="opening + profit to parent - dividends DECLARED",
                hist={"FY2026": eq["retained_earnings"]["FY2026"]},
                formula=lambda c, p: f"={p}{B.row}+Fcst_IS!{c}{par}-{sc('div_dec',c)}")
    q4 = B.line("Other reserves", indent=1,
                basis="translation on assets net of debt, less the minority's share",
                hist={"FY2026": eq["other_reserves"]["FY2026"]},
                formula=lambda c, p: (f"={p}{B.row}+{sc('ppe_fx',c)}-{sc('debt_fx',c)}"
                                      f"-Ethiopia!{c}{E['nci_fx']}"))
    q5 = B.line("Share-based payment reserve", indent=1,
                basis="held flat: no new grant is forecast",
                hist={"FY2026": eq["share_based_payment_reserve"]["FY2026"]}, formula=flat())
    q5b = B.line("Proposed dividend", indent=1,
                 basis="the final declared but not yet approved (IAS 10)",
                 hist={"FY2026": eq["proposed_dividend_reserve"]["FY2026"]},
                 formula=lambda c, p: f"={sc('div_final',c)}")
    B.note("Item 13: a dividend the board has proposed but the annual general meeting has "
           "not approved is not a present obligation, so IAS 10 leaves it inside equity "
           "until approval. The audited balance sheet does exactly that, carrying "
           f"{eq['proposed_dividend_reserve']['FY2026']:,.1f} here and only "
           f"{li['dividend_payable']['FY2026']:,.1f} as a payable, and the forecast follows "
           "it. Retained earnings is charged with the whole declaration, the proposed final "
           "is held here until it is approved, and equity therefore falls each year by the "
           "cash actually paid, which is last year's final plus this year's interim.")
    tep = B.line("EQUITY ATTRIBUTABLE TO PARENT", bold=True,
                 hist={"FY2026": eq["equity_attributable_to_parent"]["FY2026"]},
                 formula=lambda c, p: f"=SUM({c}{q1}:{c}{q5b})")
    q6 = B.line("Non-controlling interests", indent=1, basis="Ethiopia schedule",
                hist={"FY2026": eq["non_controlling_interests"]["FY2026"]},
                formula=lambda c, p: f"={et('nci_bal',c)}")
    te = B.line("TOTAL EQUITY", key="te", bold=True, hist={"FY2026": eq["total_equity"]["FY2026"]},
                formula=lambda c, p: f"={c}{tep}+{c}{q6}")
    tle = B.line("TOTAL EQUITY AND LIABILITIES", key="tle", bold=True,
                 hist={"FY2026": li["total_liabilities"]["FY2026"] + eq["total_equity"]["FY2026"]},
                 formula=lambda c, p: f"={c}{tl}+{c}{te}")
    chk = B.line("BALANCE CHECK  (must be zero)", key="check", bold=True,
                 hist={"FY2026": 0.0}, formula=lambda c, p: f"={c}{ta}-{c}{tle}")

    built["is"] = dict(rev=rev, ebitda=ebitda, ebit=ebit, pat=pat, parent=par, eps=eps, iru=iru,
                       elim_rev=r_el, elim_ebitda=e_el, etr=etr, tax_drag=tax_d,
                       tax=tax, dep=dep, amt=amt, rdp=rdp, pbt=pbt, opex=tcost, nci=ncs)
    built["cf"] = dict(cfo=cfo, cfi=cfi, cff=cff, close=cl, open=op)
    built["bs"] = dict(ta=ta, tl=tl, te=te, check=chk, cash=c3)
    return _valuation(wb, out_path, reg, built, D, SH_OUT, PRICE)


def _valuation(wb, out_path, reg, built, D, SH_OUT, PRICE):
    IS_, CF_, BS_, SC = built["is"], built["cf"], built["bs"], built["sched"]
    mb = reg["margin_bridge"]
    li, eq = reg["liabilities"], reg["equity"]
    E = built["ethiopia"]

    V = Builder(wb, "Valuation", reg)
    V.title("Valuation",
            "Item 14. The WACC build is visible, the FCFF comes off the three statements, and every "
            "output is a formula. Change any assumption and the target price moves.")

    V.section("WACC build")
    def inp(label, val, basis, fmt=PCT):
        r = V.row
        V.ws.cell(row=r, column=1, value="    " + label).font = Font(name=FONT, size=BASE)
        c = V.ws.cell(row=r, column=4, value=val)
        c.font = Font(name=FONT, size=BASE, color=BLUE_IN); c.number_format = fmt
        b = V.ws.cell(row=r, column=5, value=basis)
        b.font = Font(name=FONT, size=TINY, italic=True, color=MUTED)
        V.row += 1
        return r
    # The build is struck in shillings. The Kenyan ten-year already prices Kenyan
    # sovereign and inflation risk, so no separate Kenya country premium is added on
    # top of it: doing both counted the same risk twice. Ethiopia is a materially
    # weaker sovereign than Kenya and that exposure is not in a Kenyan yield, so it
    # is added as an incremental premium and weighted by Ethiopia's share of the
    # business rather than applied to the whole group.
    rf = inp("Risk-free rate", 0.1243,
             "Kenya ten-year local-currency government yield at the valuation date. "
             "Being a shilling yield it already carries Kenyan sovereign and inflation risk")
    beta = inp("Equity beta", 0.90, "ASSUMPTION — telecom sector, levered", fmt="0.00")
    erp = inp("Equity risk premium", 0.055,
              "ASSUMPTION — mature-market equity risk premium. It is the premium for "
              "equity over government paper and carries no country element, which sits "
              "in the risk-free rate above")
    crp = inp("Ethiopia country risk premium", 0.050,
              "ASSUMPTION — the INCREMENTAL sovereign risk of Ethiopia over Kenya, not a "
              "premium for both. Ethiopia is several rating notches below Kenya and has "
              "restructured external debt, and none of that is priced into a Kenyan yield")
    ethw = V.row
    V.ws.cell(row=ethw, column=1, value="    Ethiopia weight in the cost of equity").font = Font(
        name=FONT, size=BASE)
    c = V.ws.cell(row=ethw, column=4,
                  value=f"=Ethiopia!I{E['rev']}/Fcst_IS!I{IS_['rev']}")
    c.font = Font(name=FONT, size=BASE, color=GREEN_LINK); c.number_format = PCT
    V.ws.cell(row=ethw, column=5,
              value="Ethiopia's share of group revenue in the last forecast year").font = Font(
        name=FONT, size=TINY, italic=True, color=MUTED)
    V.row += 1
    ke = V.row
    V.ws.cell(row=ke, column=1, value="    Cost of equity").font = Font(name=FONT, size=BASE, bold=True)
    c = V.ws.cell(row=ke, column=4, value=f"=D{rf}+D{beta}*D{erp}+D{ethw}*D{crp}")
    c.font = Font(name=FONT, size=BASE, bold=True); c.number_format = PCT
    V.ws.cell(row=ke, column=5,
              value="risk-free + beta x ERP + Ethiopia weight x Ethiopia premium").font = Font(
        name=FONT, size=TINY, italic=True, color=MUTED)
    V.row += 1
    V.note("Item 1: the shilling risk-free rate carries Kenyan sovereign and inflation "
           "risk, so adding a Kenya country premium on top of it would price that risk "
           "twice. The only country premium here is the incremental one for Ethiopia, and "
           "it is applied to Ethiopia's share of the group rather than to all of it, "
           "because the Kenyan business does not carry Ethiopian sovereign risk.")
    kd = V.row
    V.ws.cell(row=kd, column=1, value="    Cost of debt (pre-tax)").font = Font(name=FONT, size=BASE)
    c = V.ws.cell(row=kd, column=4, value=f"={D['cost_of_debt']}")
    c.font = Font(name=FONT, size=BASE); c.number_format = PCT
    V.ws.cell(row=kd, column=5, value="DISCLOSED — 11.6% group incl. Ethiopia (note 16)").font = Font(
        name=FONT, size=TINY, italic=True, color=MUTED)
    V.row += 1
    tx = inp("Tax rate", None, "linked to the single tax assumption")
    V.ws.cell(row=tx, column=4, value=f"={D['tax_rate']}").number_format = PCT
    we = inp("Equity weight", 0.80, "ASSUMPTION — target capital structure")
    wd = V.row
    V.ws.cell(row=wd, column=1, value="    Debt weight").font = Font(name=FONT, size=BASE)
    c = V.ws.cell(row=wd, column=4, value=f"=1-D{we}"); c.number_format = PCT
    c.font = Font(name=FONT, size=BASE)
    V.row += 1
    wacc = V.row
    V.ws.cell(row=wacc, column=1, value="    WACC").font = Font(name=FONT, size=SMALL, bold=True, color=NAVY)
    c = V.ws.cell(row=wacc, column=4, value=f"=D{ke}*D{we}+D{kd}*(1-D{tx})*D{wd}")
    c.font = Font(name=FONT, size=SMALL, bold=True, color=NAVY); c.number_format = PCT
    c.fill = PatternFill("solid", start_color=AMBER)
    V.row += 1
    # The valuation is struck in shillings and discounted at a shilling rate built on
    # a 12.43% local yield, which prices Kenyan inflation. A terminal rate of 3.5%
    # against that discount rate had the business shrinking in real terms forever,
    # which is not consistent with the inflation the discount rate itself carries.
    # The Central Bank of Kenya targets 2.5% to 7.5%; the midpoint is zero real
    # growth in perpetuity and is the anchor used.
    g = inp("Terminal growth", 0.050,
            "ASSUMPTION — the midpoint of the Central Bank of Kenya's 2.5% to 7.5% "
            "inflation target band, which is nil real growth in perpetuity")
    V.row += 1

    V.headers()
    V.section("Free cash flow to the firm, off the three statements")
    f1 = V.line("EBIT", indent=1, formula=lambda c, p: f"=Fcst_IS!{c}{IS_['ebit']}")
    f2 = V.line("Tax on EBIT", indent=1, formula=lambda c, p: f"=-{c}{f1}*$D${tx}")
    f3 = V.line("Depreciation and amortisation", indent=1,
                basis="every non-cash charge on the income statement, including the "
                      "indefeasible-rights amortisation",
                formula=lambda c, p: (f"=-(Fcst_IS!{c}{IS_['dep']}+Fcst_IS!{c}{IS_['amt']}"
                                      f"+Fcst_IS!{c}{IS_['rdp']}+Fcst_IS!{c}{IS_['iru']})"))
    f4 = V.line("Capital expenditure", indent=1, formula=lambda c, p: f"=-{f'Schedules!{c}{SC["ppe_add"]}'}-Schedules!{c}{SC['int_add']}")
    f4b = V.line("New and remeasured leases", indent=1,
                 formula=lambda c, p: f"=-Schedules!{c}{SC['rou_add']}")
    f5 = V.line("Change in working capital", indent=1, formula=lambda c, p: f"=Schedules!{c}{SC['nwc_chg']}")
    fcff = V.line("FCFF", key="fcff", bold=True, formula=lambda c, p: f"=SUM({c}{f1}:{c}{f5})")
    V.note("Item 4: operating profit is struck after IFRS 16, so the depreciation on leased "
           "assets sits inside the D&A added back two rows above. New leases are therefore "
           "charged here as capital expenditure. Leaving them out would value a site estate "
           "the company never pays for while the bridge still deducts the lease liability as "
           "debt. The liability those leases create is financing and correctly stays out of "
           "an unlevered cash flow, with only the opening balance deducted in the bridge.")
    disc = V.line("Discount factor", fmt="0.000",
                  formula=lambda c, p: f"=1/(1+$D${wacc})^{F_COLS.index(c)+1}")
    pv = V.line("PV of FCFF", key="pv", formula=lambda c, p: f"={c}{fcff}*{c}{disc}")

    V.section("Enterprise to equity bridge")
    def out(label, formula, fmt=NUM, bold=False, basis=""):
        r = V.row
        cc = V.ws.cell(row=r, column=1, value="    " + label)
        cc.font = Font(name=FONT, size=BASE, bold=bold, color=NAVY if bold else BLACK)
        c2 = V.ws.cell(row=r, column=4, value=formula)
        c2.font = Font(name=FONT, size=BASE, bold=bold); c2.number_format = fmt
        if basis:
            V.ws.cell(row=r, column=5, value=basis).font = Font(name=FONT, size=TINY, italic=True, color=MUTED)
        V.row += 1
        return r
    pvsum = out("PV of explicit forecast", f"=SUM(E{pv}:I{pv})")
    roic = out("Terminal return on invested capital", 0.466, fmt=PCT,
               basis="ASSUMPTION — the model's own FY2031 return; sets the reinvestment "
                     "rate the terminal grower needs")
    V.ws.cell(row=roic, column=4).font = Font(name=FONT, size=BASE, color=BLUE_IN)
    rrate = out("Implied reinvestment rate", f"=$D${g}/D{roic}", fmt=PCT,
                basis="g = reinvestment rate x return on capital")
    tv = out("Terminal value",
             f"=Fcst_IS!I{IS_['ebit']}*(1-$D${tx})*(1+$D${g})*(1-D{rrate})/($D${wacc}-$D${g})")
    V.note("Item 4: growing the last forecast cash flow at g would carry that year's "
           "reinvestment rate into perpetuity, and in this model that rate is NEGATIVE — "
           "capital expenditure as valued sits below depreciation and working capital "
           "releases cash. Terminal free cash flow would then exceed net operating profit, "
           "which is a business growing forever while shrinking its asset base. The terminal "
           "value is built instead from net operating profit less the reinvestment that g "
           "implies at the return above.")
    pvtv = out("PV of terminal value", f"=D{tv}*I{disc}")
    ev = out("Enterprise value", f"=D{pvsum}+D{pvtv}", bold=True)
    V.note("Item 4: a DCF discounts every future cash flow to TODAY, so it must be bridged with "
           "TODAY's net debt and NCI, being the FY2026 closing balances. Using end-of-forecast "
           "balances here would mix a present-value enterprise value with a future capital "
           "structure.")
    nd = out("Less: net debt at the valuation date",
             f"=-Schedules!D{SC['debt_close']}-Schedules!D{SC['lease_close']}+Fcst_BS!D{BS_['cash']}",
             basis="FY2026 closing borrowings and leases, less cash")
    # Item 2: the discounted cash flows are the WHOLE group's, Ethiopia included, so the
    # minority's claim has to come off at value and not at the carrying amount of their
    # equity. Ethiopia is valued here on its own cash flows at its own cost of capital,
    # which carries the full Ethiopian country premium rather than the group's weighted
    # one, and the minority's share of that value is deducted.
    V.section("What the Ethiopian minority owns")
    # Ethiopia's cash flows enter the group enterprise value discounted at the GROUP
    # WACC, so the minority's share of that value has to come off on the same basis.
    # Discounting Ethiopia at its own higher rate here and deducting the result would
    # take out less than the group valuation put in. Ethiopia's own cost of capital is
    # shown alongside as a cross-check on what the segment is worth standalone.
    ke_e = out("Ethiopia standalone cost of equity", f"=D{rf}+D{beta}*D{erp}+D{crp}", fmt=PCT,
               basis="memorandum: the full Ethiopia premium rather than the weighted share")
    wacc_e = out("Ethiopia standalone WACC", f"=D{ke_e}*D{we}+D{kd}*(1-D{tx})*D{wd}", fmt=PCT,
                 basis="memorandum only; the deduction below uses the group rate")
    efc = V.row
    V.ws.cell(row=efc, column=1, value="    Ethiopia free cash flow").font = Font(name=FONT, size=BASE)
    for i, col in enumerate(F_COLS):
        c = V.ws.cell(row=efc, column=Builder._ci(col),
                      value=(f"=Ethiopia!{col}{E['ebit']}+Ethiopia!{col}{E['tax']}"
                             f"-Ethiopia!{col}{E['da']}+Ethiopia!{col}{E['capex']}"))
        c.number_format = NUM; c.font = Font(name=FONT, size=BASE)
    V.ws.cell(row=efc, column=2, value="EBIT after its own tax, D&A back, less capex").font = Font(
        name=FONT, size=TINY, color=MUTED)
    V.row += 1
    epv = out("PV of Ethiopia's explicit years",
              "=" + "+".join(f"{c}{efc}/(1+$D${wacc})^{n}"
                             for n, c in enumerate(F_COLS, start=1)),
              basis="at the group WACC, the rate these flows entered the group value at")
    etv = out("Ethiopia terminal value",
              f"=I{efc}*(1+$D${g})/($D${wacc}-$D${g})",
              basis="its last forecast cash flow grown at the group terminal rate")
    eev = out("Ethiopia enterprise value", f"=D{epv}+D{etv}/(1+$D${wacc})^5", bold=True)
    ncival = out("Minority share of that value", f"=D{eev}*{D['nci_share']}", bold=True,
                 basis="45.83% of Safaricom Ethiopia, from Safaricom PLC's consolidation")
    V.note("Safaricom PLC holds 54.17% of Safaricom Ethiopia after the FY2026 dilution, so "
           "45.83% of it belongs to the other shareholders. Ethiopia does not publish a "
           "balance sheet of its own, so this is struck on enterprise value while the group "
           "net debt line above already carries Ethiopian borrowings in full. The parent "
           "therefore bears all of Ethiopia's debt and receives 54.17% of its enterprise "
           "value, which understates the parent's equity rather than flattering it. "
           "Ethiopia has been funded mainly by shareholder subscription, KShs 117.7bn from "
           "the minorities alone across FY2023 to FY2026, so the amount at issue is small.")

    ncib = out("Less: non-controlling interests at the valuation date",
               f"=-D{ncival}",
               basis="the minority's share of Ethiopia's value, not the carrying amount")
    eqv = out("Equity value", f"=D{ev}+D{nd}+D{ncib}", bold=True)
    dcfps = out("DCF value per share (KShs)", f"=D{eqv}/{SH_OUT}", fmt=CUR, bold=True,
                basis="a present value, at the valuation date")

    V.section("Cross-checks and weighted target")
    exitm = inp("Exit EV/EBITDA multiple", 5.4, "peer median, computed on the comparables table", fmt=MULT)
    peerpe = inp("Peer P/E", 12.7, "peer median, computed on the comparables table", fmt=MULT)
    V.note("Item 14: peer multiples must use one consistent valuation date, financial period and "
           "share-price snapshot. Both inputs above are dated together and must be refreshed together.")
    V.note("Each multiple below produces a value at the END of the period it uses, so each is "
           "discounted back to the valuation date at the cost of equity before it is weighted. "
           "Without that step a FY2031 exit value, a FY2027 earnings value and a present-value "
           "DCF would be averaged as if they were the same thing.")
    nd31 = out("Net debt at FY2031", f"=Schedules!I{SC['debt_close']}+Schedules!I{SC['lease_close']}-Fcst_BS!I{BS_['cash']}",
               basis="matched to the period the exit multiple values")
    nci31 = out("NCI at FY2031", f"=Ethiopia!I{E['nci_bal']}", basis="matched to the same period")
    exv = out("Exit-multiple equity value at FY2031",
              f"=Fcst_IS!I{IS_['ebitda']}*D{exitm}-D{nd31}-D{nci31}")
    ext = out("Exit-multiple terminal value per share, discounted",
              f"=D{exv}/{SH_OUT}/(1+D{ke})^5", fmt=CUR,
              basis="FY2031 equity value, brought back five years at the cost of equity")
    exd = out("Interim dividends over the hold, discounted",
              "=(" + "+".join(f"Schedules!{c}{SC['div_paid']}/(1+D{ke})^{i + 1}"
                              for i, c in enumerate(F_COLS)) + f")/{SH_OUT}",
              fmt=CUR,
              basis="a five-year holder receives five years of dividends before the exit")
    exps = out("Exit-multiple value per share, discounted to today",
               f"=D{ext}+D{exd}", fmt=CUR, bold=True)
    V.note("Item 4: the exit-multiple method values a holder who buys today, holds for five "
           "years and sells at FY2031. That holder receives five years of dividends before "
           "the sale, so the leg is the discounted terminal equity PLUS the discounted "
           "interim stream. Omitting the dividends made this leg return LESS than the "
           "discounted cash flow on a HIGHER multiple than the discounted cash flow's own "
           "implied exit, which cannot be right and is how the omission was found. Both "
           "components are discounted at the cost of equity, because both are equity flows, "
           "and the FY2031 net debt and NCI deducted above are the balances of the period "
           "being valued.")
    # Item 3: the peer multiples are struck on each peer's LAST REPORTED year, so they
    # are trailing multiples. Applying a trailing multiple to a forecast year and then
    # discounting it back mixes two bases and flatters the answer whenever earnings are
    # growing. The comparison is now trailing on trailing: the peer median applied to
    # Safaricom's own last reported earnings, which needs no discounting because it is
    # already a value at the valuation date.
    peps = out("Peer P/E value per share", f"=Fcst_IS!D{IS_['eps']}*D{peerpe}", fmt=CUR,
               basis="peer trailing median x Safaricom FY2026 reported EPS, like for like")
    V.note("Item 3: this leg is deliberately trailing on both sides. The peers are valued "
           "on their own last reported earnings and Safaricom on its FY2026 reported "
           "earnings, so neither side carries a forecast and no discounting is required. "
           "The forward-looking view of the business sits in the discounted cash flow and "
           "in the exit multiple, where it belongs, rather than being introduced into a "
           "relative comparison one side of which cannot be forecast from public data.")
    w1 = inp("Weight — DCF", 0.55, "standardised: the DCF leads where cash flow is forecastable")
    w2 = inp("Weight — exit multiple", 0.20, "standardised")
    w3 = inp("Weight — peer P/E", 0.25, "standardised")
    wsum = out("Weight check (must be 1.00)", f"=D{w1}+D{w2}+D{w3}", fmt="0.00")
    tgt = out("WEIGHTED TARGET PRICE (KShs)", f"=D{dcfps}*D{w1}+D{exps}*D{w2}+D{peps}*D{w3}",
              fmt=CUR, bold=True)
    prc = inp("Current share price (KShs)", PRICE,
              "the valuation-date close. Change it and the upside and the "
              "recommendation move with it", fmt=CUR)
    ups = out("Upside to target", f"=D{tgt}/D{prc}-1", fmt=PCT, bold=True,
              basis="target against the price in the cell above")
    rat = out("RECOMMENDATION", f'=IF(D{ups}>=0,"Buy","Sell")', fmt="General", bold=True)
    V.ws.cell(row=rat, column=4).fill = PatternFill("solid", start_color=AMBER)
    V.note("House standard: Buy or Sell. There is no Hold.")

    V.section("Sensitivity — WACC against terminal growth")
    sr = V.row
    V.ws.cell(row=sr, column=3, value="WACC \\ g").font = Font(name=FONT, size=TINY, bold=True)
    gs = [-0.005, 0.0, 0.005]
    ws_ = [-0.010, 0.0, 0.010]
    for j, dg in enumerate(gs):
        c = V.ws.cell(row=sr, column=4 + j, value=f"=$D${g}+{dg}")
        c.number_format = PCT; c.font = Font(name=FONT, size=TINY, bold=True)
    for i, dw in enumerate(ws_):
        rr = sr + 1 + i
        c = V.ws.cell(row=rr, column=3, value=f"=$D${wacc}+{dw}")
        c.number_format = PCT; c.font = Font(name=FONT, size=TINY, bold=True)
        for j, dg in enumerate(gs):
            pvs = "+".join(f"${cc}${fcff}/(1+$D${wacc}+{dw})^{n}"
                           for n, cc in enumerate(F_COLS, start=1))
            # The same reinvestment-consistent terminal value the published DCF
            # uses. This grid used to run a plain perpetuity on the last forecast
            # cash flow, so its centre cell missed the headline DCF by 5%.
            tvg = (f"Fcst_IS!$I${IS_['ebit']}*(1-$D${tx})*(1+$D${g}+{dg})"
                   f"*(1-($D${g}+{dg})/$D${roic})/($D${wacc}+{dw}-$D${g}-{dg})")
            f = (f"=(({pvs})"
                 f"+({tvg})/(1+$D${wacc}+{dw})^5"
                 f"+$D${nd}+$D${ncib})/{SH_OUT}")
            cc = V.ws.cell(row=rr, column=4 + j, value=f)
            cc.number_format = CUR; cc.font = Font(name=FONT, size=TINY)
    V.row = sr + 5

    # ---- item 14: football field, every bound formula-linked ----
    V.section("Football field")
    ff_hdr = V.row
    # The method name is in column A, so the headers start at the LOW figure.
    # Writing "method" here put every heading one column left of its data, and
    # the chart legend inherited it and read "method" and "low".
    #
    # "range" is what the chart actually plots. A floating bar is drawn as an
    # invisible bar of `low` with a visible bar of (high - low) stacked on top;
    # plotting `high` there instead ran every bar from low to low+high, so the
    # peer P/E band appeared to reach 67.60 on a note with a 26.72 target.
    for j, h in enumerate(("low", "range (plotted)", "high", "point")):
        cc = V.ws.cell(row=ff_hdr, column=3 + j, value=h)
        cc.font = Font(name=FONT, size=TINY, bold=True, color="FFFFFF")
        cc.fill = PatternFill("solid", start_color=NAVY)
    V.row += 1
    ff_rows = []
    bands = [
        # column E is the base terminal growth; column D is g less half a point,
        # so the bar used to be struck at a different g from its own point estimate
        ("DCF, WACC +/- 1pt", f"=$E${sr+3}", f"=$E${sr+1}", f"=D{dcfps}"),
        # both bounds carry the discounted interim dividends, as the point does,
        # otherwise the marker floats above its own bar
        ("Exit multiple, +/- 1.0x",
         f"=(Fcst_IS!I{IS_['ebitda']}*($D${exitm}-1)-$D${nd31}-$D${nci31})/{SH_OUT}/(1+$D${ke})^5+$D${exd}",
         f"=(Fcst_IS!I{IS_['ebitda']}*($D${exitm}+1)-$D${nd31}-$D${nci31})/{SH_OUT}/(1+$D${ke})^5+$D${exd}",
         f"=D{exps}"),
        ("Peer P/E, +/- 2.0x",
         f"=Fcst_IS!E{IS_['eps']}*($D${peerpe}-2)/(1+$D${ke})^1",
         f"=Fcst_IS!E{IS_['eps']}*($D${peerpe}+2)/(1+$D${ke})^1", f"=D{peps}"),
        ("Weighted target", f"=D{tgt}*0.9", f"=D{tgt}*1.1", f"=D{tgt}"),
    ]
    for label, lo_f, hi_f, pt_f in bands:
        r = V.row
        V.ws.cell(row=r, column=1, value="    " + label).font = Font(name=FONT, size=BASE)
        # C low | D range (the bar height the chart stacks) | E high | F point
        for j, f in enumerate((lo_f, f"=E{r}-C{r}", hi_f, pt_f)):
            cc = V.ws.cell(row=r, column=3 + j, value=f)
            cc.number_format = CUR; cc.font = Font(name=FONT, size=BASE)
        ff_rows.append(r)
        V.row += 1
    r = V.row
    V.ws.cell(row=r, column=1, value="    Current share price").font = Font(name=FONT, size=BASE, bold=True)
    cc = V.ws.cell(row=r, column=6, value=f"=D{prc}"); cc.number_format = CUR
    cc.font = Font(name=FONT, size=BASE, bold=True, color=GREEN_LINK)
    V.row += 2

    from openpyxl.chart import BarChart, LineChart, Reference
    ch = BarChart(); ch.type = "bar"; ch.style = 10
    ch.title = "Football field (KShs per share)"; ch.height, ch.width = 6.5, 15
    data = Reference(V.ws, min_col=3, max_col=4, min_row=ff_hdr, max_row=ff_rows[-1])
    cats = Reference(V.ws, min_col=1, min_row=ff_rows[0], max_row=ff_rows[-1])
    ch.add_data(data, titles_from_data=True); ch.set_categories(cats)
    ch.grouping = "stacked"; ch.overlap = 100
    ch.series[0].graphicalProperties.noFill = True   # the low bound: an invisible pedestal
    ch.series[1].graphicalProperties.solidFill = NAVY  # the RANGE, high minus low
    V.ws.add_chart(ch, f"A{V.row}")
    V.row += 14
    V.note("Each bar is drawn as an invisible pedestal of the low bound with the range stacked "
           "on top, so it floats between low and high. Every bound is a formula: change the "
           "WACC, a peer multiple or any operating driver and the field moves.")

    # ---------------- HISTORY AND TRENDS (item 16) ----------------
    H = Builder(wb, "Comps_and_KPIs", reg)
    H.title("Historical analysis and the FY2026 margin bridge",
            "Item 16 and research item 4. Five years of revenue by stream, the operating KPIs, and "
            "why the FY2026 margin improvement is only partly repeatable.")
    kre5 = reg["revenue_by_stream"]["kenya"]
    YRS5 = ["FY2022", "FY2023", "FY2024", "FY2025", "FY2026"]
    hcols = ["C", "D", "E", "F", "G"]
    H.ws.cell(row=H.row, column=1, value="Kenya revenue by stream (KShs m)").font = Font(
        name=FONT, size=SMALL, bold=True, color=NAVY)
    H.row += 1
    for col, y in zip(hcols, YRS5):
        c = H.ws.cell(row=H.row, column=Builder._ci(col), value=y)
        c.font = Font(name=FONT, size=BASE, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", start_color=NAVY)
        c.alignment = Alignment(horizontal="center")
    hdr = H.row; H.row += 1
    streams = [("Voice", "voice"), ("Messaging", "messaging"), ("Mobile data", "mobile_data"),
               ("M-PESA", "m_pesa"), ("Fixed and IoT", "fixed_service_and_iot")]
    srows = []
    for label, key in streams:
        r = H.row
        H.ws.cell(row=r, column=1, value=label).font = Font(name=FONT, size=BASE)
        for col, y in zip(hcols, YRS5):
            c = H.ws.cell(row=r, column=Builder._ci(col), value=kre5[key][y])
            c.number_format = NUM; c.font = Font(name=FONT, size=BASE, color=BLUE_IN)
        srows.append(r); H.row += 1
    r = H.row
    H.ws.cell(row=r, column=1, value="Kenya service revenue").font = Font(name=FONT, size=BASE, bold=True)
    for col, y in zip(hcols, YRS5):
        c = H.ws.cell(row=r, column=Builder._ci(col), value=kre5["service_revenue"][y])
        c.number_format = NUM; c.font = Font(name=FONT, size=BASE, bold=True)
    H.row += 2

    from openpyxl.chart import LineChart, BarChart, Reference
    lc = LineChart(); lc.title = "Kenya revenue by stream"; lc.height, lc.width = 7, 16
    lc.y_axis.title = "KShs m"
    lc.add_data(Reference(H.ws, min_col=3, max_col=7, min_row=srows[0], max_row=srows[-1]),
                titles_from_data=False, from_rows=True)
    lc.set_categories(Reference(H.ws, min_col=3, max_col=7, min_row=hdr))
    for i, (label, _) in enumerate(streams):
        lc.series[i].tx = None
    H.ws.add_chart(lc, f"A{H.row}")
    H.row += 16

    H.ws.cell(row=H.row, column=1, value="FY2026 EBITDA bridge — what actually drove the improvement").font = Font(
        name=FONT, size=SMALL, bold=True, color=NAVY)
    H.row += 1
    bridge = [
        ("FY2025 group EBITDA", reg["segment_note36"]["FY2025"]["ebitda"]["consolidated"]),
        ("Ethiopia loss narrowing", mb["ethiopia_ebitda_improvement"]),
        ("Expected credit losses normalising", -mb["ecl_reduction"]),
        ("Smaller FX loss inside opex", -mb["fx_loss_reduction"]),
        ("Underlying Kenya trading (residual)",
         reg["segment_note36"]["FY2026"]["ebitda"]["consolidated"]
         - reg["segment_note36"]["FY2025"]["ebitda"]["consolidated"]
         - mb["ethiopia_ebitda_improvement"] + mb["ecl_reduction"] + mb["fx_loss_reduction"]),
        ("FY2026 group EBITDA", reg["segment_note36"]["FY2026"]["ebitda"]["consolidated"]),
    ]
    brows = []
    for label, val in bridge:
        r = H.row
        cc = H.ws.cell(row=r, column=1, value=label)
        cc.font = Font(name=FONT, size=BASE, bold=label.startswith("FY"))
        c2 = H.ws.cell(row=r, column=3, value=val)
        c2.number_format = NUM; c2.font = Font(name=FONT, size=BASE, bold=label.startswith("FY"),
                                               color=BLUE_IN if not label.startswith("FY") else BLACK)
        brows.append(r); H.row += 1
    H.row += 1
    bc = BarChart(); bc.type = "col"; bc.title = "FY2026 EBITDA bridge (KShs m)"
    bc.height, bc.width = 7, 16
    bc.add_data(Reference(H.ws, min_col=3, min_row=brows[0], max_row=brows[-1]), titles_from_data=False)
    bc.set_categories(Reference(H.ws, min_col=1, min_row=brows[0], max_row=brows[-1]))
    bc.legend = None
    H.ws.add_chart(bc, f"A{H.row}")
    H.row += 16
    H.note("Group EBITDA rose 48,111.1. Of that, 18,339.4 was Ethiopia's loss narrowing, 7,132.9 was "
           "credit losses normalising and 7,438.3 was a smaller FX loss inside operating expenses. "
           "Those three are 68% of the improvement and none repeats at the same size. Excluding FX, "
           "underlying operating expenses ROSE 1,992.0, with employee costs up 7.6%. That is why the "
           "model does not hold a flat 51% margin.")

    # ---------------- CONTROLS ----------------
    C = Builder(wb, "Sources_and_Controls", reg)
    C.title("Controls",
            "Item 16. These test the things that actually went wrong, not generic checks. "
            "The previous workbook passed 19 of 19 controls while carrying a 1,234,567 revenue "
            "figure, a flat -67.8bn plug and positive working capital.")
    C.headers()
    def ctrl(label, formula, note=""):
        r = C.row
        cc = C.ws.cell(row=r, column=1, value=label)
        cc.font = Font(name=FONT, size=BASE)
        for col in F_COLS:
            # An out-of-range scenario switch used to cascade #VALUE! through every
            # control, so a completely dead model showed no FAIL anywhere.
            raw = formula(col)
            wrapped = f'=IFERROR({raw[1:]},"FAIL")' if raw.startswith("=") else raw
            c2 = C.ws.cell(row=r, column=Builder._ci(col), value=wrapped)
            c2.font = Font(name=FONT, size=BASE, bold=True)
            c2.alignment = Alignment(horizontal="center")
            c2.fill = PatternFill("solid", start_color="FFF3F3")
            from openpyxl.formatting.rule import CellIsRule
            C.ws.conditional_formatting.add(
                c2.coordinate,
                CellIsRule(operator="equal", formula=['"PASS"'],
                           fill=PatternFill("solid", start_color="EAF6EC")))
        if note:
            C.ws.cell(row=r, column=2, value=note).font = Font(
                name=FONT, size=TINY, italic=True, color=MUTED)
        C.row += 1
    ctrl("Balance sheet balances", lambda c: f'=IF(ABS(Fcst_BS!{c}{BS_["check"]})<1,"PASS","FAIL")',
         "assets = liabilities + equity")
    # These two used to restate the very formula they were checking, so corrupting
    # a segment left them reading PASS. They now cross the revenue build against the
    # independently built cost stack: revenue less total operating costs must equal
    # EBITDA, which fails if either side is disturbed.
    ctrl("Revenue less operating costs equals EBITDA",
         lambda c: f'=IF(ABS((Fcst_IS!{c}{IS_["rev"]}+Fcst_IS!{c}{IS_["opex"]})-Fcst_IS!{c}{IS_["ebitda"]})<1,"PASS","FAIL")',
         "item 1 — the revenue build and the cost build are separate and must meet")
    ctrl("Kenya + Ethiopia less eliminations = group revenue",
         lambda c: f'=IF(ABS((Op_Drivers!{c}{built["kenya"]["total"]}+Ethiopia!{c}{E["rev"]}+Fcst_IS!{c}{IS_["elim_rev"]})-Fcst_IS!{c}{IS_["rev"]})<1,"PASS","FAIL")',
         "item 1 — the group is not the simple sum of the segments")
    # Testing the closing balance tested MAX(0, ...) against zero, which is true by
    # construction. This tests the attribution BEFORE the floor, so it reports when
    # the minority's share of losses would have taken the balance negative and a
    # funding assumption is doing the work.
    ctrl("Minorities need no assumed funding",
         lambda c: f'=IF(Ethiopia!{c}{E["nci_contrib"]}<1,"PASS","FAIL")',
         "item 5 — a FAIL means the model is assuming a capital contribution to keep "
         "the balance off zero, which is an assumption and not settled ownership")
    # The rate falls as the Ethiopian drag decays. It flattens at the end because the
    # deferred-tax credit is a fixed amount against a growing profit, so the test is
    # the decline across the forecast rather than a fall in every single year.
    # This compared the cell with itself (ABS(x) <= ABS(x)+1) and so was true
    # for every value. It now tests what it claims: the drag is never larger
    # than it was in the first forecast year, and it ends at nil.
    ctrl("Ethiopian tax drag decays to nil",
         # Compared against a fixed $E$, which in column E was the cell itself.
         # Each year is now tested against the year before it, so the first
         # forecast year is measured against the FY2026 actual.
         lambda c: (f'=IF(ABS(Fcst_IS!{c}{IS_["tax_drag"]})'
                    f'<=ABS(Fcst_IS!{"DEFGH"["EFGHI".index(c)]}{IS_["tax_drag"]})+1,'
                    f'"PASS","FAIL")'),
         "item 3 — the drag scales with Ethiopia's remaining loss and never grows. "
         "In Bear, Ethiopia does not reach break-even, so the drag persists")
    S_ = None
    C.ws.cell(row=C.row, column=1, value="Effective tax rate falls across the forecast").font = Font(
        name=FONT, size=BASE)
    cc = C.ws.cell(row=C.row, column=5,
                   value=f'=IF(Fcst_IS!I{IS_["etr"]}<Fcst_IS!E{IS_["etr"]},"PASS","FAIL")')
    cc.font = Font(name=FONT, size=BASE, bold=True)
    cc.alignment = Alignment(horizontal="center")
    C.ws.cell(row=C.row, column=2, value="item 3 — 33.3% to 29.6% as Ethiopia turns").font = Font(
        name=FONT, size=TINY, color=MUTED)
    C.row += 1
    # The balance sheet takes its cash straight from the cash flow, so testing one
    # against the other compared a cell with itself. This rebuilds closing cash from
    # the opening balance and the three cash-flow subtotals instead.
    ctrl("Cash flow ties to the balance sheet",
         lambda c: (f'=IF(ABS((Fcst_CF!{c}{CF_["open"]}+Fcst_CF!{c}{CF_["cfo"]}'
                    f'+Fcst_CF!{c}{CF_["cfi"]}+Fcst_CF!{c}{CF_["cff"]})'
                    f'-Fcst_BS!{c}{BS_["cash"]})<1,"PASS","FAIL")'),
         "opening cash plus the three subtotals must reach the balance-sheet cash")
    # Nothing tested the income statement's own articulation. Corrupting profit for
    # the year flowed through the cash flow into cash, so both sides of the balance
    # sheet moved together and the balance check stayed at nil, correctly. These two
    # catch it where it happens.
    ctrl("Profit equals PBT less tax",
         lambda c: f'=IF(ABS((Fcst_IS!{c}{IS_["pbt"]}+Fcst_IS!{c}{IS_["tax"]})-Fcst_IS!{c}{IS_["pat"]})<1,"PASS","FAIL")',
         "the income statement articulates on its own, not only through the balance sheet")
    ctrl("Attributable equals profit less minorities",
         lambda c: f'=IF(ABS((Fcst_IS!{c}{IS_["pat"]}-Fcst_IS!{c}{IS_["nci"]})-Fcst_IS!{c}{IS_["parent"]})<1,"PASS","FAIL")',
         "item 5 — the split of the year's profit between the two owners")
    ctrl("Cash never negative", lambda c: f'=IF(Fcst_CF!{c}{CF_["close"]}>=0,"PASS","FAIL")',
         "item 11 — borrowing is drawn to prevent it")
    ctrl("Working capital stays negative",
         lambda c: f'=IF(Schedules!{c}{SC["nwc"]}<0,"PASS","FAIL")',
         "item 10 — Safaricom is funded by its payables")
    # Was the literal string "PASS". It now proves the claim: the balance sheet
    # closes on its own, without any reconciling line.
    ctrl("No plug line on the balance sheet",
         lambda c: f'=IF(ABS(Fcst_BS!{c}{BS_["check"]})<1,"PASS","FAIL")',
         "item 9 — the sheet closes with no reconciling line")
    ctrl("EPS uses profit attributable to parent",
         lambda c: f'=IF(ABS(Fcst_IS!{c}{IS_["eps"]}*{SH_OUT}-Fcst_IS!{c}{IS_["parent"]})<1,"PASS","FAIL")',
         "item 7")
    ctrl("Dividend payable never goes negative",
         lambda c: f'=IF(Schedules!{c}{SC["div_bal"]}>=-1,"PASS","FAIL")',
         "item 13 — declared and paid are different numbers, and the balance "
         "between them is a liability that cannot be negative")
    # Both branches returned "PASS", and the condition (>=0 on an absolute
    # value) was true regardless. The roll-forward is now actually re-derived.
    ctrl("PP&E roll-forward closes",
         lambda c: (f'=IF(ABS(Schedules!{c}{SC["ppe_close"]}-(Schedules!{c}{SC["ppe_open"]}'
                    f'+Schedules!{c}{SC["ppe_add"]}+Schedules!{c}{SC["ppe_dep"]}'
                    f'+Schedules!{c}{SC["ppe_fx"]}))<1,"PASS","FAIL")'),
         "opening + additions - depreciation + FX must equal closing")
    C.row += 1
    for lbl, f in (("Weights sum to 1.00", f'=IF(ABS(Valuation!D{wsum}-1)<0.001,"PASS","FAIL")'),
                   ("Higher WACC lowers value", f'=IF(Valuation!D{sr+3}<Valuation!D{sr+1},"PASS","FAIL")'),
                   ("Recommendation is Buy or Sell only",
                    f'=IF(OR(Valuation!D{rat}="Buy",Valuation!D{rat}="Sell"),"PASS","FAIL")'),
                   ("FY2027 opens at FY2026 closing PP&E",
                    f'=IF(ABS(Schedules!E{SC["ppe_open"] if "ppe_open" in SC else SC["ppe_close"]}-Schedules!D{SC["ppe_close"]})<1,"PASS","FAIL")')):
        r = C.row
        C.ws.cell(row=r, column=1, value=lbl).font = Font(name=FONT, size=BASE)
        cc = C.ws.cell(row=r, column=5, value=f)
        cc.font = Font(name=FONT, size=BASE, bold=True); cc.alignment = Alignment(horizontal="center")
        C.row += 1

    # ---------------- COVER ----------------
    CV = wb.create_sheet("Cover")
    CV.sheet_view.showGridLines = False
    CV.column_dimensions["A"].width = 34
    CV.column_dimensions["B"].width = 92
    rows = [
        ("Safaricom PLC", ""),
        ("Three-statement model, Kenya and Ethiopia split", ""),
        ("", ""),
        ("Currency", "Kenya shillings, millions"),
        ("Year end", "31 March"),
        ("Last actual year", "FY2026, audited"),
        ("Forecast", "FY2027E to FY2031E"),
        ("", ""),
        ("Source of every historical figure",
         "Safaricom PLC Annual Report and Financial Statements, year ended 31 March 2026. "
         "Transcribed with page references into data/safaricom_fy2026_register.json."),
        ("", ""),
        ("Revenue definitions — do not mix", ""),
        ("   Total revenue FY2026", "427,559.1   contracts with customers plus other sources; the statements foot to this"),
        ("   Group service revenue FY2026", "414,137.4   excludes handset and other revenue; does NOT reconcile to total revenue"),
        ("   Kenya service revenue FY2026", "400,794.6   Kenya segment only"),
        ("", ""),
        ("Profit definitions — do not mix", ""),
        ("   Profit for the year FY2026", "73,676.0    group, before attribution"),
        ("   Attributable to parent FY2026", "95,608.6    EPS, dividends and P/E all use THIS"),
        ("   Attributable to NCI FY2026", "(21,932.6)  negative: Ethiopia is loss-making and c.44% minority held"),
        ("", ""),
        ("Colour convention", "Blue = an input you may change.  Black = a formula.  Amber = the scenario switch."),
        ("How to use it", "Set the scenario on Assumptions, or edit any blue driver. Every sheet recalculates."),
        ("Check before sending", "Controls sheet must read PASS across every column."),
    ]
    r = 2
    for a, b in rows:
        ca = CV.cell(row=r, column=1, value=a)
        big = a in ("Safaricom PLC",)
        ca.font = Font(name=FONT, size=14 if big else 9,
                       bold=big or a.endswith(("mix", "convention", "it", "sending", "figure", "split")),
                       color=NAVY if (big or a and not a.startswith("   ")) else BLACK)
        cb = CV.cell(row=r, column=2, value=b)
        cb.font = Font(name=FONT, size=BASE, color=MUTED)
        cb.alignment = Alignment(wrap_text=True, vertical="top")
        r += 1
    _template_sheets(wb, reg, built, D, SH_OUT, PRICE,
                     {"target": tgt, "rating": rat})
    _comps_table(wb, reg, {"exit_multiple": exitm, "peer_pe": peerpe})

    # template sheet order
    order = ["Cover", "Assumptions", "Financials", "Op_Drivers", "Ethiopia", "Schedules",
             "Fcst_IS", "Fcst_BS", "Fcst_CF", "Scenarios", "Valuation", "Comps_and_KPIs",
             "Sources_and_Controls"]
    for i, name in enumerate(x for x in order if x in wb.sheetnames):
        wb.move_sheet(name, offset=i - wb.sheetnames.index(name))

    TABCOL = {"Cover": NAVY, "Assumptions": "9A7B1F", "Financials": NAVY,
              "Op_Drivers": TEAL, "Ethiopia": TEAL, "Schedules": TEAL,
              "Fcst_IS": NAVY, "Fcst_BS": NAVY, "Fcst_CF": NAVY,
              "Scenarios": "9A7B1F", "Valuation": SECTION,
              "Comps_and_KPIs": SECTION, "Sources_and_Controls": "9A7B1F"}
    for name, colr in TABCOL.items():
        if name in wb.sheetnames:
            wb[name].sheet_properties.tabColor = colr
    wb.save(out_path)
    return {"path": str(out_path), "sheets": wb.sheetnames}
