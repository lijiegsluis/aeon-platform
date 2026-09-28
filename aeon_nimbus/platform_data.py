"""Platform data assembly — the payload behind the dashboard.

Turns the source-linked extracted records into the unified "deep" object the
frontend plots (financials, KPIs, segments, indicative valuation), and assembles
the whole-universe payload. Two assemblers share one shape:

  * assemble()        — the frozen showcase, read from data/*.json on disk
  * assemble_live(db) — the live working copy, read from Company.extracted in the DB

Keeping both on the same shape means the static snapshot and the running app
render through the identical template. Financials, KPIs, segments and notes are
the REAL extracted figures; the DCF is an indicative engine run off that history
with a transparent per-country WACC — never a fabricated figure.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

from aeon_nimbus import analytics, forecast as _fc, scenarios as scn, sector_analytics
from aeon_nimbus.config import BANK_COE_BY_COUNTRY, SUSTAINABLE_ROE_CAP, WACC_BY_COUNTRY
from aeon_nimbus.sector import config_for, valuation_kind_for

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

_SUSTAINABLE_ROE_CAP = SUSTAINABLE_ROE_CAP  # kept for existing references below



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


def _stance(upside: float | None) -> str:
    if upside is None:
        return "NR"
    return "Buy" if upside >= 0.20 else ("Hold" if upside >= -0.10 else "Reduce")


def _fc_block(fins: list[dict]) -> dict:
    """A 5-year forward projection block for the Financials tab."""
    try:
        proj = _fc.project(fins, years=5)
    except Exception:
        return {}
    return {"years": [r["fy"] for r in proj], "revenue": [r["revenue"] for r in proj],
            "ebitda": [r["ebitda"] for r in proj], "net_income": [r["net_income"] for r in proj],
            "ebitda_margin": [r["ebitda_margin"] for r in proj]}


def deep_from_extracted(rec: dict, u: dict, use_cache: bool = True) -> dict | None:
    """Unified deep object from a source-linked extracted record."""
    def _fy(f):
        raw = f.get("fy") or f.get("fy_label")
        if raw:
            return str(raw)
        fy_int = f.get("fiscal_year")
        return f"FY{fy_int}" if fy_int else ""

    # Handle both list and dict formats for financials
    fins_raw = rec.get("financials", [])
    if isinstance(fins_raw, dict):
        # Convert dict format {year: data} to list format
        fins = []
        for year, data in fins_raw.items():
            if isinstance(data, dict):
                data_copy = data.copy()
                data_copy['fiscal_year'] = int(year) if year.isdigit() else None
                fins.append(data_copy)
        fins = sorted(fins, key=_fy)
    else:
        fins = sorted(fins_raw, key=_fy)

    if not fins:
        return None
    years = [_fy(f) for f in fins]

    def s(k):
        return [f.get(k) for f in fins]

    latest = fins[-1]
    mkt = rec.get("market", {}) or {}
    price = mkt.get("share_price") or mkt.get("share_price_usd")
    shares = mkt.get("shares_outstanding_m")
    mcap = (mkt.get("market_cap_m") or mkt.get("market_cap_m_usd")
            or (price * shares if price and shares else None))
    # A quote in a different currency to the financials (e.g. a USD ADR price against CNY/JPY
    # accounts) cannot be combined with them: EV, multiples and DCF upside would be
    # meaningless, so the market block is withheld rather than mixed.
    _pc_raw = mkt.get("price_currency") or ""
    _pc = _pc_raw.split()[0].upper() if _pc_raw.strip() else ""
    _fc = (rec.get("currency") or "").upper()
    price_note = None
    if _pc and _fc and _pc != _fc:
        price_note = (f"Quote is in {_pc} but financials are in {_fc}; market cap, EV, multiples and "
                      f"valuation withheld until the quote is restated in {_fc}.")
        price = mcap = shares = None
    net_debt = latest.get("net_debt") or 0.0
    ev = (mcap + net_debt) if mcap is not None else None
    ebitda, ni, eq = latest.get("ebitda"), latest.get("net_income"), latest.get("total_equity")

    _rev0 = latest.get("revenue")
    keystats = {
        "market_cap_m": mcap, "ev_m": ev, "share_price": price, "currency": rec.get("currency"),
        "shares_m": round(mcap / price, 1) if (mcap and price) else shares,
        "ev_ebitda": round(ev / ebitda, 1) if (ev and ebitda) else None,
        "ev_sales": round(ev / _rev0, 1) if (ev and _rev0) else None,
        "pe": round(mcap / ni, 1) if (mcap and ni and ni > 0) else None,
        "pb": round(mcap / eq, 1) if (mcap and eq) else None,
        "fcf_yield": round(latest.get("free_cash_flow") / mcap, 3) if (latest.get("free_cash_flow") and mcap) else None,
        "div_yield": round(abs(latest.get("dividends_paid")) / mcap, 3) if (latest.get("dividends_paid") and mcap) else None,
        "price_source": mkt.get("source"), "price_date": mkt.get("price_date"),
        "price_note": price_note,
    }
    ebitda_margin = [round(f["ebitda"] / f["revenue"], 3) if (f.get("ebitda") and f.get("revenue")) else None for f in fins]
    roe = [round(f["net_income"] / f["total_equity"], 3) if (f.get("net_income") and f.get("total_equity")) else None for f in fins]

    dcf = rating = bank_valuation = sc = sc_bank = None
    rev = latest.get("revenue")

    # Determine valuation kind from sector routing (or explicit override in universe.json)
    _vkind = valuation_kind_for(
        u.get("sector", ""), u.get("sub_sector"),
        is_bank=bool(rec.get("bank")),
        valuation_model=u.get("valuation_model"),
    )

    if _vkind == "bank" and eq and shares and price:
        # ── Bank: justified P/B via the scenario engine ─────────────────────
        # Use stored/edited scenarios first; fall back to defaults.  This is the
        # SINGLE source of truth for the bank valuation — the studio, platform
        # headline and Excel all read from the same sc["sets"]["Base"] set.
        sc_bank = scn.normalise(
            rec.get("scenarios"), fins,
            country=u.get("country"), is_bank=True, bank=rec.get("bank") if isinstance(rec.get("bank"), dict) else None,
        )
        sv = scenario_values(fins, mkt, sc_bank)
        active = sv["active"]
        if active.get("value_per_share") is not None:
            a = sc_bank["sets"][sc_bank.get("selected", "Base")]
            def _frac(x):
                return x / 100.0 if (x is not None and abs(x) > 1.5) else x
            b = rec.get("bank", {}) if isinstance(rec.get("bank"), dict) else {}
            roe_reported = _frac(b.get("roe")) or (ni / eq if (ni and eq) else None)
            roe_v = min(a.get("roe", 0.15), _SUSTAINABLE_ROE_CAP)
            try:
                bvps = eq / shares
                pb = sector_analytics.justified_pb(roe_v, a.get("coe", 0.20), a.get("growth", 0.06))
                fair = active["value_per_share"]
                up = active.get("upside")
                bank_valuation = {
                    "justified_pb": round(pb, 2), "fair_value": round(fair, 2),
                    "bvps": round(bvps, 2), "roe": roe_v, "roe_reported": roe_reported,
                    "coe": a.get("coe", 0.20), "growth": a.get("growth", 0.06),
                }
                rating = {"stance": _stance(up), "target": round(fair, 2), "price": price,
                          "upside": up, "conviction": "Justified P/B",
                          "scenario": sc_bank.get("selected", "Base")}
            except (ValueError, ZeroDivisionError, TypeError):
                bank_valuation = None

    elif _vkind == "dcf" and rev and ebitda and shares and price:
        # ── DCF: use normalised scenarios throughout ──────────────────────────
        # scn.normalise() merges any studio-edited assumptions from rec["scenarios"]
        # with the history-derived defaults, so the dashboard, the studio and the
        # Excel always read from the same set.
        sc = scn.normalise(
            rec.get("scenarios"), fins,
            country=u.get("country"), is_bank=False,
        )
        sv = scenario_values(fins, mkt, sc)
        try:
            scen = {}
            name_map = {"downside": "Bear", "base": "Base", "upside": "Bull"}
            for disp, sname in name_map.items():
                a = sc["sets"].get(sname, sc["sets"]["Base"])
                scen[disp] = {
                    "value": sv["per_scenario"].get(sname, {}).get("value_per_share"),
                    "wacc": a["wacc"],
                    "growth": a["revenue_growth"],
                    "terminal": a["terminal_growth"],
                }
            if scen["base"]["value"] is None:
                raise ValueError("no base value")
            dcf = scen
            active = sv["active"]
            up = active.get("upside")
            if up is not None and -0.90 <= up <= 1.50:
                rating = {
                    "stance": _stance(up),
                    "target": active.get("value_per_share"),
                    "price": price, "upside": up, "conviction": "Indicative",
                    "scenario": sc.get("selected", "Base"),
                }
            else:
                dcf = None
        except (ValueError, ZeroDivisionError, TypeError):
            dcf = None

    _raw_notes = rec.get("notes", {}) or {}
    notes = _raw_notes if isinstance(_raw_notes, dict) else {}
    # numbers-anchored commentary derived from the model (factual, all companies —
    # "numbers before adjectives", per the Report Guide). No judgement/opinion.
    cur = rec.get("currency", "")
    def _p(x):
        return f"{x*100:.1f}%" if isinstance(x, (int, float)) else "—"
    revs = s("revenue")
    commentary = {"revenue_drivers": [], "cost_pressures": []}
    segrev = notes.get("segments") or [] if isinstance(notes, dict) else []
    granular = [x for x in segrev if isinstance(x, dict) and isinstance(x.get("value"), (int, float)) and x["value"] > 0
                and "total" not in (x.get("name") or "").lower()]
    if granular:
        top = max(granular, key=lambda x: x.get("value") or 0)
        commentary["revenue_drivers"].append(
            f"Largest disclosed revenue line: {top.get('name')} ({cur} {top.get('value'):,.0f}m, {top.get('period', '')}).")
    rv = [v for v in revs if isinstance(v, (int, float))]
    if len(rv) >= 2 and rv[0]:
        cg = (rv[-1] / rv[0]) ** (1 / (len(rv) - 1)) - 1
        commentary["revenue_drivers"].append(
            f"Revenue compounded at {_p(cg)} over {years[0]}–{years[-1]} ({cur} {rv[0]:,.0f}m → {rv[-1]:,.0f}m).")
        if len(rv) >= 2 and rv[-2]:
            commentary["revenue_drivers"].append(f"Latest-year revenue growth {_p(rv[-1] / rv[-2] - 1)} ({years[-1]}).")
    em = [v for v in ebitda_margin if isinstance(v, (int, float))]
    if len(em) >= 2:
        commentary["cost_pressures"].append(
            f"EBITDA margin moved from {_p(em[0])} ({years[0]}) to {_p(em[-1])} ({years[-1]}).")
    da = notes.get("depreciation_amortisation")
    if isinstance(da, (int, float)):
        commentary["cost_pressures"].append(f"Depreciation & amortisation {cur} {da:,.0f}m — the main cost below EBITDA (capex intensity).")
    lf = fins[-1] if fins else {}
    if isinstance(lf.get("tax"), (int, float)) and lf.get("pbt"):
        commentary["cost_pressures"].append(f"Effective tax rate {_p(-lf['tax'] / lf['pbt'])} ({lf.get('fy')}).")

    qualitative = rec.get("qualitative") or _qual_from_data(
        u.get("name") or rec.get("name") or "", u, rec, rating)
    # QC — run after the model so consistency checks have access to model outputs
    from aeon_nimbus import controls as _ctrl
    _co_qc = dict(rec); _co_qc["_peers"] = u.get("peers", [])
    qc = _ctrl.qc_scorecard(_co_qc)
    # Post-assembly consistency: headline target must match the active scenario value
    _ctrl.append_consistency_checks(qc, rating=rating, dcf=dcf, bank_valuation=bank_valuation)

    # Sector config for the UI
    _scfg = config_for(u.get("sector", ""), u.get("sub_sector"), is_bank=(_vkind == "bank"))

    return {
        "source": "extracted", "years": years, "qc": qc,
        "revenue": s("revenue"), "ebitda": s("ebitda"), "ebit": s("ebit"), "net_income": s("net_income"),
        "fcf": s("free_cash_flow"), "net_debt": s("net_debt"), "total_assets": s("total_assets"),
        "total_equity": s("total_equity"), "ebitda_margin": ebitda_margin, "roe": roe,
        "operating_cash_flow": s("operating_cash_flow"), "capex": s("capex"),
        "total_debt": s("total_debt"), "cash": s("cash"), "dividends_paid": s("dividends_paid"),
        # income-statement bridge + working capital (reported; shown when available)
        "direct_costs": s("direct_costs"), "ecl": s("ecl"), "other_opex": s("other_opex"),
        "net_finance": s("net_finance"), "assoc_jv": s("assoc_jv"), "hyperinflation": s("hyperinflation"),
        "pbt": s("pbt"), "tax": s("tax"), "nwc": s("nwc"),
        # KPI-support fields: gross profit and interest expense derived from income-statement notes
        "gross_profit": ([f.get("revenue", 0) - f.get("direct_costs", 0)
                          if isinstance(f.get("revenue"), (int, float)) and isinstance(f.get("direct_costs"), (int, float))
                          else None for f in fins]
                         if any(f.get("direct_costs") is not None for f in fins) else None),
        "interest_expense": ([abs(f["interest_expense"]) if isinstance(f.get("interest_expense"), (int, float)) else None
                               for f in fins]
                              if any(f.get("interest_expense") is not None for f in fins)
                              else ([abs(notes["interest_expense"])] if isinstance(notes.get("interest_expense"), (int, float)) else None)),
        "effective_tax": [round(-f["tax"] / f["pbt"], 3) if (f.get("tax") is not None and f.get("pbt")) else None for f in fins],
        "net_debt_ebitda": [round(f["net_debt"] / f["ebitda"], 2) if (f.get("net_debt") is not None and f.get("ebitda")) else None for f in fins],
        "net_margin": [round(f["net_income"] / f["revenue"], 3) if (f.get("net_income") and f.get("revenue")) else None for f in fins],
        "forecast": _fc_block(fins),
        # per-year provenance for inline source citations
        "year_sources": s("source"), "year_confidences": s("confidence"),
        "keystats": keystats, "rating": rating, "dcf": dcf, "wacc_build": None,
        "bank_valuation": bank_valuation, "peers": None, "memo": None,
        "report_file": "reports/safaricom_plc_initiation.html" if u.get("ticker") == "SCOM" else None,
        "sector_kpis": rec.get("sector_kpis", []), "segments": notes.get("segments", []),
        # Bamburi-report content (real, sourced; qualitative gaps marked in the UI, never invented)
        "business_segments": u.get("segments", []), "risks": u.get("risks", []),
        "macro_sources": u.get("macro_sources", []), "segment_revenue": notes.get("segments", []),
        "qualitative": qualitative, "commentary": commentary,
        "notes": {k: notes.get(k) for k in ("capex", "depreciation_amortisation", "interest_income",
                                            "interest_expense", "impairments", "lease_liabilities", "gross_debt")},
        "bank": rec.get("bank"),
        "sector_config": {
            "code": _scfg.code, "label": _scfg.label,
            "valuation_kind": _scfg.valuation_kind,
            "valuation_notes": _scfg.valuation_notes,
            "required_kpis": _scfg.required_kpis,
        },
        # The live normalised scenario set — studio edits are reflected here
        "active_scenarios": sc if _vkind == "dcf" else (sc_bank if _vkind == "bank" else None),
        # Pre-computed Bull/Base/Bear valuation (from batch_valuations.py / valuation_engine.py)
        "stored_valuation": rec.get("valuation"),
        "sources": rec.get("sources", []),
        "confidence": (rec.get("data_quality", {}).get("confidence")
                       or (round(sum(_yc) / len(_yc), 2) if (_yc := [x for x in s("confidence") if x is not None]) else None)),
        "caveats": rec.get("data_quality", {}).get("caveats"),
        "statements": rec.get("statements"),
    }


def _deep_safaricom() -> dict:
    """Fallback: the flagship's plottable data from its source-linked seed."""
    from aeon_nimbus.real_seed import build_safaricom_context
    c = build_safaricom_context()["companies"][0]
    hist = c["financial_history"]

    def series(key: str) -> list:
        out = []
        for y in hist:
            v = y.get(key)
            out.append(round(v["value"], 1) if isinstance(v, dict) else v)
        return out

    val = c["valuation"]
    iv = c["investment_view"]
    scen = val["scenarios"]
    return {
        "years": [y["period"] for y in hist],
        "revenue": series("revenue"), "ebitda": series("ebitda"), "ebit": series("ebit"),
        "net_income": series("net_income"), "fcf": series("free_cash_flow"), "net_debt": series("net_debt"),
        "ebitda_margin": series("ebitda_margin"), "roe": series("roe"),
        "rating": {"stance": _stance(iv.get("upside_downside")), "target": iv["target_price"],
                   "price": iv["current_price"], "upside": iv["upside_downside"],
                   "conviction": iv["conviction"].split(" ")[0]},
        "keystats": {"market_cap_m": c["market_cap"], "ev_m": c["enterprise_value"],
                     "share_price": iv["current_price"], "currency": c["currency"],
                     "ev_ebitda": val["current_multiples"]["ev_ebitda"], "pe": val["current_multiples"]["pe"],
                     "pb": val["current_multiples"]["price_book"], "div_yield": val["current_multiples"]["dividend_yield"],
                     "fcf_yield": val["current_multiples"]["fcf_yield"]},
        "dcf": {name: {"value": round(scen[name]["value_per_share"], 2),
                       "wacc": scen[name]["assumptions"]["wacc"],
                       "growth": scen[name]["assumptions"]["revenue_growth"],
                       "terminal": scen[name]["assumptions"]["terminal_growth"]}
                for name in ("downside", "base", "upside")},
        "wacc_build": val.get("wacc_build", {}),
        "peers": [{"name": p["name"], "ticker": p["ticker"], "ev_ebitda": p["ev_ebitda"],
                   "pe": p["pe"], "roe": p["roe"], "ebitda_margin": p["ebitda_margin"]} for p in val["peers"]],
        "peer_median_ev_ebitda": val.get("peer_median_ev_ebitda"),
        "memo": c["memo"], "catalysts": iv["catalysts"],
        "report_file": "reports/safaricom_plc_initiation.html",
        "source": "seed", "sector_kpis": [], "segments": [], "notes": {}, "bank": None,
        "year_sources": [], "year_confidences": [],
        "sources": [c["documents"][0].get("source_url")] if c.get("documents") else [],
        "confidence": 0.95, "caveats": None,
    }


def _entry_base(u: dict, cov: dict) -> dict:
    """The company card fields shared by both assemblers."""
    return {
        "slug": u["slug"], "name": u["name"], "ticker": u.get("ticker"),
        "exchange": u.get("exchange"), "country": u.get("country"), "currency": u.get("currency"),
        "sector": (u.get("sector") or "").split(" /")[0].split(" &")[0].strip() or "Other",
        "sub_sector": u.get("sub_sector"), "valuation_model": u.get("valuation_model"),
        "latest_fiscal_year": u.get("latest_fiscal_year"), "market_cap_usd_bn": u.get("market_cap_usd_bn"),
        "data_availability": u.get("data_availability"),
        "sector_kpis": u.get("sector_kpis", []), "peers": u.get("peers", []),
        "segments": u.get("segments", []), "risks": u.get("risks", []),
        "macro_sources": u.get("macro_sources", []),
        "annual_report_url": u.get("annual_report_url"), "interim_results_url": u.get("interim_results_url"),
        "investor_presentation_url": u.get("investor_presentation_url"), "ir_url": u.get("ir_url"),
        "five_year_financials_note": u.get("five_year_financials_note"),
        "has_report": cov.get("annual_report", False),
        "deep": None,
    }


def assemble() -> dict:
    """Frozen showcase payload — read from data/*.json on disk."""
    universe = json.loads((DATA / "universe.json").read_text())
    coverage = {c["slug"]: c for c in json.loads((DATA / "coverage.json").read_text())}
    companies = []
    for u in universe:
        entry = _entry_base(u, coverage.get(u["slug"], {}))
        ext = DATA / "extracted" / f"{u['slug']}.json"
        if ext.exists():
            entry["deep"] = deep_from_extracted(json.loads(ext.read_text()), u)
        elif u.get("ticker") == "SCOM":
            entry["deep"] = _deep_safaricom()
        companies.append(entry)
    fill_peer_comps(companies)
    compute_factor_scores(companies)
    return {"generated_at": date.today().isoformat(), "companies": companies, "live": False,
            "counts": {"total": len(companies), "deep": sum(1 for c in companies if c["deep"])}}


def assemble_live(db) -> dict:
    """Live working payload — read from Company.extracted in the DB, so Data
    Studio edits are reflected immediately."""
    from aeon_nimbus import db as D
    rows = db.query(D.Company).order_by(D.Company.name).all()
    companies = []
    for co in rows:
        u = co.universe or {}
        # Merge database fields with universe data (database fields take precedence)
        merged = {**u,
                  "slug": co.slug,
                  "name": co.name,
                  "ticker": co.ticker,
                  "exchange": co.exchange,
                  "country": co.country,
                  "sector": co.sector,
                  "sub_sector": co.sub_sector,
                  "currency": co.currency}
        entry = _entry_base(merged, {})
        ext = co.extracted or {}
        if ext.get("financials"):
            entry["deep"] = deep_from_extracted(ext, merged)
            # Populate summary market cap from deep data
            if entry["deep"] and entry["deep"].get("keystats"):
                mcap_m = entry["deep"]["keystats"].get("market_cap_m")
                if mcap_m:
                    entry["market_cap_usd_bn"] = round(mcap_m / 1000, 2)
        elif co.ticker == "SCOM":
            entry["deep"] = _deep_safaricom()
        entry["company_id"] = co.id
        companies.append(entry)
    fill_peer_comps(companies)
    compute_factor_scores(companies)
    return {"generated_at": date.today().isoformat(), "companies": companies, "live": True,
            "counts": {"total": len(companies), "deep": sum(1 for c in companies if c["deep"])}}


# ---- assumption-driven valuation (shared by the studio, matches the Excel) ----

def value_per_share(fins: list, market: dict, assumptions: dict) -> float | None:
    """Value per share for one DCF assumption set — the same engine the workbook's
    formulas replicate, so the dashboard, the studio and the Excel always agree."""
    if not fins:
        return None
    latest = fins[-1]
    rev = latest.get("revenue")
    shares = (market or {}).get("shares_outstanding_m")
    if not (rev and shares):
        return None
    fin_in = {"revenue": rev, "net_debt": latest.get("net_debt") or 0.0, "shares_outstanding": shares}
    a = {"years": 5}
    for k in ("revenue_growth", "ebitda_margin", "d_and_a_pct_sales", "tax_rate",
              "capex_pct_sales", "working_capital_pct_sales", "wacc", "terminal_growth"):
        a[k] = assumptions.get(k)
    try:
        return round(analytics.run_dcf(fin_in, a)["value_per_share"], 2)
    except (ValueError, ZeroDivisionError, TypeError, KeyError):
        return None


def bank_value_per_share(fins: list, market: dict, assumptions: dict) -> float | None:
    """Justified-P/B fair value per share for one bank assumption set."""
    if not fins:
        return None
    latest = fins[-1]
    eq, shares = latest.get("total_equity"), (market or {}).get("shares_outstanding_m")
    if not (eq and shares):
        return None
    bvps = eq / shares
    roe = min(assumptions.get("roe", 0.15), _SUSTAINABLE_ROE_CAP)
    coe, g = assumptions.get("coe", 0.20), assumptions.get("growth", 0.06)
    if not (coe and coe > g):
        return None
    try:
        pb = sector_analytics.justified_pb(roe, coe, g)
        return round(sector_analytics.fair_value_per_share_pb(bvps, pb), 2)
    except (ValueError, ZeroDivisionError, TypeError):
        return None


def _qual_from_data(name: str, u: dict, rec: dict, rating: dict | None) -> dict:
    """Assemble a sourced qualitative block from REAL data only — universe segments/risks/KPIs
    plus computed financial facts. No invented narrative."""
    segs = u.get("segments") or []
    peers = [(p.get("name") if isinstance(p, dict) else p) for p in (u.get("peers") or [])]
    kpis = rec.get("sector_kpis") or u.get("sector_kpis") or []
    risks = u.get("risks") or []
    country, sector, currency = u.get("country", ""), u.get("sector", ""), u.get("currency", "")
    SRC = "audited financial statements"

    def _n(v, default=None):
        return float(v) if isinstance(v, (int, float)) else default

    def pct(v):
        return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else "—"

    def money(v, scale="m"):
        if not isinstance(v, (int, float)):
            return "—"
        if abs(v) >= 1000:
            return f"{currency}{v / 1000:,.1f}bn"
        return f"{currency}{v:,.0f}m"

    # Derive financial commentary from filings
    fins_raw = rec.get("financials") or []
    if isinstance(fins_raw, dict):
        fins = []
        for year, data in fins_raw.items():
            if isinstance(data, dict):
                data_copy = data.copy()
                data_copy['fy'] = str(year)
                fins.append(data_copy)
        fins = sorted(fins, key=lambda f: f.get("fy", ""))
    else:
        fins = sorted(fins_raw, key=lambda f: f.get("fy", ""))
    fin_bullets = []
    if fins:
        last, first = fins[-1], fins[0]
        n = len(fins)
        rev_l = _n(last.get("revenue"))
        rev_f = _n(first.get("revenue"))
        rev_p = _n(fins[-2].get("revenue")) if n >= 2 else None
        ebitda_l = _n(last.get("ebitda"))
        ni_l = _n(last.get("net_income"))
        fcf_l = _n(last.get("fcf"))
        nd_l = _n(last.get("net_debt"))
        capex_l = _n(last.get("capex"))
        div_l = _n(last.get("dividends"))
        fy_l = last.get("fy", "latest")
        fy_s = first.get("fy", "")

        if rev_l is not None and rev_f and rev_f > 0 and n >= 2:
            cagr = (rev_l / rev_f) ** (1.0 / max(n - 1, 1)) - 1
            direction = "compound annual revenue growth" if cagr >= 0.02 else ("revenue contraction" if cagr < -0.01 else "broadly flat revenues")
            fin_bullets.append({
                "point": f"{name} has delivered {pct(cagr)} {direction} from {fy_s} to {fy_l} on our model.",
                "source": SRC})

        if ebitda_l and rev_l and rev_l > 0:
            margin = ebitda_l / rev_l
            quality = "high-quality" if margin > 0.35 else ("thin-margin" if margin < 0.12 else "solid")
            fin_bullets.append({
                "point": f"EBITDA margin stands at {pct(margin)} in {fy_l} — a {quality} operating profile for the {sector} sector.",
                "source": SRC})

        if fcf_l and ebitda_l and ebitda_l != 0:
            conv = fcf_l / ebitda_l
            desc = "strong cash conversion" if conv > 0.6 else ("negative FCF generation" if conv < 0 else "moderate cash conversion")
            fin_bullets.append({
                "point": f"FCF / EBITDA of {pct(conv)} in {fy_l} indicates {desc}.",
                "source": SRC})

        if nd_l is not None and ebitda_l and ebitda_l > 0:
            lev = nd_l / ebitda_l
            lev_desc = "net cash" if lev < 0 else ("conservatively leveraged" if lev < 1.5 else ("moderately leveraged" if lev < 3.0 else "highly leveraged"))
            fin_bullets.append({
                "point": f"Balance sheet is {lev_desc} at {lev:.1f}x net debt / EBITDA ({fy_l}).",
                "source": SRC})

        if capex_l and rev_l and rev_l > 0:
            cx_pct = abs(capex_l) / rev_l
            cx_desc = "capital-intensive" if cx_pct > 0.15 else ("asset-light" if cx_pct < 0.05 else "moderately capital-intensive")
            fin_bullets.append({
                "point": f"Capex intensity of {pct(cx_pct)} of revenue ({fy_l}) — an {cx_desc} model.",
                "source": SRC})

        if rev_l and rev_p and rev_p > 0:
            yoy = (rev_l - rev_p) / rev_p
            yoy_desc = f"grew {pct(yoy)} year-on-year" if yoy >= 0 else f"declined {pct(abs(yoy))} year-on-year"
            fin_bullets.append({
                "point": f"Revenue {yoy_desc} in {fy_l}.",
                "source": SRC})

    q = {
        "as_of": u.get("latest_fiscal_year") or (fins[-1].get("fy") if fins else "latest"),
        "basis_note": "Populated from audited financial statements (computed ratios and trends), segment disclosures, and operating KPIs. Qualitative narrative (management tone, strategic guidance) is curated per company from the filing — deeper content pending analyst curation for this name.",
        "market_overview": [],
        "revenue_drivers": [],
        "cost_pressures": [],
        "non_financial": [],
        "outlook": None,
    }

    # Market overview
    if segs:
        q["market_overview"].append({
            "point": f"{name} operates across {len(segs)} disclosed business segment{'s' if len(segs) > 1 else ''}: {', '.join((s.get('name') if isinstance(s, dict) else s) for s in segs[:5])}.",
            "source": "company segment disclosure"})
    if country and sector:
        q["market_overview"].append({
            "point": f"Listed in {country} in the {sector} sector.",
            "source": "Aeon Nimbus coverage universe"})
    if peers:
        q["market_overview"].append({
            "point": f"Key listed peers for relative valuation: {', '.join(peers[:5])}.",
            "source": "coverage universe"})

    # Revenue drivers — from fin_bullets + segment structure
    for b in fin_bullets[:3]:
        q["revenue_drivers"].append(b)

    # Cost pressures — from KPIs and leverage
    for b in fin_bullets[3:]:
        q["cost_pressures"].append(b)

    # Non-financial — KPIs
    for k in kpis:
        if isinstance(k, dict) and k.get("value") is not None:
            val = k.get("value")
            unit = k.get("unit") or ""
            period = k.get("period") or k.get("fy") or "latest"
            q["non_financial"].append({
                "point": f"{k.get('name')}: {val} {unit} ({period}).".strip(),
                "source": f"company filing — {period}"})
    q["non_financial"] = q["non_financial"][:8]

    # Key risks
    if risks:
        q["key_risks"] = [
            {"point": (r.get("description") if isinstance(r, dict) else r),
             "source": "company risk disclosure"}
            for r in risks[:8]
        ]

    # Outlook + sentiment
    if rating:
        up = rating.get("upside")
        up_str = f" ({up * 100:+.1f}% {'upside' if (up or 0) >= 0 else 'downside'})" if up is not None else ""
        q["outlook"] = {
            "point": f"Platform model stance: {rating['stance']}{up_str}. Intrinsic value {rating.get('target')} vs current price {rating.get('price')}.",
            "source": "platform valuation model"}
        q["sentiment"] = {
            "management_tone": "Not yet curated — see the chairman's / CEO's statement in the annual report.",
            "platform_stance": f"{rating['stance']}{up_str}.",
            "source": "platform valuation model"}

    return q


def _multiples_table(companies: list) -> dict:
    """Ticker → trading multiples, computed from each company's deep object, with
    sanity bounds so bad-data outliers don't surface as if real."""
    def ok(v, lo, hi):
        return v if (isinstance(v, (int, float)) and lo <= v <= hi) else None

    out: dict[str, dict] = {}
    for co in companies:
        d = co.get("deep")
        if not d:
            continue
        k = d.get("keystats", {}) or {}
        out[(co.get("ticker") or "").upper()] = {
            "name": co["name"], "ticker": co.get("ticker"),
            "ev_ebitda": ok(k.get("ev_ebitda"), 0.5, 40), "ev_sales": ok(k.get("ev_sales"), 0.2, 20),
            "pe": ok(k.get("pe"), 1, 60), "pb": ok(k.get("pb"), 0.1, 15),
            "div_yield": ok(k.get("div_yield"), 0, 0.25), "fcf_yield": ok(k.get("fcf_yield"), -0.2, 0.4),
            "roe": ok((d.get("roe") or [None])[-1], -0.5, 0.6),
            "ebitda_margin": ok((d.get("ebitda_margin") or [None])[-1], 0, 0.95),
        }
    return out


def all_multiples() -> dict:
    """Ticker → trading multiples for every covered company (for the Excel comps)."""
    return _multiples_table(assemble()["companies"])


def _zscore_series(vals: list) -> list:
    """Z-score a list, replacing None with None. Returns None for <2 valid points."""
    good = [v for v in vals if isinstance(v, (int, float))]
    if len(good) < 2:
        return [None] * len(vals)
    mean = sum(good) / len(good)
    std = (sum((x - mean) ** 2 for x in good) / len(good)) ** 0.5
    if std == 0:
        return [0.0 if isinstance(v, (int, float)) else None for v in vals]
    return [round(max(-3.0, min(3.0, (v - mean) / std)), 3)
            if isinstance(v, (int, float)) else None for v in vals]


def _last_val(series: list | None) -> float | None:
    if not series:
        return None
    for v in reversed(series):
        if isinstance(v, (int, float)):
            return v
    return None


def compute_factor_scores(companies: list) -> None:
    """Cross-sectional factor scoring — mutates each company's deep['factor_scores'].

    Factors (all expressed so that HIGHER = better):
      value    — cheap: inverse EV/EBITDA, inverse P/E, inverse P/B, FCF yield
      quality  — profitable: EBITDA margin, ROE, FCF margin
      growth   — revenue CAGR + EBITDA CAGR
      stability— inverse of ROE standard deviation (lower vol = steadier)
      leverage — inverse of ND/EBITDA (lower debt = better)

    Each raw metric is z-scored across ALL companies with a valid reading;
    composites are the unweighted mean of available z-scores (capped ±2.5).
    """
    import statistics as _stat

    slugs = [co["slug"] for co in companies]
    deeps = {co["slug"]: (co.get("deep") or {}) for co in companies}

    def raw(slug, key):
        return deeps[slug].get(key)

    def last(slug, key):
        return _last_val(deeps[slug].get(key))

    # ── raw metric extraction ────────────────────────────────────────────────
    metrics: dict[str, list] = {}

    # Value: prefer lower multiples → negate so higher z = cheaper
    ev_ebitda   = [-(raw(s, "keystats") or {}).get("ev_ebitda", None)
                    if (raw(s, "keystats") or {}).get("ev_ebitda") else None for s in slugs]
    pe          = [-(raw(s, "keystats") or {}).get("pe", None)
                    if (raw(s, "keystats") or {}).get("pe") else None for s in slugs]
    pb          = [-(raw(s, "keystats") or {}).get("pb", None)
                    if (raw(s, "keystats") or {}).get("pb") else None for s in slugs]
    fcf_yield   = [(raw(s, "keystats") or {}).get("fcf_yield") for s in slugs]

    metrics["ev_ebitda_inv"] = ev_ebitda
    metrics["pe_inv"]        = pe
    metrics["pb_inv"]        = pb
    metrics["fcf_yield"]     = fcf_yield

    # Quality
    ebitda_margin = [last(s, "ebitda_margin") for s in slugs]
    roe           = [last(s, "roe") for s in slugs]
    fcf_margin    = [(_last_val(deeps[s].get("fcf")) / _last_val(deeps[s].get("revenue"))
                      if _last_val(deeps[s].get("fcf")) and _last_val(deeps[s].get("revenue"))
                      else None) for s in slugs]

    metrics["ebitda_margin"] = ebitda_margin
    metrics["roe"]           = roe
    metrics["fcf_margin"]    = fcf_margin

    # Growth: use CAGR helper across revenue and ebitda series
    def _cagr(series):
        vals = [v for v in (series or []) if isinstance(v, (int, float)) and v > 0]
        if len(vals) < 2:
            return None
        n = len(vals) - 1
        try:
            return (vals[-1] / vals[0]) ** (1 / n) - 1
        except Exception:
            return None

    rev_cagr    = [_cagr(deeps[s].get("revenue")) for s in slugs]
    ebitda_cagr = [_cagr(deeps[s].get("ebitda")) for s in slugs]
    metrics["rev_cagr"]    = rev_cagr
    metrics["ebitda_cagr"] = ebitda_cagr

    # Stability: inverse std-dev of ROE (more stable = better)
    def _roe_std(slug):
        roe_series = [v for v in (deeps[slug].get("roe") or []) if isinstance(v, (int, float))]
        if len(roe_series) < 3:
            return None
        try:
            return _stat.stdev(roe_series)
        except Exception:
            return None

    roe_std = [_roe_std(s) for s in slugs]
    metrics["roe_stability"] = [(-v if isinstance(v, (int, float)) else None) for v in roe_std]

    # Leverage: lower ND/EBITDA = better (negate)
    nd_ebitda = [(raw(s, "keystats") or {}).get("nd_ebitda") for s in slugs]
    metrics["leverage_inv"] = [(-v if isinstance(v, (int, float)) else None) for v in nd_ebitda]

    # ── z-score every metric ────────────────────────────────────────────────
    zscores: dict[str, list] = {k: _zscore_series(v) for k, v in metrics.items()}

    # ── composite factor scores (mean of available z-scores) ─────────────────
    factor_groups = {
        "value":     ["ev_ebitda_inv", "pe_inv", "pb_inv", "fcf_yield"],
        "quality":   ["ebitda_margin", "roe", "fcf_margin"],
        "growth":    ["rev_cagr", "ebitda_cagr"],
        "stability": ["roe_stability"],
        "leverage":  ["leverage_inv"],
    }

    for i, slug in enumerate(slugs):
        scores = {}
        for factor, keys in factor_groups.items():
            zvals = [zscores[k][i] for k in keys if isinstance(zscores[k][i], (int, float))]
            scores[factor] = round(sum(zvals) / len(zvals), 3) if zvals else None
        if slug in deeps and deeps[slug]:
            deeps[slug]["factor_scores"] = scores


def fill_peer_comps(companies: list) -> list:
    """Second pass over an assembled payload: attach each company's peers' REAL
    multiples + the peer median to its deep object, so the dashboard's Peers tab
    shows real numbers (not just names). Kept out of deep_from_extracted to avoid
    an assemble→deep→comps→assemble recursion."""
    table = _multiples_table(companies)

    def _median(xs):
        xs = sorted(x for x in xs if isinstance(x, (int, float)))
        if not xs:
            return None
        m = len(xs)
        return round(xs[m // 2] if m % 2 else (xs[m // 2 - 1] + xs[m // 2]) / 2, 1)

    for co in companies:
        d = co.get("deep")
        if not d or not co.get("peers"):
            continue
        peers = [p for p in comps_for({"peers": co["peers"]}, table)
                 if p.get("name") and any(isinstance(p.get(k), (int, float))
                    for k in ("ev_ebitda", "pe", "roe", "ebitda_margin"))]
        if not peers:
            continue
        d["peers"] = peers
        med = {"ev_ebitda": _median(p.get("ev_ebitda") for p in peers),
               "ev_sales": _median(p.get("ev_sales") for p in peers),
               "pe": _median(p.get("pe") for p in peers)}
        d["peer_median_ev_ebitda"] = med["ev_ebitda"]
        d["peer_medians"] = med
        d["valuation_methods"] = _valuation_methods(d, med)
    return companies


def _valuation_methods(d: dict, med: dict) -> list[dict]:
    """Cross-method valuation (H500 standard: DCF + comparable EV/Sales, EV/EBITDA,
    P/E), each as an implied value per share. Peer-median multiple × the subject's
    latest metric, bridged to equity via net debt, ÷ shares. Only real inputs."""
    k = d.get("keystats", {}) or {}
    shares = k.get("shares_m")
    nd = (d.get("net_debt") or [None])[-1]
    ebitda = (d.get("ebitda") or [None])[-1]
    rev = (d.get("revenue") or [None])[-1]
    ni = (d.get("net_income") or [None])[-1]
    out = []
    if d.get("dcf"):
        b = d["dcf"]["base"]["value"]
        out.append({"method": "DCF (base)", "value": b,
                    "lo": d["dcf"]["downside"]["value"], "hi": d["dcf"]["upside"]["value"],
                    "basis": "unlevered FCFF @ WACC"})
    if shares and shares > 0 and nd is not None:
        if med.get("ev_ebitda") and ebitda:
            out.append({"method": "EV/EBITDA (peers)", "value": round((med["ev_ebitda"] * ebitda - nd) / shares, 2),
                        "basis": f"{med['ev_ebitda']}x peer median × EBITDA − net debt"})
        if med.get("ev_sales") and rev:
            out.append({"method": "EV/Sales (peers)", "value": round((med["ev_sales"] * rev - nd) / shares, 2),
                        "basis": f"{med['ev_sales']}x peer median × revenue − net debt"})
        if med.get("pe") and ni and ni > 0:
            out.append({"method": "P/E (peers)", "value": round((med["pe"] * ni) / shares, 2),
                        "basis": f"{med['pe']}x peer median × net income"})
    px = k.get("share_price")
    if px:
        out.append({"method": "Current price", "value": px, "basis": f"market @ {k.get('price_date') or ''}", "ref": True})
    return out


def comps_for(universe_entry: dict, table: dict | None = None) -> list[dict]:
    """Peer comparables (with multiples where the peer is in our universe)."""
    table = all_multiples() if table is None else table
    out = []
    for p in (universe_entry.get("peers") or []):
        tk = (p.get("ticker") if isinstance(p, dict) else "").split("/")[0].split(":")[-1].strip().upper()
        nm = p.get("name") if isinstance(p, dict) else str(p)
        m = table.get(tk)
        if not m:
            for _, mm in table.items():
                a, b = nm.lower(), (mm["name"] or "").lower()
                if a[:12] and (a[:12] in b or b[:12] in a):
                    m = mm
                    break
        if isinstance(p, dict):
            # Use embedded multiples from peer dict when the coverage table entry
            # lacks usable values (or when peer isn't in coverage at all).
            # Normalise alternative key spellings (pe_approx, roe_approx_pct, etc.)
            def _n(keys, default=None):
                for k in keys:
                    v = p.get(k)
                    if isinstance(v, (int, float)):
                        return v
                return default
            has_useful = m and any(isinstance(m.get(k), (int, float))
                                   for k in ("ev_ebitda", "pe", "roe", "ebitda_margin"))
            if not has_useful:
                ev_ebitda = _n(["ev_ebitda", "ev_ebitda_approx"])
                pe        = _n(["pe", "pe_approx", "pe_fwd"])
                roe_raw   = _n(["roe", "roe_approx_pct", "roe_approx"])
                roe       = round(roe_raw / 100, 4) if isinstance(roe_raw, (int, float)) and roe_raw > 1 else roe_raw
                ebitda_margin = _n(["ebitda_margin", "ebitda_margin_pct"])
                if ebitda_margin is not None and ebitda_margin > 1:
                    ebitda_margin = round(ebitda_margin / 100, 4)
                m = {"name": nm, "ticker": tk, "ev_ebitda": ev_ebitda, "pe": pe,
                     "roe": roe, "ebitda_margin": ebitda_margin}
        out.append(m or {"name": nm, "ticker": tk})
    return out


def scenario_values(fins: list, market: dict, scenarios: dict) -> dict:
    """Value per share, upside and stance for every scenario + the active one."""
    price = (market or {}).get("share_price")
    is_bank = scenarios.get("kind") == "bank"
    per = {}
    for name in scenarios.get("order", ["Bull", "Base", "Bear"]):
        a = scenarios["sets"].get(name, {})
        vps = bank_value_per_share(fins, market, a) if is_bank else value_per_share(fins, market, a)
        up = (vps / price - 1) if (vps and price) else None
        per[name] = {"value_per_share": vps, "upside": up, "stance": _stance(up)}
    sel = scenarios.get("selected", "Base")
    return {"kind": scenarios.get("kind", "dcf"), "per_scenario": per, "selected": sel,
            "active": per.get(sel, {}), "price": price}
def _prune_headers(rows: list) -> list:
    """Remove section headers that have no data rows following them."""
    result, i = [], 0
    while i < len(rows):
        if rows[i].get("style") == "H":
            j = i + 1
            while j < len(rows) and rows[j].get("style") == "H":
                j += 1
            if j < len(rows) and rows[j].get("style") != "H":
                result.append(rows[i])
            i = j
        else:
            result.append(rows[i])
            i += 1
    return result

# (key, label, style, sign) — sign=-1 shows cost as deduction; callable=computed subtotal
_IS_ROWS = [
    ("H", "Income Statement", "H", None),
    ("revenue",      "Revenue",            None,   1),
    ("cogs",         "Cost of Sales",      None,  -1),
    ("_gross",       "Gross Profit",       "sub",  lambda f: (f.get("revenue") or 0) - abs(f.get("cogs") or 0)),
    ("opex",         "Operating Expenses", None,  -1),
    ("ebitda",       "EBITDA",             "sub",  1),
    ("da",           "D&A",                None,  -1),
    ("ebit",         "EBIT",               "sub",  1),
    ("interest",     "Net Interest",       None,  -1),
    ("net_income",   "Net Income",         "hl",   1),
    ("eps",          "EPS",                None,   1),
]
_BS_ROWS = [
    ("H", "Balance Sheet", "H", None),
    ("total_assets",   "Total Assets",   None,  1),
    ("total_equity",   "Total Equity",   "sub", 1),
    ("net_debt",       "Net Debt",       None,  1),
]
_CF_ROWS = [
    ("H", "Cash Flow", "H", None),
    ("operating_cf",  "Operating CF",   None,  1),
    ("capex",         "Capex",          None, -1),
    ("fcf",           "Free Cash Flow", "sub", 1),
    ("dividends",     "Dividends Paid", None, -1),
]


def synth_statements(fins: list, currency: str | None = None, unit: str | None = None) -> dict | None:
    """Build a full reported-detail {columns, income_statement, balance_sheet, cash_flow}
    block from the collected granular line items — the same shape the hand-curated
    datasets carry — so an auto-added company shows a bottom-up statement (platform
    Statements tab + Excel Financial_Statements tab), not totals only. Values are the
    reported figures; only structural subtotals with no reported line are summed."""
    fins = [f for f in (fins or []) if f.get("fy")]
    if not fins:
        return None
    fins = sorted(fins, key=lambda f: f.get("fy", ""))
    cols = [f["fy"] for f in reversed(fins)]           # newest-first, to match the header
    by_fy = {f["fy"]: f for f in fins}

    def build(spec):
        rows = []
        for key, label, style, sign in spec:
            if key == "H":
                rows.append({"label": label, "style": "H"})
                continue
            vals, seen = [], False
            for fy in cols:
                f = by_fy[fy]
                if callable(sign):
                    v = sign(f)                                  # computed subtotal
                else:
                    v = f.get(key)
                    if isinstance(v, (int, float)) and sign == -1:
                        v = -abs(v)                              # show a source-positive cost as a deduction
                if isinstance(v, (int, float)):
                    seen = True
                    vals.append(round(v, 1))
                else:
                    vals.append(None)
            if seen:
                r = {"label": label, "style": style or None, "values": vals}
                if not callable(sign):        # a reported line maps to an editable item; computed subtotals do not
                    r["item"] = key
                rows.append(r)
        return _prune_headers(rows)

    inc, bs, cf = build(_IS_ROWS), build(_BS_ROWS), build(_CF_ROWS)
    if not (inc or bs or cf):
        return None
    basis = f"{currency or ''} {unit or 'millions'}".strip()
    return {
        "basis": basis, "columns": cols,
        "income_statement": inc, "balance_sheet": bs, "cash_flow": cf,
        "source": "Synthesised bottom-up from the collected line items (public aggregator).",
        "note": "Reported figures; expense lines shown as deductions. Subtotals are as reported "
                "except structural totals (non-current assets, financing, net change) which are summed. "
                "Blank (–) = line not separately disclosed that year.",
        "synthesised": True,
    }


_FIN_NESTED_MAP = {
    # income_statement sub-keys → flat keys
    "income_statement": {
        "revenue": "revenue", "gross_profit": "gross_profit", "ebit": "ebit",
        "ebitda": "ebitda", "net_income": "net_income", "da": "da",
        "interest_expense": "interest_expense", "tax": "tax", "eps_diluted": "eps",
        "operating_income": "ebit",
    },
    # balance_sheet sub-keys → flat keys
    "balance_sheet": {
        "total_assets": "total_assets", "total_equity": "total_equity",
        "cash": "cash", "total_debt": "total_debt",
        "net_debt": "net_debt",
    },
    # cash_flow_statement sub-keys → flat keys
    "cash_flow_statement": {
        "operating_cash_flow": "operating_cash_flow",
        "capex": "capex", "free_cash_flow": "free_cash_flow",
        "dividends_paid": "dividends_paid",
    },
}


def _flatten_fin_record(rec: dict) -> dict:
    """Flatten a nested financial record {income_statement:{...}, balance_sheet:{...}} to flat keys."""
    if not any(k in rec for k in _FIN_NESTED_MAP):
        return rec
    flat = {k: v for k, v in rec.items() if k not in _FIN_NESTED_MAP}
    for section, mapping in _FIN_NESTED_MAP.items():
        sub = rec.get(section) or {}
        for src_key, dst_key in mapping.items():
            if sub.get(src_key) is not None and flat.get(dst_key) is None:
                flat[dst_key] = sub[src_key]
    # Derive net_debt if not present
    if flat.get("net_debt") is None and flat.get("total_debt") is not None and flat.get("cash") is not None:
        flat["net_debt"] = flat["total_debt"] - flat["cash"]
    return flat


def _ensure_statements(ext: dict) -> dict:
    """Attach a synthesised bottom-up statements block when the dataset has financials
    but no hand-authored one (auto-added companies). Shallow-copies so the caller's
    stored record is never mutated; hand-curated statements blocks are left untouched."""
    if not ext:
        return {}
    # Flatten any nested financial records (income_statement/balance_sheet sub-objects)
    fins = ext.get("financials")
    if fins and any(k in (fins[0] if fins else {}) for k in _FIN_NESTED_MAP):
        ext = {**ext, "financials": [_flatten_fin_record(f) for f in fins]}
    if ext.get("statements") or not ext.get("financials"):
        return ext
    syn = synth_statements(ext.get("financials"), ext.get("currency"), ext.get("unit"))
    return {**ext, "statements": syn} if syn else ext


def merged_extracted(extracted: dict) -> dict:
    """Apply analyst overrides on top of the sourced record, without mutating it.

    Collected / approved figures are the immutable baseline; deliberate analyst edits
    live in extracted['overrides'] and are layered here at read time. Every read
    surface — the deep object, the scenarios/DCF, the Excel export — goes through this,
    so an edit shows everywhere at once and a reset is simply dropping the overrides.
    Overridden (fy, item) pairs and market fields are tagged in '_edited' so the UI can
    mark them and offer a per-cell revert. Structure of extracted['overrides']:
        {"financials": {"FY2024": {"revenue": 1.5e6, ...}}, "market": {"share_price": 71.0}}
    """
    ov = (extracted or {}).get("overrides") or {}
    fov, mov = ov.get("financials") or {}, ov.get("market") or {}
    if not (fov or mov):
        return _ensure_statements(extracted or {})
    ext = copy.deepcopy(extracted)
    edited_fin: dict[str, list] = {}
    if fov:
        by_fy = {f.get("fy"): f for f in ext.get("financials", [])}
        for fy, items in fov.items():
            f = by_fy.get(fy)
            if f is None:  # an edit can add a fiscal year the collector never returned
                f = {"fy": fy, "source": "analyst edit", "confidence": 1.0}
                ext.setdefault("financials", []).append(f)
                by_fy[fy] = f
            for item, val in items.items():
                if val is None:
                    continue
                f[item] = val
                edited_fin.setdefault(fy, []).append(item)
        # keep net debt consistent when the analyst edits debt or cash (unless net debt
        # itself was edited) — mirrors the sourced merge so the balance sheet stays tied.
        for f in ext.get("financials", []):
            if "net_debt" in (fov.get(f.get("fy")) or {}):
                continue
            if f.get("total_debt") is not None and f.get("cash") is not None:
                f["net_debt"] = f["total_debt"] - f["cash"]
        ext["financials"] = sorted(ext.get("financials", []), key=lambda f: f.get("fy", ""))
    edited_mkt: list = []
    if mov:
        m = dict(ext.get("market") or {})
        for k, v in mov.items():
            if v is None:
                continue
            m[k] = v
            edited_mkt.append(k)
        ext["market"] = m
    ext["_edited"] = {"financials": edited_fin, "market": edited_mkt}
    return _ensure_statements(ext)


