#!/usr/bin/env python3
"""
Simplified token-efficient scaling using Yahoo Finance.
Handles JSON serialization properly and focuses on highest-impact companies.
"""
import yfinance as yf
import json
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np

# High-impact companies only (204 total)
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

def to_json_safe(obj):
    """Convert any object to JSON-safe type."""
    if isinstance(obj, (pd.Timestamp, pd.DatetimeIndex, datetime)):
        return str(obj)
    elif isinstance(obj, (np.integer, np.floating)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    elif pd.isna(obj):
        return None
    elif isinstance(obj, dict):
        return {str(k): to_json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [to_json_safe(v) for v in obj]
    return obj

def fetch_company(ticker, region):
    """Fetch and save one company."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        if not info or 'symbol' not in info:
            return None, "No data"

        # Get financials - convert to dict immediately
        try:
            inc = stock.financials.to_dict('index') if hasattr(stock.financials, 'to_dict') else {}
            bal = stock.balance_sheet.to_dict('index') if hasattr(stock.balance_sheet, 'to_dict') else {}
            cf = stock.cashflow.to_dict('index') if hasattr(stock.cashflow, 'to_dict') else {}
        except:
            inc, bal, cf = {}, {}, {}

        if not any([inc, bal, cf]):
            return None, "No financials"

        data = {
            "ticker": ticker,
            "name": info.get("longName", ticker),
            "sector": info.get("sector", "Unknown"),
            "country": info.get("country", region),
            "market_cap": info.get("marketCap"),
            "currency": info.get("currency", "USD"),
            "info": info,
            "financials": {"income": inc, "balance": bal, "cashflow": cf},
            "source": "Yahoo_Finance",
            "ingested_at": datetime.now().isoformat(),
        }

        # Save
        slug = ticker.lower().replace(".", "_").replace("-", "_")
        path = Path("data/extracted") / f"{slug}.json"
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            json.dump(to_json_safe(data), f, indent=2)

        return slug, None

    except Exception as e:
        return None, str(e)[:80]

def main():
    print("=" * 70)
    print("SCALING TO 700 COMPANIES - YAHOO FINANCE (FREE)")
    print("=" * 70)

    total = sum(len(t) for t in TICKERS.values())
    print(f"\n📊 Target: {total} companies\n")

    success, failed = 0, 0

    for region, tickers in TICKERS.items():
        print(f"\n{region} ({len(tickers)} companies):")
        for ticker in tickers:
            slug, error = fetch_company(ticker, region)
            if slug:
                print(f"  ✓ {ticker:15} → {slug}")
                success += 1
            else:
                print(f"  ✗ {ticker:15} → {error}")
                failed += 1

    print(f"\n\n{'='*70}")
    print(f"COMPLETE: ✓ {success}  ✗ {failed}")
    print(f"{'='*70}\n")

    print("Next: curl http://localhost:5174/api/bulk/quality-report")

if __name__ == "__main__":
    main()
