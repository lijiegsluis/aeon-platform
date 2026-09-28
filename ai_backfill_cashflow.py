"""AI fallback: for companies where the deterministic collector's cash flow does not
foot, read the audited cash-flow statement straight from the filing PDF (Groq) and
merge the detailed line items. Verified anchors (operating_cash_flow / capex /
dividends_paid / free_cash_flow / net_income) are kept untouched, so the valuation
never moves; a company is only written if its operating residual now falls under the
gate (i.e. the filing decomposition actually reconciles to the reported OCF).

Run:  set -a; . ./.env; set +a; python3 ai_backfill_cashflow.py [--write]
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path

from aeon_nimbus import ai_research, ingest, platform_data as pd

ROOT = Path(__file__).resolve().parent
EXT = ROOT / "data" / "extracted"
UNI = {u["slug"]: u for u in json.loads((ROOT / "data" / "universe.json").read_text())}
ADD = ["da", "other_operating_cf", "change_receivables", "change_inventory", "change_payables",
       "change_other_wc", "asset_sales", "other_investing", "investing_cash_flow",
       "net_debt_issued", "other_financing"]
GATE = 0.40
AI_LIST = ["absa_group", "bua_foods_plc", "capitec_bank_holdings_limited", "equity_group_holdings",
           "maroc_telecom", "naspers_limited", "sasol_limited", "scancom_plc_mtn_ghana",
           "seplat_energy_plc", "standard_bank_group", "zenith_bank_plc"]


def _ratio(fins: list) -> float | None:
    fins = [f for f in fins if f.get("fy")]
    if not fins:
        return None
    f = sorted(fins, key=lambda x: x["fy"])[-1]
    ocf = f.get("operating_cash_flow")
    if not isinstance(ocf, (int, float)) or ocf == 0:
        return None
    res = pd._cf_residual(f, "operating_cash_flow", pd._CF_OP_COMPS)
    return None if res is None else abs(res) / abs(ocf)


def main(write: bool = False) -> None:
    if not ai_research.available():
        print("AI provider not available (source .env for GROQ_API_KEY). Aborting.")
        return
    written = 0
    for slug in AI_LIST:
        co = json.loads((EXT / f"{slug}.json").read_text())
        url = co.get("financials_url") or co.get("annual_report_url")
        if not url:
            print(f"  {slug:42s} no filing url"); continue
        try:
            content, ctype = ingest._http_get(url)
        except Exception as e:  # noqa: BLE001
            print(f"  {slug:42s} fetch ERR {type(e).__name__}"); continue
        if not content or len(content) < 20_000 or ("pdf" not in (ctype or "").lower() and not url.lower().endswith(".pdf")):
            print(f"  {slug:42s} not a usable pdf ({ctype}, {len(content or b'')//1024}KB)"); continue
        try:
            props = ai_research.extract_statements_from_pdf(content, co, max_pages=6)
        except Exception as e:  # noqa: BLE001
            print(f"  {slug:42s} extract ERR {type(e).__name__}: {e}"); continue
        byfy: dict = {}
        for p in props:
            if p.get("item") in ADD and isinstance(p.get("value"), (int, float)):
                byfy.setdefault(p.get("fy"), {})[p["item"]] = p["value"]
        fins = [dict(f) for f in co.get("financials", [])]
        for f in fins:
            for k, v in byfy.get(f.get("fy"), {}).items():
                if not isinstance(f.get(k), (int, float)):
                    f[k] = v
        rr = _ratio(fins)
        nf = len({k for d in byfy.values() for k in d})
        ok = rr is not None and rr <= GATE and nf >= 6
        rrs = "" if rr is None else f"{rr * 100:.0f}%"
        act = ("WROTE" if write else "pass") if ok else "still-fails"
        print(f"  {slug:42s} pdf={len(content)//1024}KB ai_cf_fields={nf:2d} resid/OCF={rrs:5s} -> {act}", flush=True)
        if write and ok:
            co["financials"] = fins
            (EXT / f"{slug}.json").write_text(json.dumps(co, indent=2, ensure_ascii=False))
            written += 1
        time.sleep(5)
    print(f"\ndone. {'wrote ' + str(written) if write else 'dry run (no writes)'}")


if __name__ == "__main__":
    main(write="--write" in sys.argv[1:])
