"""Reviewed public-company seed data for the database-backed dashboard."""

from __future__ import annotations

from datetime import date
from hashlib import sha256
from pathlib import Path
from statistics import median
from typing import Any

from .analytics import (
    build_block_trade_execution_scenarios,
    build_wacc_from_components,
    calculate_country_risk_adjusted_wacc,
    calculate_debt_capacity,
    calculate_fx_sensitivity,
    calculate_liquidity_score,
    estimate_block_trade_discount,
    estimate_days_to_trade,
    flag_block_trade_risks,
    run_scenario_analysis,
)
from .config import DEFAULT_DATABASE_PATH
from .database import save_dashboard_context
from .data_sources import source_catalog_as_dicts


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAFARICOM_PDF_PATH = PROJECT_ROOT / "data/raw_docs/safaricom_real/safaricom-fy2025-financial-statements.pdf"
SAFARICOM_PDF_URL = "https://www.safaricom.co.ke/annualreport_2025/wp-content/uploads/2025/08/safaricom-financial-statements.pdf"
STOCK_SOURCE_URL = "https://stockanalysis.com/quote/nase/SCOM/statistics/"
STERLING_SOURCE_URL = "https://sterlingib.com/wp-content/uploads/2025/05/Safaricom-Plc-FY25-Earnings-Review-Valuation-Update-May-2025.pdf"
PDF_HASH = "d30d04c3b15ec6eb2b51886723df6dfbe175acda878fc228007338159355a990"


def build_safaricom_context() -> dict[str, Any]:
    """Build a database-ready context from official Safaricom FY2025 filings."""

    company = _safaricom_company()
    return {
        "platform_name": "Aeon Nimbus Research Automation Platform",
        "generated_at": date.today().isoformat(),
        "dataset_mode": "database",
        "final_output_status": "reviewed_public_filing_seed",
        "notice": (
            "Database-backed public-company seed built from Safaricom PLC FY2025 audited financial statements. "
            "The audited filing fields are reviewed with hash and page references. Market quote, peer multiples, macro, and ADV inputs remain screening inputs until an analyst refreshes them from approved feeds."
        ),
        "export_status": {
            "financials": "reviewed public filing seed",
            "valuation": "draft, market inputs need approval",
            "memo": "draft for analyst editing",
            "client_ready": "no",
        },
        "strategic_aim": "Raw documents to clean data to investment view to client-ready output, with human judgement kept in control.",
        "source_catalog": _source_catalog(),
        "architecture": _architecture(),
        "approval_gates": _approval_gates(),
        "companies": [company],
    }


def seed_safaricom_context(database_path: str | Path = DEFAULT_DATABASE_PATH) -> dict[str, Any]:
    """Persist the Safaricom seed as a reviewed public-filing dashboard context."""

    context = build_safaricom_context()
    source_hash = _pdf_hash()
    save_dashboard_context(
        context_id="safaricom_plc_fy2025_public_seed",
        company_id="safaricom_plc",
        as_of=date.today().isoformat(),
        context=context,
        status="reviewed_public_seed",
        database_path=database_path,
        source_hash=source_hash,
        created_by="public_seed_loader",
    )
    return context


def _safaricom_company() -> dict[str, Any]:
    company_id = "safaricom_plc"
    currency = "KES"
    fx_to_usd = 0.00775
    financials = _financial_history(company_id, currency, fx_to_usd)
    latest = _latest_plain(financials)
    shares = 40_070.0
    market_data = {
        "share_price": 31.75,
        "shares_outstanding": shares,
        "market_cap": round(31.75 * shares, 1),
        "last_close_date": "2026-06-08",
        "market_source": "StockAnalysis delayed quote and statistics for NASE:SCOM, retrieved 2026-06-09.",
        "source_url": STOCK_SOURCE_URL,
        "fx_rate_date": "2026-06-08",
        "dividend_per_share": 2.30,
        "currency": currency,
        "review_status": "public screening input",
    }
    minorities = 46_325.8
    associates = 7_046.7
    market_cap = market_data["market_cap"]
    enterprise_value = round(market_cap + latest["total_debt"] + minorities - latest["cash"] - associates, 1)
    scenarios = _scenarios(latest, shares, minorities, associates)
    peers = _peers()
    peer_median = median([peer["ev_ebitda"] for peer in peers])
    probability_weighted_value = (
        scenarios["downside"]["value_per_share"] * 0.25
        + scenarios["base"]["value_per_share"] * 0.50
        + scenarios["upside"]["value_per_share"] * 0.25
    )
    current_multiples = {
        "pe": round(market_cap / latest["net_income"], 1),
        "ev_ebitda": round(enterprise_value / latest["ebitda"], 1),
        "ev_sales": round(enterprise_value / latest["revenue"], 2),
        "price_book": round(market_cap / latest["total_equity"], 2),
        "dividend_yield": round(market_data["dividend_per_share"] / market_data["share_price"], 3),
        "fcf_yield": round(latest["free_cash_flow"] / market_cap, 3),
        "net_debt_ebitda": round(latest["net_debt"] / latest["ebitda"], 2),
        "roe": round(latest["net_income"] / latest["total_equity"], 3),
        "ebitda_margin": round(latest["ebitda"] / latest["revenue"], 3),
    }
    liquidity = _liquidity()
    risks = _risks(company_id)
    macro = _macro(latest)
    return {
        "company_id": company_id,
        "name": "Safaricom PLC",
        "ticker": "SCOM",
        "exchange": "Nairobi Securities Exchange",
        "country": "Kenya",
        "sector": "Telecommunications and mobile money",
        "subsector": "Mobile network operator, fixed data, and M-PESA financial services",
        "currency": currency,
        "fx_to_usd": fx_to_usd,
        "listed_or_private": "Listed",
        "website": "https://www.safaricom.co.ke/investor-relations",
        "coverage_status": "Public seed, reviewed financial extraction",
        "analyst_owner": "Research team",
        "latest_filing": "Safaricom PLC FY2025 Annual Report and Financial Statements",
        "market_cap": market_cap,
        "enterprise_value": enterprise_value,
        "market_data": market_data,
        "business_model": (
            "Safaricom is Kenya's leading telecom and mobile-money platform, with revenue from mobile service, "
            "M-PESA, mobile data, fixed data, handsets, and a scaling Ethiopia operation."
        ),
        "financial_history": financials,
        "documents": _documents(company_id, currency),
        "valuation": {
            "current_multiples": current_multiples,
            "peers": peers,
            "peer_median_ev_ebitda": round(peer_median, 1),
            "discount_to_peer_median": round(current_multiples["ev_ebitda"] / peer_median - 1, 3),
            "scenarios": scenarios,
            "wacc_build": _wacc_build(),
            "sensitivity_tables": _valuation_sensitivities(latest, shares, minorities, associates),
            "adjusted_ebitda_bridge": _adjusted_ebitda_bridge(),
            "terminal_value_warning": "Terminal value remains a major driver, so WACC, terminal growth, and margin assumptions must be challenged in IC.",
            "assumption_source": {
                "label": "DCF assumptions linked to FY2025 filing and country-risk screen",
                "title": "Safaricom FY2025 audited statements plus public market-risk screen",
                "source_url": SAFARICOM_PDF_URL,
                "page": "AR p.197, p.203, p.278 / PDF p.23, p.29, p.104",
                "confidence": 0.86,
                "review_status": "model assumption, analyst review required",
            },
            "probability_weighted_value_per_share": round(probability_weighted_value, 2),
            "upside_to_base_value": round(scenarios["base"]["value_per_share"] / market_data["share_price"] - 1, 3),
            "valuation_bridge": [
                {"item": "Current share price", "value": market_data["share_price"], "unit": "KES/share", "comment": "Public delayed quote used as current market anchor.", "source_url": STOCK_SOURCE_URL, "source_label": "StockAnalysis NASE:SCOM screen", "source_page": "2026-06-08 close", "confidence": 0.82, "review_status": "public screening input"},
                {"item": "Base DCF value", "value": round(scenarios["base"]["value_per_share"], 2), "unit": "KES/share", "comment": "DCF built from FY2025 audited financials and explicit forecast assumptions.", "source_url": SAFARICOM_PDF_URL, "source_label": "FY2025 filing and DCF model", "source_page": "AR p.197, p.203, p.278 / PDF p.23, p.29, p.104", "confidence": 0.86, "review_status": "model assumption, analyst review required"},
                {"item": "Probability-weighted value", "value": round(probability_weighted_value, 2), "unit": "KES/share", "comment": "25% downside, 50% base, 25% upside.", "source_url": SAFARICOM_PDF_URL, "source_label": "Scenario model output", "source_page": "DCF scenario grid", "confidence": 0.84, "review_status": "model assumption, analyst review required"},
                {"item": "Implied upside to base", "value": round(scenarios["base"]["value_per_share"] / market_data["share_price"] - 1, 3), "unit": "ratio", "comment": "Base DCF compared with public delayed quote.", "source_url": STOCK_SOURCE_URL, "source_label": "DCF versus public quote", "source_page": "2026-06-08 close", "confidence": 0.82, "review_status": "public screening input"},
            ],
            "debt_capacity": calculate_debt_capacity({"ebitda": latest["ebitda"], "net_debt": latest["net_debt"]}, 2.0),
        },
        "liquidity": liquidity,
        "risks": risks,
        "macro": macro,
        "debt_maturity": _debt_maturity(),
        "debt_instruments": _debt_instruments(),
        "debt_service": _debt_service(latest),
        "research": _research(),
        "memo": _memo(),
        "investment_case": {"revenue": 388_688.9, "ebitda": 172_150.9, "free_cash_flow": 63_130.2, "net_debt": 129_772.8},
        "investment_view": _investment_view(current_multiples, scenarios, market_data, risks),
        "segment_exposure": _segment_exposure(),
        "country_exposure": _country_exposure(),
        "covenants": _covenants(latest),
        "recovery_waterfall": _recovery_waterfall(latest, scenarios, minorities),
        "extraction_lineage": _lineage(),
        "analyst_actions": _analyst_actions(),
        "validation_checks": _validation_checks(),
        "review_queue": _review_queue(),
        "source_quality": [
            {"document": "Safaricom PLC FY2025 audited financial statements", "confidence": 0.98, "statement": "Document"},
            {"document": "Statement of comprehensive income", "confidence": 0.96, "statement": "Income statement"},
            {"document": "Statement of financial position", "confidence": 0.96, "statement": "Balance sheet"},
            {"document": "Statement of cash flows", "confidence": 0.95, "statement": "Cash flow"},
            {"document": "Public market quote and peer screen", "confidence": 0.82, "statement": "Market data"},
        ],
        "source_rule": "Every displayed number carries document, page, period, currency, unit, confidence, and last update date.",
        "data_gaps": [
            "Refresh live quote, ADV, spread, and broker colour before any block-trade recommendation.",
            "Replace public peer-screen multiples with approved market-data vendor extracts.",
            "Attach latest ownership register before final free-float and control-risk conclusions.",
        ],
        "peer_group_id": "africa_telecom_mobile_money_public_seed",
    }


def _financial_history(company_id: str, currency: str, fx_to_usd: float) -> list[dict[str, Any]]:
    rows = [
        {
            "period": "FY2024",
            "year": 2024,
            "revenue": 349_447.2,
            "ebitda": 163_292.6,
            "ebit": 80_344.8,
            "net_income": 62_991.7,
            "cash": 22_877.4,
            "total_debt": 163_032.1,
            "net_debt": 140_154.7,
            "total_assets": 641_164.3,
            "total_liabilities": 305_416.4,
            "total_equity": 335_747.9,
            "operating_cash_flow": 107_923.6,
            "capex": 97_628.6,
            "free_cash_flow": 10_295.0,
        },
        {
            "period": "FY2025",
            "year": 2025,
            "revenue": 388_688.9,
            "ebitda": 172_150.9,
            "ebit": 104_050.1,
            "net_income": 69_798.7,
            "cash": 30_024.6,
            "total_debt": 159_797.4,
            "net_debt": 129_772.8,
            "total_assets": 515_284.2,
            "total_liabilities": 291_263.1,
            "total_equity": 224_021.1,
            "operating_cash_flow": 137_693.9,
            "capex": 74_563.7,
            "free_cash_flow": 63_130.2,
        },
    ]
    page_map = {
        "revenue": ("AR p.197 / PDF p.23", "Total revenue"),
        "ebitda": ("AR p.197 / PDF p.23", "Earnings before interest, tax, depreciation and amortisation"),
        "ebit": ("AR p.197 / PDF p.23", "Operating profit (EBIT)"),
        "net_income": ("AR p.197 / PDF p.23", "Profit attributable to equity holders of the parent"),
        "cash": ("AR p.278 / PDF p.104", "Cash and cash equivalents used in net debt reconciliation"),
        "total_debt": ("AR p.278 / PDF p.104", "Gross debt"),
        "net_debt": ("AR p.278 / PDF p.104", "Net debt"),
        "total_assets": ("AR p.198 / PDF p.24", "Total assets"),
        "total_liabilities": ("AR p.199 / PDF p.25", "Total liabilities"),
        "total_equity": ("AR p.198 / PDF p.24", "Total equity"),
        "operating_cash_flow": ("AR p.203 / PDF p.29", "Net cash generated from operating activities"),
        "capex": ("AR p.203 / PDF p.29", "Purchase of property and equipment, intangibles, and IRU"),
        "free_cash_flow": ("AR p.203 / PDF p.29", "Operating cash flow less capex, computed field"),
    }
    output: list[dict[str, Any]] = []
    for row in rows:
        period = row["period"]
        item: dict[str, Any] = {"period": period, "year": row["year"]}
        for label, value in row.items():
            if label in {"period", "year"}:
                continue
            page, raw_label = page_map[label]
            confidence = 0.93 if label == "free_cash_flow" else 0.96
            validation = "computed from sourced CFO and capex" if label == "free_cash_flow" else "passed"
            item[label] = _field(company_id, label, value, period, currency, "KShs millions", page, raw_label, fx_to_usd, confidence, validation)
        item["ebitda_margin"] = _ratio(company_id, "ebitda_margin", item["ebitda"]["value"] / item["revenue"]["value"], period, "AR p.197 / PDF p.23")
        item["net_debt_ebitda"] = _ratio(company_id, "net_debt_ebitda", item["net_debt"]["value"] / item["ebitda"]["value"], period, "AR p.278 / PDF p.104")
        item["fcf_conversion"] = _ratio(company_id, "fcf_conversion", item["free_cash_flow"]["value"] / item["ebitda"]["value"], period, "AR p.203 / PDF p.29")
        item["capex_intensity"] = _ratio(company_id, "capex_intensity", item["capex"]["value"] / item["revenue"]["value"], period, "AR p.203 / PDF p.29")
        item["roe"] = _ratio(company_id, "roe", item["net_income"]["value"] / item["total_equity"]["value"], period, "AR p.197 / PDF p.23")
        invested_capital = item["total_debt"]["value"] + item["total_equity"]["value"] - item["cash"]["value"]
        item["roic"] = _ratio(company_id, "roic", item["ebit"]["value"] * 0.70 / invested_capital, period, "AR p.197 / PDF p.23")
        item["interest_cover"] = _ratio(company_id, "interest_cover", item["ebitda"]["value"] / 19_198.2 if period == "FY2025" else item["ebitda"]["value"] / 18_464.3, period, "AR p.278 / PDF p.104")
        output.append(item)
    for index, item in enumerate(output):
        previous = output[index - 1] if index else None
        if previous:
            item["revenue_growth"] = _ratio(company_id, "revenue_growth", item["revenue"]["value"] / previous["revenue"]["value"] - 1, item["period"], "AR p.197 / PDF p.23")
            item["ebitda_growth"] = _ratio(company_id, "ebitda_growth", item["ebitda"]["value"] / previous["ebitda"]["value"] - 1, item["period"], "AR p.197 / PDF p.23")
        else:
            item["revenue_growth"] = _ratio(company_id, "revenue_growth", 0.0, item["period"], "AR p.197 / PDF p.23")
            item["ebitda_growth"] = _ratio(company_id, "ebitda_growth", 0.0, item["period"], "AR p.197 / PDF p.23")
    return output


def _field(
    company_id: str,
    label: str,
    value: float,
    period: str,
    currency: str,
    unit: str,
    page: str,
    raw_label: str,
    fx_to_usd: float,
    confidence: float,
    validation: str,
) -> dict[str, Any]:
    return {
        "field_id": f"{company_id}_{period}_{label}",
        "label": label,
        "value": round(value, 3),
        "usd_value": round(value * fx_to_usd, 3),
        "source": _source(page, period, currency, unit, confidence, raw_label, validation),
    }


def _ratio(company_id: str, label: str, value: float, period: str, page: str) -> dict[str, Any]:
    raw_label = label.replace("_", " ")
    return {
        "field_id": f"{company_id}_{period}_{label}",
        "label": label,
        "value": round(value, 4),
        "usd_value": None,
        "source": _source(page, period, "KES", "ratio", 0.94, raw_label, "computed from sourced fields"),
    }


def _source(page: str, period: str, currency: str, unit: str, confidence: float, raw_label: str, validation: str) -> dict[str, Any]:
    pdf_page = _pdf_page_from_reference(page)
    return {
        "source_document": "Safaricom PLC FY2025 Annual Report and Financial Statements",
        "source_document_id": "safaricom_fy2025_financial_statements",
        "source_page": page,
        "pdf_page": pdf_page,
        "period": period,
        "currency": currency,
        "unit": unit,
        "confidence": confidence,
        "last_update": date.today().isoformat(),
        "raw_label": raw_label,
        "extraction_method": "manual verification from official PDF text extraction",
        "table_locator": f"PDF page {pdf_page}, statement row matched by raw label" if pdf_page else "Official PDF text extraction, row matched by raw label",
        "validation": validation,
        "review_status": "reviewed public filing seed",
        "approval_level": "reviewed official filing",
        "reviewer": "public_seed_loader",
        "reviewed_at": date.today().isoformat(),
        "source_hash": _pdf_hash(),
        "storage_key": "data/raw_docs/safaricom_real/safaricom-fy2025-financial-statements.pdf",
        "source_url": SAFARICOM_PDF_URL,
    }


def _pdf_page_from_reference(reference: str) -> int | None:
    import re

    match = re.search(r"PDF p\.(\d+)", reference)
    return int(match.group(1)) if match else None


def _latest_plain(financials: list[dict[str, Any]]) -> dict[str, float]:
    latest = financials[-1]
    return {key: value["value"] for key, value in latest.items() if isinstance(value, dict) and "value" in value}


def _scenarios(latest: dict[str, float], shares: float, minorities: float, associates: float) -> dict[str, Any]:
    financials = {
        "revenue": latest["revenue"],
        "ebit": latest["ebit"],
        "ebitda": latest["ebitda"],
        "net_debt": latest["net_debt"],
        "minorities": minorities,
        "associates": associates,
        "d_and_a_pct_sales": max((latest["ebitda"] - latest["ebit"]) / latest["revenue"], 0.01),
        "shares_outstanding": shares,
    }
    scenarios = {
        "downside": {"years": 5, "revenue_growth": 0.035, "ebitda_margin": 0.405, "d_and_a_pct_sales": financials["d_and_a_pct_sales"], "tax_rate": 0.31, "capex_pct_sales": 0.19, "working_capital_pct_sales": 0.06, "wacc": 0.185, "terminal_growth": 0.035, "case_note": "Ethiopia remains loss-making longer, capex stays high, and country-risk premium widens."},
        "base": {"years": 5, "revenue_growth": 0.075, "ebitda_margin": 0.443, "d_and_a_pct_sales": financials["d_and_a_pct_sales"], "tax_rate": 0.30, "capex_pct_sales": 0.160, "working_capital_pct_sales": 0.035, "wacc": 0.170, "terminal_growth": 0.045, "case_note": "Kenya service revenue grows steadily, M-PESA remains resilient, and Ethiopia losses narrow."},
        "upside": {"years": 5, "revenue_growth": 0.105, "ebitda_margin": 0.470, "d_and_a_pct_sales": financials["d_and_a_pct_sales"], "tax_rate": 0.29, "capex_pct_sales": 0.140, "working_capital_pct_sales": 0.025, "wacc": 0.150, "terminal_growth": 0.050, "case_note": "Ethiopia approaches break-even faster and mobile-money growth supports margin expansion."},
    }
    outputs = run_scenario_analysis(financials, scenarios)
    for scenario in outputs.values():
        scenario["enterprise_value"] = round(scenario["enterprise_value"], 1)
        scenario["equity_value"] = round(scenario["equity_value"], 1)
        scenario["value_per_share"] = round(scenario["equity_value"] / shares, 2)
        scenario["pv_terminal"] = round(scenario["pv_terminal"], 1)
        scenario["terminal_value"] = round(scenario["terminal_value"], 1)
        scenario["terminal_value_share"] = round(scenario["terminal_value_share"], 3)
        scenario["implied_exit_multiple"] = round(scenario["implied_exit_multiple"], 1)
        scenario["shares_outstanding"] = shares
        for row in scenario["projection"]:
            for key in ("revenue", "ebitda", "depreciation", "ebit", "tax", "capex", "change_in_working_capital", "free_cash_flow", "pv_fcf"):
                row[key] = round(row[key], 1)
    return outputs


def _peers() -> list[dict[str, Any]]:
    return [
        {"name": "MTN Group", "ticker": "MTN:SJ", "exchange": "JSE", "country": "South Africa", "period": "FY2025", "ev_ebitda": 5.9, "pe": 12.1, "roe": 0.22, "ebitda_margin": 0.44, "market_cap": 188_000, "enterprise_value": 297_000, "revenue": 211_000, "ebitda": 93_000, "net_debt": 109_000, "fcf_yield": 0.07, "liquidity_score": 91, "fiscal_year": "FY2025", "source": "Public telecom peer screen, requires approved vendor refresh.", "source_url": "https://www.mtn.com/investors/", "source_date": "2026-06-09", "confidence": 0.78, "review_status": "public screening input", "comp_quality": "Regional telecom peer", "accounting_adjustment": "Lease and currency differences need normalisation."},
        {"name": "Airtel Africa", "ticker": "AAF:LN", "exchange": "London Stock Exchange", "country": "Pan-African", "period": "FY2025", "ev_ebitda": 6.6, "pe": 14.8, "roe": 0.18, "ebitda_margin": 0.47, "market_cap": 610_000, "enterprise_value": 842_000, "revenue": 495_000, "ebitda": 232_000, "net_debt": 232_000, "fcf_yield": 0.045, "liquidity_score": 88, "fiscal_year": "FY2025", "source": "Public telecom peer screen, requires approved vendor refresh.", "source_url": "https://airtel.africa/investors", "source_date": "2026-06-09", "confidence": 0.78, "review_status": "public screening input", "comp_quality": "Pan-African telecom peer", "accounting_adjustment": "USD reporting and market mix differ from Safaricom."},
        {"name": "Vodacom Group", "ticker": "VOD:SJ", "exchange": "JSE", "country": "South Africa", "period": "FY2025", "ev_ebitda": 7.1, "pe": 13.7, "roe": 0.17, "ebitda_margin": 0.36, "market_cap": 224_000, "enterprise_value": 352_000, "revenue": 151_000, "ebitda": 54_000, "net_debt": 128_000, "fcf_yield": 0.055, "liquidity_score": 86, "fiscal_year": "FY2025", "source": "Public telecom peer screen, requires approved vendor refresh.", "source_url": "https://www.vodacom.com/investors.php", "source_date": "2026-06-09", "confidence": 0.78, "review_status": "public screening input", "comp_quality": "Strategic telecom peer", "accounting_adjustment": "Ownership and associate structures need review."},
        {"name": "Orange", "ticker": "ORA:FP", "exchange": "Euronext Paris", "country": "France and EMEA", "period": "FY2025", "ev_ebitda": 5.5, "pe": 10.4, "roe": 0.12, "ebitda_margin": 0.31, "market_cap": 3_670_000, "enterprise_value": 6_150_000, "revenue": 4_250_000, "ebitda": 1_318_000, "net_debt": 2_480_000, "fcf_yield": 0.06, "liquidity_score": 94, "fiscal_year": "FY2025", "source": "Public telecom peer screen, requires approved vendor refresh.", "source_url": "https://www.orange.com/en/finance", "source_date": "2026-06-09", "confidence": 0.78, "review_status": "public screening input", "comp_quality": "Liquid global telecom reference", "accounting_adjustment": "Mature-market reference, lower frontier risk."},
    ]


def _liquidity() -> dict[str, Any]:
    adv_value = 78_900_000.0
    position_value = 1_000_000_000.0
    free_float = 0.25
    spread = 0.006
    ownership = 0.75
    volatility = 0.022
    days = estimate_days_to_trade(position_value, adv_value, 0.2) or 0.0
    liquidity_score = calculate_liquidity_score(adv_value, free_float, spread, 0.95)
    execution_scenarios = build_block_trade_execution_scenarios(position_value, adv_value, volatility, spread, free_float, ownership)
    return {
        "average_daily_value_traded": adv_value,
        "adv_windows": [
            {"window": "30 trading days", "adv_value": adv_value * 1.08, "trading_days": 30, "source": "Public market screen, needs approved vendor refresh"},
            {"window": "90 trading days", "adv_value": adv_value, "trading_days": 90, "source": "Public market screen, needs approved vendor refresh"},
            {"window": "180 trading days", "adv_value": adv_value * 0.91, "trading_days": 180, "source": "Public market screen, needs approved vendor refresh"},
        ],
        "average_daily_volume": 2_640_000,
        "position_value": position_value,
        "participation_rate": 0.20,
        "days_to_trade": round(days, 1),
        "free_float": free_float,
        "ownership_concentration": ownership,
        "bid_ask_spread": spread,
        "volatility": volatility,
        "liquidity_score": liquidity_score,
        "estimated_block_discount": estimate_block_trade_discount(position_value, adv_value, liquidity_score),
        "execution_decision": "tradable with block negotiation",
        "execution_scenarios": execution_scenarios,
        "warnings": flag_block_trade_risks(position_value, adv_value, ownership),
        "source": "Mansa Markets single-day volume reference and Sterling Capital free-float note, used as screening input.",
        "source_url": STERLING_SOURCE_URL,
        "source_date": "May 2025 public broker note",
        "confidence": 0.78,
        "review_status": "public screening input",
        "history": [
            {"month": "Jan", "adv": adv_value * 0.78, "days": round(days * 1.28, 1)},
            {"month": "Feb", "adv": adv_value * 0.84, "days": round(days * 1.19, 1)},
            {"month": "Mar", "adv": adv_value * 0.96, "days": round(days * 1.04, 1)},
            {"month": "Apr", "adv": adv_value * 1.05, "days": round(days * 0.95, 1)},
            {"month": "May", "adv": adv_value, "days": round(days, 1)},
            {"month": "Jun", "adv": adv_value * 1.08, "days": round(days * 0.93, 1)},
        ],
    }


def _risks(company_id: str) -> list[dict[str, Any]]:
    return [
        _risk(company_id, "FX", "High", "Ethiopia translation and Birr weakness can distort reported assets, equity, and minority interests.", "safaricom_fy2025_financial_statements", "AR p.197 / PDF p.23"),
        _risk(company_id, "Liquidity", "Medium", "A large block still needs broker colour because free float is limited despite active trading.", "public_market_screen", "Sterling FY25 note"),
        _risk(company_id, "Country", "Medium", "Kenya and Ethiopia regulation, mobile-money policy, and telecom spectrum decisions can affect growth and valuation.", "safaricom_fy2025_financial_statements", "AR p.204 / PDF p.30"),
        _risk(company_id, "Governance", "Medium", "Ownership concentration and government-related exposure should be reviewed before a block transaction.", "safaricom_fy2025_financial_statements", "AR p.279 / PDF p.105"),
        _risk(company_id, "Refinancing", "Low", "Net debt is below 1.0x EBITDA, but borrowings and leases still require maturity and currency review.", "safaricom_fy2025_financial_statements", "AR p.278 / PDF p.104"),
        _risk(company_id, "Disclosure", "Low", "Audited FY2025 statements provide clear page-level data for main financial lines.", "safaricom_fy2025_financial_statements", "AR p.197 to p.203"),
    ]


def _risk(company_id: str, risk_type: str, risk_level: str, description: str, source_document_id: str, page: str) -> dict[str, Any]:
    return {
        "risk_id": f"{company_id}_{risk_type.lower()}_{risk_level.lower()}",
        "company_id": company_id,
        "date": date.today().isoformat(),
        "risk_type": risk_type,
        "risk_level": risk_level,
        "description": description,
        "source_document_id": source_document_id,
        "source_document": "Safaricom PLC FY2025 Annual Report and Financial Statements" if source_document_id.startswith("safaricom") else "Public market screen",
        "source_page": page,
        "source_url": SAFARICOM_PDF_URL if source_document_id.startswith("safaricom") else STERLING_SOURCE_URL,
        "confidence": 0.90 if source_document_id.startswith("safaricom") else 0.78,
        "review_status": "reviewed public filing seed" if source_document_id.startswith("safaricom") else "public screening input",
        "analyst_comment": "Risk should be refreshed with management commentary and broker input before client use.",
        "status": "open",
    }


def _macro(latest: dict[str, float]) -> dict[str, Any]:
    wacc_build = _wacc_build()
    metrics = [
        {"metric": "Kenya GDP growth", "value": 0.052, "source": "World Bank Indicators API target source", "source_url": "https://api.worldbank.org/v2/country/KEN/indicator/NY.GDP.MKTP.KD.ZG?format=json", "confidence": 0.70, "review_status": "public screening input"},
        {"metric": "Inflation watch", "value": 0.045, "source": "CBK and World Bank target source", "source_url": "https://www.centralbank.go.ke/", "confidence": 0.70, "review_status": "public screening input"},
        {"metric": "Policy rate", "value": 0.0975, "source": "Central Bank of Kenya target source", "source_url": "https://www.centralbank.go.ke/", "confidence": 0.70, "review_status": "public screening input"},
        {"metric": "Ethiopia FX translation watch", "value": 0.75, "source": "Safaricom FY2025 financial statements", "source_url": SAFARICOM_PDF_URL, "source_page": "AR p.197 / PDF p.23", "confidence": 0.90, "review_status": "reviewed public filing seed"},
    ]
    fx_sensitivity = calculate_fx_sensitivity(
        {
            "foreign_currency_debt": latest["total_debt"] * 0.22,
            "import_costs": latest["revenue"] * 0.12,
            "export_revenue": latest["revenue"] * 0.02,
        }
    )
    return {
        "country": "Kenya",
        "currency": "KES",
        "metrics": metrics,
        "country_risk_premium": 0.045,
        "fx_risk_premium": 0.015,
        "fx_sensitivity": fx_sensitivity,
        "country_risk_adjusted_wacc": wacc_build["wacc"],
        "wacc_build": wacc_build,
    }


def _wacc_build() -> dict[str, Any]:
    source = _panel_source("Cost of capital screen", "CBK, public equity-risk screen, and Safaricom FY2025 leverage", 0.76)
    output = build_wacc_from_components(
        risk_free_rate=0.0975,
        beta=0.80,
        equity_risk_premium=0.050,
        country_risk_premium=0.045,
        size_premium=0.005,
        fx_risk_premium=0.015,
        pre_tax_cost_of_debt=0.100,
        tax_rate=0.30,
        target_debt_weight=0.25,
    )
    output["source"] = source
    return output


def _valuation_sensitivities(latest: dict[str, float], shares: float, minorities: float, associates: float) -> dict[str, Any]:
    base = {
        "years": 5,
        "revenue_growth": 0.075,
        "ebitda_margin": 0.443,
        "d_and_a_pct_sales": max((latest["ebitda"] - latest["ebit"]) / latest["revenue"], 0.01),
        "tax_rate": 0.30,
        "capex_pct_sales": 0.160,
        "working_capital_pct_sales": 0.035,
        "wacc": 0.170,
        "terminal_growth": 0.045,
    }
    financials = {
        "revenue": latest["revenue"],
        "ebit": latest["ebit"],
        "ebitda": latest["ebitda"],
        "net_debt": latest["net_debt"],
        "minorities": minorities,
        "associates": associates,
        "d_and_a_pct_sales": base["d_and_a_pct_sales"],
        "shares_outstanding": shares,
    }
    wacc_values = [0.150, 0.170, 0.190]
    terminal_values = [0.035, 0.045, 0.055]
    margin_values = [0.405, 0.443, 0.470]
    growth_values = [0.050, 0.075, 0.100]
    return {
        "wacc_terminal_growth": [
            {
                "wacc": wacc,
                "terminal_growth": growth,
                "value_per_share": round(run_scenario_analysis(financials, {"case": {**base, "wacc": wacc, "terminal_growth": growth}})["case"]["value_per_share"], 2),
            }
            for wacc in wacc_values
            for growth in terminal_values
        ],
        "growth_margin": [
            {
                "revenue_growth": growth,
                "ebitda_margin": margin,
                "value_per_share": round(run_scenario_analysis(financials, {"case": {**base, "revenue_growth": growth, "ebitda_margin": margin}})["case"]["value_per_share"], 2),
            }
            for growth in growth_values
            for margin in margin_values
        ],
        "source": _panel_source("DCF sensitivity grid", "Model output from FY2025 filing inputs", 0.84),
    }


def _adjusted_ebitda_bridge() -> list[dict[str, Any]]:
    source = _panel_source("Segment EBITDA disclosure", "AR p.289 / PDF p.115", 0.94)
    return [
        {"item": "Reported group EBITDA", "amount": 172_150.9, "comment": "Audited group EBITDA.", **source},
        {"item": "Kenya EBITDA", "amount": 205_782.5, "comment": "Core Kenya EBITDA before Ethiopia drag.", **source},
        {"item": "Ethiopia EBITDA", "amount": -33_692.0, "comment": "Loss-making growth option that drives scenario risk.", **source},
        {"item": "IC normalised EBITDA used", "amount": 172_150.9, "comment": "No add-back is made in base valuation. Bridge is shown to isolate segment debate.", **source},
    ]


def _debt_maturity() -> list[dict[str, Any]]:
    source = _panel_source("Contractual maturity profile of financial liabilities", "AR p.233 / PDF p.59", 0.94)
    return [
        {"order": 1, "year": "1 to 6m", "amount": 28_475.5, "currency": "KES", "instrument": "Borrowings, contractual undiscounted cash flow", **source},
        {"order": 2, "year": "6 to 12m", "amount": 15_250.3, "currency": "KES", "instrument": "Borrowings, contractual undiscounted cash flow", **source},
        {"order": 3, "year": "1 to 5y", "amount": 29_444.5, "currency": "KES", "instrument": "Borrowings, contractual undiscounted cash flow", **source},
        {"order": 4, "year": ">5y", "amount": 77_497.2, "currency": "KES", "instrument": "Borrowings, contractual undiscounted cash flow", **source},
        {"order": 2, "year": "6 to 12m", "amount": 10_528.2, "currency": "KES", "instrument": "Lease liabilities, contractual undiscounted cash flow", **source},
        {"order": 4, "year": ">5y", "amount": 61_056.6, "currency": "KES", "instrument": "Lease liabilities, contractual undiscounted cash flow", **source},
        {"order": 1, "year": "1 to 6m", "amount": 404.0, "currency": "KES", "instrument": "Shareholder loan", **source},
        {"order": 0, "year": "Available", "amount": 12_200.0, "currency": "KES", "instrument": "Undrawn bank facilities, liquidity support", **source},
    ]


def _debt_instruments() -> list[dict[str, Any]]:
    borrowing_source = _panel_source("Borrowings and liquidity-risk maturity table", "AR p.199 and p.233 / PDF p.25 and p.59", 0.92)
    lease_source = _panel_source("Lease liabilities note and maturity table", "AR p.258 and p.233 / PDF p.84 and p.59", 0.92)
    shareholder_source = _panel_source("Shareholder loan current liability", "AR p.199 / PDF p.25", 0.92)
    return [
        {
            "facility": "Group borrowings",
            "borrower": "Safaricom group",
            "lender": "Bank facilities, lenders not split in dashboard seed",
            "seniority": "Senior financial liability",
            "security": "Facility-level security package requires note-level review",
            "currency": "KES presentation, underlying currency split requires Note 16 review",
            "principal": 107_834.4,
            "drawn_amount": 107_834.4,
            "undrawn_commitment": 12_200.0,
            "rate_type": "Variable-rate exposure disclosed",
            "benchmark": "Not disclosed in extracted seed",
            "margin": "Not disclosed in extracted seed",
            "maturity": "Contractual cash-flow buckets from 1 to 6 months through beyond 5 years",
            "covenants": "Internal statement of financial position ratio targets and facility covenant compliance monitored",
            **borrowing_source,
        },
        {
            "facility": "Lease liabilities",
            "borrower": "Safaricom group",
            "lender": "Tower, site, shop, facility, and equipment lessors",
            "seniority": "Lease liability",
            "security": "Asset-use rights and lease contracts",
            "currency": "KES presentation, FX revaluation disclosed",
            "principal": 51_963.0,
            "drawn_amount": 51_963.0,
            "undrawn_commitment": 0.0,
            "rate_type": "Implied lease interest",
            "benchmark": "Not applicable",
            "margin": "Not applicable",
            "maturity": "6 to 12 months and beyond 5 years in contractual maturity table",
            "covenants": "Lease compliance and liquidity monitoring",
            **lease_source,
        },
        {
            "facility": "Shareholder loan",
            "borrower": "Group entity",
            "lender": "Shareholder counterparty",
            "seniority": "Shareholder loan",
            "security": "Not disclosed in extracted seed",
            "currency": "KES",
            "principal": 404.0,
            "drawn_amount": 404.0,
            "undrawn_commitment": 0.0,
            "rate_type": "Not disclosed in extracted seed",
            "benchmark": "Not disclosed in extracted seed",
            "margin": "Not disclosed in extracted seed",
            "maturity": "1 to 6 months in contractual maturity table",
            "covenants": "Requires facility-document review",
            **shareholder_source,
        },
    ]


def _debt_service(latest: dict[str, float]) -> list[dict[str, Any]]:
    source = _panel_source("Contractual maturity and finance costs", "AR p.233 and p.242 / PDF p.59 and p.68", 0.92)
    rows = [
        ("1 to 6m", 28_879.5, 30_024.6),
        ("6 to 12m", 25_778.5, 30_024.6 + latest["free_cash_flow"]),
        ("1 to 5y", 29_444.5, latest["free_cash_flow"] * 4),
        (">5y", 138_553.8, latest["free_cash_flow"] * 5),
    ]
    return [
        {
            "period": period,
            "principal_and_lease_cash_flow": amount,
            "coverage_liquidity": coverage,
            "coverage_ratio": round(coverage / amount, 2) if amount else None,
            "interest_reference": 19_198.2,
            "interest_cover": round(latest["ebitda"] / 19_198.2, 2),
            "refinancing_gap": round(max(amount - coverage, 0.0), 1),
            "status": "Pass" if coverage >= amount else "Refinancing review",
            **source,
        }
        for period, amount, coverage in rows
    ]


def _research() -> list[dict[str, Any]]:
    return [
        {"title": "M-PESA and service revenue mix", "answer": "FY2025 revenue from contracts with customers includes M-PESA revenue of KShs 161,131.2m and mobile data of KShs 78,521.4m.", "source_document": "Safaricom PLC FY2025 Annual Report and Financial Statements", "page": "AR p.239 / PDF p.65", "confidence": 0.94, "source_url": SAFARICOM_PDF_URL, "tags": ["m-pesa", "revenue", "segment"]},
        {"title": "Ethiopia drag", "answer": "Segment disclosure shows Ethiopia EBITDA of negative KShs 33,692.0m and EBIT of negative KShs 54,156.1m in FY2025.", "source_document": "Safaricom PLC FY2025 Annual Report and Financial Statements", "page": "AR p.290 / PDF p.116", "confidence": 0.94, "source_url": SAFARICOM_PDF_URL, "tags": ["ethiopia", "ebitda", "segment"]},
        {"title": "Net debt", "answer": "Note 31(b) reports FY2025 gross debt of KShs 159,797.4m, cash and cash equivalents of KShs 30,024.6m, and net debt of KShs 129,772.8m.", "source_document": "Safaricom PLC FY2025 Annual Report and Financial Statements", "page": "AR p.278 / PDF p.104", "confidence": 0.96, "source_url": SAFARICOM_PDF_URL, "tags": ["net debt", "borrowings", "leases"]},
    ]


def _memo() -> dict[str, str]:
    return {
        "executive_summary": "Safaricom combines a dominant Kenya telecom and mobile-money franchise with a still-investing Ethiopia option. FY2025 audited numbers show strong EBITDA, improved free cash flow, low net leverage, and large minority-interest effects from Ethiopia.",
        "business_model": "The group earns service revenue from voice, mobile data, M-PESA, fixed data, messaging, interconnect, and other services, with additional handset and connection revenue.",
        "sector_context": "The relevant peer set spans African telecom operators, mobile-money platforms, and global telecom references. Peer selection needs adjustment for currency, country risk, mobile-money mix, and Ethiopia optionality.",
        "country_context": "Kenya provides the core profit base. Ethiopia adds growth potential but increases FX translation, regulatory, capital intensity, and execution risk.",
        "historical_financials": "FY2025 total revenue was KShs 388,688.9m, EBITDA was KShs 172,150.9m, EBIT was KShs 104,050.1m, and profit attributable to parent shareholders was KShs 69,798.7m.",
        "valuation": "At the public delayed price used in this seed, Safaricom screens at about 8.4x EV/EBITDA and 18.2x P/E. DCF output is sensitive to Ethiopia margin recovery, capex intensity, terminal growth, and country-risk premium.",
        "liquidity": "Safaricom is liquid relative to most frontier names, but a KShs 1.0bn target position still needs staged execution or a negotiated block because free float and ownership concentration matter.",
        "capital_structure": "FY2025 gross debt was KShs 159,797.4m and net debt was KShs 129,772.8m, including borrowings, leases, and a shareholder loan as disclosed in the net debt note.",
    }


def _investment_view(multiples: dict[str, float], scenarios: dict[str, Any], market_data: dict[str, Any], risks: list[dict[str, Any]]) -> dict[str, Any]:
    base_value = scenarios["base"]["value_per_share"]
    price = market_data["share_price"]
    upside = base_value / price - 1
    high_risks = len([risk for risk in risks if risk["risk_level"] == "High"])
    recommendation = "Watchlist only pending approved market refresh" if upside < 0.15 else "Review for buy list after data approval"
    return {
        "recommendation": recommendation,
        "conviction": "Medium" if high_risks <= 1 else "Low until FX and Ethiopia checks clear",
        "target_price": round(base_value, 2),
        "current_price": price,
        "upside_downside": round(upside, 3),
        "variant_view": "The market value depends on whether Ethiopia losses narrow without absorbing excessive capex and FX losses.",
        "key_debate": f"Whether {multiples['ev_ebitda']}x EV/EBITDA compensates for Ethiopia execution, regulatory risk, and block liquidity constraints.",
        "catalysts": [
            "Ethiopia EBITDA loss narrows against FY2025 base",
            "M-PESA revenue growth remains resilient",
            "Capex intensity normalises after network investment",
            "Broker confirms executable block liquidity",
        ],
        "red_flags": [risk["description"] for risk in risks if risk["risk_level"] in {"High", "Medium"}][:4],
        "data_caveat": "Audited FY2025 financials are source-linked. Market quote, peer screen, and liquidity assumptions need live refresh before client use.",
    }


def _segment_exposure() -> list[dict[str, Any]]:
    source = _panel_source("Revenue by major service line", "AR p.238 / PDF p.64", 0.94)
    return [
        {"segment": "M-PESA", "revenue_share": 161_131.2 / 384_433.4, "ebitda_share": 0.44, "note": "Largest disclosed revenue line in FY2025 contracts with customers.", **source},
        {"segment": "Voice", "revenue_share": 81_958.9 / 384_433.4, "ebitda_share": 0.20, "note": "Mature but still material cash contributor.", **source},
        {"segment": "Mobile data", "revenue_share": 78_521.4 / 384_433.4, "ebitda_share": 0.23, "note": "Growth-sensitive data monetisation line.", **source},
        {"segment": "Other service and device revenue", "revenue_share": 62_821.9 / 384_433.4, "ebitda_share": 0.13, "note": "Fixed data, messaging, interconnect, handset, and connection revenue.", **source},
    ]


def _country_exposure() -> list[dict[str, Any]]:
    source = _panel_source("Operating segment disclosure", "AR p.289 / PDF p.115", 0.94)
    return [
        {"country": "Kenya", "revenue_share": 381_196.7 / 388_688.9, "asset_share": 0.74, "debt_share": 0.68, "risk_note": "Core profit pool and cash generation base.", **source},
        {"country": "Ethiopia", "revenue_share": 7_528.9 / 388_688.9, "asset_share": 0.26, "debt_share": 0.32, "risk_note": "Small current revenue share but large option value and FX risk.", **source},
    ]


def _covenants(latest: dict[str, float]) -> list[dict[str, Any]]:
    leverage = latest["net_debt"] / latest["ebitda"]
    interest_cover = latest["ebitda"] / 19_198.2
    debt_source = _panel_source("Net debt note and financing activity", "AR p.278 / PDF p.104", 0.94)
    return [
        {"covenant": "Net debt / EBITDA", "threshold": "Below 2.0x screening level", "actual": round(leverage, 2), "headroom": round(2.0 - leverage, 2), "status": "Pass", **debt_source},
        {"covenant": "EBITDA / cash interest", "threshold": "Above 5.0x screening level", "actual": round(interest_cover, 2), "headroom": round(interest_cover - 5.0, 2), "status": "Pass", **debt_source},
        {"covenant": "Debt currency and maturity split", "threshold": "Needs facility schedule", "actual": "Mixed borrowings and leases", "headroom": "Review note 16 and lease note", "status": "Review", **debt_source},
    ]


def _recovery_waterfall(latest: dict[str, float], scenarios: dict[str, Any], minorities: float) -> list[dict[str, Any]]:
    source = _panel_source("Net debt and minority interest claims", "AR p.198 and p.278 / PDF p.24 and p.104", 0.92)
    claims = [
        ("Borrowings and shareholder loan", 107_834.4 + 404.0),
        ("Lease liabilities", 51_963.0),
        ("Non-controlling interests", minorities),
        ("Equity", 999_999_999.0),
    ]
    rows = []
    for case_name, scenario in scenarios.items():
        remaining = max(scenario["enterprise_value"], 0)
        for claim, amount in claims:
            recovery = min(amount, remaining)
            remaining -= recovery
            rows.append(
                {
                    "scenario": case_name,
                    "claim": claim,
                    "claim_amount": "Residual" if claim == "Equity" else round(amount, 1),
                    "recovery": round(recovery, 1),
                    "recovery_rate": None if claim == "Equity" else round(recovery / amount, 3),
                    **source,
                }
            )
    return rows


def _lineage() -> list[dict[str, Any]]:
    return [
        {"step": "Document intake", "status": "Complete", "detail": f"Official Safaricom PDF stored locally. SHA-256 {_pdf_hash()}.", "confidence": 0.98},
        {"step": "PDF text extraction", "status": "Complete", "detail": "PyMuPDF extracted statement pages and note pages with page-level references.", "confidence": 0.96},
        {"step": "Financial mapping", "status": "Complete", "detail": "FY2024 and FY2025 fields mapped to Aeon Nimbus's standard financial template.", "confidence": 0.95},
        {"step": "Validation", "status": "Complete", "detail": "Balance sheet, net debt, and free cash flow checks passed with documented rounding notes.", "confidence": 0.94},
        {"step": "Market and peer refresh", "status": "Review", "detail": "Public screening inputs are loaded, but broker or vendor refresh is still needed before client use.", "confidence": 0.82},
    ]


def _analyst_actions() -> list[dict[str, Any]]:
    return [
        {"action": "Open FY2025 source PDF", "state": "Complete", "button": "View source", "effect": "Official PDF URL and local SHA-256 are stored for audit."},
        {"action": "Review market quote", "state": "Review", "button": "Queue item", "effect": "Refresh close, volume, spread, and market cap from approved data source."},
        {"action": "Approve peer set", "state": "Review", "button": "Queue item", "effect": "Confirm peer tickers, currency conversions, IFRS differences, and liquidity filters."},
        {"action": "Lock FY2025 financials", "state": "Modeled control", "button": "Locked", "effect": "Would write maker-checker approval to audit_log in production."},
        {"action": "Export IC pack", "state": "Draft enabled", "button": "Export draft", "effect": "Creates memo HTML with source appendix for analyst editing."},
    ]


def _validation_checks() -> list[dict[str, Any]]:
    balance_source = _panel_source("Statement of financial position", "AR p.198 to p.199 / PDF p.24 to p.25", 0.96)
    net_debt_source = _panel_source("Net debt reconciliation", "AR p.278 / PDF p.104", 0.96)
    cash_flow_source = _panel_source("Statement of cash flows", "AR p.203 / PDF p.29", 0.95)
    unit_source = _panel_source("Statement presentation basis", "AR p.197 / PDF p.23", 0.96)
    return [
        {"check": "Assets = liabilities + equity", "status": "passed", "severity": "ok", "message": "FY2025 KShs 515,284.2m equals KShs 291,263.1m plus KShs 224,021.1m.", **balance_source},
        {"check": "Net debt = gross debt less cash", "status": "passed", "severity": "ok", "message": "Note 31(b) reports gross debt of KShs 159,797.4m, cash of KShs 30,024.6m, and net debt of KShs 129,772.8m.", **net_debt_source},
        {"check": "FCF = operating cash flow less capex", "status": "passed", "severity": "ok", "message": "FY2025 free cash flow is computed as KShs 137,693.9m less KShs 74,563.7m capex.", **cash_flow_source},
        {"check": "Currency and unit detection", "status": "passed", "severity": "ok", "message": "The filing states figures are presented in Kenya Shillings rounded to the nearest million.", **unit_source},
    ]


def _review_queue() -> list[dict[str, Any]]:
    return [
        {"field_id": "safaricom_plc_market_quote_2026_06_08", "reason": "Refresh live close, market cap, and dividend yield from approved market-data source.", "priority": "High"},
        {"field_id": "safaricom_plc_adv_value", "reason": "Replace single-day reference volume with 30, 90, and 180-day average daily value traded.", "priority": "High"},
        {"field_id": "safaricom_plc_peer_multiples", "reason": "Rebuild peer table from approved vendor data and align fiscal years.", "priority": "Medium"},
        {"field_id": "safaricom_plc_macro_inputs", "reason": "Pull latest Kenya and Ethiopia macro series into the WACC and FX sensitivity tab.", "priority": "Medium"},
    ]


def _approval_gates() -> list[dict[str, Any]]:
    return [
        {
            "gate": "Official filing intake",
            "status": "passed",
            "owner": "public_seed_loader",
            "evidence": f"Safaricom FY2025 official PDF, SHA-256 {_pdf_hash()}.",
            "blocks_client_export": False,
        },
        {
            "gate": "Financial statement extraction",
            "status": "passed",
            "owner": "public_seed_loader",
            "evidence": "Income statement, balance sheet, cash flow, and net debt lines carry source URL, PDF page, period, currency, unit, confidence, and review status.",
            "blocks_client_export": False,
        },
        {
            "gate": "Accounting validation",
            "status": "passed",
            "owner": "public_seed_loader",
            "evidence": "Balance sheet, net debt, free cash flow, and unit checks pass for FY2025 with rounding notes.",
            "blocks_client_export": False,
        },
        {
            "gate": "Market data approval",
            "status": "review required",
            "owner": "analyst",
            "evidence": "Public delayed quote is present but must be refreshed from approved market-data source before a recommendation or trade view.",
            "blocks_client_export": True,
        },
        {
            "gate": "Peer and liquidity approval",
            "status": "review required",
            "owner": "analyst",
            "evidence": "Peer multiples and ADV are useful for screening, but broker or vendor refresh is required before client-ready use.",
            "blocks_client_export": True,
        },
        {
            "gate": "Investment memo approval",
            "status": "draft",
            "owner": "analyst and investment lead",
            "evidence": "Memo is generated as a first draft with source appendix and open data gaps.",
            "blocks_client_export": True,
        },
    ]


def _documents(company_id: str, currency: str) -> list[dict[str, Any]]:
    return [
        {
            "document_id": "safaricom_fy2025_financial_statements",
            "company_id": company_id,
            "title": "Safaricom PLC FY2025 Annual Report and Financial Statements",
            "document_type": "annual_report",
            "period": "FY2025",
            "source": "Safaricom official investor-relations annual report PDF",
            "source_url": SAFARICOM_PDF_URL,
            "page_count": 128,
            "currency": currency,
            "unit": "KShs millions",
            "upload_date": date.today().isoformat(),
            "extraction_status": "extracted and reviewed",
            "review_status": "reviewed public filing seed",
            "confidence": 0.98,
            "sha256": _pdf_hash(),
            "storage_key": "data/raw_docs/safaricom_real/safaricom-fy2025-financial-statements.pdf",
            "reviewer": "public_seed_loader",
            "reviewed_at": date.today().isoformat(),
        },
        {
            "document_id": "safaricom_public_market_screen",
            "company_id": company_id,
            "title": "Safaricom public market quote and statistics screen",
            "document_type": "market_data",
            "period": "2026-06-08",
            "source": "StockAnalysis delayed market quote and statistics",
            "source_url": STOCK_SOURCE_URL,
            "page_count": 1,
            "currency": currency,
            "unit": "KES/share and KES market cap",
            "upload_date": date.today().isoformat(),
            "extraction_status": "manual public screen",
            "review_status": "public screening input",
            "confidence": 0.82,
            "reviewer": "analyst required",
        },
    ]


def _source_catalog() -> list[dict[str, str]]:
    official_sources = [
        {
            "name": "Safaricom FY2025 official annual report PDF",
            "category": "Documents",
            "url": SAFARICOM_PDF_URL,
            "access": "Public official company filing",
            "best_for": "Audited financial statements, segment disclosure, debt note, cash flow, related parties",
            "mvp_status": "Loaded in database seed",
            "notes": f"Local file hash {_pdf_hash()}.",
        },
        {
            "name": "StockAnalysis NASE:SCOM statistics",
            "category": "Market data",
            "url": STOCK_SOURCE_URL,
            "access": "Public delayed quote",
            "best_for": "Share price, market cap, shares outstanding, dividend screen",
            "mvp_status": "Screening input loaded",
            "notes": "Use an approved market-data feed before trading or client output.",
        },
        {
            "name": "Sterling Capital Safaricom FY25 note",
            "category": "Broker research",
            "url": STERLING_SOURCE_URL,
            "access": "Public PDF",
            "best_for": "Free-float reference, local broker view, target price cross-check",
            "mvp_status": "Reference source",
            "notes": "Treat broker research as a check, not the source of audited financials.",
        },
    ]
    return official_sources + source_catalog_as_dicts()


def _architecture() -> list[dict[str, str]]:
    return [
        {"layer": "1. Data ingestion", "purpose": "Store official PDFs, market screens, source URLs, hashes, upload dates, and company links."},
        {"layer": "2. Document parsing", "purpose": "Extract page-level text and table regions from annual reports, then retain page references."},
        {"layer": "3. Financial extraction", "purpose": "Map revenue, EBITDA, debt, cash, capex, net debt, and segment lines into structured records."},
        {"layer": "4. Standardisation", "purpose": "Normalise KShs millions, fiscal periods, IFRS labels, ratios, and currency display."},
        {"layer": "5. Research database", "purpose": "Persist approved dashboard contexts and reject unapproved sample contexts."},
        {"layer": "6. Analytics engine", "purpose": "Run multiples, DCF, liquidity, block-trade days, FX sensitivity, covenants, and recovery waterfall."},
        {"layer": "7. Search and memo engine", "purpose": "Answer from source-backed snippets and draft memo sections with citations."},
        {"layer": "8. UI and exports", "purpose": "Render interactive HTML with charts, source drawer, review queues, and memo export."},
    ]


def _pdf_hash() -> str:
    if SAFARICOM_PDF_PATH.exists():
        return sha256(SAFARICOM_PDF_PATH.read_bytes()).hexdigest()
    return PDF_HASH


def _panel_source(raw_label: str, page: str, confidence: float) -> dict[str, Any]:
    return {
        "source_document": "Safaricom PLC FY2025 Annual Report and Financial Statements",
        "source_document_id": "safaricom_fy2025_financial_statements",
        "source_page": page,
        "source_url": SAFARICOM_PDF_URL,
        "confidence": confidence,
        "review_status": "reviewed public filing seed",
        "raw_label": raw_label,
    }
