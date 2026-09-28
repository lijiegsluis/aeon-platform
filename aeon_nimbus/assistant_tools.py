"""What the assistant can actually do.

Six tools made a demonstration. An analyst asks things like "which banks earn
more than 20% on equity", "how fast has M-PESA grown", "compare Safaricom and
MTN on margin", "where did that number come from" and "put the price up to
36.20" — so those are the tools.

Two lines run through all of it.

READS ARE FREE, WRITES ARE PROPOSALS. Everything here that computes, compares,
screens or explains runs on request. Nothing here writes. The write path is a
proposal the analyst approves, and it is recorded against them.

FIGURES CARRY THEIR PROVENANCE. Where a figure came from a filing, the tool
returns the page. Where the platform calculated it, the tool says so. The
assistant is told never to state a figure a tool did not return, and these
return enough that it never needs to.
"""

from __future__ import annotations

from typing import Any

from aeon_nimbus import db as D

# Metrics an analyst names in a sentence, mapped onto what the record calls them.
METRICS = {
    "revenue": "revenue", "sales": "revenue", "turnover": "revenue",
    "ebitda": "ebitda", "ebit": "ebit", "operating profit": "ebit",
    "net income": "net_income", "profit": "net_income", "earnings": "net_income",
    "pat": "profit_after_tax", "profit after tax": "profit_after_tax",
    "operating cash flow": "operating_cash_flow", "ocf": "operating_cash_flow",
    "capex": "capex", "capital expenditure": "capex",
    "free cash flow": "free_cash_flow", "fcf": "free_cash_flow",
    "total assets": "total_assets", "assets": "total_assets",
    "equity": "total_equity", "total equity": "total_equity",
    "debt": "total_debt", "total debt": "total_debt",
    "cash": "cash", "net debt": "net_debt",
    "dividends": "dividends_paid", "dividends paid": "dividends_paid",
}

# Ratios the platform can work out from what it holds. Each says how, so the
# assistant can repeat the definition rather than imply a house convention.
# numerator, denominator, definition, and HOW IT IS READ. The last one matters:
# a bare 2.9478 for return on equity told a reader nothing, and the assistant
# reported exactly that because the tool handed it a naked decimal. A ratio the
# desk quotes as a percentage comes back as a percentage.
RATIOS = {
    "ebitda margin": ("ebitda", "revenue", "EBITDA divided by revenue", "%"),
    "ebit margin": ("ebit", "revenue", "EBIT divided by revenue", "%"),
    "net margin": ("net_income", "revenue", "net income divided by revenue", "%"),
    "roe": ("net_income", "total_equity", "net income divided by total equity", "%"),
    "roa": ("net_income", "total_assets", "net income divided by total assets", "%"),
    "capex intensity": ("capex", "revenue", "capital expenditure divided by revenue", "%"),
    "net debt to ebitda": ("net_debt", "ebitda", "net debt divided by EBITDA", "x"),
    "gearing": ("total_debt", "total_equity", "total debt divided by total equity", "x"),
}


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _canon_metric(name: str) -> str | None:
    n = (name or "").strip().lower()
    return METRICS.get(n) or (n if n in set(METRICS.values()) else None)


def build(db) -> dict[str, Any]:
    """Return the callable tools, bound to this database session."""

    # ---------------------------------------------------------------- finding
    def _all():
        return db.query(D.Company).order_by(D.Company.name).all()

    def _norm(x: str) -> str:
        """Slugs differ only by their separator often enough to matter.

        The model writes "absa-group" where the record says "absa_group", and an
        exact match returned nothing: the page link built from it pointed at a
        company that did not exist. Spaces, hyphens and underscores are all the
        same character for the purpose of finding a company.
        """
        return "".join(ch for ch in (x or "").lower() if ch.isalnum())

    def _find(text: str):
        t = (text or "").strip().lower()
        n = _norm(t)
        rows = _all()
        for c in rows:
            if t in ((c.slug or "").lower(), (c.ticker or "").lower()):
                return c
        for c in rows:
            if n and n in (_norm(c.slug), _norm(c.ticker), _norm(c.name)):
                return c
        return next((c for c in rows if t and t in (c.name or "").lower()), None)

    def _deep(c):
        from aeon_nimbus import platform_data as pdata
        try:
            return pdata.deep_from_extracted(c.extracted or {}, c.universe or {}) or {}
        except Exception:
            return {}

    def _series(c, metric):
        """(years, values) for one metric, from the reported financials."""
        fins = sorted([f for f in ((c.extracted or {}).get("financials") or [])
                       if f.get("fy")], key=lambda x: str(x["fy"]))
        key = _canon_metric(metric)
        if not key:
            return [], []
        return [str(f["fy"]) for f in fins], [_num(f.get(key)) for f in fins]

    SECTOR_WORDS = {
        "bank": "financials", "banks": "financials", "banking": "financials",
        "lender": "financials", "lenders": "financials",
        "telco": "telecommunications", "telcos": "telecommunications",
        "telecom": "telecommunications", "telecoms": "telecommunications",
        "brewer": "consumer", "brewers": "consumer", "cement": "materials",
        "oil": "energy", "miner": "materials", "retailer": "consumer",
    }

    # ------------------------------------------------------------------ reads
    def search_companies(query: str = ""):
        import re as _re
        ql = (query or "").lower().strip()
        for word in _re.findall(r"[a-z]+", ql):
            if word in SECTOR_WORDS:
                ql = SECTOR_WORDS[word]
                break
        out = []
        for c in _all():
            hay = " ".join(str(x or "") for x in
                           (c.name, c.ticker, c.sector, c.country, c.exchange)).lower()
            if not ql or ql in hay:
                out.append({"name": c.name, "slug": c.slug, "ticker": c.ticker,
                            "sector": c.sector, "country": c.country})
        return {"count": len(out), "results": out[:40]}

    def get_company(slug_or_ticker: str = ""):
        c = _find(slug_or_ticker)
        if not c:
            return {"error": f"no company matching {slug_or_ticker!r} is in coverage"}
        ex = c.extracted or {}
        # sub_sector and notes are the only text the platform holds about what a
        # company actually DOES, and this tool used to leave both out. Asked
        # "what does Absa Group do", the model spent four calls hunting for a
        # description tool that does not exist, then answered from the sector
        # alone. The line was sitting on the page the whole time.
        return {"name": c.name, "slug": c.slug, "ticker": c.ticker, "sector": c.sector,
                "business": getattr(c, "sub_sector", None) or c.sector,
                "country": c.country, "exchange": c.exchange,
                "currency": ex.get("currency"), "unit": ex.get("unit"),
                # The reported business lines, which is the platform's own
                # answer to "what does this company do" and is sourced rather
                # than remembered: M-PESA revenue, CIB headline earnings, and
                # so on, each with its figure.
                "segments": [
                    {k: v for k, v in seg.items() if k in ("name", "value", "unit", "fy")}
                    for seg in ((ex.get("notes") or {}).get("segments") or [])][:12],
                "annual_report": ex.get("annual_report_url"),
                "data_quality": (ex.get("data_quality") or {}).get("grade"),
                "financials_by_year": [{k: v for k, v in f.items() if k != "source"}
                                       for f in (ex.get("financials") or [])],
                "market": ex.get("market") or {},
                "note": "State the fiscal year with any figure. 'business' is the "
                        "platform's own description of what this company does; it is "
                        "all the platform holds, so do not add to it from memory."}

    def get_metric(slug_or_ticker: str = "", metric: str = ""):
        """One metric across every year, with growth."""
        c = _find(slug_or_ticker)
        if not c:
            return {"error": f"no company matching {slug_or_ticker!r}"}
        years, vals = _series(c, metric)
        if not years:
            return {"error": f"{metric!r} is not a metric the platform holds. "
                             f"It holds: {', '.join(sorted(set(METRICS.values())))}"}
        yoy = []
        for i in range(1, len(vals)):
            a, b = vals[i - 1], vals[i]
            yoy.append(round((b / a - 1) * 100, 1) if (a and b and a != 0) else None)
        first, last = next((v for v in vals if v), None), next(
            (v for v in reversed(vals) if v), None)
        cagr = None
        n = len(([v for v in vals if v])) - 1
        if first and last and n > 0 and first > 0:
            cagr = round(((last / first) ** (1 / n) - 1) * 100, 1)
        return {"company": c.name, "metric": metric, "currency": (c.extracted or {}).get("currency"),
                "unit": (c.extracted or {}).get("unit"),
                "years": years, "values": vals, "yoy_percent": yoy, "cagr_percent": cagr}

    def get_ratio(slug_or_ticker: str = "", ratio: str = ""):
        c = _find(slug_or_ticker)
        if not c:
            return {"error": f"no company matching {slug_or_ticker!r}"}
        r = (ratio or "").strip().lower()
        if r not in RATIOS:
            return {"error": f"{ratio!r} is not one the platform computes. "
                             f"It computes: {', '.join(RATIOS)}"}
        num_k, den_k, how, unit = RATIOS[r]
        years, num = _series(c, num_k)
        _, den = _series(c, den_k)
        raw = [(n / d) if (n is not None and d) else None for n, d in zip(num, den)]
        # Formatted here rather than left to the model. It has no way to know
        # whether 2.9478 is 2.9x or 295%, and it guessed wrong.
        shown = [None if v is None else
                 (f"{v * 100:,.1f}%" if unit == "%" else f"{v:,.2f}x") for v in raw]
        latest = next(((y, s_) for y, s_ in zip(reversed(years), reversed(shown)) if s_), None)
        return {"company": c.name, "ratio": r, "definition": how, "unit": unit,
                "years": years, "values": shown, "raw": raw,
                "latest": {"fy": latest[0], "value": latest[1]} if latest else None,
                "note": "Calculated by the platform from reported figures, not a reported "
                        "ratio. Quote the values as they are written here, with the "
                        "percent or the x."}

    def compare(companies: str = "", metric: str = "revenue"):
        """Several companies side by side on one metric, latest year."""
        names = [x.strip() for x in (companies or "").split(",") if x.strip()]
        rows = []
        for n in names:
            c = _find(n)
            if not c:
                rows.append({"asked_for": n, "error": "not in coverage"})
                continue
            years, vals = _series(c, metric)
            latest = next(((y, v) for y, v in zip(reversed(years), reversed(vals)) if v), None)
            rows.append({"company": c.name, "slug": c.slug, "currency": (c.extracted or {}).get("currency"),
                         "fy": latest[0] if latest else None,
                         "value": latest[1] if latest else None})
        return {"metric": metric, "rows": rows,
                "warning": "Currencies differ between companies. Do not add or rank "
                           "across different currencies without saying so."}

    def _as_ratio(x):
        """An analyst says "20%" and means 0.20. The stored ratios are decimals,
        so a bare 20 compared against 0.2207 excluded every bank that qualified
        and the answer came back "we cover no banks with ROE above 20%"."""
        if x is None:
            return None
        v = float(x)
        return v / 100.0 if abs(v) > 1.5 else v

    def screen(sector: str = "", country: str = "", ratio: str = "",
               minimum: float | None = None, maximum: float | None = None):
        """Filter coverage. e.g. banks with ROE above 20%."""
        minimum, maximum = _as_ratio(minimum), _as_ratio(maximum)
        import re as _re
        s = (sector or "").lower().strip()
        for word in _re.findall(r"[a-z]+", s):
            if word in SECTOR_WORDS:
                s = SECTOR_WORDS[word]
                break
        hits = []
        for c in _all():
            if s and s not in (c.sector or "").lower():
                continue
            if country and country.lower() not in (c.country or "").lower():
                continue
            entry = {"company": c.name, "slug": c.slug, "sector": c.sector,
                     "country": c.country}
            if ratio:
                rr = get_ratio(c.slug, ratio)
                # "raw", not "values": the latter carries a percent sign for the
                # reader and cannot be compared with a number.
                vals = [v for v in (rr.get("raw") or []) if v is not None]
                if not vals:
                    continue
                latest = vals[-1]
                if minimum is not None and latest < minimum:
                    continue
                if maximum is not None and latest > maximum:
                    continue
                # As the reader will see it, for the same reason as above.
                u = RATIOS[(ratio or "").strip().lower()][3]
                entry[ratio] = (f"{latest * 100:,.1f}%" if u == "%" else f"{latest:,.2f}x")
            hits.append(entry)
        return {"count": len(hits), "results": hits,
                "criteria": {"sector": sector, "country": country, "ratio": ratio,
                             "minimum": minimum, "maximum": maximum}}

    def rank(metric: str = "revenue", sector: str = "", top: int = 10,
             ascending: bool = False):
        rows = []
        for c in _all():
            if sector and sector.lower() not in (c.sector or "").lower():
                continue
            years, vals = _series(c, metric)
            latest = next((v for v in reversed(vals) if v), None)
            if latest is None:
                continue
            rows.append({"company": c.name, "slug": c.slug, "value": latest,
                         "currency": (c.extracted or {}).get("currency"),
                         "fy": next((y for y, v in zip(reversed(years), reversed(vals))
                                     if v), None)})
        rows.sort(key=lambda x: x["value"], reverse=not ascending)
        return {"metric": metric, "results": rows[:max(1, min(int(top or 10), 40))],
                "warning": "Currencies differ. Ranking across currencies is only "
                           "meaningful if they are converted first; say so if you use it."}

    def get_statements(slug_or_ticker: str = "", statement: str = "balance_sheet"):
        """The detailed lines read out of the filing, with the page."""
        c = _find(slug_or_ticker)
        if not c:
            return {"error": f"no company matching {slug_or_ticker!r}"}
        st = ((c.extracted or {}).get("statements") or {})
        rows = st.get(statement) or []
        if not rows:
            return {"company": c.name, "statement": statement, "lines": [],
                    "note": "No detailed statement has been extracted for this company yet. "
                            "Say so rather than using the summary figures as if they were "
                            "the filed statement."}
        return {"company": c.name, "statement": statement,
                "lines": [{"label": r.get("label"), "values": r.get("values"),
                           "page": r.get("page"), "as_filed": r.get("raw_label"),
                           "source": r.get("source")} for r in rows[:40]],
                "note": "Read from the filing. 'page' is the page it was read from."}

    def get_valuation(slug_or_ticker: str = ""):
        c = _find(slug_or_ticker)
        if not c:
            return {"error": f"no company matching {slug_or_ticker!r}"}
        d = _deep(c)
        return {"company": c.name, "slug": c.slug, "rating": d.get("rating"),
                "methods": d.get("valuation_methods"), "wacc_build": d.get("wacc_build"),
                "peer_medians": d.get("peer_medians"),
                "note": "If rating is absent the company is NOT RATED. Say that rather than guess."}

    def get_controls(slug_or_ticker: str = ""):
        c = _find(slug_or_ticker)
        if not c:
            return {"error": f"no company matching {slug_or_ticker!r}"}
        d = _deep(c)
        return {"company": c.name, "controls": d.get("qc"), "caveats": d.get("caveats"),
                "confidence": d.get("confidence")}

    def get_peers(slug_or_ticker: str = ""):
        c = _find(slug_or_ticker)
        if not c:
            return {"error": f"no company matching {slug_or_ticker!r}"}
        d = _deep(c)
        return {"company": c.name, "peers": d.get("peers") or (c.universe or {}).get("peers"),
                "peer_medians": d.get("peer_medians")}

    def get_sources(slug_or_ticker: str = ""):
        """Where this company's figures came from."""
        c = _find(slug_or_ticker)
        if not c:
            return {"error": f"no company matching {slug_or_ticker!r}"}
        ex = c.extracted or {}
        fins = ex.get("financials") or []
        return {"company": c.name,
                "annual_report": ex.get("annual_report_url") or (c.universe or {}).get("annual_report_url"),
                "per_year": [{"fy": f.get("fy"), "source": f.get("source"),
                              "confidence": f.get("confidence")} for f in fins],
                "data_quality": ex.get("data_quality")}

    def get_history(slug_or_ticker: str = "", limit: int = 20):
        """What has been changed recently, and by whom."""
        q = db.query(D.AuditLog)
        if slug_or_ticker:
            c = _find(slug_or_ticker)
            if c:
                q = q.filter(D.AuditLog.entity == "company",
                             D.AuditLog.entity_id == str(c.id))
        rows = q.order_by(D.AuditLog.ts.desc()).limit(min(int(limit or 20), 60)).all()
        return {"results": [{"when": r.ts.isoformat() if r.ts else None,
                             "who": r.actor, "action": r.action,
                             "detail": r.detail} for r in rows]}

    def list_metrics():
        """So the assistant can say what it is able to look up."""
        return {"metrics": sorted(set(METRICS.values())),
                "ratios": sorted(RATIOS),
                "statements": ["income_statement", "balance_sheet", "cash_flow"]}

    return {
        "search_companies": search_companies,
        "get_company": get_company,
        "get_metric": get_metric,
        "get_ratio": get_ratio,
        "compare": compare,
        "screen": screen,
        "rank": rank,
        "get_statements": get_statements,
        "get_valuation": get_valuation,
        "get_controls": get_controls,
        "get_peers": get_peers,
        "get_sources": get_sources,
        "get_history": get_history,
        "list_metrics": list_metrics,
        "_resolve_slug": lambda t: (_find(t).slug if _find(t) else None),
    }
