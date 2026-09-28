#!/usr/bin/env python3
"""
Working token-efficient scaling using Yahoo Finance.
Fixed to handle all data structure edge cases.
"""
import yfinance as yf
import json
from pathlib import Path
from datetime import datetime
import time

# High-impact companies (204 total)
TICKERS = {
    "China": ["BABA", "TCEHY", "BIDU", "PDD", "JD", "NTES", "LI", "NIO", "XPEV", "BILI", "TME", "YUMC", "TAL", "EDU", "IQ", "MOMO"],
    "Japan": ["TM", "SONY", "HMC", "NSANY", "MUFG", "SMFG"],
    "HongKong": ["0700.HK", "0941.HK", "1299.HK", "0005.HK", "0001.HK"],
    "SEAsia": ["GRAB", "SE", "CPNG"],
    "UK": ["HSBA.L", "SHEL.L", "AZN.L", "ULVR.L", "BP.L", "GSK.L", "RIO.L", "BATS.L", "DGE.L", "NG.L", "VOD.L", "BARC.L", "LLOY.L"],
    "Germany": ["VOW3.DE", "SAP.DE", "SIE.DE", "BMW.DE", "BAS.DE", "ALV.DE", "DTE.DE", "DBK.DE", "MUV2.DE"],
    "France": ["MC.PA", "OR.PA", "SAN.PA", "TTE.PA", "AIR.PA", "BNP.PA", "AI.PA", "SU.PA", "RI.PA"],
    "Nordics": ["NVO", "ASML", "SPOT", "NESTE.HE", "NOKIA.HE", "EQNR.OL"],
    "Swiss": ["NESN.SW", "ROG.SW", "NOVN.SW", "ABBN.SW"],
    "ItalySpain": ["RACE.MI", "ISP.MI", "ENI.MI", "ITX.MC", "SAN.MC", "TEF.MC"],
    "USA_Mega": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "V", "UNH", "JNJ", "WMT", "JPM", "XOM", "LLY", "PG", "MA", "HD", "CVX", "ABBV", "MRK", "COST", "AVGO", "PEP", "KO", "ADBE", "CRM", "TMO", "MCD", "CSCO", "ACN", "NFLX", "ABT", "NKE", "DHR", "VZ", "TXN", "ORCL", "DIS", "NEE", "PM", "CMCSA", "WFC", "BMY", "UNP", "RTX", "AMD", "QCOM", "T", "INTC"],
    "USA_Tech": ["NOW", "SNOW", "DDOG", "MDB", "NET", "CRWD", "ZS", "OKTA", "TEAM", "WDAY", "VEEV", "PANW", "FTNT", "ZM", "TWLO", "SHOP", "SQ", "PYPL", "UBER", "ABNB", "DASH", "COIN", "RBLX", "PLTR", "SOFI", "HOOD", "TTD", "MELI", "ETSY", "BKNG"],
    "USA_Finance": ["BAC", "C", "GS", "MS", "SCHW", "AXP", "BLK", "SPGI", "CB", "MMC", "ICE", "CME", "MCO", "TFC", "USB", "PNC", "COF", "BK"],
    "USA_Health": ["ISRG", "SYK", "BSX", "MDT", "EW", "ZBH", "BAX", "BDX", "CVS", "CI", "HUM", "CNC", "HCA"],
    "USA_Consumer": ["SBUX", "CMG", "YUM", "DPZ", "BA", "LMT", "GD", "NOC", "HON", "CAT", "DE", "GE", "FDX", "UPS", "DAL", "UAL"],
}

def fetch_company(ticker, region):
    """Fetch and save one company - simple and robust."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        # Basic validation
        if not info or not isinstance(info, dict) or 'symbol' not in info:
            return None, "No data"

        # Simple data structure - just store what we get
        data = {
            "ticker": ticker,
            "name": info.get("longName", ticker),
            "sector": info.get("sector", "Unknown"),
            "country": info.get("country", region),
            "market_cap": info.get("marketCap"),
            "currency": info.get("currency", "USD"),
            "website": info.get("website"),
            "industry": info.get("industry"),
            "employees": info.get("fullTimeEmployees"),
            "description": info.get("longBusinessSummary", "")[:500],
            "source": "Yahoo_Finance",
            "ingested_at": datetime.now().isoformat(),
        }

        # Save
        slug = ticker.lower().replace(".", "_").replace("-", "_")
        path = Path("data/extracted") / f"{slug}.json"
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        return slug, None

    except Exception as e:
        return None, str(e)[:80]

def main():
    print("="*70)
    print("SCALING TO 300+ COMPANIES - YAHOO FINANCE (FREE)")
    print("="*70)

    total = sum(len(t) for t in TICKERS.values())
    print(f"\n📊 Target: {total} companies\n")

    success, failed = 0, 0
    results_by_region = {}

    for region, tickers in TICKERS.items():
        print(f"\n{region} ({len(tickers)} companies):")
        region_success, region_failed = 0, 0

        for ticker in tickers:
            slug, error = fetch_company(ticker, region)
            if slug:
                print(f"  ✓ {ticker:15} → {slug}")
                success += 1
                region_success += 1
            else:
                print(f"  ✗ {ticker:15} → {error}")
                failed += 1
                region_failed += 1
            time.sleep(0.1)  # Rate limiting

        results_by_region[region] = {"success": region_success, "failed": region_failed}

    print(f"\n\n{'='*70}")
    print(f"INGESTION COMPLETE")
    print(f"{'='*70}")
    print(f"\n✅ Success: {success}")
    print(f"❌ Failed: {failed}")
    print(f"\nBy Region:")
    for region, stats in results_by_region.items():
        print(f"  {region:15} ✓ {stats['success']:3}  ✗ {stats['failed']:3}")

    print(f"\n{'='*70}")
    print("NEXT STEPS")
    print(f"{'='*70}\n")
    print("1. Check quality:")
    print("   curl http://localhost:5174/api/bulk/quality-report\n")
    print("2. Auto-enrich thin companies:")
    print("   curl -X POST http://localhost:5174/api/bulk/enrich\n")
    print("3. Rebuild platform:")
    print("   python3 build_platform.py\n")

if __name__ == "__main__":
    main()
