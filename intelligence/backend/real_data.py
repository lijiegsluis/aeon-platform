"""
Aeon Intelligence - Real Data Layer

Synchronous, dependency-light fetchers for free/no-key data sources, used to
replace the mock generators in populated_api.py. Every function fails soft:
on any network/parse error it logs and returns None/[] so a single flaky
source never takes the whole app down. Callers keep last-known-good data.
"""

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional

import feedparser
import requests

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# SEC requires a descriptive User-Agent with contact info for fair-use access.
SEC_UA = "AeonIntelligence research contact@aeonnimbus.example"

_SESSION = requests.Session()


def _get(url: str, headers: Optional[Dict[str, str]] = None, params: Optional[Dict[str, Any]] = None,
          timeout: float = 8.0) -> Optional[requests.Response]:
    try:
        resp = _SESSION.get(url, headers=headers, params=params, timeout=timeout)
        if resp.status_code == 200:
            return resp
        print(f"[real_data] GET {url} -> HTTP {resp.status_code}")
    except Exception as e:
        print(f"[real_data] GET {url} failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Sentiment
# ---------------------------------------------------------------------------

def get_fear_greed() -> Optional[Dict[str, Any]]:
    """CNN Fear & Greed Index. Needs a browser-like UA or CNN returns 418."""
    resp = _get(
        "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
        headers={
            "User-Agent": BROWSER_UA,
            "Accept": "application/json",
            "Referer": "https://www.cnn.com/markets/fear-and-greed",
        },
    )
    if not resp:
        return None
    try:
        data = resp.json()
        fg = data.get("fear_and_greed", {})
        return {"value": round(fg.get("score", 50)), "rating": fg.get("rating", "neutral")}
    except Exception as e:
        print(f"[real_data] fear_greed parse error: {e}")
        return None


def get_vix() -> Optional[float]:
    """Real-time VIX via Yahoo Finance chart API. Rate-limited, so callers should cache."""
    resp = _get(
        "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX",
        headers={"User-Agent": BROWSER_UA},
        params={"interval": "1d", "range": "5d"},
    )
    if not resp:
        return None
    try:
        result = resp.json()["chart"]["result"][0]
        closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
        return round(closes[-1], 2) if closes else None
    except Exception as e:
        print(f"[real_data] vix parse error: {e}")
        return None


def get_reddit_sentiment(subreddit: str = "wallstreetbets", limit: int = 25) -> Optional[Dict[str, Any]]:
    """Public Reddit JSON endpoint - no API key needed. Used as a social-sentiment proxy."""
    resp = _get(
        f"https://www.reddit.com/r/{subreddit}/hot.json",
        headers={"User-Agent": BROWSER_UA},
        params={"limit": limit},
    )
    if not resp:
        return None
    try:
        children = resp.json().get("data", {}).get("children", [])
        posts = [c.get("data", {}) for c in children]
        if not posts:
            return None
        avg_ratio = sum(p.get("upvote_ratio", 0.5) for p in posts) / len(posts)
        total_score = sum(p.get("score", 0) for p in posts)
        # Map avg upvote ratio (0.5-1.0 typical range) onto a 0-100 sentiment score
        value = max(0, min(100, round((avg_ratio - 0.5) * 200)))
        return {"value": value, "avg_upvote_ratio": round(avg_ratio, 3), "total_score": total_score,
                "top_titles": [p.get("title", "") for p in posts[:5]]}
    except Exception as e:
        print(f"[real_data] reddit parse error: {e}")
        return None


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

# Feeds verified reachable with a standard UA (Reuters/CNBC direct feeds 403 without
# a residential IP, so we stick to sources that responded 200 in testing).
NEWS_FEEDS = [
    ("MarketWatch", "http://feeds.marketwatch.com/marketwatch/topstories/"),
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("Investing.com", "https://www.investing.com/rss/news_25.rss"),
    ("Seeking Alpha", "https://seekingalpha.com/market_currents.xml"),
]

_BULLISH_WORDS = {"beat", "beats", "surge", "surges", "rally", "rallies", "soar", "soars", "jump", "jumps",
                   "gain", "gains", "record", "upgrade", "upgraded", "strong", "growth", "raises", "raise",
                   "approval", "approved", "outperform", "bullish"}
_BEARISH_WORDS = {"miss", "misses", "plunge", "plunges", "crash", "crashes", "drop", "drops", "fall", "falls",
                   "downgrade", "downgraded", "weak", "cuts", "cut", "recall", "lawsuit", "probe", "fraud",
                   "bearish", "selloff", "sell-off", "slump", "slumps"}


def _classify_sentiment(text: str) -> str:
    words = set(re.findall(r"[a-z']+", text.lower()))
    bullish = len(words & _BULLISH_WORDS)
    bearish = len(words & _BEARISH_WORDS)
    if bullish > bearish:
        return "bullish"
    if bearish > bullish:
        return "bearish"
    return "neutral"


def _extract_tickers(text: str, watchlist: List[str]) -> str:
    upper = text.upper()
    found = [t for t in watchlist if re.search(rf"\b{re.escape(t)}\b", upper)]
    return ",".join(found) if found else "SPY"


WATCHLIST = ["NVDA", "AAPL", "MSFT", "GOOGL", "META", "TSLA", "AMD", "AMZN", "ORCL", "COIN", "SQ", "SHOP",
             "SPY", "QQQ", "XLE", "BTC", "ETH"]


def get_real_news(limit: int = 30) -> List[Dict[str, Any]]:
    """Aggregate real RSS feeds into the app's news item shape."""
    items: List[Dict[str, Any]] = []
    seen_titles = set()

    for source, url in NEWS_FEEDS:
        # Fetch via requests (uses certifi's CA bundle) rather than feedparser's own
        # urlopen, which relies on the system SSL store and can fail to verify certs
        # in some Python installs (e.g. python.org builds without Install Certificates run).
        resp = _get(url, headers={"User-Agent": BROWSER_UA})
        if resp is None:
            continue
        try:
            feed = feedparser.parse(resp.content)
        except Exception as e:
            print(f"[real_data] feed parse failed for {source}: {e}")
            continue

        for entry in feed.entries[:20]:
            title = entry.get("title", "").strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            summary = re.sub("<[^<]+?>", "", entry.get("summary", ""))[:280]
            full_text = f"{title} {summary}"

            published_at = datetime.now()
            for key in ("published_parsed", "updated_parsed"):
                parsed = entry.get(key)
                if parsed:
                    try:
                        published_at = datetime(*parsed[:6])
                    except Exception:
                        pass
                    break

            items.append({
                "title": title,
                "published_at": published_at.isoformat(),
                "source": source,
                "sentiment": _classify_sentiment(full_text),
                "summary": summary or title,
                "tickers": _extract_tickers(full_text, WATCHLIST),
                "url": entry.get("link", ""),
                "urgency": "high" if _classify_sentiment(full_text) != "neutral" else "medium",
            })

    items.sort(key=lambda x: x["published_at"], reverse=True)
    return items[:limit]


# ---------------------------------------------------------------------------
# Crypto
# ---------------------------------------------------------------------------

_COINS = [("bitcoin", "BTC", "Bitcoin"), ("ethereum", "ETH", "Ethereum"), ("solana", "SOL", "Solana")]


def get_crypto_prices() -> Optional[List[Dict[str, Any]]]:
    ids = ",".join(c[0] for c in _COINS)
    resp = _get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": ids, "vs_currencies": "usd", "include_24hr_change": "true", "include_market_cap": "true",
                "include_24hr_vol": "true"},
    )
    if not resp:
        return None
    try:
        data = resp.json()
        out = []
        for idx, (coingecko_id, symbol, name) in enumerate(_COINS):
            d = data.get(coingecko_id)
            if not d:
                continue
            out.append({
                "id": idx + 1,
                "symbol": symbol,
                "name": name,
                "price": d.get("usd"),
                "change_24h": round(d.get("usd_24h_change", 0.0), 2),
                "market_cap": d.get("usd_market_cap"),
                "volume_24h": d.get("usd_24h_vol"),
                "source": "CoinGecko",
                "timestamp": datetime.now().isoformat(),
            })
        return out or None
    except Exception as e:
        print(f"[real_data] crypto parse error: {e}")
        return None


# ---------------------------------------------------------------------------
# SEC EDGAR: ticker map + real-time Form 4 insider trading
# ---------------------------------------------------------------------------

_TICKER_MAP_CACHE: Dict[str, Dict[str, str]] = {}


def get_sec_ticker_map() -> Dict[str, Dict[str, str]]:
    """CIK (unpadded str) -> {ticker, name}. Cached in-process; call refresh_ticker_map() to force reload."""
    global _TICKER_MAP_CACHE
    if _TICKER_MAP_CACHE:
        return _TICKER_MAP_CACHE
    resp = _get("https://www.sec.gov/files/company_tickers.json", headers={"User-Agent": SEC_UA})
    if not resp:
        return {}
    try:
        data = resp.json()
        mapping = {}
        for entry in data.values():
            cik = str(entry.get("cik_str"))
            mapping[cik] = {"ticker": entry.get("ticker", ""), "name": entry.get("title", "")}
        _TICKER_MAP_CACHE = mapping
        return mapping
    except Exception as e:
        print(f"[real_data] ticker map parse error: {e}")
        return {}


_FORM4_ATOM_URL = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&company=&dateb="
                    "&owner=include&count={count}&output=atom")

_ENTRY_RE = re.compile(
    r"<entry>.*?<title>(?P<title>[^<]*)</title>\s*"
    r"<link rel=\"alternate\" type=\"text/html\" href=\"(?P<href>[^\"]*)\"/>.*?"
    r"<updated>(?P<updated>[^<]*)</updated>.*?</entry>",
    re.DOTALL,
)


def _fetch_form4_index_entries(count: int = 60) -> List[Dict[str, str]]:
    resp = _get(_FORM4_ATOM_URL.format(count=count), headers={"User-Agent": SEC_UA})
    if not resp:
        return []
    entries = []
    for m in _ENTRY_RE.finditer(resp.text):
        entries.append({"title": m.group("title"), "href": m.group("href"), "updated": m.group("updated")})
    return entries


def _find_form4_xml_url(index_url: str) -> Optional[str]:
    resp = _get(index_url, headers={"User-Agent": SEC_UA})
    if not resp:
        return None
    # The plain (non-xslF345X06) .xml file is the raw ownershipDocument.
    candidates = re.findall(r'href="(/Archives/edgar/data/[^"]*?\.xml)"', resp.text)
    plain = [c for c in candidates if "xslF345X06" not in c]
    if not plain:
        return None
    return "https://www.sec.gov" + plain[0]


def _parse_form4_xml(xml_text: str) -> Optional[Dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        print(f"[real_data] form4 xml parse error: {e}")
        return None

    def _text(path: str, node=root) -> str:
        el = node.find(path)
        return el.text.strip() if el is not None and el.text else ""

    issuer_cik = _text("issuer/issuerCik").lstrip("0") or "0"
    issuer_ticker = _text("issuer/issuerTradingSymbol")
    issuer_name = _text("issuer/issuerName")
    owner_name = _text("reportingOwner/reportingOwnerId/rptOwnerName")
    is_officer = _text("reportingOwner/reportingOwnerRelationship/isOfficer") == "true"
    is_director = _text("reportingOwner/reportingOwnerRelationship/isDirector") == "true"
    officer_title = _text("reportingOwner/reportingOwnerRelationship/officerTitle")
    role = officer_title or ("Director" if is_director else ("Officer" if is_officer else "10% Owner"))

    bought_value = 0.0
    bought_shares = 0
    sold_value = 0.0
    sold_shares = 0
    latest_date = ""

    for txn in root.findall(".//nonDerivativeTransaction"):
        shares_txt = _text("transactionAmounts/transactionShares/value", txn)
        price_txt = _text("transactionAmounts/transactionPricePerShare/value", txn)
        acq_disp = _text("transactionAmounts/transactionAcquiredDisposedCode/value", txn)
        date_txt = _text("transactionDate/value", txn)
        if date_txt:
            latest_date = max(latest_date, date_txt)
        try:
            shares = float(shares_txt)
            price = float(price_txt) if price_txt else 0.0
        except ValueError:
            continue
        value = shares * price
        if acq_disp == "A":
            bought_shares += int(shares)
            bought_value += value
        elif acq_disp == "D":
            sold_shares += int(shares)
            sold_value += value

    if bought_value == 0 and sold_value == 0:
        return None

    is_buy = bought_value >= sold_value
    return {
        "issuer_cik": issuer_cik,
        "issuer_ticker": issuer_ticker,
        "issuer_name": issuer_name,
        "insider_name": owner_name,
        "role": role,
        "is_buy": is_buy,
        "shares": bought_shares if is_buy else sold_shares,
        "total_value": bought_value if is_buy else sold_value,
        "price": (bought_value / bought_shares) if is_buy and bought_shares else
                 (sold_value / sold_shares if sold_shares else 0.0),
        "date": latest_date or datetime.now().strftime("%Y-%m-%d"),
    }


def get_recent_form4_trades(max_filings: int = 25) -> List[Dict[str, Any]]:
    """Fetch and parse the most recent real SEC Form 4 filings. Network-heavy - call on a slow refresh cycle."""
    entries = _fetch_form4_index_entries(count=max_filings * 2)
    seen_hrefs = set()
    trades: List[Dict[str, Any]] = []

    for entry in entries:
        href = entry["href"]
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        if len(trades) >= max_filings:
            break

        xml_url = _find_form4_xml_url(href)
        if not xml_url:
            continue
        time.sleep(0.15)  # be gentle with SEC's servers
        xml_resp = _get(xml_url, headers={"User-Agent": SEC_UA})
        if not xml_resp:
            continue
        parsed = _parse_form4_xml(xml_resp.text)
        if parsed:
            parsed["filed_at"] = entry["updated"][:10] if entry.get("updated") else parsed["date"]
            trades.append(parsed)
        time.sleep(0.15)

    return trades
