"""Data acquisition for the pan-African research platform.

Everything a company's model needs, sourced from free/public endpoints:

  * primary filings (annual + interim report PDFs) from the company IR site
  * free macro per country (World Bank Open Data, no key)
  * free FX to USD (open.er-api.com, covers African currencies, no key)

African IR sites often sit behind Cloudflare-style bot protection that 403s a
default client but serves a real browser. So downloads send a browser
User-Agent and fall back to a curl subprocess (which the universe-hardening
research confirmed returns HTTP 200 on these hosts).

Nothing here fabricates a figure. A download either succeeds and is recorded
with its byte count + sha256, or it is recorded as missing. That honesty is the
point: the platform must never present a number it cannot source.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import requests

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# World Bank ISO3 codes for the countries in the universe.
COUNTRY_ISO3 = {
    "Kenya": "KEN",
    "South Africa": "ZAF",
    "Nigeria": "NGA",
    "Senegal": "SEN",
    "Morocco": "MAR",
    "Egypt": "EGY",
    "Ghana": "GHA",
    "Tanzania": "TZA",
    "Uganda": "UGA",
}

# World Bank indicators that matter for a country-risk / macro section.
WB_INDICATORS = {
    "gdp_usd": "NY.GDP.MKTP.CD",
    "gdp_growth_pct": "NY.GDP.MKTP.KD.ZG",
    "inflation_pct": "FP.CPI.TOTL.ZG",
    "gdp_per_capita_usd": "NY.GDP.PCAP.CD",
    "lending_rate_pct": "FR.INR.LEND",
    "population": "SP.POP.TOTL",
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path, *, timeout: int = 90, max_mb: int = 80) -> dict[str, Any]:
    """Download `url` to `dest`, browser-UA first then curl fallback.

    Returns a manifest record: never raises, so one bad URL cannot abort a run.
    """
    if not url:
        return {"ok": False, "reason": "no url"}
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Attempt 1: requests with a browser UA.
    try:
        with requests.get(
            url, headers=BROWSER_HEADERS, stream=True, timeout=timeout, allow_redirects=True
        ) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if resp.status_code == 200 and ("pdf" in ctype.lower() or url.lower().endswith(".pdf")):
                total = 0
                with dest.open("wb") as fh:
                    for chunk in resp.iter_content(1 << 20):
                        total += len(chunk)
                        if total > max_mb * (1 << 20):
                            break
                        fh.write(chunk)
                if total > 1024:
                    return _ok(dest, resp.status_code, ctype, "requests")
            http_code = resp.status_code
            reason = f"requests http {resp.status_code}, content-type {ctype!r}"
    except requests.RequestException as exc:
        http_code = None
        reason = f"requests error: {exc}"

    # Attempt 2: curl with a browser UA (beats Cloudflare fingerprinting).
    try:
        proc = subprocess.run(
            ["curl", "-sSL", "-A", BROWSER_HEADERS["User-Agent"], "--max-time", str(timeout),
             "-o", str(dest), "-w", "%{http_code} %{content_type}", url],
            capture_output=True, text=True, timeout=timeout + 15,
        )
        meta = proc.stdout.strip().split(" ", 1)
        code = meta[0] if meta else ""
        ctype = meta[1] if len(meta) > 1 else ""
        if dest.exists() and dest.stat().st_size > 1024 and ("pdf" in ctype.lower() or code == "200"):
            return _ok(dest, code, ctype, "curl")
        return {"ok": False, "reason": f"{reason}; curl http {code}, content-type {ctype!r}"}
    except Exception as exc:  # noqa: BLE001 - acquisition must never abort a run
        return {"ok": False, "reason": f"{reason}; curl error: {exc}"}


def _ok(dest: Path, code: Any, ctype: str, via: str) -> dict[str, Any]:
    size = dest.stat().st_size
    return {
        "ok": True, "path": str(dest), "bytes": size, "mb": round(size / (1 << 20), 2),
        "http_code": str(code), "content_type": ctype, "sha256": _sha256(dest), "via": via,
    }


def fetch_macro(country: str, *, start: int = 2019, end: int = 2025) -> dict[str, Any]:
    """Free macro series per country from the World Bank (no key)."""
    iso3 = COUNTRY_ISO3.get(country)
    if not iso3:
        return {"ok": False, "reason": f"no ISO3 for {country!r}"}
    out: dict[str, Any] = {"country": country, "iso3": iso3, "series": {}}
    for name, code in WB_INDICATORS.items():
        url = (
            f"https://api.worldbank.org/v2/country/{iso3}/indicator/{code}"
            f"?format=json&per_page=60&date={start}:{end}"
        )
        try:
            data = requests.get(url, timeout=30).json()
            rows = data[1] if isinstance(data, list) and len(data) > 1 and data[1] else []
            out["series"][name] = {
                r["date"]: r["value"] for r in rows if r.get("value") is not None
            }
        except Exception as exc:  # noqa: BLE001
            out["series"][name] = {"error": str(exc)}
    out["ok"] = any(v and "error" not in v for v in out["series"].values())
    return out


def fetch_fx(base: str = "USD") -> dict[str, Any]:
    """Free USD FX rates (open.er-api.com covers KES/ZAR/NGN/EGP/MAD/XOF)."""
    try:
        data = requests.get(f"https://open.er-api.com/v6/latest/{base}", timeout=30).json()
        if data.get("result") == "success":
            return {"ok": True, "base": base, "as_of": data.get("time_last_update_utc"),
                    "rates": data.get("rates", {})}
        return {"ok": False, "reason": f"api result {data.get('result')}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc)}
