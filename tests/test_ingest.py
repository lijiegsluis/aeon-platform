"""Deterministic ingestion: file parsers, confidence, merge, web plumbing."""

import io

from aeon_nimbus import ingest

COMPANY = {"unit": "millions", "currency": "KES",
           "financials": [{"fy": "FY2024"}, {"fy": "FY2023"}]}

CSV = ("Item,FY2025,FY2024,FY2023\n"
       "Revenue,350000,335250,310900\n"
       "EBITDA,190000,180100,168400\n"
       "Profit for the year,72000,69800,62100\n"
       "Total assets,560000,545000,520000\n"
       "Total equity,220000,210000,198000\n"
       "Borrowings,118000,120000,131000\n"
       "Cash and cash equivalents,45000,42000,38000\n"
       "Purchase of property plant and equipment,(43000),(41000),(39500)\n").encode()


def test_csv_maps_items_years_and_signs():
    props = ingest.parse_file("scom.csv", CSV, COMPANY)
    by = {(p["fy"], p["item"]): p for p in props}
    # canonical mapping, including the identity alias "Revenue" the shared vocab lacks
    assert by[("FY2025", "revenue")]["value"] == 350000
    assert by[("FY2024", "net_income")]["value"] == 69800      # "Profit for the year"
    assert by[("FY2023", "total_debt")]["value"] == 131000     # "Borrowings"
    # bracketed cash-flow figure keeps its negative sign
    assert by[("FY2025", "capex")]["value"] == -43000
    # statement classification + confidence carried through
    assert by[("FY2025", "revenue")]["statement"] == "income_statement"
    assert by[("FY2025", "revenue")]["confidence"] >= 0.9
    # three years, eight line items
    assert len({p["fy"] for p in props}) == 3
    assert {"revenue", "ebitda", "net_income", "total_assets", "total_equity",
            "total_debt", "cash", "capex"} <= {p["item"] for p in props}


def test_xlsx_parse():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "IS"
    for row in [["KES millions", "FY2025", "FY2024"], ["Revenue", 350000, 335250],
                ["Operating profit", 96000, 88000], ["Net income", 72000, 69800]]:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    props = ingest.parse_file("m.xlsx", buf.getvalue(), COMPANY)
    by = {(p["fy"], p["item"]): p["value"] for p in props}
    assert by[("FY2025", "revenue")] == 350000
    assert by[("FY2025", "ebit")] == 96000        # "Operating profit" -> ebit
    assert by[("FY2024", "net_income")] == 69800


def test_unsupported_type_raises():
    import pytest
    with pytest.raises(ValueError):
        ingest.parse_file("notes.txt", b"hello", COMPANY)


def test_merge_sets_values_and_derives_net_debt():
    props = ingest.parse_file("scom.csv", CSV, COMPANY)
    merged = ingest.merge_facts(COMPANY, props)
    f25 = next(f for f in merged["financials"] if f["fy"] == "FY2025")
    assert f25["revenue"] == 350000
    # net debt derived from total_debt - cash when not explicitly provided
    assert f25["net_debt"] == 118000 - 45000
    # merge does not mutate the input
    assert all("revenue" not in f for f in COMPANY["financials"])


def test_confidence_threshold_split():
    props = ingest.parse_file("scom.csv", CSV, COMPANY)
    hi = [p for p in props if p["confidence"] >= 0.85]
    lo = [p for p in props if p["confidence"] < 0.85]
    assert hi and not lo  # clean exact matches all clear a 0.85 bar


def test_collect_web_is_source_gated_and_degrades():
    # a fetch that returns nothing useful yields no proposals (never fabricates)
    calls = []

    def fetch(url):
        calls.append(url)
        return None, None

    company = {"ticker": "SCOM", "annual_report_url": "https://example.com/ar.pdf",
               "currency": "KES", "unit": "millions", "financials": []}
    out = ingest.collect_web(company, fetch=fetch)
    assert out == []
    assert calls and calls[0] == "https://example.com/ar.pdf"  # official filing tried first
