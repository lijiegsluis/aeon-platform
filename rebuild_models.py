"""Rebuild every workbook, then read back what each one publishes.

The two steps belong together. A rebuilt workbook and a stale stored valuation
is exactly the disagreement this was meant to remove, only harder to spot,
because the screen then shows a number no workbook contains.
"""
import warnings
from pathlib import Path
warnings.filterwarnings("ignore")

from aeon_nimbus import config as cfg, db as D, platform_data, published
from aeon_nimbus.excel_model import compile_model

s = D.SessionLocal()
ok = fail = 0
for c in sorted(s.query(D.Company).all(), key=lambda x: x.name):
    try:
        compile_model(platform_data.merged_extracted(c.extracted or {}),
                      c.universe or {}, cfg.model_path(c.slug))
        ok += 1
    except Exception as e:
        fail += 1
        print(f"  FAIL {c.name[:40]:40} {type(e).__name__}: {str(e)[:70]}")
print(f"rebuilt {ok}, failed {fail}")
print("reading back what each workbook publishes:", published.refresh(s, model_path=cfg.model_path))
# The live site rebuilds its database from data/extracted on every cold start,
# so a valuation held only here would never reach it.
print("writing them into the seed files:",
      published.persist_to_seed(s, data_dir=Path(__file__).resolve().parent / "data"))
