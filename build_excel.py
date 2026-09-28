"""Generate a linked Excel model for a company from its approved dataset.

Run:  python3 build_excel.py [slug]   (default: bamburi_cement_plc pilot)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from aeon_nimbus.excel_model import compile_model

ROOT = Path(__file__).resolve().parent


def main(slug: str = "bamburi_cement_plc") -> None:
    company = json.loads((ROOT / "data" / "extracted" / f"{slug}.json").read_text())
    universe = {u["slug"]: u for u in json.loads((ROOT / "data" / "universe.json").read_text())}[slug]
    out = compile_model(company, universe, ROOT / "output" / "models" / f"{slug}_model.xlsx")
    print(f"Model {out['model_id']}  ->  {out['path']}")
    print(f"Data quality: {out['data_quality']['score']}/100 (band {out['data_quality']['band']}) "
          f"| export {'ALLOWED' if out['export_allowed'] else 'BLOCKED'}")
    passed = sum(1 for c in out["controls"] if c["status"] == "pass")
    print(f"Controls: {passed}/{len(out['controls'])} pass")
    print(f"Sheets ({len(out['sheets'])}): {', '.join(out['sheets'])}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "bamburi_cement_plc")
