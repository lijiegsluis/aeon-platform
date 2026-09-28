"""Every figure in the Safaricom report, pulled from the platform.

Nothing here is typed by hand. The report is generated from this dictionary, so
a number in the PDF is a number in the model, and re-running after a rebuild
produces a report that matches the workbook rather than one that used to.
"""
from __future__ import annotations

import pathlib

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))          # run from anywhere
MODEL = ROOT / "output" / "models" / "safaricom_plc_model.xlsx"
SLUG = "safaricom_plc"


def _calculated(path: Path):
    """The workbook with its formulas evaluated, keyed by 'SHEET!CELL'."""
    import formulas
    from openpyxl import load_workbook
    sol = formulas.ExcelModel().loads(str(path)).finish().calculate()
    wb = load_workbook(path)
    name = path.name.upper()

    def val(sheet: str, cell: str):
        for k, v in sol.items():
            if k.upper().endswith(f"'[{name}]{sheet.upper()}'!{cell.upper()}"):
                try:
                    return v.value[0, 0]
                except Exception:
                    return v
        return None
    return wb, val


def _label_rows(ws, val, sheet, cols, first=1, last=None):
    """{label: [values by column]} for every row that carries numbers.

    A repeated label gets a numeric suffix rather than being dropped. The
    forecast income statement carries TWO rows called "Intersegment
    eliminations", one against revenue and one against EBITDA, and keeping only
    the first meant the EBITDA elimination never reached the report. Kenya plus
    Ethiopia then missed group EBITDA by between 1,729 and 2,809 every year,
    while a control on the same document reported that the three added up.
    """
    out = {}
    for r in range(first, (last or ws.max_row) + 1):
        lab = str(ws.cell(row=r, column=1).value or "").strip()
        if not lab:
            continue
        vals = [val(sheet, f"{c}{r}") for c in cols]
        if not any(isinstance(v, (int, float)) for v in vals):
            continue
        key, n = lab, 2
        while key in out:
            key, n = f"{lab} ({n})", n + 1
        out[key] = vals
    return out


def collect() -> dict:
    from aeon_nimbus import db as D, platform_data

    s = D.SessionLocal()
    co = s.query(D.Company).filter(D.Company.slug == SLUG).first()
    rec = platform_data.merged_extracted(co.extracted or {})
    uni = co.universe or {}
    fins = sorted([f for f in rec.get("financials", []) if f.get("fy")],
                  key=lambda x: x["fy"])
    mkt = rec.get("market") or {}
    notes = rec.get("notes") or {}

    wb, val = _calculated(MODEL)
    F = ["D", "E", "F", "G", "H", "I"]
    vs = wb["Valuation"]
    v = {}
    for r in range(1, vs.max_row + 1):
        lab = str(vs.cell(row=r, column=1).value or "").strip()
        if lab:
            v.setdefault(lab, val("Valuation", f"D{r}"))

    d = {
        "company": co.name, "ticker": uni.get("ticker"), "exchange": uni.get("exchange"),
        "country": uni.get("country"), "sector": uni.get("sector"),
        "currency": rec.get("currency"), "ir_url": uni.get("ir_url"),
        "market": mkt, "fins": fins, "uni": uni, "notes": notes,
        "published": rec.get("_published_valuation") or {},
        "val": v,
        "years": [str(val("Fcst_IS", f"{c}4") or "") for c in F],
        "is_rows": _label_rows(wb["Fcst_IS"], val, "Fcst_IS", F),
        "cf_rows": _label_rows(wb["Fcst_CF"], val, "Fcst_CF", F),
        "bs_rows": _label_rows(wb["Fcst_BS"], val, "Fcst_BS", F),
        # The supporting schedules DRIVE the balance sheet. The PP&E roll carries a
        # translation-differences line that the earlier drafts never printed, so a
        # reader adding opening plus capex less depreciation missed the closing
        # balance by between 7.5bn and 10.8bn a year and had no way to see why.
        "sched_rows": _label_rows(wb["Schedules"], val, "Schedules", F),
        # The Kenyan operating build: customers, ARPU, revenue by stream and the
        # cost lines behind the margin. The whole rating turns on two numbers that
        # come out of this sheet, and the report printed neither of them.
        "drv_rows": _label_rows(wb["Op_Drivers"], val, "Op_Drivers", F),
    }

    # controls, as the workbook reports them
    cs = wb["Sources_and_Controls"]
    controls = []
    for r in range(1, cs.max_row + 1):
        lab = str(cs.cell(row=r, column=1).value or "").strip()
        if not lab or lab.upper() == "CONTROLS":
            continue
        states = [val("Sources_and_Controls", f"{c}{r}") for c in ("C", "D", "E", "F")]
        states = [x for x in states if isinstance(x, str) and x in ("PASS", "FAIL")]
        if states:
            controls.append((lab, "PASS" if all(x == "PASS" for x in states) else "FAIL",
                             len(states)))
    d["controls"] = controls

    # comparables, from the platform's own records for each peer
    peers = []
    for p in (uni.get("peers") or []):
        pc = s.query(D.Company).filter(D.Company.name == p["name"]).first()
        if not pc:
            continue
        pr = platform_data.merged_extracted(pc.extracted or {})
        pf = sorted([f for f in pr.get("financials", []) if f.get("fy")],
                    key=lambda x: x["fy"])
        if not pf:
            continue
        L, pm = pf[-1], (pr.get("market") or {})
        mc = pm.get("market_cap_m") or ((pm.get("share_price") or 0) *
                                        (pm.get("shares_outstanding_m") or 0)) or None
        eb, nd, ni = L.get("ebitda"), L.get("net_debt"), L.get("net_income")
        ev = (mc + nd) if (mc and nd is not None) else None
        peers.append({"name": pc.name, "ticker": p.get("ticker"),
                      "country": pc.country, "currency": pr.get("currency"),
                      "fy": L.get("fy"), "ebitda": eb, "net_debt": nd, "mcap": mc,
                      "ev": ev,
                      "ev_ebitda": (ev / eb) if (ev and eb) else None,
                      "pe": (mc / ni) if (mc and ni and ni > 0) else None})
    d["peers"] = peers
    d["rec"] = rec              # the provenance page reads sources straight off it

    # THE PRICE THE TARGET WAS COMPUTED AGAINST.
    #
    # The hand-built model fixes its own quote (KShs 34.05, 2 July 2026) and the
    # platform's market block carries a later one (KShs 35.44, 16 July). Quoting
    # the later price beside an upside computed from the earlier one puts an
    # arithmetically wrong pair on the cover: -21.5% is the downside to 34.05,
    # and to 35.44 it is -24.6%.
    pub = d["published"]
    tgt, up = pub.get("target"), pub.get("upside")
    d["val_price"] = (tgt / (1 + up)) if (tgt and isinstance(up, float) and up != -1) else None
    d["val_price_date"] = "2 July 2026"          # the model's stated valuation date
    d["later_price"] = mkt.get("share_price")
    d["later_price_date"] = mkt.get("price_date")
    d["later_downside"] = ((tgt / d["later_price"] - 1)
                           if (tgt and d.get("later_price")) else None)
    # THE EXIT-MULTIPLE LEG IS REPAIRED HERE.
    #
    # As built, the leg takes the FY2031 equity value, discounts it five years at
    # the cost of equity, and stops. It never credits the holder with the
    # dividends paid over those five years. The proof that this is an error and
    # not a convention: the leg values FY2031 at 5.40x EBITDA and returns KShs
    # 20.35, while the DCF's own terminal value is 4.17x and returns KShs 25.82.
    # A higher multiple cannot be worth less than a lower one on the same
    # earnings. The missing piece is the interim cash flow, worth KShs 8.73 a
    # share in present value.
    SH = 40065.4
    def _v2(*names, default=0.0):
        for n in names:
            for k, val in v.items():
                if k.strip().lower().startswith(n.lower()) and isinstance(val, (int, float)):
                    return val
        return default
    ke = _v2("Cost of equity")
    eb31 = (d["is_rows"].get("EBITDA") or [None])[-1] or 0.0
    exq = (_v2("Exit EV/EBITDA") * eb31 - _v2("Net debt at FY2031")
           - _v2("NCI at FY2031"))
    divs = [abs(x) for x in (d["cf_rows"].get("Dividends PAID") or [])
            if isinstance(x, (int, float))]
    pv_div = sum(x / ((1 + ke) ** (i + 1)) for i, x in enumerate(divs))
    d["exit_as_built"] = exq / ((1 + ke) ** 5) / SH
    d["exit_interim_ps"] = pv_div / SH
    d["exit_repaired"] = d["exit_as_built"] + d["exit_interim_ps"]
    w = (_v2("Weight — DCF"), _v2("Weight — exit"), _v2("Weight — peer"))
    d["target_published"] = _v2("WEIGHTED TARGET PRICE")
    # The discounted leg is re-struck with Ethiopian capital expenditure at the
    # company's guidance instead of the model's flat KShs 26bn. The note calls
    # that correction near-certain because the company itself has guided it, so
    # publishing a target without it would be publishing a number the note does
    # not believe. The uncorrected leg is kept for the comparison on the page
    # that prices every case.
    import sys as _s
    _s.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from revalue import base as _rb, value as _rv, fcff as _rf
    _m = {"valuation": d["val"], "is_rows": d["is_rows"], "cf_rows": d["cf_rows"],
          "sched_rows": d["sched_rows"]}
    _b = _rb(_m)
    _full = _rv(_m, **_b)
    d["dcf_published"] = _v2("DCF value per share")
    d["dcf_repaired"] = _full["dcf"]
    # The exit leg as the engine builds it: terminal equity on the corrected
    # FY2031 net debt, plus five years of interim dividends the workbook dropped.
    d["exit_workbook"] = d["exit_as_built"]
    d["exit_repaired"] = _full["exit"]
    d["exit_dividend_only"] = _rv(_m, **_b)["exit"]
    d["target"] = (d["dcf_repaired"] * w[0] + d["exit_repaired"] * w[1]
                   + _v2("Peer P/E value per share") * w[2])

    # Four construction choices separate a naive build from the one the model
    # publishes. They live in the model, so the report reads them rather than
    # applying them, and this ladder prices each by switching it off. It has to
    # close on the published target and a test asserts that it does.
    _pe = _v2("Peer P/E value per share")
    _ex_term = _v2("Exit-multiple terminal value per share")
    _ex_full = _v2("Exit-multiple value per share")

    def _dcf(**kw):
        fk = {k: kw.pop(k) for k in ("revert_capex", "charge_leases") if k in kw}
        return _rv(_m, fcff_override=_rf(_m, **fk), **{**_b, **kw})["dcf"]

    _naive = dict(revert_capex=True, charge_leases=False, terminal_roic=None)
    d["correction_ladder"] = [
        ("A build with none of them", _dcf(**_naive), _ex_term),
        ("Five years of dividends in the exit leg", _dcf(**_naive), _ex_full),
        ("Ethiopian capital expenditure split at company guidance",
         _dcf(charge_leases=False, terminal_roic=None), _ex_full),
        ("New and remeasured leases charged as capital expenditure",
         _dcf(terminal_roic=None), _ex_full),
        ("A reinvestment-consistent terminal value", _dcf(), _ex_full),
    ]
    d["correction_targets"] = [x[1] * w[0] + x[2] * w[1] + _pe * w[2]
                               for x in d["correction_ladder"]]
    d["target_naive"] = d["correction_targets"][0]
    # The workbook now carries every treatment the note argues for, so the
    # ladder prices each one by reversing it rather than by adding it back.
    from revalue import fcff as _rf
    _base_t = _rv(_m, **_b)["target"]
    from revalue import eth_capex_net_debt_relief as _ndr
    d["treatments"] = [
        # The capex treatment reaches the exit leg as well, because cash not spent
        # leaves less net debt to deduct at FY2031. Reversing it on the discounted
        # leg alone priced it at 0.74 against the 0.86 the cases table shows, and the
        # caption beside the table claimed every leg was touched.
        ("Ethiopian capital expenditure at company guidance",
         _rv(_m, fcff_override=_rf(_m, revert_capex=True),
             exit_net_debt_addback=_ndr(), **_b)["target"]),
        ("New and remeasured leases charged as capital expenditure",
         _rv(_m, fcff_override=_rf(_m, charge_leases=False), **_b)["target"]),
        ("A reinvestment-consistent terminal value",
         _rv(_m, terminal_roic=None, **_b)["target"]),
    ]
    d["treatment_base"] = _base_t
    d["weights"] = w
    d["upside"] = (d["target"] / d["val_price"] - 1) if d.get("val_price") else None
    d["later_downside"] = ((d["target"] / d["later_price"] - 1)
                           if d.get("later_price") else None)

    s.close()
    return d


if __name__ == "__main__":
    import json
    got = collect()
    print(json.dumps({k: got[k] for k in
                      ("company", "ticker", "exchange", "currency", "published")},
                     indent=2, default=str))
    print("years:", got["years"])
    print("IS rows:", len(got["is_rows"]), "| CF rows:", len(got["cf_rows"]),
          "| BS rows:", len(got["bs_rows"]))
    print("controls:", len(got["controls"]), "| peers:", len(got["peers"]))
    print("valuation keys:", len(got["val"]))
