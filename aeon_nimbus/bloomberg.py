"""Populate the Bloomberg earnings-estimates template from platform forecasts.

Bloomberg agrees a column layout with each contributor once, and its own
instructions are blunt about what happens afterwards: "any change to the columns
or structure of the file will cause processing failure". So this module never
rebuilds the workbook. It opens the .xlsx as the zip archive it is, rewrites
cell VALUES inside one worksheet part, and copies every other part through byte
for byte.

That distinction matters more than it sounds. Loading this template with
openpyxl and saving it drops nine parts, including all six customXml items
Bloomberg ships in the file, and rewrites the comments and shared strings. The
result opens fine in Excel and would be rejected downstream. Measured, not
assumed: see tests/test_bloomberg.py.

WHAT THE PLATFORM CAN AND CANNOT SUPPLY

The template asks for far more than the platform forecasts. Annual estimates it
has; quarterly and semi-annual splits it does not model at all, and several
annual lines are held as history only. Nothing is guessed to fill a column. Each
unpopulated field comes back as a gap with the reason, so the analyst is told
"FY1 dividends per share: the platform holds dividends for FY2022-FY2026 but
does not forecast them" rather than being handed a blank cell.

FISCAL YEAR ALIGNMENT

Bloomberg's rule: column J (FYE-YYMM) is FY0, the most recent REPORTED year.
FY1 is the first ESTIMATED year. If the template's FY0 disagrees with the
platform's last reported year, every estimate in the row is filed against the
wrong period. That is silent and it is exactly the class of error this platform
exists to prevent, so it is checked per company and blocks the row.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any, Callable, Iterable

# The sheet Bloomberg reads. The others are instructions and lookups.
SHEET_TITLE = "Template"

# Identity columns, by their header text. Everything else is an estimate.
IDENTITY = ("COMPANY NAME", "TICKER & EXCHANGE CODE", "ISIN / SEDOL / CUSIP",
            "ANALYST (FULL NAME)", "FYE-YYMM", "CURRENCY", "BASIC/DILUTED",
            "ACCT STAND", "PARENT / CONSOLIDATED", "ANALYST REC", "PTG",
            "HORIZON", "TARGET PRICE CURRENCY", "LAST REVIEW DATE",
            "EE REPORT DATE")


def _norm(h: Any) -> str:
    """Header text as a comparable key. The template has trailing spaces on
    several headers and inconsistent spacing around slashes."""
    return re.sub(r"\s+", " ", str(h or "").replace("\xa0", " ")).strip().upper()


# ---------------------------------------------------------------- derivation
#
# Each rule takes the company's deep record and the estimate index (0 = FY1,
# 1 = FY2) and returns a number, or None with a reason. Returning a reason
# rather than nothing is the point: a blank cell tells the analyst nothing.

def _series(deep: dict, key: str) -> list:
    return list((deep.get("forecast") or {}).get(key) or [])


def _hist_years(deep: dict) -> list:
    return list(deep.get("years") or [])


def _at(deep: dict, key: str, i: int):
    s = _series(deep, key)
    return s[i] if i < len(s) else None


def _shares(deep: dict):
    return ((deep.get("keystats") or {}).get("shares_m")) or None


def _per_share(deep: dict, key: str, i: int):
    v, n = _at(deep, key, i), _shares(deep)
    if v is None or not n:
        return None
    return round(v / n, 4)


def _history_only(field: str, deep_key: str):
    """A line the platform holds as history but does not forecast.

    Says so with the years it does hold, because "missing" and "held for five
    reported years but not projected" call for different work.
    """
    def rule(deep, i):
        held = deep.get(deep_key)
        yrs = _hist_years(deep)
        if held and yrs:
            return None, (f"held for {yrs[0]}-{yrs[-1]} as reported history, "
                          f"not forecast")
        return None, "not held by the platform"
    return rule


def _ok(fn: Callable[[dict, int], Any], why_missing: str):
    def rule(deep, i):
        v = fn(deep, i)
        return (v, None) if v is not None else (None, why_missing)
    return rule


# Header (normalised) -> rule. Anything absent from this map is a field the
# platform makes no attempt at, and is reported as such.
RULES: dict[str, Callable[[dict, int], tuple]] = {
    "EPS ADJ FY1":            _ok(lambda d, i: _per_share(d, "net_income", 0), "no forecast net income or share count"),
    "EPS ADJ FY2":            _ok(lambda d, i: _per_share(d, "net_income", 1), "forecast does not reach FY2"),
    "EPS REP / GAAP FY1":     _ok(lambda d, i: _per_share(d, "net_income", 0), "no forecast net income or share count"),
    "EPS REP / GAAP FY2":     _ok(lambda d, i: _per_share(d, "net_income", 1), "forecast does not reach FY2"),
    "SALES FY1":              _ok(lambda d, i: _at(d, "revenue", 0), "no forecast revenue"),
    "EBITDA FY1":             _ok(lambda d, i: _at(d, "ebitda", 0), "no forecast EBITDA"),
    "EBIT FY1":               _ok(lambda d, i: _at(d, "ebit", 0), "no forecast EBIT"),
    "OP FY1":                 _ok(lambda d, i: _at(d, "ebit", 0), "no forecast EBIT"),
    "NI - ADJ FY1":           _ok(lambda d, i: _at(d, "net_income", 0), "no forecast net income"),
    "NI - REP / GAAP FY1":    _ok(lambda d, i: _at(d, "net_income", 0), "no forecast net income"),
    # Held as history, not projected. The commonest gap, and the one the
    # analyst most often needs to close by hand.
    "DPS FY1":                _history_only("DPS FY1", "dividends_paid"),
    "NET DEBT FY1":           _history_only("NET DEBT FY1", "net_debt"),
    "PTP FY1":                _history_only("PTP FY1", "pbt"),
    "BPS FY1":                _history_only("BPS FY1", "total_equity"),
    "CPS FY1":                _history_only("CPS FY1", "operating_cash_flow"),
    "EV FY1":                 _history_only("EV FY1", "net_debt"),
}

# Splits the platform does not model. Named so the gap report can group them
# instead of listing sixty near-identical rows.
_PERIODIC = re.compile(r"\b(QTR[1-4]|S[12])\b")


def field_kind(header: str) -> str:
    h = _norm(header)
    if h in (_norm(x) for x in IDENTITY):
        return "identity"
    if _PERIODIC.search(h):
        return "periodic"
    return "annual"


# ------------------------------------------------------------------ template

def read_layout(xlsx: bytes) -> dict:
    """Column letters keyed by header text, read from the template itself.

    Deliberately not hardcoded. Bloomberg agrees a layout per contributor and
    may agree a different one later; reading row 1 means a revised template
    works without a code change, and an unrecognised header is reported rather
    than silently written to the wrong column.
    """
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    if SHEET_TITLE not in wb.sheetnames:
        raise ValueError(f"no {SHEET_TITLE!r} sheet; this is not the Bloomberg template")
    ws = wb[SHEET_TITLE]
    cols: dict[str, str] = {}
    for cell in next(ws.iter_rows(min_row=1, max_row=1)):
        if cell.value is not None and _norm(cell.value):
            cols.setdefault(_norm(cell.value), cell.column_letter)
    wb.close()
    return cols


def read_rows(xlsx: bytes) -> list[dict]:
    """The companies already in the template, with their row numbers."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    ws = wb[SHEET_TITLE]
    out = []
    for r, row in enumerate(ws.iter_rows(min_row=2, max_col=13, values_only=True), start=2):
        name = (row[0] or "")
        if not str(name).strip():
            continue
        out.append({"row": r, "name": str(name).strip(),
                    "ticker": str(row[1] or "").strip(),
                    "isin": str(row[2] or "").strip(),
                    "analyst": str(row[8] or "").strip(),
                    "fye": str(row[9] or "").strip(),
                    "currency": str(row[10] or "").strip()})
    wb.close()
    return out


def first_free_row(xlsx: bytes) -> int:
    rows = read_rows(xlsx)
    return (max(r["row"] for r in rows) + 1) if rows else 2


# ------------------------------------------------------- fiscal-year checking

_FYE = re.compile(r"^\s*(\d{2})\s*/\s*(\d{2})\s*$")


def fy0_from_fye(fye: str) -> int | None:
    """The reported year column J stands for. '26/03' -> 2026."""
    m = _FYE.match(str(fye or ""))
    if not m:
        return None
    return 2000 + int(m.group(1))


def last_reported_year(deep: dict) -> int | None:
    yrs = [y for y in _hist_years(deep) if re.search(r"\d{4}", str(y))]
    if not yrs:
        return None
    return int(re.search(r"(\d{4})", str(yrs[-1])).group(1))


def first_forecast_year(deep: dict) -> int | None:
    yrs = (deep.get("forecast") or {}).get("years") or []
    if not yrs:
        return None
    m = re.search(r"(\d{4})", str(yrs[0]))
    return int(m.group(1)) if m else None


def check_alignment(deep: dict, fye: str) -> str | None:
    """Whether the template's FY0 matches the platform's last reported year.

    A mismatch files every estimate in the row against the wrong period, and
    nothing downstream would notice. Blocks the row rather than warning.
    """
    tmpl_fy0 = fy0_from_fye(fye)
    plat_fy0 = last_reported_year(deep)
    if tmpl_fy0 is None:
        return f"FYE-YYMM {fye!r} is not in YY/MM form, so FY0 cannot be read"
    if plat_fy0 is None:
        return "the platform holds no reported years for this company"
    if tmpl_fy0 != plat_fy0:
        return (f"template FY0 is {tmpl_fy0} but the platform's last reported "
                f"year is FY{plat_fy0}. Estimates would be filed against the "
                f"wrong period.")
    return None


# --------------------------------------------------------------- populating

def populate(deep: dict, layout: dict, fye: str) -> tuple[dict, list[dict]]:
    """Values by column letter, plus the gaps and why each one is a gap."""
    values: dict[str, Any] = {}
    gaps: list[dict] = []

    blocked = check_alignment(deep, fye)
    if blocked:
        return {}, [{"field": "FISCAL YEAR", "column": "J", "kind": "blocking",
                     "reason": blocked}]

    periodic = 0
    for header, col in layout.items():
        kind = field_kind(header)
        if kind == "identity":
            continue
        if kind == "periodic":
            periodic += 1
            continue
        rule = RULES.get(header)
        if rule is None:
            gaps.append({"field": header, "column": col, "kind": "unmapped",
                         "reason": "the platform does not model this measure"})
            continue
        value, why = rule(deep, 0)
        if value is None:
            gaps.append({"field": header, "column": col, "kind": "missing",
                         "reason": why or "not available"})
        else:
            values[col] = value
    if periodic:
        gaps.append({"field": f"{periodic} quarterly and half-year columns",
                     "column": "", "kind": "periodic",
                     "reason": "the platform forecasts annually only"})
    return values, gaps


# ------------------------------------------------------------ surgical write

_CELL = r'<c r="{ref}"((?:\s+[a-zA-Z:]+="[^"]*")*)\s*(?:/>|>.*?</c>)'


def _set_cells(sheet_xml: str, values: dict[str, Any]) -> tuple[str, list[str]]:
    """Write values into existing cells, keeping each cell's style index.

    The style attribute (s=) is what carries the formatting Bloomberg's parser
    and the analyst both expect, so it is preserved verbatim. The type
    attribute (t=) is dropped for numbers and set to inline for text, because a
    number left pointing at the shared-string table reads as a string.
    """
    missing = []
    for ref, val in values.items():
        pat = re.compile(_CELL.format(ref=re.escape(ref)), re.S)

        def repl(m, val=val, ref=ref):
            attrs = re.sub(r'\s+t="[^"]*"', "", m.group(1))
            if isinstance(val, (int, float)):
                return f'<c r="{ref}"{attrs}><v>{val}</v></c>'
            text = (str(val).replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;"))
            return f'<c r="{ref}"{attrs} t="inlineStr"><is><t>{text}</t></is></c>'

        sheet_xml, n = pat.subn(repl, sheet_xml, count=1)
        if n == 0:
            missing.append(ref)
    return sheet_xml, missing


def _sheet_part(z: zipfile.ZipFile) -> str:
    """Which worksheet part holds the Template sheet.

    Resolved through the workbook relationships rather than assumed to be
    sheet1.xml, because the order of the parts is not guaranteed to match the
    order of the tabs.
    """
    import xml.etree.ElementTree as ET
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid = None
    for sh in wb.findall(".//m:sheets/m:sheet", ns):
        if sh.get("name") == SHEET_TITLE:
            rid = sh.get(f"{{{ns['r']}}}id")
            break
    if not rid:
        raise ValueError(f"no {SHEET_TITLE!r} sheet in the workbook")
    for rel in rels:
        if rel.get("Id") == rid:
            target = rel.get("Target").lstrip("/")
            return target if target.startswith("xl/") else f"xl/{target}"
    raise ValueError("the Template sheet has no relationship target")


def write(template: bytes, edits: dict[int, dict[str, Any]]) -> bytes:
    """Return the template with `edits` applied. Nothing else is touched.

    edits: {row_number: {column_letter: value}}

    Every part except the one worksheet is copied through byte for byte, which
    is what keeps Bloomberg's customXml, its comments and the shared string
    table exactly as they arrived.
    """
    zin = zipfile.ZipFile(io.BytesIO(template))
    part = _sheet_part(zin)
    xml = zin.read(part).decode("utf-8")

    flat: dict[str, Any] = {}
    for row, cols in edits.items():
        for col, val in cols.items():
            flat[f"{col}{row}"] = val

    xml, missing = _set_cells(xml, flat)
    if missing:
        raise ValueError(f"these cells are not in the sheet: {missing[:8]}")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = xml.encode("utf-8") if item.filename == part else zin.read(item.filename)
            zout.writestr(item, data)
    return buf.getvalue()

# ------------------------------------------------------------- auditing what
# is already in the file

# Which reported series an estimate column would be confused with, if somebody
# pasted last year's actuals into it. Keyed by the platform's own series name.
_ACTUAL_OF = {
    "SALES FY1": "revenue",
    "EBITDA FY1": "ebitda",
    "EBIT FY1": "ebit",
    "OP FY1": "ebit",
    "NI - ADJ FY1": "net_income",
    "NI - REP / GAAP FY1": "net_income",
    "PTP FY1": "pbt",
    "NET DEBT FY1": "net_debt",
}


def audit_existing(existing: dict, deep: dict, layout: dict) -> list[dict]:
    """Problems with values already sitting in the row.

    Two things are worth catching, and both were found in the shipped template.

    FY0 ACTUALS IN AN FY1 COLUMN. Someone fills the estimate columns from the
    last annual report. Bloomberg then ingests last year's results as this
    year's forecast, and nothing downstream can tell. Detected by matching the
    cell against the platform's own reported figure for that line.

    A PER-SHARE COLUMN HOLDING A WHOLE-COMPANY FIGURE. An EPS column with a
    six-figure number in it is revenue or net income in the wrong place. Caught
    on magnitude against the share count.

    `existing` is {column_letter: value} read from the row.
    """
    by_col = {c: h for h, c in layout.items()}
    shares = _shares(deep)
    out: list[dict] = []

    for col, val in existing.items():
        if not isinstance(val, (int, float)):
            continue
        header = by_col.get(col)
        if not header:
            continue

        series_key = _ACTUAL_OF.get(header)
        if series_key:
            actual = (deep.get(series_key) or [])
            if actual and _close(val, actual[-1]):
                yrs = _hist_years(deep)
                out.append({
                    "column": col, "field": header, "value": val,
                    "kind": "actuals_as_estimate",
                    "reason": (f"equals the reported {yrs[-1] if yrs else 'FY0'} "
                               f"figure. This is an estimate column: Bloomberg "
                               f"would file last year's actual as the forecast.")})
                continue

        # EPS, DPS, BPS and CPS are per-share. A value near the whole-company
        # scale is a pasted total.
        if re.match(r"^(EPS|DPS|BPS|CPS)\b", header) and shares and abs(val) > shares:
            out.append({
                "column": col, "field": header, "value": val,
                "kind": "not_per_share",
                "reason": (f"a per-share column holding {val:,.1f}, which "
                           f"exceeds the {shares:,.0f}m share count. This is a "
                           f"whole-company figure in the wrong column.")})
    return out


def _close(a, b, tol: float = 1e-6) -> bool:
    try:
        if b == 0:
            return abs(a) < tol
        return abs(a - b) / abs(b) < tol
    except (TypeError, ZeroDivisionError):
        return False


def read_values(xlsx: bytes, row: int, layout: dict) -> dict:
    """The estimate values already in one row, by column letter."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(xlsx), data_only=True)
    ws = wb[SHEET_TITLE]
    est = {c for h, c in layout.items() if field_kind(h) != "identity"}
    out = {}
    for col in est:
        v = ws[f"{col}{row}"].value
        if v is not None and str(v).strip() not in ("", "\xa0"):
            out[col] = v
    wb.close()
    return out
