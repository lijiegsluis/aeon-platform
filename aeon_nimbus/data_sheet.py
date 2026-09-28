"""The editing surface: a browser grid that exports a live Excel workbook.

The analyst edits in the browser and gets a finished workbook out. The trick
that keeps that cheap is what the export carries: FORMULAS, not values.

A browser formula engine is only ever a preview. The exported workbook holds the
formula strings the analyst typed, so Excel recomputes them natively on open —
which means Excel is the authority on arithmetic, there is no second engine to
disagree with it, and the analyst receives a live model rather than a picture of
one. Anything the browser preview got wrong corrects itself the moment the file
opens.

The reverse direction also works, and is kept, because it costs almost nothing:

Three facts about this platform make that nearly free:

  · the upload parser reads a plain label-by-fiscal-year grid and IGNORES any
    row whose label it does not recognise, so rows and columns can be added
    freely without corrupting anything
  · it opens the workbook with data_only=True, which reads the values Excel
    cached when it saved. Every Excel function therefore works — SUM, INDEX,
    XLOOKUP, whatever the analyst likes — with no formula engine on our side
  · ProposedFact already carries prior_value and a status, so an upload becomes
    a reviewable before-and-after rather than a silent overwrite

So the whole feature is: export a grid in exactly the shape the parser reads.
The round trip then closes by construction, and the approval path, the
provenance and the change history are the ones that already exist.

    GET  /api/studio/companies/{id}/data-sheet   -> download
    POST /api/studio/companies/{id}/upload       -> staged for review (existing)
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

NAVY, INK, MUTED, BLUE_IN = "123142", "17262E", "5C6B72", "0000FF"
AMBER, TINT = "FFF3C4", "E3EBE6"
FONT = "Calibri"
NUM = '#,##0;\\(#,##0\\);"-"'

# The order a reader expects a statement in. Anything held that is not listed
# still appears, after these, so nothing is silently dropped.
ORDER = [
    "revenue", "ebitda", "ebit", "net_income", "profit_after_tax",
    "operating_cash_flow", "capex", "free_cash_flow", "dividends_paid",
    "total_assets", "total_equity", "total_debt", "cash", "net_debt",
]
SKIP = {"fy", "source", "confidence"}


def _label(key: str) -> str:
    return key.replace("_", " ").strip().capitalize()


def build(rec: dict, universe: dict, out: Path | None = None) -> bytes:
    """Return the data sheet for one company as xlsx bytes."""
    fins = sorted([f for f in (rec.get("financials") or []) if f.get("fy")],
                  key=lambda x: str(x["fy"]))
    name = universe.get("name") or rec.get("name") or "Company"
    cur = rec.get("currency") or "USD"

    keys = [k for k in ORDER if any(k in f for f in fins)]
    keys += sorted({k for f in fins for k in f
                    if k not in keys and k not in SKIP
                    and isinstance(f.get(k), (int, float))})

    wb = Workbook()
    ws = wb.active
    ws.title = "Data"

    ws["A1"] = f"{name} — data sheet"
    ws["A1"].font = Font(name=FONT, size=14, bold=True, color=NAVY)
    ws["A2"] = (f"{cur} millions. Edit any blue figure, add rows, add columns, use any Excel "
                f"function you like, then upload this file back to the platform.")
    ws["A2"].font = Font(name=FONT, size=9, color=MUTED)
    ws["A3"] = ("Every change is shown against its current value for approval before it is "
                "applied. A row whose label the platform does not recognise is ignored, so "
                "workings can be added safely.")
    ws["A3"].font = Font(name=FONT, size=9, color=MUTED)

    # The header row the parser looks for: the row carrying the most year tokens.
    hdr = 5
    ws.cell(row=hdr, column=1, value="Item").font = Font(name=FONT, size=11, bold=True, color="FFFFFF")
    ws.cell(row=hdr, column=1).fill = PatternFill("solid", start_color=NAVY)
    for j, f in enumerate(fins):
        c = ws.cell(row=hdr, column=2 + j, value=str(f["fy"]))
        c.font = Font(name=FONT, size=11, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", start_color=NAVY)
        c.alignment = Alignment(horizontal="right")

    r = hdr + 1
    for key in keys:
        ws.cell(row=r, column=1, value=_label(key)).font = Font(name=FONT, size=11, color=INK)
        for j, f in enumerate(fins):
            v = f.get(key)
            c = ws.cell(row=r, column=2 + j,
                        value=float(v) if isinstance(v, (int, float)) else None)
            c.number_format = NUM
            # blue is the platform's convention for a figure a human may change
            c.font = Font(name=FONT, size=11, color=BLUE_IN)
            c.fill = PatternFill("solid", start_color=TINT)
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="Workings — anything below here is ignored on upload").font = \
        Font(name=FONT, size=10, bold=True, color=MUTED)
    ws.cell(row=r, column=1).fill = PatternFill("solid", start_color=AMBER)

    ws.column_dimensions["A"].width = 34
    for j in range(len(fins)):
        ws.column_dimensions[get_column_letter(2 + j)].width = 14
    ws.freeze_panes = ws.cell(row=hdr + 1, column=2)

    buf = BytesIO()
    wb.save(buf)
    data = buf.getvalue()
    if out:
        Path(out).write_bytes(data)
    return data


def from_grid(grid: dict, *, title: str = "Model", currency: str = "USD") -> bytes:
    """Turn the browser grid's state into a live Excel workbook.

    `grid` is what the front end holds:

        {"rows": [[{"v": 120.0}, {"f": "=B6*1.15"}, {"v": "Revenue"}], ...],
         "widths": [34, 14, 14]}

    A cell carries EITHER a literal value ("v") or a formula string ("f").
    Formulas are written through as formulas, so Excel computes them on open.
    That is the whole reason the browser engine can stay a preview: if it and
    Excel ever disagree, the file the analyst receives is right, because Excel
    did the arithmetic itself.

    Formatting follows the house convention already used by the models: blue for
    a figure a human typed, black for anything computed.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Model"

    ws["A1"] = title
    ws["A1"].font = Font(name=FONT, size=14, bold=True, color=NAVY)
    ws["A2"] = f"{currency} millions. Blue is an input, black is a formula."
    ws["A2"].font = Font(name=FONT, size=9, color=MUTED)

    top = 4
    for i, row in enumerate(grid.get("rows") or []):
        for j, cell in enumerate(row or []):
            if not isinstance(cell, dict):
                cell = {"v": cell}
            f, v = cell.get("f"), cell.get("v")
            c = ws.cell(row=top + i, column=1 + j)
            if isinstance(f, str) and f.strip().startswith("="):
                c.value = f.strip()
                c.font = Font(name=FONT, size=11, color=INK)      # computed
            elif v is not None and v != "":
                c.value = v
                is_num = isinstance(v, (int, float)) and not isinstance(v, bool)
                c.font = Font(name=FONT, size=11,
                              color=BLUE_IN if is_num else INK)   # typed figure
            if cell.get("bold"):
                c.font = Font(name=FONT, size=11, bold=True,
                              color=c.font.color.rgb if c.font and c.font.color else INK)
            if isinstance(c.value, (int, float)) or (
                    isinstance(c.value, str) and c.value.startswith("=")):
                c.number_format = cell.get("fmt") or NUM

    widths = grid.get("widths") or []
    for j, w in enumerate(widths):
        ws.column_dimensions[get_column_letter(1 + j)].width = w
    if not widths:
        ws.column_dimensions["A"].width = 34
    if grid.get("freeze_at"):
        ws.freeze_panes = grid["freeze_at"]

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
