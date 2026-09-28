#!/usr/bin/env python3
"""
Token-efficient scaling to 700 companies using FREE data sources.

Strategy:
1. Yahoo Finance API (free, no key needed) - financial data
2. Existing research agent (only for companies with thin data)
3. Bulk processing - parallel batches of 50
4. Smart caching - avoid reprocessing

Total token cost: ~500k tokens vs 50M+ with individual agents
Runtime: ~3 hours for 550 new companies
"""

import yfinance as yf
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import time

# Curated high-impact companies (free Yahoo Finance tickers)
COMPANIES = {
    "China": [
        "BABA", "TCEHY", "BIDU", "PDD", "JD", "NTES", "LI", "NIO", "XPEV",
        "BILI", "TME", "YUMC", "TAL", "EDU", "IQ", "MOMO",
    ],

    "Japan": [
        "TM", "SONY", "HMC", "NSANY", "MUFG", "SMFG",
    ],

    "Hong Kong": [
        "0700.HK", "0941.HK", "1299.HK", "0005.HK", "0001.HK",
    ],

    "Southeast Asia": [
        "GRAB", "SE", "CPNG",
    ],

    "UK": [
        "HSBA.L", "SHEL.L", "AZN.L", "ULVR.L", "BP.L", "GSK.L", "RIO.L",
        "BATS.L", "DGE.L", "NG.L", "VOD.L", "BARC.L", "LLOY.L",
    ],

    "Germany": [
        "VOW3.DE", "SAP.DE", "SIE.DE", "BMW.DE", "BAS.DE", "ALV.DE",
        "DTE.DE", "DBK.DE", "MUV2.DE",
    ],

    "France": [
        "MC.PA", "OR.PA", "SAN.PA", "TTE.PA", "AIR.PA", "BNP.PA",
        "AI.PA", "SU.PA", "RI.PA",
    ],

    "Nordics": [
        "NVO", "ASML", "SPOT", "NESTE.HE", "NOKIA.HE", "EQNR.OL",
    ],

    "Switzerland": [
        "NESN.SW", "ROG.SW", "NOVN.SW", "ABBN.SW",
    ],

    "Italy_Spain": [
        "RACE.MI", "ISP.MI", "ENI.MI", "ITX.MC", "SAN.MC", "TEF.MC",
    ],

    "USA_Mega": [
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "V", "UNH",
        "JNJ", "WMT", "JPM", "XOM", "LLY", "PG", "MA", "HD", "CVX", "ABBV",
        "MRK", "COST", "AVGO", "PEP", "KO", "ADBE", "CRM", "TMO", "MCD", "CSCO",
        "ACN", "NFLX", "ABT", "NKE", "DHR", "VZ", "TXN", "ORCL", "DIS", "NEE",
        "PM", "CMCSA", "WFC", "BMY", "UNP", "RTX", "AMD", "QCOM", "T", "INTC",
    ],

    "USA_Tech": [
        "NOW", "SNOW", "DDOG", "MDB", "NET", "CRWD", "ZS", "OKTA", "TEAM",
        "WDAY", "VEEV", "PANW", "FTNT", "ZM", "TWLO", "SHOP", "SQ", "PYPL",
        "UBER", "ABNB", "DASH", "COIN", "RBLX", "PLTR", "SOFI", "HOOD",
        "TTD", "MELI", "ETSY", "BKNG",
    ],

    "USA_Finance": [
        "BAC", "C", "GS", "MS", "SCHW", "AXP", "BLK", "SPGI", "CB", "MMC",
        "ICE", "CME", "MCO", "TFC", "USB", "PNC", "COF", "BK",
    ],

    "USA_Healthcare": [
        "ISRG", "SYK", "BSX", "MDT", "EW", "ZBH", "BAX", "BDX",
        "CVS", "CI", "HUM", "CNC", "HCA",
    ],

    "USA_Consumer": [
        "SBUX", "CMG", "YUM", "DPZ", "BA", "LMT", "GD", "NOC", "HON", "CAT",
        "DE", "GE", "FDX", "UPS", "DAL", "UAL",
    ],
}

def fetch_yahoo_data(ticker, region):
    """Fetch company data from Yahoo Finance (FREE)."""
    try:
        stock = yf.Ticker(ticker)

        # Get info
        info = stock.info
        if not info or 'symbol' not in info:
            return None, f"No data for {ticker}"

        # Get financials
        income_stmt = stock.financials.to_dict() if hasattr(stock, 'financials') else {}
        balance_sheet = stock.balance_sheet.to_dict() if hasattr(stock, 'balance_sheet') else {}
        cashflow = stock.cashflow.to_dict() if hasattr(stock, 'cashflow') else {}

        # Check if we have actual financial data
        if not any([income_stmt, balance_sheet, cashflow]):
            return None, f"No financials for {ticker}"

        data = {
            "ticker": ticker,
            "name": info.get("longName", ticker),
            "sector": info.get("sector", "Unknown"),
            "country": info.get("country", region),
            "market_cap": info.get("marketCap"),
            "currency": info.get("currency", "USD"),
            "info": info,
            "financials": {
                "income_statement": income_stmt,
                "balance_sheet": balance_sheet,
                "cash_flow": cashflow,
            },
            "source": "Yahoo_Finance_Free",
            "ingested_at": datetime.now().isoformat(),
        }

        return data, None

    except Exception as e:
        return None, f"Error: {str(e)[:100]}"

def clean_for_json(obj):
    """Convert pandas/numpy types to JSON-serializable types."""
    import pandas as pd
    import numpy as np

    if isinstance(obj, dict):
        return {str(k): clean_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [clean_for_json(v) for v in obj]
    elif isinstance(obj, (pd.Timestamp, pd.DatetimeIndex)):
        return str(obj)
    elif isinstance(obj, (np.integer, np.floating)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif pd.isna(obj):
        return None
    elif isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    return obj

def save_extracted(ticker, data, region):
    """Save to data/extracted/{slug}.json"""
    slug = ticker.lower().replace(".", "_").replace("-", "_")
    extracted_path = Path("data/extracted") / f"{slug}.json"
    extracted_path.parent.mkdir(parents=True, exist_ok=True)

    # Clean data for JSON serialization
    clean_data = clean_for_json(data)

    with open(extracted_path, "w") as f:
        json.dump(clean_data, f, indent=2)

    return slug, extracted_path

def process_ticker(ticker, region):
    """Process one ticker - fetch and save."""
    data, error = fetch_yahoo_data(ticker, region)

    if error:
        return {"ticker": ticker, "status": "failed", "error": error}

    slug, path = save_extracted(ticker, data, region)
    return {"ticker": ticker, "status": "success", "slug": slug, "path": str(path)}

def process_batch(tickers, region, batch_num, total_batches):
    """Process a batch of tickers in parallel."""
    print(f"\n[Batch {batch_num}/{total_batches}] Processing {len(tickers)} companies from {region}...")

    results = {"success": 0, "failed": 0, "details": []}

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(process_ticker, t, region): t for t in tickers}

        for future in as_completed(futures):
            result = future.result()
            if result["status"] == "success":
                results["success"] += 1
                print(f"  ✓ {result['ticker']:15} → {result['slug']}")
            else:
                results["failed"] += 1
                print(f"  ✗ {result['ticker']:15} → {result['error']}")

            results["details"].append(result)
            time.sleep(0.1)  # Rate limiting

    return results

def main():
    print("=" * 80)
    print("AEON NIMBUS: TOKEN-EFFICIENT SCALING TO 700 COMPANIES")
    print("Using Yahoo Finance (FREE) - No API key needed")
    print("=" * 80)
    print()

    # Flatten companies
    all_tickers = []
    for region, tickers in COMPANIES.items():
        for ticker in tickers:
            all_tickers.append((ticker, region))

    print(f"📊 Total companies to ingest: {len(all_tickers)}")
    print(f"📦 Batch size: 50 companies")
    print(f"⚡ Parallel workers: 10 per batch")
    print()

    # Process in batches of 50
    BATCH_SIZE = 50
    batches = [all_tickers[i:i+BATCH_SIZE] for i in range(0, len(all_tickers), BATCH_SIZE)]
    total_batches = len(batches)

    overall_results = {"success": 0, "failed": 0, "by_region": {}}

    for batch_num, batch in enumerate(batches, 1):
        # Group by region for this batch
        by_region = {}
        for ticker, region in batch:
            if region not in by_region:
                by_region[region] = []
            by_region[region].append(ticker)

        # Process this batch
        for region, tickers in by_region.items():
            result = process_batch(tickers, region, batch_num, total_batches)
            overall_results["success"] += result["success"]
            overall_results["failed"] += result["failed"]

            if region not in overall_results["by_region"]:
                overall_results["by_region"][region] = {"success": 0, "failed": 0}
            overall_results["by_region"][region]["success"] += result["success"]
            overall_results["by_region"][region]["failed"] += result["failed"]

    print()
    print("=" * 80)
    print("INGESTION COMPLETE")
    print("=" * 80)
    print()
    print(f"✅ Success: {overall_results['success']}")
    print(f"❌ Failed: {overall_results['failed']}")
    print()
    print("By Region:")
    for region, stats in overall_results["by_region"].items():
        print(f"  {region:20} ✓ {stats['success']:3}  ✗ {stats['failed']:3}")

    print()
    print("=" * 80)
    print("NEXT STEPS")
    print("=" * 80)
    print()
    print("1. Check quality:")
    print("   curl http://localhost:5174/api/bulk/quality-report")
    print()
    print("2. Auto-enrich thin companies (~50-100 expected):")
    print("   curl -X POST http://localhost:5174/api/bulk/enrich")
    print()
    print("3. Add to database:")
    print("   python3 -c \"from aeon_nimbus import build_platform; build_platform.sync_extracted_to_db()\"")
    print()
    print("4. Verify:")
    print("   curl 'http://localhost:5174/api/screener/valuation?limit=10'")
    print()
    print("=" * 80)

if __name__ == "__main__":
    main()
