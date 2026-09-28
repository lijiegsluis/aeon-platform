"""Financial label, currency, unit, and period standardisation."""

from __future__ import annotations

import re
from typing import Any


LINE_ITEM_SYNONYMS: dict[str, str] = {
    "turnover": "revenue",
    "sales": "revenue",
    "net sales": "revenue",
    "revenue from contracts with customers": "revenue",
    "cost of sales": "cost_of_sales",
    "cost of goods sold": "cost_of_sales",
    "gross profit": "gross_profit",
    "operating profit before depreciation": "ebitda",
    "ebitda": "ebitda",
    "depreciation and amortisation": "depreciation_and_amortisation",
    "depreciation and amortization": "depreciation_and_amortisation",
    "operating profit": "ebit",
    "ebit": "ebit",
    "finance income": "finance_income",
    "finance cost": "finance_cost",
    "finance costs": "finance_cost",
    "profit before tax": "profit_before_tax",
    "income tax expense": "tax",
    "tax": "tax",
    "profit for the year": "net_income",
    "net income": "net_income",
    "earnings per share": "eps",
    "cash and cash equivalents": "cash",
    "cash": "cash",
    "trade and other receivables": "receivables",
    "inventories": "inventory",
    "inventory": "inventory",
    "current assets": "current_assets",
    "property plant and equipment": "ppe",
    "property, plant and equipment": "ppe",
    "right of use assets": "right_of_use_assets",
    "intangible assets": "intangibles",
    "total assets": "total_assets",
    "borrowings": "total_debt",
    "loans and borrowings": "total_debt",
    "interest-bearing liabilities": "total_debt",
    "short term debt": "short_term_debt",
    "long term debt": "long_term_debt",
    "lease liabilities": "lease_liabilities",
    "trade and other payables": "payables",
    "total liabilities": "total_liabilities",
    "share capital": "share_capital",
    "retained earnings": "retained_earnings",
    "non-controlling interests": "minority_interest",
    "total equity": "total_equity",
    "net cash generated from operating activities": "operating_cash_flow",
    "operating cash flow": "operating_cash_flow",
    "purchase of property plant and equipment": "capex",
    "purchase of ppe": "capex",
    "additions to property and equipment": "capex",
    "capital expenditure": "capex",
    "free cash flow": "free_cash_flow",
    "dividends paid": "dividends_paid",
    "net change in cash": "net_change_in_cash",
}

CURRENCY_PATTERNS = {
    "USD": r"\b(US\$|USD|United States dollars?)\b",
    "GBP": r"\b(GBP|pounds sterling|sterling|£)\b",
    "EUR": r"\b(EUR|euro|€)\b",
    "KES": r"\b(KES|KSh|Kenya shillings?)\b",
    "NGN": r"\b(NGN|Naira|₦)\b",
    "TZS": r"\b(TZS|Tanzanian shillings?)\b",
    "ZMW": r"\b(ZMW|Zambian kwacha)\b",
    "XOF": r"\b(XOF|CFA franc)\b",
    "MAD": r"\b(MAD|Moroccan dirham)\b",
    "EGP": r"\b(EGP|Egyptian pound)\b",
    "ZAR": r"\b(ZAR|Rand|South African rand)\b",
}

UNIT_PATTERNS = {
    "billions": r"\b(billions?|bn)\b",
    "millions": r"\b(millions?|mn|m)\b",
    "thousands": r"\b(thousands?|000)\b",
    "actual": r"\b(actual|units)\b",
}


def _normalise_label(label: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", label).lower()
    return re.sub(r"\s+", " ", cleaned).strip()


def standardise_line_item(raw_label: str) -> str | None:
    """Map a raw accounting label into Aeon Nimbus's standard template."""

    label = _normalise_label(raw_label)
    if label in LINE_ITEM_SYNONYMS:
        return LINE_ITEM_SYNONYMS[label]
    for synonym, standard in LINE_ITEM_SYNONYMS.items():
        if synonym in label:
            return standard
    return None


def detect_currency_and_units(text_or_document: str) -> dict[str, str | None]:
    """Detect likely currency and unit from document text."""

    currency = None
    unit = None
    text = text_or_document or ""
    for code, pattern in CURRENCY_PATTERNS.items():
        if re.search(pattern, text, flags=re.IGNORECASE):
            currency = code
            break
    for unit_name, pattern in UNIT_PATTERNS.items():
        if re.search(pattern, text, flags=re.IGNORECASE):
            unit = unit_name
            break
    return {"currency": currency, "unit": unit}


def normalise_currency(value: float, source_currency: str, target_currency: str, fx_rate: float) -> float:
    """Convert financial values using a supplied FX rate."""

    if source_currency.upper() == target_currency.upper():
        return value
    return value * fx_rate


def standardise_fiscal_year(company_id: str, raw_period: str) -> str:
    """Align raw periods to a clean FY year string."""

    match = re.search(r"(20\d{2}|19\d{2})", raw_period)
    if match:
        return f"FY{match.group(1)}"
    return raw_period.strip().upper()


def clean_negative_values(value: Any) -> float | None:
    """Detect brackets, minus signs, commas, and cash flow sign conventions."""

    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text in {"-", "na", "n/a"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.replace("(", "").replace(")", "").replace(",", "")
    text = text.replace(" ", "")
    try:
        number = float(text)
    except ValueError:
        return None
    return -abs(number) if negative else number


def map_local_accounting_labels(raw_label: str, country: str | None = None, sector: str | None = None) -> str | None:
    """Use country and sector context to improve line-item mapping."""

    standard = standardise_line_item(raw_label)
    if standard:
        return standard
    label = _normalise_label(raw_label)
    if sector and "cement" in sector.lower() and "clinker" in label:
        return "cost_of_sales"
    if country and country.upper() in {"KENYA", "TZ", "TANZANIA"} and "ksh" in label:
        return "revenue"
    return None
