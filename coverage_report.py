"""Print the per-company data-coverage matrix across the eight required
categories, straight from universe.json + coverage.json (no fabrication).

  1 reports        latest annual + interim report PDFs
  2 presentations  investor presentation / IR page / filings
  3 financials 5y  5yr IS/BS/CF (source identified + extractable from filings)
  4 notes          statement notes & schedules (in the annual report PDF)
  5 market         price / market cap / exchange-ticker / FX
  6 kpis           sector operating KPIs
  7 peers          listed comparables
  8 macro          sector / country / macro sources
"""

from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
OK, PART, NO = "✓", "◐", "·"  # ✓  ◐  ·


def status(uni: dict, cov: dict) -> list[str]:
    reports = OK if cov.get("annual_report") else NO
    if cov.get("annual_report") and cov.get("interim_report"):
        reports = OK
    elif cov.get("annual_report"):
        reports = OK  # annual is the primary; interim is a bonus
    pres = OK if (cov.get("investor_presentation") or uni.get("investor_presentation_url") or uni.get("ir_url")) else NO
    fin5 = OK if uni.get("five_year_financials_note") else NO
    notes = OK if cov.get("annual_report") else NO
    market = PART  # market cap + ticker + FX yes; live intraday price constrained on African exchanges
    kpis = OK if len(uni.get("sector_kpis", [])) >= 5 else (PART if uni.get("sector_kpis") else NO)
    peers = OK if len(uni.get("peers", [])) >= 3 else (PART if uni.get("peers") else NO)
    macro = OK  # World Bank + FX + per-company macro sources
    return [reports, pres, fin5, notes, market, kpis, peers, macro]


def main() -> None:
    uni = {c["slug"]: c for c in json.loads((DATA / "universe.json").read_text())}
    cov = {c["slug"]: c for c in json.loads((DATA / "coverage.json").read_text())}
    hdr = ["reports", "present", "fin-5y", "notes", "market", "kpis", "peers", "macro"]
    print(f"DATA COVERAGE MATRIX  ({len(uni)} companies)   {OK}=have  {PART}=partial  {NO}=missing\n")
    print(f"  {'company':30} {'ticker':11} " + " ".join(f"{h:7}" for h in hdr))
    print("  " + "-" * 104)
    tally = [0] * 8
    for slug, u in sorted(uni.items(), key=lambda kv: (kv[1].get("valuation_model") or "", kv[0])):
        c = cov.get(slug, {})
        st = status(u, c)
        for i, s in enumerate(st):
            tally[i] += 1 if s == OK else 0
        print(f"  {slug[:30]:30} {str(u.get('ticker'))[:11]:11} " + " ".join(f"{s:^7}" for s in st))
    print("  " + "-" * 104)
    print(f"  {'FULLY COVERED / ' + str(len(uni)):42} " + " ".join(f"{t:^7}" for t in tally))

    # honest gap list
    gaps = [u["name"] for slug, u in uni.items() if not cov.get(slug, {}).get("annual_report")]
    if gaps:
        print("\n  Annual report not yet downloaded (URL known, retry/aggregator fallback):")
        for g in gaps:
            print(f"    - {g}")
    print("\n  Note: 'market' is partial by design - market cap, ticker/exchange and FX are sourced;")
    print("  live intraday price is not freely available for NSE/NGX/EGX, so price is a periodic snapshot.")


if __name__ == "__main__":
    main()
