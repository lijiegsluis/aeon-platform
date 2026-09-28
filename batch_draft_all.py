#!/usr/bin/env python3
"""Batch AI qualitative draft for all companies missing qualitative content.

Run from Platform_Source_Code/:
    python3 batch_draft_all.py [--only-missing] [--limit N]

Directly calls ai_research.draft_qualitative() for each company and
persists the result into the DB's extracted.qualitative field.
"""
import argparse
import copy
import json
import sys
import time
from pathlib import Path

# Ensure we run from the right dir
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

try:
    import dotenv
    dotenv.load_dotenv(ROOT / ".env")
except ImportError:
    # fallback: manually parse .env
    env_file = ROOT / ".env"
    if env_file.exists():
        import os
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from aeon_nimbus import ai_research
from aeon_nimbus.db import Company, SessionLocal


def _needs_draft(co: Company) -> bool:
    ext = co.extracted or {}
    q = ext.get("qualitative") or {}
    return not any(q.get(k) for k in ("market_overview", "revenue_drivers", "cost_pressures", "outlook"))


def run(only_missing: bool = True, limit: int | None = None, dry_run: bool = False) -> None:
    if not ai_research.available():
        print("ERROR: No AI API key configured. Set GEMINI_API_KEY / GROQ_API_KEY / ANTHROPIC_API_KEY in .env")
        sys.exit(1)

    with SessionLocal() as db:
        companies = db.query(Company).order_by(Company.id).all()
        targets = [co for co in companies if not only_missing or _needs_draft(co)]
        if limit:
            targets = targets[:limit]

        print(f"Drafting qualitative for {len(targets)} companies (dry_run={dry_run}) …\n")

        ok = errors = skipped = 0
        for i, co in enumerate(targets, 1):
            prefix = f"[{i:3d}/{len(targets)}]  {co.slug:<50}"
            facts = ai_research.facts_from(co.extracted or {}, co.universe or {})
            if not facts.get("years") and not facts.get("segments"):
                print(f"{prefix}  SKIP  (no financial data or segment info)")
                skipped += 1
                continue
            if dry_run:
                print(f"{prefix}  DRY-RUN  ({len(facts.get('fin_facts', []))} fin facts, "
                      f"{len(facts.get('segments', []))} segs, {len(facts.get('risks', []))} risks)")
                ok += 1
                continue
            try:
                draft = ai_research.draft_qualitative(facts)
                ext = copy.deepcopy(co.extracted or {})
                ext["qualitative"] = draft
                co.extracted = ext
                db.add(co)
                db.commit()
                moat = draft.get("moat_assessment", {})
                overall = moat.get("overall", {})
                moat_score = overall.get("score", "—")
                alpha = (draft.get("alpha_thesis") or "")[:80]
                print(f"{prefix}  OK  moat={moat_score}  alpha: {alpha}")
                ok += 1
            except Exception as e:
                print(f"{prefix}  ERROR  {e}")
                errors += 1
            # Brief pause to avoid rate-limiting
            time.sleep(0.2)

        print(f"\nDone. ok={ok}  errors={errors}  skipped={skipped}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Batch AI qualitative draft for all companies")
    p.add_argument("--all", action="store_true", help="Re-draft even companies that already have qualitative content")
    p.add_argument("--limit", type=int, default=None, help="Max number of companies to process")
    p.add_argument("--dry-run", action="store_true", help="Show what would be processed without calling the AI")
    args = p.parse_args()
    run(only_missing=not args.all, limit=args.limit, dry_run=args.dry_run)
