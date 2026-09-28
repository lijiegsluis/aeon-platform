"""Ground-truth audit: compare stored figures against each company's OWN filing PDF.

Fully deterministic — no AI anywhere in the loop. Uses the platform's page-aware
statement parser (ingest._parse_pdf: locate_statements + label matching + column
year headers), so every extracted figure carries the filing page it was read from
and nothing can be invented. Companies whose filing cannot be fetched or parsed
are reported UNVERIFIED, never guessed.

Anchors: revenue, pbt, net_income, total_assets, total_equity,
operating_cash_flow, capex, dividends_paid. Tolerance 1% (rounding).

Streams one JSON line per company to /tmp/filing_audit.jsonl (resumable).

Run:  python3 verify_vs_filings.py
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

from aeon_nimbus import ingest

ROOT = Path(__file__).resolve().parent
OUT = Path("/tmp/filing_audit.jsonl")
ANCHORS = ["revenue", "pbt", "net_income", "total_assets", "total_equity",
           "operating_cash_flow", "capex", "dividends_paid"]
TOL = 0.01


def check(slug: str) -> dict:
    d = json.loads((ROOT / "data" / "extracted" / f"{slug}.json").read_text())
    out = {"slug": slug, "status": None, "matches": 0, "mismatches": [], "checked": 0}
    urls = [u for u in (d.get("financials_url"), d.get("annual_report_url")) if u]
    if not urls:
        out["status"] = "UNVERIFIED — no filing url"
        return out
    props = []
    for url in urls:
        try:
            content, ctype = ingest._http_get(url)
        except Exception as e:  # noqa: BLE001
            out["status"] = f"UNVERIFIED — fetch failed ({type(e).__name__})"
            continue
        if not content or len(content) < 20_000:
            out["status"] = f"UNVERIFIED — not a usable pdf ({ctype}, {len(content or b'') // 1024}KB)"
            continue
        try:
            props = ingest._parse_pdf(content, url.rsplit("/", 1)[-1], d, source_url=url)
        except Exception as e:  # noqa: BLE001
            out["status"] = f"UNVERIFIED — parse failed ({type(e).__name__})"
            props = []
        if props:
            out["url"] = url
            break
    if not props:
        out["status"] = out["status"] or "UNVERIFIED — statements not located in the pdf"
        return out
    stored = {f["fy"]: f for f in d.get("financials", []) if f.get("fy")}
    cands: dict = {}
    for p in props:
        item, fy = p.get("item"), p.get("fy")
        if item not in ANCHORS or fy not in stored:
            continue
        fv = p.get("value")
        if isinstance(fv, (int, float)):
            cands.setdefault((item, fy), []).append((fv, str(p.get("source", ""))[:100]))
    for (item, fy), vals in cands.items():
        sv = stored[fy].get(item)
        if not isinstance(sv, (int, float)):
            continue
        out["checked"] += 1
        b = abs(sv)
        hit = next(((fv, s) for fv, s in vals if abs(abs(fv) - b) <= max(1.5, TOL * max(abs(fv), b))), None)
        if hit:
            out["matches"] += 1
        else:
            top = sorted(vals, key=lambda x: -abs(x[0]))[:3]
            out["mismatches"].append({"item": item, "fy": fy, "stored": sv,
                                      "filing_candidates": [round(v, 1) for v, _ in top],
                                      "src": top[0][1]})
    out["status"] = "OK" if out["checked"] else "UNVERIFIED — filing years/items did not overlap anchors"
    return out


def main() -> None:
    done = set()
    if OUT.exists():
        for line in OUT.read_text().splitlines():
            try:
                done.add(json.loads(line)["slug"])
            except Exception:  # noqa: BLE001
                pass
    slugs = sorted(p.stem for p in (ROOT / "data" / "extracted").glob("*.json"))
    with OUT.open("a") as fh:
        for i, slug in enumerate(slugs):
            if slug in done:
                continue
            r = check(slug)
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            print(f"[{i+1:2d}/{len(slugs)}] {slug:44s} {r['status'][:36]:38s} "
                  f"checked={r['checked']:2d} match={r['matches']:2d} mismatch={len(r['mismatches'])}",
                  file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
