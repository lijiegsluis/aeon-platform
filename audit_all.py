"""Audit every model against every standard, and fail loudly.

This calculates each workbook rather than trusting the engine that produced it.

An earlier version of this file reported "30 of 30 models pass every check"
while ten bank models carried a zero loan book, twenty-nine carried ~70 pasted
constants each, and the flagship broke its balance sheet by KShs 31bn in its
own Bear scenario. Every one of those was invisible for a specific reason, and
each reason is now a check:

  · a pasted constant written "=51682.274" is a str, so an isinstance check for
    int/float never saw it                          -> LITERAL_FORMULA
  · only the shipped scenario was ever calculated   -> every scenario is run
  · a control hardcoded ="PASS" was read as a pass  -> the formula is inspected
  · a bank was checked for the WORD "loan"          -> the values must be non-zero
  · a reference past the end of a sheet evaluates
    to 0 rather than #REF!                          -> DANGLING
  · nothing compared the sheet's currency label
    with the company's reporting currency           -> CURRENCY

    python audit_all.py                # every company, every scenario
    python audit_all.py safaricom      # one company, by slug fragment
    python audit_all.py --base-only    # skip the scenario sweep (quick)
"""

import json
import logging
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

import formulas                                        # noqa: E402
from openpyxl import load_workbook                     # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from aeon_nimbus import drivers as dr              # noqa: E402
from aeon_nimbus import valuation as val           # noqa: E402

ROOT = Path(__file__).parent
FCOLS = list("EFGHI")
ERRORS = ("#REF!", "#DIV/0!", "#VALUE!", "#NAME?", "#N/A", "#NULL!", "#NUM!")
STATEMENTS = ("Op_Drivers", "Schedules", "Fcst_IS", "Fcst_BS", "Fcst_CF")

# "=51682.274" — a pasted value wearing a formula's clothes.
LITERAL_FORMULA = re.compile(r"^=\s*-?\d+(?:\.\d+)?\s*$")
CELL_REF = re.compile(r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_]*))!\$?([A-Z]{1,3})\$?(\d+)")
IF_PARTS = re.compile(r"^=IF\((.+)\)$", re.S)
PLUG_LABELS = ("other net", "balancing", "plug", "residual")


def _rows(ws):
    return {str(ws.cell(row=r, column=1).value or "").strip(): r
            for r in range(1, ws.max_row + 1)}


def _split_args(s):
    """Split IF arguments on top-level commas."""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur); cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


# --------------------------------------------------------------- static checks
def static_checks(wb, universe):
    """Everything provable from the formulas, without calculating."""
    fails = []

    # 1. Excel errors sitting in the file
    bad = [f"{ws.title}!{c.coordinate}" for ws in wb.worksheets
           for row in ws.iter_rows() for c in row
           if isinstance(c.value, str) and any(e in c.value for e in ERRORS)]
    if bad:
        fails.append(f"Excel errors in {len(bad)} cells, first {bad[:3]}")

    # 2. pasted values where a formula belongs — BOTH spellings
    hard = []
    for t in STATEMENTS:
        if t not in wb.sheetnames:
            continue
        for row in wb[t].iter_rows():
            for c in row:
                if c.column_letter not in FCOLS:
                    continue
                if isinstance(c.value, (int, float)):
                    hard.append(f"{t}!{c.coordinate}")
                elif isinstance(c.value, str) and LITERAL_FORMULA.match(c.value.strip()):
                    hard.append(f"{t}!{c.coordinate}={c.value.strip()}")
    if hard:
        fails.append(f"{len(hard)} hardcoded forecast cells, first {hard[:3]}")

    # 3. references past the end of a sheet: they read 0, never #REF!
    dangling = []
    sizes = {ws.title.upper(): ws.max_row for ws in wb.worksheets}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if not isinstance(c.value, str) or not c.value.startswith("="):
                    continue
                for q, plain, _col, rownum in CELL_REF.findall(c.value):
                    target = (q or plain).upper()
                    if target in sizes and int(rownum) > sizes[target]:
                        dangling.append(f"{ws.title}!{c.coordinate} -> {target}!{rownum} (blank)")
    if dangling:
        fails.append(f"{len(dangling)} references past the end of a sheet, "
                     f"silently reading 0: {dangling[:2]}")

    # 4. controls that cannot fail
    if "Sources_and_Controls" in wb.sheetnames:
        cs = wb["Sources_and_Controls"]
        fake = []
        for row in cs.iter_rows():
            for c in row:
                v = c.value
                if not isinstance(v, str) or not v.startswith("="):
                    continue
                label = str(cs.cell(row=c.row, column=1).value or "")[:40]
                body = v.strip()
                if re.fullmatch(r'="?PASS"?', body, re.I):
                    fake.append(f"{c.coordinate} is the constant PASS ({label})")
                    continue
                m = IF_PARTS.match(body)
                if m:
                    args = _split_args(m.group(1))
                    if len(args) == 3 and args[1].strip() == args[2].strip():
                        fake.append(f"{c.coordinate} both branches identical ({label})")
                        continue
                    cond = args[0]
                    # Comparing one cell against string constants — IF(OR(D9="Buy",
                    # D9="Sell"),...) — is a real test, not a self-comparison. Only
                    # a condition with no literals can be tautological this way.
                    if '"' in cond:
                        continue
                    refs = ["".join(g[:2]) + g[2] + g[3] for g in CELL_REF.findall(cond)]
                    bare = re.findall(r"\b([A-Z]{1,3}\d+)\b", re.sub(CELL_REF, "", cond))
                    allrefs = refs + bare
                    if allrefs and len(set(allrefs)) == 1 and len(allrefs) > 1:
                        fake.append(f"{c.coordinate} compares a cell with itself ({label})")
        if fake:
            fails.append(f"{len(fake)} controls cannot fail: {fake[:3]}")

    # 5. the sheet must be labelled in the currency the company reports in
    ccy = (universe.get("currency") or "").strip().upper()
    if ccy:
        wrong = []
        for t in STATEMENTS + ("Valuation",):
            if t not in wb.sheetnames:
                continue
            head = str(wb[t]["A4"].value or "")
            if head and "million" in head.lower() and ccy not in head.upper():
                wrong.append(f"{t}!A4='{head}'")
        if wrong:
            fails.append(f"sheets labelled in the wrong currency (reports in {ccy}): {wrong[:3]}")

    return fails


# ------------------------------------------------------------- dynamic checks
def _solver(path):
    return formulas.ExcelModel().loads(str(path)).finish().calculate()


def _reader(sol, wb):
    def val_at(sheet, cell):
        key = f"{sheet.upper()}'!{cell}"
        x = next((sol[k] for k in sol if k.upper().endswith(key)), None)
        try:
            return float(x.value[0, 0])
        except Exception:
            try:
                return x.value[0, 0]
            except Exception:
                c = wb[sheet][cell].value
                return float(c) if isinstance(c, (int, float)) else None
    return val_at


def scenario_switch(wb):
    """Locate the scenario selector, so every case gets calculated, not just
    the one that happens to be saved in the file."""
    if "Assumptions" not in wb.sheetnames:
        return None
    ws = wb["Assumptions"]
    for r in range(1, min(ws.max_row, 30) + 1):
        for col in range(1, 5):
            lab = str(ws.cell(row=r, column=col).value or "").lower()
            if "scenario" in lab:
                for cc in range(col, col + 3):
                    v = ws.cell(row=r, column=cc).value
                    if isinstance(v, int) and 1 <= v <= 3:
                        return ws.cell(row=r, column=cc).coordinate
    return None


def dynamic_checks(path, wb, universe, label=""):
    """Everything that needs the workbook calculated."""
    fails, info = [], {}
    try:
        sol = _solver(path)
    except Exception as e:
        return [f"workbook will not calculate: {type(e).__name__}: {e}"], info
    val_at = _reader(sol, wb)
    tag = f"[{label}] " if label else ""

    # the balance sheet must close
    br = _rows(wb["Fcst_BS"])
    row = next((r for k, r in br.items() if k.upper().startswith("BALANCE CHECK")), None)
    if not row:
        fails.append(f"{tag}no balance check on the forecast balance sheet")
    else:
        diffs = [val_at("Fcst_BS", f"{c}{row}") for c in FCOLS]
        info["max_imbalance"] = max((abs(d) for d in diffs if isinstance(d, float)), default=None)
        if info["max_imbalance"] is None:
            fails.append(f"{tag}balance check does not evaluate")
        elif info["max_imbalance"] > 1.0:
            fails.append(f"{tag}balance sheet out by up to {info['max_imbalance']:,.0f}")

    # cash must never go negative
    cr = _rows(wb["Fcst_CF"])
    crow = cr.get("CLOSING CASH")
    if crow:
        cash = [val_at("Fcst_CF", f"{c}{crow}") for c in FCOLS]
        info["min_cash"] = min((x for x in cash if isinstance(x, float)), default=None)
        if info["min_cash"] is not None and info["min_cash"] < -1.0:
            fails.append(f"{tag}cash goes negative, low point {info['min_cash']:,.0f}")

    # a bank needs a loan book with money in it, not a row with the right label
    sector = dr.classify(universe)
    info["sector"] = sector
    if sector == dr.BANK and "Schedules" in wb.sheetnames:
        sr = _rows(wb["Schedules"])
        for want, human in (("Closing net loans", "loan book"),
                            ("Customer deposits", "deposit base")):
            r = next((v for k, v in sr.items() if k.strip().lower() == want.lower()), None)
            if not r:
                fails.append(f"{tag}a bank with no {human} schedule")
                continue
            vals = [val_at("Schedules", f"{c}{r}") for c in FCOLS]
            if all(isinstance(v, float) and abs(v) < 1.0 for v in vals):
                fails.append(f"{tag}the {human} is zero in every forecast year")

    # opening equity must exist, and no plug may carry the balance sheet
    if "Fcst_BS" in wb.sheetnames:
        eq = next((r for k, r in br.items() if k.strip().lower() in ("equity", "total equity")), None)
        if eq:
            v = val_at("Fcst_BS", f"D{eq}")
            if isinstance(v, float) and abs(v) < 1.0:
                fails.append(f"{tag}opening equity is zero")
        ta = next((r for k, r in br.items() if k.upper().startswith("TOTAL ASSETS")), None)
        for k, r in br.items():
            if any(p in k.lower() for p in PLUG_LABELS) and ta:
                plug, assets = val_at("Fcst_BS", f"D{r}"), val_at("Fcst_BS", f"D{ta}")
                if isinstance(plug, float) and isinstance(assets, float) and assets:
                    share = abs(plug) / abs(assets)
                    if share > 0.05:
                        fails.append(f"{tag}'{k}' is {share:.0%} of the balance sheet — a plug")

    # every control must read PASS
    if "Sources_and_Controls" in wb.sheetnames:
        cs = wb["Sources_and_Controls"]
        for r in range(1, cs.max_row + 1):
            label_ = str(cs.cell(row=r, column=1).value or "").strip()
            if not label_ or label_.startswith(("KES", "KShs", "These", "Item", "Controls")):
                continue
            got = [val_at("Sources_and_Controls", f"{c}{r}") for c in FCOLS]
            got = [g for g in got if isinstance(g, str)]
            if any(g == "FAIL" for g in got):
                fails.append(f"{tag}control failed: {label_}")

    # the rating must be binary, and the upside must be sane
    if "Valuation" in wb.sheetnames:
        vs = wb["Valuation"]
        vr = next((r for r in range(1, vs.max_row + 1)
                   if "RECOMMENDATION" in str(vs.cell(row=r, column=1).value or "")), None)
        if vr:
            if "Hold" in str(vs.cell(row=vr, column=4).value):
                fails.append(f"{tag}the rating allows Hold")
            info["rating"] = val_at("Valuation", f"D{vr}")
        ur = next((r for r in range(1, vs.max_row + 1)
                   if "upside" in str(vs.cell(row=r, column=1).value or "").lower()), None)
        if ur:
            up = val_at("Valuation", f"D{ur}")
            info["upside"] = up
            # 3x is already an extraordinary call; 140x is a units bug
            if isinstance(up, float) and abs(up) > 3.0:
                fails.append(f"{tag}upside of {up:+.0%} is not a view, it is a units error")
    return fails, info


# --------------------------------------------------------------------- driver
def audit(path, universe, base_only=False):
    import shutil
    import tempfile
    wb = load_workbook(path)
    fails = static_checks(wb, universe)

    f, info = dynamic_checks(path, wb, universe)
    fails += f

    switch = scenario_switch(wb)
    if switch and not base_only:
        shipped = wb["Assumptions"][switch].value
        for code, name in ((1, "Bear"), (2, "Base"), (3, "Bull")):
            if code == shipped:
                continue                       # already covered above
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / path.name
                shutil.copy(path, p)
                w2 = load_workbook(p)
                w2["Assumptions"][switch] = code
                w2.save(p)
                f2, _ = dynamic_checks(p, load_workbook(p), universe, label=name)
                fails += f2
    elif not switch:
        fails.append("no scenario switch found, so the scenarios cannot be tested")
    return fails, info


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    base_only = "--base-only" in sys.argv
    only = args[0] if args else None
    uni = json.loads((ROOT / "data" / "universe.json").read_text())
    total = clean = 0
    problems = {}
    print(f"{'company':<34}{'sector':<10}{'imbalance':>12}{'min cash':>14}{'rating':>8}  status")
    for u in uni:
        slug = u["slug"]
        if only and only not in slug:
            continue
        p = ROOT / "output" / "models" / f"{slug}_model.xlsx"
        if not p.exists():
            continue
        total += 1
        fails, info = audit(p, u, base_only=base_only)
        mi, mc = info.get("max_imbalance"), info.get("min_cash")
        status = "OK" if not fails else f"{len(fails)} ISSUE(S)"
        if not fails:
            clean += 1
        else:
            problems[u["name"]] = fails
        print(f"  {u['name'][:32]:<34}{info.get('sector','?'):<10}"
              f"{(f'{mi:,.4f}' if isinstance(mi,float) else '-'):>12}"
              f"{(f'{mc:,.0f}' if isinstance(mc,float) else '-'):>14}"
              f"{str(info.get('rating','-')):>8}  {status}", flush=True)
    print(f"\n{clean} of {total} models pass every check")
    if problems:
        print("\nISSUES")
        for name, fs in problems.items():
            for f in fs:
                print(f"  {name}: {f}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
