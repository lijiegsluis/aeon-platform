"""Initiation-of-coverage report generator.

Takes a source-linked company dict (the seed contract) and renders a
self-contained HTML initiation report: rating box, thesis, financial analysis,
valuation (sector-appropriate), risks, catalysts and a page-referenced source
appendix. Every figure traces to the seed's source references — the generator
selects and formats, it does not invent.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from aeon_nimbus.config import TEMPLATE_DIR

_SYMBOL = {"KES": "KSh", "ZAR": "R", "NGN": "₦", "EGP": "E£", "USD": "$", "MAD": "MAD ", "XOF": "CFA "}


def money(value: float | None, currency: str = "KES", millions: bool = True) -> str:
    """Format a reported figure. Seeds store income-statement lines in millions."""
    if value is None:
        return "—"
    pref = _SYMBOL.get(currency, currency + " ")
    v = value * 1_000_000 if millions else value
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1e12:
        return f"{sign}{pref}{v / 1e12:.2f}tn"
    if v >= 1e9:
        return f"{sign}{pref}{v / 1e9:.1f}bn"
    if v >= 1e6:
        return f"{sign}{pref}{v / 1e6:.0f}m"
    return f"{sign}{pref}{v:,.0f}"


def price(value: float | None, currency: str = "KES") -> str:
    """A per-share price (plain value, not millions)."""
    if value is None:
        return "—"
    return f"{_SYMBOL.get(currency, currency + ' ')}{value:,.2f}"


def pct(value: float | None, dp: int = 1) -> str:
    return "—" if value is None else f"{value * 100:.{dp}f}%"


def mult(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}x"


def _rating(upside: float | None) -> str:
    """A transparent stance from DCF upside to the current price."""
    if upside is None:
        return "Under review"
    if upside >= 0.20:
        return "Buy"
    if upside >= -0.10:
        return "Hold"
    return "Reduce"


class ReportGateError(ValueError):
    """Raised when a report cannot be generated because QC or consistency checks fail."""


def _validate_report_gate(company: dict[str, Any]) -> None:
    """Block report generation if the company has unresolved critical QC failures
    or placeholder text.  Raises ReportGateError with a human-readable message."""
    from aeon_nimbus import controls as _ctrl
    # Run the full control suite
    controls = _ctrl.run_controls(company)
    crit_fails = [c for c in controls if c["severity"] == "critical" and c["status"] == "fail"]
    if crit_fails:
        details = "; ".join(f"{c['control_id']} {c['description']} [{c['actual']}]" for c in crit_fails)
        raise ReportGateError(
            f"Report generation blocked: {len(crit_fails)} critical QC failure(s). "
            f"Resolve before generating: {details}"
        )
    # Check for placeholder text in investment_view narrative fields
    import re
    _ph = re.compile(r"\b(todo|tbd|xxx|insert|placeholder|coming soon|n/?a|tba|fill in|fixme)\b", re.I)
    iv = company.get("investment_view") or {}
    narrative_fields = {
        "key_debate": iv.get("key_debate") or "",
        "variant_view": iv.get("variant_view") or "",
        "data_caveat": iv.get("data_caveat") or "",
    }
    ph_hits = [k for k, v in narrative_fields.items() if _ph.search(str(v))]
    if ph_hits:
        raise ReportGateError(
            f"Report generation blocked: unfinished placeholder text in {', '.join(ph_hits)}. "
            "Complete these fields before generating."
        )


def _validate_target_consistency(iv: dict, val: dict) -> None:
    """Warn (log) when the report's target price doesn't reconcile with any
    scenario value.  Does NOT block — the seed may have a hand-authored target
    that intentionally differs from the mechanical DCF."""
    target = iv.get("target_price")
    scenarios = val.get("scenarios") or {}
    base_scen = (scenarios.get("sets") or {}).get("Base") or {}
    base_val = base_scen.get("value_per_share")
    if target is None or base_val is None:
        return
    diff = abs(target - base_val) / abs(base_val) if base_val else None
    if diff is not None and diff > 0.05:
        import warnings
        warnings.warn(
            f"Report target price {target} differs from Base scenario DCF {base_val:.2f} "
            f"by {diff * 100:.1f}%. Verify the target is intentional or update the scenario.",
            stacklevel=4,
        )


def build_initiation(company: dict[str, Any], rec: dict[str, Any] | None = None,
                     universe_entry: dict[str, Any] | None = None,
                     db=None) -> dict[str, Any]:
    """Assemble the report view-model from a company seed.

    If *db* is provided and a Company DB record exists, the live-computed DCF
    rating and target price override the seed's investment_view values so the
    report always reflects the latest Studio assumptions.
    """
    # Attempt live-data override when a db session is available
    if db is not None and rec is not None and universe_entry is not None:
        try:
            from aeon_nimbus import platform_data as _pdata
            live = _pdata.deep_from_extracted(rec, universe_entry)
            if live and live.get("rating"):
                r = live["rating"]
                iv_override = dict(company.get("investment_view") or {})
                if r.get("target") is not None:
                    iv_override["target_price"] = r["target"]
                if r.get("stance"):
                    iv_override["stance"] = r["stance"]
                if r.get("upside") is not None:
                    iv_override["upside_downside"] = r["upside"]
                if r.get("price") is not None:
                    iv_override["current_price"] = r["price"]
                company = {**company, "investment_view": iv_override}
        except Exception:
            pass  # fall back to seed values

    iv = company["investment_view"]
    val = company["valuation"]
    memo = company["memo"]
    market = company["market_data"]
    currency = company["currency"]

    # Financial summary table: line items are {value, source} dicts, ratios are
    # plain floats, so format here and hand the template ready strings.
    metrics = [
        ("Revenue", "revenue", "money"), ("EBITDA", "ebitda", "money"),
        ("EBIT", "ebit", "money"), ("Net income", "net_income", "money"),
        ("Free cash flow", "free_cash_flow", "money"), ("Net debt", "net_debt", "money"),
        ("EBITDA margin", "ebitda_margin", "pct"), ("Net debt / EBITDA", "net_debt_ebitda", "mult"),
        ("ROE", "roe", "pct"), ("ROIC", "roic", "pct"),
    ]
    fmt = {"money": lambda v: money(v, currency), "pct": pct, "mult": mult}
    fin_rows = []
    for label, key, kind in metrics:
        cells = []
        for yr in company["financial_history"]:
            v = yr.get(key)
            if isinstance(v, dict):
                v = v.get("value")
            cells.append(fmt[kind](v))
        fin_rows.append({"label": label, "cells": cells})
    financials_table = {"years": [y["period"] for y in company["financial_history"]], "rows": fin_rows}

    thesis = [
        memo["executive_summary"],
        memo["business_model"],
        memo["country_context"],
    ]
    sections = [
        ("Business model", memo["business_model"]),
        ("Sector context", memo["sector_context"]),
        ("Country and macro", memo["country_context"]),
        ("Historical financials", memo["historical_financials"]),
        ("Valuation", memo["valuation"]),
        ("Liquidity", memo["liquidity"]),
        ("Capital structure", memo["capital_structure"]),
    ]
    return {
        "meta": {
            "name": company["name"], "ticker": company["ticker"], "exchange": company["exchange"],
            "country": company["country"], "sector": company["sector"], "currency": currency,
            "as_of": date.today().isoformat(), "latest_filing": company["latest_filing"],
            "website": company.get("website"),
        },
        "rating": {
            "stance": _rating(iv.get("upside_downside")),
            "target_price": iv["target_price"], "current_price": iv["current_price"],
            "upside": iv["upside_downside"], "conviction": iv["conviction"],
            # Aeon Nimbus publishes stop / position size / horizon alongside entry and
            # target — timestamped upfront, per the publication's core promise. These
            # are optional in the seed contract: absent -> "—" in the template, never
            # invented here.
            "stop_loss": iv.get("stop_loss"),
            "position_size_pct": iv.get("position_size_pct"),
            "horizon_months": iv.get("horizon_months"),
            "market_cap": company["market_cap"], "ev": company["enterprise_value"],
            "ev_ebitda": val["current_multiples"]["ev_ebitda"], "pe": val["current_multiples"]["pe"],
            "dividend_yield": val["current_multiples"]["dividend_yield"],
        },
        "thesis": thesis,
        "key_debate": iv["key_debate"],
        "variant_view": iv["variant_view"],
        "sections": sections,
        "financials_table": financials_table,
        "multiples": val["current_multiples"],
        "peers": val["peers"],
        "peer_median_ev_ebitda": val.get("peer_median_ev_ebitda"),
        "scenarios": val["scenarios"],
        "wacc_build": val.get("wacc_build", {}),
        "valuation_bridge": val.get("valuation_bridge", []),
        "risks": company["risks"],
        "catalysts": iv["catalysts"],
        "data_caveat": iv["data_caveat"],
        "documents": company["documents"],
    }


def render_initiation_html(company: dict[str, Any], output_path: str | Path,
                            skip_gate: bool = False,
                            rec: dict[str, Any] | None = None,
                            universe_entry: dict[str, Any] | None = None,
                            db=None) -> Path:
    if not skip_gate:
        _validate_report_gate(company)
    _validate_target_consistency(
        company.get("investment_view") or {},
        company.get("valuation") or {},
    )
    report = build_initiation(company, rec=rec, universe_entry=universe_entry, db=db)
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(("html", "xml")),
        trim_blocks=True, lstrip_blocks=True,
    )
    env.filters["money"] = money
    env.filters["price"] = price
    env.filters["pct"] = pct
    env.filters["mult"] = mult
    html = env.get_template("report.html").render(r=report)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
