"""
filing_alerts.py — check IR pages for filings newer than stored data.

No external dependencies beyond the stdlib and requests (already in requirements).
Never raises; all errors are swallowed and return None / empty list.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional

import requests


# ---------------------------------------------------------------------------
# HTML parser — collect year mentions from <a> tags
# ---------------------------------------------------------------------------

class _AnchorYearParser(HTMLParser):
    """Collect the most recent 20XX year found in <a> href attributes and link text."""

    def __init__(self):
        super().__init__()
        self._years: list[int] = []
        self._in_a = False
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._in_a = True
            self._buf = ""
            for name, val in attrs:
                if name == "href" and val:
                    for m in re.findall(r"20\d{2}", val):
                        self._years.append(int(m))

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a:
            for m in re.findall(r"20\d{2}", self._buf):
                self._years.append(int(m))
            self._in_a = False
            self._buf = ""

    def handle_data(self, data):
        if self._in_a:
            self._buf += data

    def max_year(self) -> Optional[int]:
        return max(self._years) if self._years else None


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

_HEADERS = {"User-Agent": "AeonNimbus/1.0 (filing-alert-checker; research platform)"}


def _fetch_max_year(url: str) -> Optional[int]:
    """HTTP-GET *url* and return the most recent 20XX year found in anchor tags."""
    try:
        r = requests.get(url, timeout=5, headers=_HEADERS, allow_redirects=True)
        r.raise_for_status()
        parser = _AnchorYearParser()
        parser.feed(r.text)
        return parser.max_year()
    except Exception:
        return None


def _latest_stored_year(financials: list[dict]) -> Optional[int]:
    """Extract the most recent integer year from a list of financial period dicts."""
    years: list[int] = []
    for f in financials or []:
        fy = str(f.get("fy") or "")
        m = re.search(r"20\d{2}", fy)
        if m:
            years.append(int(m.group()))
    return max(years) if years else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_company_for_new_filing(co_universe: dict, co_extracted: dict) -> Optional[dict]:
    """Return an alert dict if the IR page mentions a year newer than stored data, else None."""
    url = co_universe.get("ir_url") or co_universe.get("annual_report_url")
    if not url:
        return None

    filing_year = _fetch_max_year(url)
    if filing_year is None:
        return None

    financials = (co_extracted or {}).get("financials") or []
    stored_year = _latest_stored_year(financials)
    if stored_year is None:
        return None

    if filing_year <= stored_year:
        return None

    return {
        "slug": co_universe.get("slug") or co_universe.get("ticker", ""),
        "name": co_universe.get("name", ""),
        "filing_year": filing_year,
        "stored_year": stored_year,
        "ir_url": url,
        "detected_at": datetime.now(timezone.utc).isoformat(),
    }


def check_all_companies(companies: list[dict]) -> list[dict]:
    """Check each company for a new IR filing; throttle at 1 req/s. Return list of alerts."""
    alerts: list[dict] = []
    first = True
    for co in companies:
        universe = co.get("universe") or {}
        extracted = co.get("extracted") or {}

        # Skip companies with no IR URL silently
        if not (universe.get("ir_url") or universe.get("annual_report_url")):
            continue

        if not first:
            time.sleep(1)
        first = False

        # Enrich universe with slug/name from the wrapper dict for the alert payload
        merged_universe = {
            "slug": co.get("slug", ""),
            "name": co.get("name", ""),
            **universe,
        }
        result = check_company_for_new_filing(merged_universe, extracted)
        if result is not None:
            alerts.append(result)

    return alerts


def format_alert(alert: dict) -> str:
    """One-liner human-readable description of an alert."""
    return (
        f"New filing detected: {alert.get('name')} — "
        f"{alert.get('filing_year')} (stored: {alert.get('stored_year')}) — "
        f"{alert.get('ir_url')}"
    )
