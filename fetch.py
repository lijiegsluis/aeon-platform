"""Acquire primary sources + free macro/FX for every company in the universe.

Run from the project root:  python3 -u fetch.py
Writes filings to data/raw_docs/<slug>/, macro to data/macro/, FX to
data/market/, and a per-company coverage manifest to data/coverage.json.

Downloads run first and concurrently (they are the point); FX and macro follow.
Macro is best-effort: the World Bank API is sometimes slow, so it runs
concurrently with a short timeout and never blocks the filings. Idempotent:
existing files are skipped, so re-runs only fetch what is missing. Nothing is
fabricated; each item is recorded as obtained (bytes + sha256) or missing.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from aeon_nimbus import sources

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DOC_KINDS = {
    "annual_report": "annual_report_url",
    "interim_report": "interim_results_url",
    "investor_presentation": "investor_presentation_url",
}


def _download_task(company: dict, kind: str, url_key: str) -> tuple[str, str, dict]:
    slug = company["slug"]
    dest = DATA / "raw_docs" / slug / f"{kind}.pdf"
    if dest.exists() and dest.stat().st_size > 5120:
        return slug, kind, {"ok": True, "path": str(dest), "mb": round(dest.stat().st_size / (1 << 20), 2),
                            "via": "cached", "sha256": sources._sha256(dest)}
    return slug, kind, sources.download(company.get(url_key, ""), dest, timeout=60)


def download_all(universe: list[dict]) -> dict[str, dict[str, dict]]:
    tasks = [(c, kind, key) for c in universe for kind, key in DOC_KINDS.items() if c.get(key)]
    results: dict[str, dict[str, dict]] = {c["slug"]: {} for c in universe}
    print(f"Downloading {len(tasks)} documents across {len(universe)} companies (6-way concurrent)\n", flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(_download_task, c, kind, key) for c, kind, key in tasks]
        for fut in as_completed(futs):
            slug, kind, res = fut.result()
            results[slug][kind] = res
            tag = f"{res.get('mb')}MB via {res.get('via')}" if res.get("ok") else f"MISSING ({str(res.get('reason',''))[:44]})"
            print(f"  {slug[:30]:30} {kind:22} {tag}", flush=True)
    return results


def macro_all(countries: list[str]) -> None:
    (DATA / "macro").mkdir(parents=True, exist_ok=True)
    print("\nMacro (World Bank, concurrent, best-effort):", flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(sources.fetch_macro, c): c for c in countries}
        for fut in as_completed(futs):
            country = futs[fut]
            try:
                macro = fut.result()
            except Exception as exc:  # noqa: BLE001
                macro = {"ok": False, "country": country, "reason": str(exc)}
            iso = macro.get("iso3", country.replace(" ", "_"))
            (DATA / "macro" / f"{iso}.json").write_text(json.dumps(macro, indent=2))
            g = macro.get("series", {}).get("gdp_growth_pct", {})
            latest = sorted(g.items())[-1] if g and "error" not in g else None
            print(f"  {country:14} {'ok' if macro.get('ok') else 'pending (WB slow)'}"
                  f"{'  gdp_growth ' + latest[0] + '=' + str(round(latest[1],1)) if latest else ''}", flush=True)


def main() -> None:
    universe = json.loads((DATA / "universe.json").read_text())
    print(f"Universe: {len(universe)} companies", flush=True)

    docs = download_all(universe)

    fx = sources.fetch_fx("USD")
    (DATA / "market").mkdir(parents=True, exist_ok=True)
    (DATA / "market" / "fx_usd.json").write_text(json.dumps(fx, indent=2))
    if fx.get("ok"):
        r = fx["rates"]
        print("\nFX ok: " + ", ".join(f"{c}={r[c]}" for c in ("KES", "ZAR", "NGN", "EGP", "MAD", "XOF") if c in r), flush=True)

    macro_all(sorted({c["country"] for c in universe if c.get("country")}))

    coverage = []
    for c in universe:
        d = docs.get(c["slug"], {})
        coverage.append({
            "slug": c["slug"], "name": c["name"], "country": c.get("country"),
            "sector": c.get("sector"), "valuation_model": c.get("valuation_model"),
            "annual_report": d.get("annual_report", {}).get("ok", False),
            "annual_report_mb": d.get("annual_report", {}).get("mb"),
            "interim_report": d.get("interim_report", {}).get("ok", False),
            "investor_presentation": d.get("investor_presentation", {}).get("ok", False),
            "financials_5yr_source": bool(c.get("five_year_financials_note")),
            "notes_schedules_source": d.get("annual_report", {}).get("ok", False),
            "market_data": {"market_cap_usd_bn": c.get("market_cap_usd_bn"), "fx": fx.get("ok", False)},
            "sector_kpis": len(c.get("sector_kpis", [])),
            "peers": len(c.get("peers", [])),
            "macro_sources": len(c.get("macro_sources", [])) or bool(c.get("country")),
            "documents": d,
        })
    (DATA / "coverage.json").write_text(json.dumps(coverage, indent=2, ensure_ascii=False))

    ok_ann = sum(1 for c in coverage if c["annual_report"])
    ok_pres = sum(1 for c in coverage if c["investor_presentation"])
    print(f"\nCoverage: annual reports {ok_ann}/{len(coverage)}, presentations {ok_pres}/{len(coverage)}. Wrote data/coverage.json", flush=True)


if __name__ == "__main__":
    main()
