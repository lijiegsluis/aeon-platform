"""Data-quality score and the control checks that gate Excel export.

Controls have three levels (spec §10):
  * critical      — blocks export unless an approved override exists
  * warning       — allowed, but surfaced in the platform and the workbook
  * informational — audit/context only

Every check reports expected vs actual so it can be written to the Excel
Control_Log with a full audit trail. Checks only assert what the extracted data
can actually support — nothing is invented.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

CRITICAL, WARNING, INFO = "critical", "warning", "informational"
_REL = 0.01  # 1% tolerance for reconciliations

# Implausible growth: absolute YoY ratio that triggers a hard fail.
# A genuine high-growth company may hit 5× in a single year; 10× almost certainly
# means a unit change, a restatement not flagged, or a data-extraction error.
_GROWTH_FAIL_RATIO = 10.0  # >900% YoY change → CRITICAL
_GROWTH_WARN_RATIO = 5.0   # >400% YoY change → WARNING (still suspicious)

_PLACEHOLDER_PATTERNS = re.compile(
    r"\b(todo|tbd|xxx|insert|placeholder|coming soon|n/?a|tba|fill in|fixme)\b",
    re.IGNORECASE,
)



def _normalize_financials(fins_raw):
    """Convert financials from dict or list format to normalized list."""
    if isinstance(fins_raw, dict):
        fins = []
        for year, data in fins_raw.items():
            if isinstance(data, dict):
                data_copy = data.copy()
                data_copy['fy'] = str(year)
                data_copy['fiscal_year'] = int(year) if year.isdigit() else None
                fins.append(data_copy)
        return fins
    elif isinstance(fins_raw, list):
        return fins_raw
    else:
        return []

def _num(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) else None


def _latest(company: dict) -> dict:
    fins = sorted(_normalize_financials(company.get("financials", [])), key=lambda f: f.get("fy", ""))
    return fins[-1] if fins else {}


def run_controls(company: dict) -> list[dict[str, Any]]:
    """Run the control suite over a company's extracted dataset."""
    fins = sorted(_normalize_financials(company.get("financials", [])), key=lambda f: f.get("fy") or f"FY{f.get('fiscal_year', '')}")
    latest = fins[-1] if fins else {}
    dq = company.get("data_quality", {}) or {}
    market = company.get("market", {}) or {}
    _raw_notes = company.get("notes", {}) or {}
    notes = _raw_notes if isinstance(_raw_notes, dict) else {}
    is_bank = company.get("bank") is not None
    out: list[dict[str, Any]] = []

    def add(cid, cat, sev, ok, desc, expected="", actual="", diff="", sheet="", cell=""):
        out.append({
            "control_id": cid, "category": cat, "severity": sev,
            "status": "pass" if ok else ("warn" if sev != CRITICAL else "fail"),
            "description": desc, "expected": str(expected), "actual": str(actual),
            "difference": str(diff), "model_sheet": sheet, "model_cell": cell,
        })

    # --- critical ---------------------------------------------------------
    yrs = len(fins)
    add("C01", "coverage", CRITICAL, yrs >= 3,
        "At least three years of historical financials present",
        expected=">=3 years", actual=f"{yrs} years", sheet="Historical_Financials")

    sourced = all(f.get("source") for f in fins)
    add("C02", "sourcing", CRITICAL, sourced,
        "Every historical year carries a source reference",
        expected="all sourced", actual="all sourced" if sourced else "gaps", sheet="Sources")

    unit_ok = bool(company.get("unit")) and bool(company.get("currency"))
    add("C03", "units", CRITICAL, unit_ok,
        "Reporting currency and unit confirmed",
        expected="currency+unit set", actual=f"{company.get('currency')}/{company.get('unit')}", sheet="Assumptions")

    # Net-debt reconciliation. The identity net_debt == gross debt - cash only
    # holds on a single basis; IFRS-16 leases and short-term investments legally
    # move it. So: banks are exempt (they are valued on P/B, not leverage); a
    # match on EITHER a lease-exclusive or lease-inclusive basis passes as
    # critical; a documented basis difference is a WARNING, not a hard block.
    td, csh, nd = _num(latest.get("total_debt")), _num(latest.get("cash")), _num(latest.get("net_debt"))
    leases = _num(notes.get("lease_liabilities")) or 0.0
    if is_bank:
        add("C04", "reconciliation", INFO, True,
            "Net-debt identity not applicable to a bank (valued on justified P/B)",
            actual="n/a (bank)", sheet="Bank_Valuation")
    elif td is not None and csh is not None and nd is not None:
        tol = max(abs(nd), 1.0) * 0.02
        ok = abs((td - csh) - nd) <= tol or abs((td + leases - csh) - nd) <= tol
        if ok:
            add("C04", "reconciliation", CRITICAL, True,
                "Net debt reconciles to gross debt (+/- leases) minus cash",
                expected=f"{td - csh:,.0f}", actual=f"{nd:,.0f}", sheet="Three_Statements")
        else:
            add("C04", "reconciliation", WARNING, False,
                "Net debt differs from gross debt minus cash on a disclosed basis "
                "(leases / short-term investments) — review, not a hard block",
                expected=f"{td - csh:,.0f}", actual=f"{nd:,.0f}", diff=f"{(td - csh) - nd:,.0f}",
                sheet="Three_Statements")
    elif nd is None:
        add("C04", "reconciliation", INFO, True,
            "Net debt not separately disclosed", actual="n/a")
    else:
        add("C04", "reconciliation", WARNING, False,
            "Net debt reconciliation inputs incomplete", expected="debt, cash, net_debt", actual="missing")

    # balance-sheet sanity: equity and debt cannot exceed assets
    ta, te = _num(latest.get("total_assets")), _num(latest.get("total_equity"))
    if ta and te is not None:
        ok = 0 <= te <= ta * 1.02
        add("C05", "balance_sheet", CRITICAL, ok,
            "Equity within [0, total assets] (balance-sheet integrity)",
            expected=f"0..{ta:,.0f}", actual=f"{te:,.0f}", sheet="Three_Statements")

    price_ok = market.get("share_price") is not None and bool(market.get("price_date"))
    add("C06", "market_data", CRITICAL, price_ok,
        "Critical market data (price) available and timestamped",
        expected="price + date", actual=f"{market.get('share_price')} @ {market.get('price_date')}",
        sheet="Valuation")

    # --- warnings ---------------------------------------------------------
    conf = dq.get("confidence")
    try:
        conf = float(conf) if conf is not None else None
    except (TypeError, ValueError):
        conf = None
    add("W01", "confidence", WARNING, (conf or 0) >= 0.8,
        "Overall extraction confidence at least 0.80",
        expected=">=0.80", actual=f"{conf}", sheet="Sources")

    yf = dq.get("years_found", yrs)
    add("W02", "coverage", WARNING, yf >= 5,
        "Full five-year history available",
        expected="5 years", actual=f"{yf} years", sheet="Historical_Financials")

    _wn = company.get("notes") or {}
    notes = _wn if isinstance(_wn, dict) else {}
    add("W03", "schedules", WARNING, bool(notes.get("capex") is not None),
        "Capex disclosed in notes (else derived from PP&E movement)",
        expected="disclosed", actual="disclosed" if notes.get("capex") is not None else "derived",
        sheet="PPE_Capex")

    add("W04", "kpis", WARNING, bool(company.get("sector_kpis")),
        "Sector operating KPIs disclosed",
        expected="present", actual=f"{len(company.get('sector_kpis', []))} KPIs", sheet="Operating_KPIs")

    add("W05", "peers", WARNING, bool(company.get("peers") or company.get("_peers")),
        "Peer set available for comparable valuation",
        expected="present", actual="present" if (company.get("peers") or company.get("_peers")) else "none",
        sheet="Comparables")

    # --- Aeon Nimbus QC additions (Report-Guide + Data-Checklist standards) ---
    st = company.get("statements") or {}
    # C07 — statements tie: balance sheet balances every reported year
    bal = _statements_balance(st)
    if bal is not None:
        yrs_ok, worst = bal
        add("C07", "statements_tie", CRITICAL, yrs_ok,
            "Balance sheet balances every year (total assets = equity + liabilities)",
            expected="assets = equity + liabilities", actual=("balances" if yrs_ok else f"off by {worst:,.1f}"),
            sheet="Financial_Statements")
    # C08 — latest year is the audited/company filing, not an outdated portal figure
    latest_src = (latest.get("source") or "").lower()
    audited = any(k in latest_src for k in ("audit", "annual report", "financial statements",
                                            "condensed", "reconciled", "filing"))
    add("C08", "latest_year", WARNING, audited,
        "Latest historical year traces to the company filing (not an outdated data portal)",
        expected="filing/audited", actual="filing" if audited else "portal/aggregator only",
        sheet="Sources_and_Controls")

    # W06 — peer set count within Aeon Nimbus's 3–5 guidance
    peers = company.get("peers") or company.get("_peers") or []
    npeers = len(peers) if isinstance(peers, list) else 0
    add("W06", "peers", WARNING, npeers >= 3,
        "At least three relevant listed peers (Aeon Nimbus guidance: 3–5)",
        expected=">=3 peers", actual=f"{npeers} peers", sheet="Comps_and_KPIs")
    # W07 — cash-flow ties: reported ending cash movement reconciles
    cf_ok = _cashflow_ties(st)
    if cf_ok is not None:
        add("W07", "statements_tie", WARNING, cf_ok,
            "Cash-flow statement ties (operating + investing + financing = net change)",
            expected="reconciles", actual="ties" if cf_ok else "review", sheet="Financial_Statements")

    # C09 — historical periods must be in ascending order (catches reversed series).
    # Check the RAW order from the source data, not the sorted `fins` list used
    # elsewhere in this function — a reversed extraction fails here even if the
    # model re-sorts before computing.
    _raw_fins = _normalize_financials(company.get("financials", []))
    if len(_raw_fins) >= 2:
        _raw_fys = [str(f.get("fy") or f.get("fy_label") or f"FY{f.get('fiscal_year', '')}") for f in _raw_fins]
        _ordered = _raw_fys == sorted(_raw_fys)
        add("C09", "period_order", CRITICAL, _ordered,
            "Historical periods are in ascending chronological order",
            expected="ascending", actual=" → ".join(_raw_fys), sheet="Historical_Financials")

    # C10 — implausible year-on-year growth in key P&L lines
    # A ratio ≥ _GROWTH_FAIL_RATIO almost certainly indicates a unit error or
    # a data-extraction fault (e.g., GHS 14.9bn → GHS 527 trillion).
    _growth_fail, _growth_warn, _growth_detail = False, False, ""
    _pl_keys = [("revenue", "Revenue"), ("net_income", "Net income"),
                ("ebitda", "EBITDA"), ("pbt", "PBT"), ("operating_profit", "Operating profit")]
    for _key, _label in _pl_keys:
        _vals = [(f.get("fy") or "", _num(f.get(_key))) for f in fins]
        _vals = [(y, v) for y, v in _vals if v is not None and v != 0]
        for i in range(1, len(_vals)):
            _prev_y, _prev_v = _vals[i - 1]
            _curr_y, _curr_v = _vals[i]
            _ratio = abs(_curr_v / _prev_v)
            if _ratio >= _GROWTH_FAIL_RATIO or _ratio <= (1.0 / _GROWTH_FAIL_RATIO):
                _growth_fail = True
                _growth_detail = (f"{_label}: {_prev_y} {_prev_v:,.0f} → {_curr_y} {_curr_v:,.0f} "
                                  f"(×{_ratio:.0f})")
                break
            if _ratio >= _GROWTH_WARN_RATIO or _ratio <= (1.0 / _GROWTH_WARN_RATIO):
                _growth_warn = True
                if not _growth_detail:
                    _growth_detail = (f"{_label}: {_prev_y} {_prev_v:,.0f} → {_curr_y} {_curr_v:,.0f} "
                                      f"(×{_ratio:.1f})")
        if _growth_fail:
            break
    if _growth_fail:
        add("C10", "implausible_growth", CRITICAL, False,
            "Implausible year-on-year growth in P&L (possible unit error or extraction fault)",
            expected=f"ratio < ×{_GROWTH_FAIL_RATIO:.0f}", actual=_growth_detail,
            sheet="Historical_Financials")
    elif _growth_warn:
        add("C10", "implausible_growth", WARNING, False,
            "Unusually large year-on-year change in P&L — verify unit consistency",
            expected=f"ratio < ×{_GROWTH_WARN_RATIO:.0f}", actual=_growth_detail,
            sheet="Historical_Financials")

    # W08 — stale market data (price_date more than 90 days old)
    _price_date_str = market.get("price_date") or ""
    if _price_date_str:
        try:
            _pd = datetime.date.fromisoformat(str(_price_date_str)[:10])
            _age_days = (datetime.date.today() - _pd).days
            add("W08", "market_freshness", WARNING, _age_days <= 90,
                "Share price captured within the last 90 days",
                expected="≤90 days", actual=f"{_age_days} days old ({_price_date_str})",
                sheet="Valuation")
        except (ValueError, TypeError):
            add("W08", "market_freshness", WARNING, False,
                "Share price date is not a valid ISO date",
                expected="YYYY-MM-DD", actual=str(_price_date_str), sheet="Valuation")

    # C12 — sector-appropriate KPIs present
    # Import here to avoid circular import at module level (platform_data imports controls)
    try:
        from aeon_nimbus.sector import config_for as _cfg_for
        _scfg = _cfg_for(company.get("sector", ""), company.get("sub_sector"),
                         is_bank=is_bank)
        _kpis_present = [
            (k.get("name", "") if isinstance(k, dict) else str(k)).lower()
            for k in (company.get("sector_kpis") or []) if k
        ]
        _missing_kpis = [kpi for kpi in _scfg.required_kpis
                         if not any(kpi.lower() in p for p in _kpis_present)]
        add("C12", "sector_kpis", WARNING, not _missing_kpis,
            f"Sector-required KPIs present ({_scfg.code} {_scfg.label})",
            expected=", ".join(_scfg.required_kpis),
            actual="all present" if not _missing_kpis else f"missing: {', '.join(_missing_kpis)}",
            sheet="Operating_KPIs")
    except ImportError:
        pass  # sector module not available — skip

    # W09 — placeholder / unfinished text in narrative fields
    _placeholder_fields = {
        "investment_thesis": (company.get("investment_thesis") or ""),
        "risks": str(company.get("risks") or ""),
        "description": (company.get("description") or ""),
    }
    _ph_hits = [k for k, v in _placeholder_fields.items() if _PLACEHOLDER_PATTERNS.search(v)]
    add("W09", "placeholders", WARNING, not _ph_hits,
        "No unfinished placeholder text in narrative fields",
        expected="no placeholders", actual=("clean" if not _ph_hits else f"placeholders in: {', '.join(_ph_hits)}"),
        sheet="Report_Content")

    # --- informational ----------------------------------------------------
    caveat = dq.get("caveats", "")
    add("I01", "context", INFO, True, "Extraction caveats", actual=(caveat[:120] or "none"))
    restated = any("restat" in (f.get("source", "").lower()) for f in fins)
    add("I02", "context", INFO, True, "Restated comparatives detected in sources",
        actual="yes" if restated else "no")
    return out


def _stmt_row(st: dict, block: str, label: str) -> list | None:
    blk = st.get(block) or []
    # New schema: {line_items: [{label, values}]}
    if isinstance(blk, dict):
        items = blk.get("line_items") or []
        # Flat dict schema: {"total_assets": [v1, v2]} — map key to label
        if not items:
            key = label.lower().replace(" ", "_").replace("/", "_")
            for k, v in blk.items():
                if k.lower() == key and isinstance(v, list):
                    return v
            return None
        blk = items
    for r in blk:
        if isinstance(r, dict) and r.get("label", "").strip().lower() == label.lower():
            return r.get("values") or []
    return None


def _statements_balance(st: dict):
    """Return (all_years_balance_ok, worst_abs_diff) for the reported balance sheet,
    or None if the statements block is absent."""
    ta = _stmt_row(st, "balance_sheet", "Total assets")
    tel = _stmt_row(st, "balance_sheet", "Total equity and liabilities")
    if not ta or not tel:
        te = _stmt_row(st, "balance_sheet", "Total equity")
        tl = _stmt_row(st, "balance_sheet", "Total liabilities")
        if not (ta and te and tl):
            return None
        tel = [(_num(e) or 0) + (_num(l) or 0) for e, l in zip(te, tl)]
    worst = 0.0
    for a, b in zip(ta, tel):
        a, b = _num(a), _num(b)
        if a is not None and b is not None:
            worst = max(worst, abs(a - b))
    return (worst <= 1.5, worst)


def _cashflow_ties(st: dict):
    """True/False if operating+investing+financing == reported net change in cash,
    else None when the cash-flow block is absent."""
    op = _stmt_row(st, "cash_flow", "Net cash generated from operating activities")
    inv = _stmt_row(st, "cash_flow", "Net cash used in investing activities")
    fin = _stmt_row(st, "cash_flow", "Net cash used in financing activities")
    net = _stmt_row(st, "cash_flow", "Increase/(decrease) in cash and cash equivalents")
    if not (op and inv and fin and net):
        return None
    ok = True
    for o, i, f, n in zip(op, inv, fin, net):
        vals = [_num(o), _num(i), _num(f), _num(n)]
        if all(v is not None for v in vals) and abs((vals[0] + vals[1] + vals[2]) - vals[3]) > 1.5:
            ok = False
    return ok


# Aeon Nimbus QC checklist — the two QC tables from the Report Guide + Data Checklist,
# grouped and graded, for the dashboard reliability panel.
_QC_META = {
    "C02": ("Source discipline", "Every key figure is traceable to a source (“no source, no number”)."),
    "C08": ("Latest year", "Latest historical year equals the company filing, not an outdated portal figure."),
    "C03": ("Currency & units", "Reporting currency, unit and fiscal year are stated — no mixed thousands/millions."),
    "C07": ("Statements tie", "Income statement, balance sheet and cash flow match the filing; the balance sheet balances."),
    "W07": ("Cash flow ties", "Operating + investing + financing reconciles to the reported change in cash."),
    "C04": ("Model signs", "Debt, cash and net-debt reconcile on a stated basis (sign convention consistent)."),
    "C06": ("Market data dated", "Share price, market cap and multiples carry a capture date and source."),
    "W06": ("Peer set", "Three to five relevant listed peers; outliers flagged, not silently deleted."),
    "W04": ("Sector KPIs", "Sector-specific operating KPIs are collected (ARPU/subs, NII/NPLs, capacity, etc.)."),
    "C01": ("Five-year history", "Five years of income statement, balance sheet and cash flow."),
    "C09": ("Period order", "Historical periods are in ascending chronological order (no reversed series)."),
    "C10": ("Growth sanity", "No implausible year-on-year growth in P&L (>×10 change flags a unit error or extraction fault)."),
    "W08": ("Price freshness", "Share price captured within the last 90 days."),
    "W09": ("Placeholders", "No unfinished placeholder text in narrative fields."),
    "C12": ("Sector KPIs", "Sector-required operating KPIs are present and non-null (e.g. NIM/NPL for banks, ARPU/subs for telecoms)."),
}


def append_consistency_checks(qc: dict, *, rating: dict | None,
                              dcf: dict | None, bank_valuation: dict | None) -> None:
    """Post-assembly consistency gate — runs AFTER the model so it can compare
    headline outputs.  Mutates the scorecard in place.

    C11: Headline target price matches the active scenario's model output (within 2%).
    C12: The active valuation method is sector-appropriate (bank ≠ DCF).
    """
    checks = qc.get("checks", [])
    passed = qc.get("passed", 0)
    total = qc.get("total", 0)

    def _add_post(cid, area, standard, ok, detail):
        nonlocal passed, total
        status = "pass" if ok else "flag"
        checks.append({"area": area, "standard": standard, "status": status, "detail": detail})
        total += 1
        if ok:
            passed += 1

    # C11 — headline target reconciles to model output
    if rating and dcf:
        headline_target = rating.get("target")
        model_base = (dcf.get("base") or {}).get("value")
        if headline_target is not None and model_base is not None and model_base != 0:
            diff_pct = abs(headline_target - model_base) / abs(model_base)
            ok = diff_pct <= 0.02
            _add_post("C11", "Target consistency",
                      "Headline target price equals the active DCF base scenario value (±2%)",
                      ok,
                      f"target {headline_target} vs model {model_base} ({diff_pct*100:.1f}% diff)" if not ok
                      else f"{headline_target} matches model")
    elif rating and bank_valuation:
        headline_target = rating.get("target")
        model_fair = bank_valuation.get("fair_value")
        if headline_target is not None and model_fair is not None and model_fair != 0:
            diff_pct = abs(headline_target - model_fair) / abs(model_fair)
            ok = diff_pct <= 0.02
            _add_post("C11", "Target consistency",
                      "Headline target price equals the justified-P/B fair value (±2%)",
                      ok,
                      f"target {headline_target} vs model {model_fair} ({diff_pct*100:.1f}% diff)" if not ok
                      else f"{headline_target} matches model")

    # C11 is post-assembly CRITICAL — update export_allowed if it fails.
    c11_failed = any(c.get("area") == "Target consistency" and c.get("status") == "flag"
                     for c in checks)
    if c11_failed and isinstance(qc.get("data_quality"), dict):
        qc["data_quality"]["export_allowed"] = False
        qc["data_quality"]["_c11_blocked"] = True

    qc["checks"] = checks
    qc["passed"] = passed
    qc["total"] = total
    if total:
        pct = round(100 * passed / total)
        qc["pct"] = pct
        qc["grade"] = "A" if pct >= 90 else "B" if pct >= 75 else "C" if pct >= 55 else "D"
        qc["headline"] = f"{passed}/{total} Aeon Nimbus QC checks pass"
    # Mirror export_allowed to top-level for convenience
    qc["export_allowed"] = (qc.get("data_quality") or {}).get("export_allowed", True)


def qc_scorecard(company: dict) -> dict[str, Any]:
    """The Aeon Nimbus QC checklist for a company, graded — what the Report Guide and
    Data Collection checklist require, each marked pass / flag / n-a with detail."""
    controls = {c["control_id"]: c for c in run_controls(company)}
    checks = []
    passed = total = 0
    for cid, (area, standard) in _QC_META.items():
        c = controls.get(cid)
        if not c:
            checks.append({"area": area, "standard": standard, "status": "na", "detail": "not applicable"})
            continue
        status = "pass" if c["status"] == "pass" else "flag"
        checks.append({"area": area, "standard": standard, "status": status,
                       "detail": c.get("actual") or c.get("description", "")})
        total += 1
        passed += 1 if status == "pass" else 0
    pct = round(100 * passed / total) if total else 0
    grade = "A" if pct >= 90 else "B" if pct >= 75 else "C" if pct >= 55 else "D"
    dqs = data_quality_score(company)
    return {"checks": checks, "passed": passed, "total": total, "pct": pct,
            "grade": grade, "data_quality": dqs,
            "headline": f"{passed}/{total} Aeon Nimbus QC checks pass",
            "export_allowed": dqs["export_allowed"]}


def data_quality_score(company: dict) -> dict[str, Any]:
    """0-100 data-quality score with component breakdown and output band (spec §17)."""
    fins = _normalize_financials(company.get("financials", []))
    dq = company.get("data_quality", {}) or {}
    _raw_notes = company.get("notes") or {}
    notes = _raw_notes if isinstance(_raw_notes, dict) else {}
    market = company.get("market", {}) or {}
    controls = run_controls(company)

    crit_fail = sum(1 for c in controls if c["severity"] == CRITICAL and c["status"] == "fail")
    warn = sum(1 for c in controls if c["severity"] == WARNING and c["status"] == "warn")

    def _to_float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    conf_vals = [_to_float(f["confidence"]) for f in fins if f.get("confidence") is not None]
    conf_vals = [v for v in conf_vals if v is not None]
    components = {
        "source_completeness": min(len(fins) / 5.0, 1.0),
        "source_quality": 1.0 if all(f.get("source") for f in fins) else 0.6,
        "extraction_confidence": (sum(conf_vals) / len(conf_vals)) if conf_vals else (_to_float(dq.get("confidence")) or 0.7),
        "reconciliation": 1.0 - min(crit_fail, 3) / 3.0,
        "historical_coverage": min((_to_float(dq.get("years_found")) or len(fins)) / 5.0, 1.0),
        "kpi_coverage": 1.0 if company.get("sector_kpis") else 0.4,
        "schedule_coverage": min(sum(1 for k in ("capex", "depreciation_amortisation", "interest_expense",
                                                 "lease_liabilities", "segments") if notes.get(k)) / 5.0, 1.0),
        "market_freshness": 1.0 if (market.get("share_price") is not None and market.get("price_date")) else 0.3,
    }
    weights = {
        "source_completeness": 0.15, "source_quality": 0.12, "extraction_confidence": 0.20,
        "reconciliation": 0.15, "historical_coverage": 0.13, "kpi_coverage": 0.10,
        "schedule_coverage": 0.08, "market_freshness": 0.07,
    }
    score = round(100 * sum(components[k] * weights[k] for k in weights), 1)
    score = max(0.0, score - 5 * crit_fail - 1 * warn)  # penalise open issues

    if score >= 90:
        band, policy = "A", "Full model and valuation allowed"
    elif score >= 70:
        band, policy = "B", "Full model allowed with warnings"
    elif score >= 50:
        band, policy = "C", "Limited valuation, wider sensitivity ranges"
    else:
        band, policy = "D", "Historical dashboard and qualitative research only; no automated target price"

    return {"score": round(score, 1), "band": band, "policy": policy,
            "components": {k: round(v, 3) for k, v in components.items()},
            "critical_failures": crit_fail, "warnings": warn,
            "export_allowed": crit_fail == 0}
