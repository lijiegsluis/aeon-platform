"""Generate initiation-of-coverage reports.

Run from the project root:  python3 build_reports.py
Currently builds the flagship (Safaricom) from its source-linked seed; extends
to the full universe as each company's seed lands (Plan 4).
"""

from __future__ import annotations

from pathlib import Path

from aeon_nimbus.real_seed import build_safaricom_context
from aeon_nimbus.report_initiation import render_initiation_html

ROOT = Path(__file__).resolve().parent


def main() -> None:
    company = build_safaricom_context()["companies"][0]
    out = render_initiation_html(company, ROOT / "output" / "reports" / "safaricom_plc_initiation.html")
    print(f"Wrote {out}  ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
