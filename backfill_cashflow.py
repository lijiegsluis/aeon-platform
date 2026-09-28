"""One-time backfill of detailed cash-flow line items into the seed datasets.

For each company that has no hand-authored cash-flow statement, re-run the
deterministic collector (stockanalysis.com), take its signed detail line items,
and merge the NON-ANCHOR fields into data/extracted/<slug>.json. The verified
operating_cash_flow / capex / dividends_paid / free_cash_flow / net_income are kept
as anchors (untouched) so the valuation never moves; synth_statements then builds a
footing bottom-up cash flow using an "Other (residual)" plug per section.

A residual gate decides which companies keep the collector detail (clean decomposition)
vs. are flagged for AI extraction from the filing (residual too large, e.g. MTN's
hyperinflation items that stockanalysis lumps together).

Run:  python3 backfill_cashflow.py            # dry run, report only
      python3 backfill_cashflow.py --write     # write KEEP companies (backs up first)
"""
from __future__ import annotations
import json
import shutil
import sys
from pathlib import Path

from aeon_nimbus import ingest, platform_data as pd

ROOT = Path(__file__).resolve().parent
EXT = ROOT / "data" / "extracted"
BACKUP = ROOT / "data" / "_backup_extracted_precf"
UNI = {u["slug"]: u for u in json.loads((ROOT / "data" / "universe.json").read_text())}

# fields the collector may add; the anchors (OCF/capex/dividends/FCF/net_income) are never overwritten
ADD = ["da", "other_operating_cf", "change_receivables", "change_inventory", "change_payables",
       "change_other_wc", "asset_sales", "other_investing", "investing_cash_flow",
       "net_debt_issued", "other_financing"]
GATE = 0.40   # flag for AI when latest-year |operating residual| / |OCF| exceeds this
MIN_FIELDS = 6


def _latest_ratio(fins: list) -> float | None:
    fins = [f for f in fins if f.get("fy")]
    if not fins:
        return None
    f = sorted(fins, key=lambda x: x["fy"])[-1]
    ocf = f.get("operating_cash_flow")
    if not isinstance(ocf, (int, float)) or ocf == 0:
        return None
    res = pd._cf_residual(f, "operating_cash_flow", pd._CF_OP_COMPS)
    return None if res is None else abs(res) / abs(ocf)


def process(slug: str) -> dict:
    co = json.loads((EXT / f"{slug}.json").read_text())
    if (co.get("statements") or {}).get("cash_flow"):
        return {"slug": slug, "gate": "skip(has block)", "fields": 0, "ratio": None, "fins": None}
    u = UNI.get(slug, {})
    co2 = {**co, "ticker": u.get("ticker"), "exchange": u.get("exchange")}
    try:
        props = ingest.collect_web(co2)
    except Exception as e:  # noqa: BLE001
        return {"slug": slug, "gate": f"ERR {type(e).__name__}", "fields": 0, "ratio": None, "fins": None}
    byfy: dict = {}
    for p in props:
        if p.get("item") in ADD and isinstance(p.get("value"), (int, float)):
            byfy.setdefault(p.get("fy"), {})[p["item"]] = p["value"]
    fins = [dict(f) for f in co.get("financials", [])]
    for f in fins:
        for k, v in byfy.get(f.get("fy"), {}).items():
            if not isinstance(f.get(k), (int, float)):
                f[k] = v
    ratio = _latest_ratio(fins)
    nfields = len({k for d in byfy.values() for k in d})
    gate = "KEEP" if (ratio is not None and ratio <= GATE and nfields >= MIN_FIELDS) else "AI"
    return {"slug": slug, "gate": gate, "fields": nfields, "ratio": ratio, "fins": fins, "co": co}


def main(write: bool = False) -> None:
    if write and not BACKUP.exists():
        shutil.copytree(EXT, BACKUP)
        print(f"backed up seed data -> {BACKUP}")
    rows = [process(fp.stem) for fp in sorted(EXT.glob("*.json"))]
    for r in rows:
        rr = "" if r["ratio"] is None else f"{r['ratio'] * 100:.0f}%"
        print(f"  {r['slug']:42s} {r['gate']:16s} fields={r['fields']:2d} resid/OCF={rr}")
        if write and r["gate"] == "KEEP":
            r["co"]["financials"] = r["fins"]
            (EXT / f"{r['slug']}.json").write_text(json.dumps(r["co"], indent=2, ensure_ascii=False))
    keep = [r for r in rows if r["gate"] == "KEEP"]
    ai = [r for r in rows if r["gate"] == "AI"]
    skip = [r for r in rows if r["gate"].startswith("skip")]
    err = [r for r in rows if r["gate"].startswith("ERR")]
    print(f"\nKEEP(collector): {len(keep)} | AI-fallback: {len(ai)} | skip: {len(skip)} | err: {len(err)}")
    print("AI-fallback list:", [r["slug"] for r in ai])
    if err:
        print("errors:", [(r["slug"], r["gate"]) for r in err])
    if write:
        print(f"\nwrote {len(keep)} enriched datasets.")


if __name__ == "__main__":
    main(write="--write" in sys.argv[1:])
