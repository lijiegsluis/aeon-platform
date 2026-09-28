"""Render the platform payload into the self-contained HTML app.

One template, two modes: the static snapshot renders with live=False (Data Studio
hidden); the running app renders with live=True (Data Studio wired to /api/studio).
plotly.js is cached across renders so the live app does not rebuild it per request.
"""

from __future__ import annotations

import json

from jinja2 import Environment, FileSystemLoader, select_autoescape

from aeon_nimbus.config import TEMPLATE_DIR

_PLOTLY: str | None = None


def _plotly_js() -> str:
    global _PLOTLY
    if _PLOTLY is None:
        from plotly.offline import get_plotlyjs
        _PLOTLY = get_plotlyjs()
    return _PLOTLY


def render_platform(payload: dict, live: bool = False, user=None) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)),
                      autoescape=select_autoescape(("html",)), trim_blocks=True, lstrip_blocks=True)
    payload = {**payload, "live": live}
    user_label = ""
    user_role = ""
    if user:
        user_label = getattr(user, "name", None) or getattr(user, "email", "") or ""
        user_role = getattr(user, "role", "") or ""

    # Load frontend extensions JavaScript
    extensions_js = ""
    from pathlib import Path
    ext_path = Path(__file__).parent / "frontend_extensions.js"
    if ext_path.exists():
        extensions_js = ext_path.read_text()

    return env.get_template("platform.html").render(
        data_json=json.dumps(payload, ensure_ascii=False),
        plotly_js=_plotly_js(),
        live=live,
        user_label=user_label,
        user_role=user_role,
        extensions_js=extensions_js)
