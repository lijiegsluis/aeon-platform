"""Live price fetching with accurate exchange-suffix mappings for all covered markets.

Supports: US (NASDAQ/NYSE), JSE, LSE, Euronext Paris/Amsterdam, Xetra, SIX Swiss,
Nasdaq Copenhagen, Tokyo, Korea Exchange, Hong Kong, BSE/NSE India, Taiwan, NSE Kenya,
NGX Nigeria, Ghana GSE, EGX Egypt, Casablanca, BRVM Senegal.

Companies not on yfinance-covered exchanges (NSE Kenya, NGX, BRVM, GSE, EGX)
fall back to the stored manual price from extracted.market.
"""
from __future__ import annotations

import time
import threading
from typing import Any

# ── Exchange → yfinance suffix map ───────────────────────────────────────────
# Keys are lowercase substrings matched against the exchange field in universe.json.
# More-specific strings must come before shorter overlapping ones.
_EXCHANGE_SUFFIX: list[tuple[str, str]] = [
    # South Africa
    ("johannesburg", ".JO"),
    # UK
    ("london stock exchange", ".L"),
    # Euronext
    ("euronext paris", ".PA"),
    ("euronext amsterdam", ".AS"),
    # Germany
    ("xetra", ".DE"),
    ("frankfurt", ".DE"),
    # Switzerland
    ("six swiss", ".SW"),
    # Nordic
    ("nasdaq copenhagen", ".CO"),
    ("nasdaq stockholm", ".ST"),
    ("nasdaq helsinki", ".HE"),
    ("oslo", ".OL"),
    # Japan
    ("tokyo stock exchange", ".T"),
    # Korea
    ("korea exchange", ".KS"),
    # Hong Kong
    ("hong kong stock exchange", ".HK"),
    ("hong kong", ".HK"),
    # India (use NYSE ADR tickers for BSE/NSE cross-listed cos; otherwise .NS)
    ("bombay stock exchange", ".NS"),
    ("nse india", ".NS"),
    # Taiwan
    ("taiwan stock exchange", ".TW"),
    # Singapore
    ("sgx", ".SI"),
    ("singapore", ".SI"),
    # Australia
    ("asx", ".AX"),
    ("australian securities", ".AX"),
    # Canada
    ("tsx", ".TO"),
    ("toronto", ".TO"),
    # US — no suffix needed
    ("nasdaq (united states)", ""),
    ("nyse (united states)", ""),
    ("nasdaq", ""),
    ("nyse", ""),
]

# ── Per-ticker manual overrides (covers tickers yfinance doesn't auto-resolve) ─
# Format: company_ticker (uppercase) → yfinance ticker string
_MANUAL_YF: dict[str, str] = {
    # South Africa JSE
    "MTN": "MTN.JO",
    "VOD": "VOD.JO",
    "SBK": "SBK.JO",
    "FSR": "FSR.JO",
    "ABG": "ABG.JO",
    "NPN": "NPN.JO",
    "BID": "BID.JO",
    "SHP": "SHP.JO",
    "CPI": "CPI.JO",
    "TBS": "TBS.JO",
    # SOL dual-listed — use JSE primary
    "SOL": "SOL.JO",
    # UK LSE
    "AAF": "AAF.L",
    "SHEL": "SHEL.L",
    "AZN": "AZN.L",
    "ULVR": "ULVR.L",
    "HSBA": "HSBA.L",
    # Seplat dual-listed — use LSE
    "SEPL": "SEPL.L",
    # France Euronext
    "TTE": "TTE.PA",
    "MC": "MC.PA",
    "BNP": "BNP.PA",
    "AIR": "AIR.PA",
    # Germany Xetra
    "SAP": "SAP.DE",
    "SIE": "SIE.DE",
    "VOW3": "VOW3.DE",
    # Switzerland SIX
    "NESN": "NESN.SW",
    "ROG": "ROG.SW",
    # Denmark Nasdaq Copenhagen
    "NOVO-B": "NOVO-B.CO",
    # Japan
    "TM": "TM",           # ADR on NYSE — yfinance works bare
    "SONY": "SONY",       # ADR on NYSE
    # Korea
    "005930": "005930.KS",
    # Hong Kong
    "0700": "0700.HK",
    "BYDDY": "BYDDY",     # US OTC ADR
    # India — US ADRs work bare
    "HDB": "HDB",
    "INFY": "INFY",
    # Taiwan — ADR
    "TSM": "TSM",
    # China — US listings work bare
    "BABA": "BABA",
    "JD": "JD",
    # Netherlands
    "ASML": "ASML",       # cross-listed on NASDAQ, works bare
    # US stocks — bare tickers, no suffix needed
    "AAPL": "AAPL", "MSFT": "MSFT", "GOOGL": "GOOGL", "AMZN": "AMZN",
    "NVDA": "NVDA", "META": "META", "AVGO": "AVGO", "TSLA": "TSLA",
    "AMD": "AMD", "CRM": "CRM", "ORCL": "ORCL", "MRVL": "MRVL",
    "RDDT": "RDDT", "INTC": "INTC", "NFLX": "NFLX", "COST": "COST",
    "SBUX": "SBUX", "NKE": "NKE", "DIS": "DIS", "JPM": "JPM",
    "BAC": "BAC", "GS": "GS", "MS": "MS", "C": "C", "WFC": "WFC",
    "V": "V", "MA": "MA", "AXP": "AXP", "BRK.B": "BRK-B",
    "BLK": "BLK", "SPGI": "SPGI",
    "XOM": "XOM", "CVX": "CVX", "COP": "COP", "SLB": "SLB",
    "LLY": "LLY", "JNJ": "JNJ", "UNH": "UNH", "PFE": "PFE",
    "ABT": "ABT", "MRK": "MRK", "TMO": "TMO",
    "ABBV": "ABBV", "AMGN": "AMGN",
    "HD": "HD", "WMT": "WMT", "MCD": "MCD", "SBUX": "SBUX",
    "KO": "KO", "PG": "PG",
    "BA": "BA", "CAT": "CAT", "DE": "DE", "UNP": "UNP", "RTX": "RTX",
    "NEE": "NEE", "DUK": "DUK",
    "UBER": "UBER", "TMUS": "TMUS", "VZ": "VZ",
    "NOW": "NOW", "SNOW": "SNOW", "PLTR": "PLTR", "PANW": "PANW",
    # African exchanges — yfinance coverage is poor; these return None gracefully
    # NSE Kenya — not reliably covered by yfinance
    "SCOM": None,  # Safaricom — use stored price
    "EQTY": None,
    "KCB": None,
    "COOP": None,
    "BAMB": None,
    "EABL": None,
    # NGX Nigeria — not covered
    "MTNN": None,
    "ZENITHBANK": None,
    "NB": None,
    "DANGCEM": None,
    "BUAFOODS": None,
    "BUACEMENT": None,
    "GTCO": None,
    # Ghana GSE — not covered
    "MTNGH": None,
    # Casablanca — not covered
    "IAM": None,
    # BRVM Senegal — not covered
    "SNTS": None,
    # Egypt EGX — not covered
    "COMI": None,
}


def resolve_yf_ticker(ticker: str, exchange: str = "") -> str | None:
    """Return the yfinance-resolvable ticker string, or None if not fetchable."""
    t = ticker.strip().upper()
    if t in _MANUAL_YF:
        return _MANUAL_YF[t]  # may be None — caller handles
    # Auto-derive from exchange
    ex = exchange.lower()
    for key, suffix in _EXCHANGE_SUFFIX:
        if key in ex:
            return t + suffix if suffix else t
    # US default — bare ticker works
    return t


# ── In-memory price cache ─────────────────────────────────────────────────────
_cache: dict[str, dict] = {}   # ticker → {price, change_pct, currency, ts}
_CACHE_TTL = 300  # 5 minutes
_lock = threading.Lock()


def _fetch_batch_yf(yf_tickers: list[str]) -> dict[str, tuple[float | None, float | None]]:
    """Fetch price + change_pct for a list of yfinance tickers. Returns {yft: (price, chg%)}."""
    try:
        import yfinance as yf
    except ImportError:
        return {}
    result: dict[str, tuple[float | None, float | None]] = {}
    for yft in yf_tickers:
        try:
            info = yf.Ticker(yft).fast_info
            price = getattr(info, "last_price", None)
            prev = getattr(info, "previous_close", None)
            if price is None or price == 0:
                result[yft] = (None, None)
                continue
            chg = ((price - prev) / prev * 100) if prev and prev != 0 else None
            result[yft] = (round(float(price), 4), round(float(chg), 2) if chg is not None else None)
        except Exception:
            result[yft] = (None, None)
    return result


def get_live_prices(companies: list[dict]) -> list[dict]:
    """
    For each company dict (must have keys: ticker, slug, exchange, currency,
    and optionally yf_ticker + extracted.market.share_price for fallback),
    return a list of price records.
    """
    now = time.time()
    results = []
    need_fetch: list[tuple[str, str]] = []  # (original_ticker, yf_ticker)

    for co in companies:
        ticker = (co.get("ticker") or "").upper()
        if not ticker:
            continue
        yft = co.get("yf_ticker") or resolve_yf_ticker(ticker, co.get("exchange", ""))
        if yft is None:
            # Not on yfinance — use stored manual price
            mkt = (co.get("extracted") or {}).get("market") or {}
            price = mkt.get("share_price") or mkt.get("price")
            price_date = mkt.get("price_date", "")
            results.append({
                "ticker": ticker,
                "slug": co.get("slug", ticker.lower()),
                "price": price,
                "change_pct": None,
                "currency": co.get("currency", ""),
                "source": "manual",
                "price_date": price_date,
                "stale": bool(price_date and price_date < "2026-01-01"),
            })
            continue
        with _lock:
            cached = _cache.get(yft)
        if cached and now - cached["ts"] < _CACHE_TTL:
            results.append({**cached, "ticker": ticker, "slug": co.get("slug", ticker.lower()), "source": "live"})
        else:
            need_fetch.append((ticker, yft, co.get("slug", ""), co.get("currency", "")))

    if need_fetch:
        yf_tickers = [x[1] for x in need_fetch]
        fetched = _fetch_batch_yf(yf_tickers)
        for orig, yft, slug, currency in need_fetch:
            price, chg = fetched.get(yft, (None, None))
            entry = {"ticker": orig, "slug": slug, "price": price, "change_pct": chg,
                     "currency": currency, "source": "live", "yf_ticker": yft,
                     "price_date": "", "stale": False, "ts": now}
            with _lock:
                _cache[yft] = entry
            results.append(entry)

    return results
