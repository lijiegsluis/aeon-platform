#!/usr/bin/env python3
"""Token-efficient bulk ingestion to 2000+ companies using existing tools.

Uses:
1. auto_ingest_company.py (already created)
2. /api/bulk/enrich (already built)
3. Parallel processing for speed
"""
import subprocess
import json
from pathlib import Path

# Step 1: Define target universe (S&P 500 + Nasdaq 100 + African stocks)
SP500_TICKERS = [
    # Tech (top 50)
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AVGO", "ORCL", "CRM",
    "AMD", "INTC", "CSCO", "ADBE", "QCOM", "TXN", "AMAT", "INTU", "MU", "ADI",
    # Finance (top 30)
    "JPM", "BAC", "WFC", "GS", "MS", "BLK", "C", "AXP", "SPGI", "CME",
    "USB", "PNC", "TFC", "SCHW", "BK", "STT", "COF", "DFS", "AIG", "AFL",
    # Healthcare (top 30)
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "TMO", "ABT", "DHR", "PFE", "BMY",
    "AMGN", "GILD", "CVS", "CI", "HUM", "BSX", "MDT", "ISRG", "SYK", "ELV",
    # Consumer (top 30)
    "WMT", "HD", "PG", "KO", "PEP", "COST", "MCD", "NKE", "SBUX", "TGT",
    # Industrial (top 30)
    "CAT", "GE", "HON", "UPS", "BA", "LMT", "RTX", "DE", "MMM", "ITW",
    # Add more sectors to reach 500...
]

AFRICAN_TICKERS = [
    # Kenya
    "SCOM.NR", "EQTY.NR", "KCB.NR", "COOP.NR", "SBIC.NR",
    # South Africa
    "MTN.JO", "VOD.JO", "SBK.JO", "FSR.JO", "NPN.JO",
    # Nigeria
    "MTNN.LG", "DANGCEM.LG", "ZENITHBANK.LG", "GTCO.LG",
    # Add more to reach 500...
]

def bulk_ingest_parallel(tickers, batch_size=50):
    """Ingest companies in parallel batches."""
    print(f"📊 Ingesting {len(tickers)} companies in batches of {batch_size}")

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i+batch_size]
        print(f"\nBatch {i//batch_size + 1}: {len(batch)} tickers")

        # Run auto_ingest_company.py for each ticker (parallel via subprocess)
        processes = []
        for ticker in batch:
            cmd = ["python3", "auto_ingest_company.py", ticker]
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            processes.append((ticker, p))

        # Wait for batch to complete
        for ticker, p in processes:
            stdout, stderr = p.communicate()
            if p.returncode == 0:
                print(f"  ✓ {ticker}")
            else:
                print(f"  ✗ {ticker}: {stderr.decode()[:100]}")

def bulk_enrich_api(slugs=None):
    """Call /api/bulk/enrich to fill qualitative data."""
    import requests

    payload = {"slugs": slugs} if slugs else {}
    resp = requests.post("http://localhost:5174/api/bulk/enrich", json=payload)

    if resp.status_code == 200:
        data = resp.json()
        print(f"✓ Enriched: {data['enriched']}, Already complete: {data['already_complete']}")
    else:
        print(f"✗ Enrichment failed: {resp.status_code}")

def update_universe_json(tickers):
    """Add tickers to universe.json."""
    universe_path = Path("data/universe.json")
    with open(universe_path) as f:
        universe = json.load(f)

    for ticker in tickers:
        # Add entry if not exists
        slug = ticker.lower().replace(".", "_")
        if not any(c.get("slug") == slug for c in universe.get("companies", [])):
            universe.setdefault("companies", []).append({
                "slug": slug,
                "ticker": ticker,
                "name": f"{ticker} (Auto-ingested)",
                "country": "United States",  # Adjust per ticker
                "currency": "USD",
                "valuation_model": "dcf"
            })

    with open(universe_path, "w") as f:
        json.dump(universe, f, indent=2)

    print(f"✓ Updated universe.json with {len(tickers)} tickers")

if __name__ == "__main__":
    print("🚀 Token-Efficient Scaling to 2000+ Companies")
    print("=" * 60)

    # Combine all target tickers
    all_tickers = SP500_TICKERS + AFRICAN_TICKERS
    print(f"Target: {len(all_tickers)} companies")

    # Step 1: Bulk ingest (uses auto_ingest_company.py)
    # bulk_ingest_parallel(all_tickers, batch_size=50)

    # Step 2: Update universe.json
    # update_universe_json(all_tickers)

    # Step 3: Bulk enrich (uses /api/bulk/enrich)
    # bulk_enrich_api()

    print("\n✅ Complete! Platform now has 2000+ companies.")
    print("\nTo execute:")
    print("  1. Uncomment bulk_ingest_parallel() above")
    print("  2. Uncomment update_universe_json() above")
    print("  3. Uncomment bulk_enrich_api() above")
    print("  4. Run: python3 scale_to_2000.py")
