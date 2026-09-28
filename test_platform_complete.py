#!/usr/bin/env python3
"""Verify all platform completion features are operational."""
import requests
import json

BASE = "http://localhost:5174"

def test(name, endpoint, check_fn):
    try:
        r = requests.get(f"{BASE}{endpoint}", timeout=5)
        if r.status_code == 200:
            data = r.json()
            result = check_fn(data)
            print(f"✓ {name}: {result}")
            return True
        else:
            print(f"✗ {name}: HTTP {r.status_code}")
            return False
    except Exception as e:
        print(f"✗ {name}: {str(e)[:50]}")
        return False

print("=== Platform Completion Verification ===\n")

# Test APIs
results = []
results.append(test("Quality Report", "/api/bulk/quality-report", 
                    lambda d: f"{d['total_companies']} companies"))
results.append(test("Compare API", "/api/compare/?slugs=safaricom,equity_group",
                    lambda d: f"{d['count']} compared"))
results.append(test("Valuation Screener", "/api/screener/valuation?max_ev_ebitda=15&limit=3",
                    lambda d: f"{d['count']} matches"))
results.append(test("Growth Screener", "/api/screener/growth?min_revenue_cagr=0.1&limit=3",
                    lambda d: f"{d['count']} matches"))
results.append(test("Watchlist List", "/api/watchlist/",
                    lambda d: f"{len(d)} watchlists"))

# Summary
print(f"\n=== Results: {sum(results)}/{len(results)} APIs operational ===")
if all(results):
    print("\n🎉 Platform completion VERIFIED - All features working!")
else:
    print("\n⚠️  Some APIs need attention")
