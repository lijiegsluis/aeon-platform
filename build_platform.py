"""Assemble the frozen showcase and write the self-contained static index.html.

The data-assembly logic now lives in aeon_nimbus.platform_data (shared with
the live app) and the rendering in aeon_nimbus.render. This stays as the CLI
that writes the no-server snapshot and the flagship report.

Run from the project root:  python3 build_platform.py
"""

from __future__ import annotations

from pathlib import Path

# Re-exported so existing imports (tests, ad-hoc scripts) keep working.
from aeon_nimbus.platform_data import assemble, deep_from_extracted  # noqa: F401
from aeon_nimbus.render import render_platform
from aeon_nimbus.real_seed import build_safaricom_context
from aeon_nimbus.report_initiation import render_initiation_html

ROOT = Path(__file__).resolve().parent


def main() -> None:
    payload = assemble()
    html = render_platform(payload, live=False)
    out = ROOT / "output" / "platform" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    scom = build_safaricom_context()["companies"][0]
    render_initiation_html(scom, out.parent / "reports" / "safaricom_plc_initiation.html", skip_gate=True)
    print(f"Wrote {out}  ({out.stat().st_size:,} bytes)  — {payload['counts']['total']} companies, "
          f"{payload['counts']['deep']} deep")


if __name__ == "__main__":
    main()
