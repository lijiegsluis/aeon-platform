"""Locate and extract the primary financial statements from an IFRS annual
report PDF, with page references, so every figure the model uses can be traced.

This is deliberately conservative: it finds the statement pages by anchor text
and pulls labelled line items with their reported values. A human still reviews
before anything is marked approved (the platform's source-linked contract), but
this turns a 180-page PDF into located, page-referenced figures rather than a
manual read.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

# Anchor phrases that mark each primary statement in an IFRS report.
STATEMENT_ANCHORS = {
    "income_statement": [
        "statement of profit or loss",
        "income statement",
        "statement of comprehensive income",
    ],
    "balance_sheet": [
        "statement of financial position",
        "balance sheet",
    ],
    "cash_flow": [
        "statement of cash flows",
        "cash flow statement",
    ],
    "notes": ["notes to the financial statements", "notes to the consolidated"],
}

# Line items worth pulling per statement (uses the standardisation vocabulary).
KEY_LINES = {
    "income_statement": ["revenue", "service revenue", "ebitda", "operating profit",
                         "profit before tax", "profit for the year", "net interest income",
                         "earnings per share"],
    "balance_sheet": ["total assets", "total equity", "borrowings", "cash and cash equivalents",
                      "total liabilities", "loans and advances", "customer deposits"],
    "cash_flow": ["cash generated from operat", "net cash from operat", "purchase of property",
                  "dividends paid", "capital expenditure"],
}

_NUM = re.compile(r"\(?-?\d[\d,]*(?:\.\d+)?\)?")


def _to_float(token: str) -> float | None:
    t = token.strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.replace("(", "").replace(")", "").replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def _dense_rows(rows: list[str]) -> int:
    """Rows that read like a statement line: a label plus >=2 real figures."""
    dense = 0
    for r in rows:
        big = [t for t in _NUM.findall(r)
               if len(t.replace(",", "").replace("(", "").replace(")", "").replace(".", "").lstrip("-")) >= 3]
        if len(big) >= 2:
            dense += 1
    return dense


def locate_statements(doc: fitz.Document) -> dict[str, Any]:
    """Find the real statement pages, not narrative mentions.

    A primary statement page has its heading near the top AND a dense block of
    numeric rows. Full integrated reports mention "total assets" in prose, so we
    score every page by numeric density and keep the best-scoring page whose
    heading (top of page) matches each statement anchor.
    """
    best: dict[str, tuple[int, int]] = {}  # stmt -> (score, page)
    notes_pages: list[int] = []
    for i in range(doc.page_count):
        rows = _rows_by_y(doc, i + 1)
        dense = _dense_rows(rows)
        full = " ".join(rows).lower()
        if any(a in full for a in STATEMENT_ANCHORS["notes"]):
            notes_pages.append(i + 1)
        if dense < 6:  # narrative pages are not statement tables
            continue
        top = " ".join(rows[:14]).lower()  # the page heading region
        for key, anchors in STATEMENT_ANCHORS.items():
            if key == "notes":
                continue
            if any(a in top for a in anchors) and dense > best.get(key, (0, 0))[0]:
                best[key] = (dense, i + 1)
    located: dict[str, Any] = {k: v[1] for k, v in best.items()}
    located["notes_page_count"] = len(notes_pages)
    return located


def _rows_by_y(doc: fitz.Document, page_1based: int, y_tol: float = 3.0) -> list[str]:
    """Reconstruct visual rows from word coordinates.

    Financial statements are tables, so plain text splits the label column from
    the number columns. Grouping words by their y-position rebuilds each row as
    "label  value  value" the way it reads on the page.
    """
    words = doc.load_page(page_1based - 1).get_text("words")  # (x0,y0,x1,y1,word,...)
    words.sort(key=lambda w: (round(w[1] / y_tol), w[0]))
    rows: list[str] = []
    current_y: float | None = None
    buf: list[str] = []
    for x0, y0, _x1, _y1, word, *_ in words:
        if current_y is None or abs(y0 - current_y) <= y_tol:
            buf.append(word)
            current_y = y0 if current_y is None else current_y
        else:
            rows.append(" ".join(buf))
            buf = [word]
            current_y = y0
    if buf:
        rows.append(" ".join(buf))
    return rows


def extract_line_items(doc: fitz.Document, page_1based: int, wanted: list[str]) -> list[dict[str, Any]]:
    """Pull labelled line items + reported values from a statement page."""
    rows: list[dict[str, Any]] = []
    for line in _rows_by_y(doc, page_1based):
        low = line.lower()
        for label in wanted:
            if label in low:
                nums = [_to_float(n) for n in _NUM.findall(line)]
                nums = [n for n in nums if n is not None and abs(n) >= 1]
                # Drop a leading note-reference (small integer just before the
                # reported figures, e.g. "Loans ... 22 882,457 819,236").
                if (len(nums) >= 3 and float(nums[0]).is_integer() and abs(nums[0]) < 100
                        and abs(nums[1]) >= 1000):
                    nums = nums[1:]
                if nums:
                    rows.append({"raw_label": line.strip()[:56], "matched": label,
                                 "values": nums[:2], "page": page_1based})
                break
    return rows


def extract(pdf_path: str | Path) -> dict[str, Any]:
    """Locate statements and pull key line items with page references."""
    doc = fitz.open(pdf_path)
    located = locate_statements(doc)
    figures: dict[str, list[dict[str, Any]]] = {}
    for stmt, wanted in KEY_LINES.items():
        page = located.get(stmt)
        figures[stmt] = extract_line_items(doc, page, wanted) if page else []
    result = {"pdf": str(pdf_path), "pages": doc.page_count, "located": located, "figures": figures}
    doc.close()
    return result


if __name__ == "__main__":
    import json
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else (
        "/Users/nikolasdionsavio/Documents/Personal Projects/Hamilcar_project/"
        "Sample_ER_algorithm/data/raw_docs/safaricom_real/"
        "safaricom-fy2025-financial-statements.pdf"
    )
    out = extract(path)
    print(f"PDF: {Path(out['pdf']).name}  ({out['pages']} pages)")
    print(f"Located: income p{out['located'].get('income_statement')}, "
          f"balance p{out['located'].get('balance_sheet')}, "
          f"cash-flow p{out['located'].get('cash_flow')}, "
          f"notes sections {out['located'].get('notes_page_count')}")
    for stmt, rows in out["figures"].items():
        print(f"\n{stmt} (page {out['located'].get(stmt)}):")
        for r in rows[:8]:
            print(f"  p{r['page']:>3}  {r['matched']:22} {r['raw_label']:42} -> {r['values']}")
