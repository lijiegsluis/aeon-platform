"""Connectors and source catalog for market, FX, macro, and document data."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import requests


@dataclass(frozen=True)
class DataSource:
    name: str
    category: str
    url: str
    access: str
    best_for: str
    mvp_status: str
    notes: str


SOURCE_CATALOG: list[DataSource] = [
    DataSource(
        name="Company investor relations pages",
        category="Documents",
        url="Company-specific websites",
        access="Usually public, no standard API",
        best_for="Annual reports, interim reports, presentations, announcements",
        mvp_status="Primary source",
        notes="Use manual upload first, then add targeted scrapers for repeat coverage names.",
    ),
    DataSource(
        name="Stock exchange announcement pages",
        category="Documents",
        url="Local exchange websites such as NSE Kenya, DSE Tanzania, JSE, EGX, BRVM",
        access="Public pages, layouts vary",
        best_for="Market announcements, filings, trading notices",
        mvp_status="Targeted scrapers later",
        notes="Treat as semi-structured. Keep source URL and capture date.",
    ),
    DataSource(
        name="World Bank Indicators API",
        category="Macro",
        url="https://datahelpdesk.worldbank.org/knowledgebase/articles/889392",
        access="No API key required",
        best_for="GDP growth, inflation, population, reserves, debt indicators",
        mvp_status="Ready",
        notes="Use for country macro panels and country-risk context.",
    ),
    DataSource(
        name="Frankfurter FX API",
        category="FX",
        url="https://frankfurter.dev/docs/",
        access="No API key required",
        best_for="Daily FX reference rates for major currencies",
        mvp_status="Ready",
        notes="Good no-key MVP feed. Local frontier currencies may need central bank sources.",
    ),
    DataSource(
        name="Alpha Vantage",
        category="Market data",
        url="https://www.alphavantage.co/documentation/",
        access="Free API key required, rate limited",
        best_for="Historical prices and volumes where symbol support exists",
        mvp_status="Optional",
        notes="Useful for MVP demos. Validate coverage for African tickers before relying on it.",
    ),
    DataSource(
        name="Financial Modeling Prep",
        category="Market and fundamentals",
        url="https://site.financialmodelingprep.com/developer/docs/quickstart",
        access="API key required",
        best_for="Global equities, company profiles, statements, ratios",
        mvp_status="Optional",
        notes="Can accelerate peer data, but audited annual reports should remain the source of truth.",
    ),
    DataSource(
        name="OECD Africa Capital Markets Report",
        category="Market context",
        url="https://www.oecd.org/en/publications/africa-capital-markets-report-2025_7d26e1d3-en",
        access="Public report",
        best_for="Context on African market depth, liquidity, and market structure",
        mvp_status="Reference",
        notes="Useful for explaining why liquidity and source quality matter.",
    ),
]


def source_catalog_as_dicts() -> list[dict[str, str]]:
    return [asdict(source) for source in SOURCE_CATALOG]


def fetch_world_bank_indicator(country_code: str, indicator: str, start_year: int, end_year: int) -> list[dict[str, Any]]:
    """Fetch a World Bank indicator series for one country."""

    url = (
        f"https://api.worldbank.org/v2/country/{country_code}/indicator/{indicator}"
        f"?format=json&per_page=200&date={start_year}:{end_year}"
    )
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or len(payload) < 2:
        return []
    return [
        {
            "country": item.get("country", {}).get("value"),
            "country_code": country_code.upper(),
            "indicator": indicator,
            "year": item.get("date"),
            "value": item.get("value"),
            "source": "World Bank Indicators API",
        }
        for item in payload[1]
        if item.get("value") is not None
    ]


def fetch_frankfurter_rate(base: str, quote: str, rate_date: str | None = None) -> dict[str, Any]:
    """Fetch a no-key FX rate from Frankfurter."""

    endpoint_date = rate_date or date.today().isoformat()
    url = f"https://api.frankfurter.app/{endpoint_date}?from={base.upper()}&to={quote.upper()}"
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    payload = response.json()
    return {
        "base": payload.get("base", base.upper()),
        "quote": quote.upper(),
        "date": payload.get("date", endpoint_date),
        "rate": payload.get("rates", {}).get(quote.upper()),
        "source": "Frankfurter FX API",
    }


def fetch_alpha_vantage_daily(symbol: str) -> list[dict[str, Any]]:
    """Fetch daily market data from Alpha Vantage when ALPHA_VANTAGE_API_KEY is set."""

    api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        raise RuntimeError("Set ALPHA_VANTAGE_API_KEY to use Alpha Vantage.")
    params = {
        "function": "TIME_SERIES_DAILY_ADJUSTED",
        "symbol": symbol,
        "outputsize": "compact",
        "apikey": api_key,
    }
    response = requests.get("https://www.alphavantage.co/query", params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    series = payload.get("Time Series (Daily)", {})
    return [
        {
            "date": day,
            "open": float(values["1. open"]),
            "high": float(values["2. high"]),
            "low": float(values["3. low"]),
            "close": float(values["4. close"]),
            "adjusted_close": float(values["5. adjusted close"]),
            "volume": float(values["6. volume"]),
            "source": "Alpha Vantage",
        }
        for day, values in series.items()
    ]


def fetch_fmp_profile(symbol: str) -> dict[str, Any]:
    """Fetch a company profile from FMP when FMP_API_KEY is set."""

    api_key = os.getenv("FMP_API_KEY")
    if not api_key:
        raise RuntimeError("Set FMP_API_KEY to use Financial Modeling Prep.")
    url = f"https://financialmodelingprep.com/stable/profile?symbol={symbol}&apikey={api_key}"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, list) and payload:
        return payload[0]
    if isinstance(payload, dict):
        return payload
    return {}
