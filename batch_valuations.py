#!/usr/bin/env python3
"""Batch Bull/Base/Bear valuation for all companies with financial data.

Run from Platform_Source_Code/:
    python3 batch_valuations.py [--all] [--limit N] [--dry-run]
"""
import argparse
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

try:
    import dotenv
    dotenv.load_dotenv(ROOT / ".env")
except ImportError:
    import os
    for line in (ROOT / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from aeon_nimbus.db import Company, SessionLocal
from aeon_nimbus.valuation_engine import compute_valuation


def _needs_valuation(co: Company) -> bool:
    ext = co.extracted or {}
    v = ext.get("valuation") or {}
    return not v.get("rating", {}).get("target_price")


def run(only_missing: bool = True, limit: int | None = None, dry_run: bool = False):
    with SessionLocal() as db:
        companies = db.query(Company).order_by(Company.id).all()
        targets = [co for co in companies if not only_missing or _needs_valuation(co)]
        if limit:
            targets = targets[:limit]

        print(f"Running valuations for {len(targets)} companies (dry_run={dry_run})\n")
        ok = errors = skipped = 0

        for i, co in enumerate(targets, 1):
            prefix = f"[{i:3d}/{len(targets)}]  {co.slug:<52}"
            ext = co.extracted or {}
            fins = ext.get("financials") or []
            if not fins:
                print(f"{prefix}  SKIP  (no financials)")
                skipped += 1
                continue

            if dry_run:
                print(f"{prefix}  DRY-RUN  ({len(fins)} years of financials)")
                ok += 1
                continue

            try:
                val = compute_valuation(ext, co.universe or {})
                if val is None:
                    print(f"{prefix}  SKIP  (valuation returned None)")
                    skipped += 1
                    continue

                new_ext = copy.deepcopy(ext)
                new_ext["valuation"] = val
                co.extracted = new_ext
                db.add(co)
                db.commit()

                r = val.get("rating", {})
                base = val.get("base", {})
                bull = (val.get("scenarios", {}).get("Bull") or {}).get("value_per_share")
                bear = (val.get("scenarios", {}).get("Bear") or {}).get("value_per_share")
                cur = r.get("current_price")
                target = r.get("target_price")
                upside = base.get("upside_pct")
                stance = r.get("stance", "—")
                cur_str = f"{cur:.2f}" if cur else "—"
                tgt_str = f"{target:.2f}" if target else "—"
                up_str = f"{upside*100:+.1f}%" if upside is not None else "—"
                bull_str = f"{bull:.2f}" if bull else "—"
                bear_str = f"{bear:.2f}" if bear else "—"
                print(f"{prefix}  {stance:<10}  cur={cur_str}  tgt={tgt_str}  {up_str}  bull={bull_str}  bear={bear_str}")
                ok += 1

            except Exception as e:
                import traceback
                print(f"{prefix}  ERROR  {e}")
                traceback.print_exc()
                errors += 1

        print(f"\nDone. ok={ok}  errors={errors}  skipped={skipped}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--all", action="store_true", help="Re-value even companies already valued")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(only_missing=not args.all, limit=args.limit, dry_run=args.dry_run)
