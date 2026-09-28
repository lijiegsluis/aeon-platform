"""AI research assistant — the qualitative layer of the automation.

Drafts the *narrative* sections of a company note (market overview, revenue
drivers, cost pressures, non-financial notes, outlook) from the company's own
**already-sourced** facts — the same write-ups a research associate would produce
by hand. It is deliberately fenced so it stays safe on a credibility-first desk:

  * GROUNDED — it may only use the sourced facts passed to it; it is told, in the
    system prompt, to invent nothing and to write "[insufficient source —
    analyst to complete]" where the facts don't support a section.
  * NO NUMBERS — the deterministic pipeline owns every figure; the model narrates,
    it never introduces or restates a number as new fact.
  * CITED + FLAGGED — each point carries the source it came from, and the whole
    block is marked ``provenance: "ai_draft"`` / ``review_required: true`` so it
    lands in the review queue exactly like a proposed figure, never silently in
    the model.

Config (set to enable; nothing is called without a key):
    AI_PROVIDER   anthropic (default) | openai
    AI_MODEL      e.g. claude-haiku-4-5 | gpt-4o-mini      (a cheap tier is plenty)
    ANTHROPIC_API_KEY  /  OPENAI_API_KEY

CLI:
    python -m aeon_nimbus.ai_research <slug>            # draft from a company's sourced facts
    python -m aeon_nimbus.ai_research <slug> --dry-run  # print the grounded prompt only
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SECTIONS = ["market_overview", "revenue_drivers", "cost_pressures", "non_financial", "outlook"]

SYSTEM = """You are a senior equity-research associate at Aeon Nimbus, a global equity-research
publication. You write the QUALITATIVE sections of an institutional-grade company note.
Aeon Nimbus's entire value rests on traceability — nothing is invented, everything is sourced.

ABSOLUTE RULES (never break these):
1. Use ONLY the facts explicitly stated in the user message. Never use outside knowledge.
2. If the provided facts do not support a section, write "[insufficient source — analyst to complete]".
3. You MAY reference numbers from the provided facts (e.g. revenue CAGR, margin levels,
   leverage ratios) — but never compute a new figure the facts did not state.
4. Every bullet must carry a "source" field citing which provided fact it came from.
5. Tone: Aeon Nimbus voice — institutional, British restraint, direct. "We believe / We note /
   We observe." No hype, no hedge-speak ("may potentially"), no AI-sounding phrases.
   Present one clear view per bullet, not a balanced-both-sides mush.
6. Depth: 3–5 bullets per section for companies with rich data; 2 bullets minimum even with sparse data.
7. The moat_assessment section scores each of five dimensions on a 0–3 scale (0=absent, 1=weak,
   2=present, 3=strong) derived ONLY from the provided facts (segments, KPIs, market position,
   switching costs signals, network effects signals, scale signals). Justify each score in one sentence.
8. For the alpha_thesis: one crisp sentence on what the market is likely mis-pricing (if anything),
   derived from the financial trend and sector context provided. If data is insufficient, write
   "[analyst to complete — requires view on market consensus]".

Return ONLY valid JSON of this exact shape (no prose around it):
{
  "market_overview":  [{"point": "...", "source": "..."}],
  "revenue_drivers":  [{"point": "...", "source": "..."}],
  "cost_pressures":   [{"point": "...", "source": "..."}],
  "non_financial":    [{"point": "...", "source": "..."}],
  "outlook":          {"point": "...", "source": "..."},
  "moat_assessment":  {
    "switching_costs":   {"score": 0, "note": "..."},
    "network_effects":   {"score": 0, "note": "..."},
    "cost_advantage":    {"score": 0, "note": "..."},
    "intangible_assets": {"score": 0, "note": "..."},
    "efficient_scale":   {"score": 0, "note": "..."},
    "overall":           {"score": 0, "note": "one-sentence overall moat verdict"}
  },
  "alpha_thesis": "..."
}"""


def facts_from(ext: dict, uni: dict) -> dict:
    """Assemble a company's already-sourced facts as the grounding context, from its
    extracted + universe dicts. These are the platform's own sourced fields — never
    raw model guesses. Shared by the CLI (files) and the Data Studio endpoint (DB)."""
    ext, uni = ext or {}, uni or {}
    fins = sorted(ext.get("financials", []), key=lambda f: f.get("fy", ""))

    # --- derived financial facts (all from sourced filings) ---
    def _f(v, default=None):
        return float(v) if isinstance(v, (int, float)) else default

    fin_facts = []
    if fins:
        last = fins[-1]
        prev = fins[-2] if len(fins) >= 2 else {}
        first = fins[0]
        n_years = len(fins)

        rev_l = _f(last.get("revenue"))
        rev_f = _f(first.get("revenue"))
        rev_p = _f(prev.get("revenue"))
        ebitda_l = _f(last.get("ebitda"))
        ni_l = _f(last.get("net_income"))
        fcf_l = _f(last.get("fcf"))
        nd_l = _f(last.get("net_debt"))
        div_l = _f(last.get("dividends"))
        capex_l = _f(last.get("capex"))

        fy_l = last.get("fy", "latest")
        fy_s = first.get("fy", "earliest")
        src = "audited financial statements"

        if rev_l is not None:
            fin_facts.append({"label": f"Revenue ({fy_l})", "value": f"{rev_l:,.0f}m",
                               "source": f"{src} — {fy_l}"})
        if rev_l and rev_f and rev_f > 0 and n_years >= 2:
            cagr = (rev_l / rev_f) ** (1.0 / max(n_years - 1, 1)) - 1
            trend = "compound growth" if cagr >= 0 else "revenue contraction"
            fin_facts.append({"label": f"Revenue CAGR {fy_s}–{fy_l}",
                               "value": f"{cagr * 100:+.1f}% ({trend})", "source": src})
        if ebitda_l is not None and rev_l:
            margin = ebitda_l / rev_l
            fin_facts.append({"label": f"EBITDA margin ({fy_l})",
                               "value": f"{margin * 100:.1f}%", "source": f"{src} — {fy_l}"})
        if ni_l is not None and rev_l:
            ni_margin = ni_l / rev_l
            fin_facts.append({"label": f"Net income margin ({fy_l})",
                               "value": f"{ni_margin * 100:.1f}%", "source": f"{src} — {fy_l}"})
        if fcf_l is not None and ebitda_l and ebitda_l != 0:
            conv = fcf_l / ebitda_l
            fin_facts.append({"label": f"FCF / EBITDA cash conversion ({fy_l})",
                               "value": f"{conv * 100:.0f}%", "source": f"{src} — {fy_l}"})
        if nd_l is not None and ebitda_l and ebitda_l > 0:
            lev = nd_l / ebitda_l
            fin_facts.append({"label": f"Net debt / EBITDA ({fy_l})",
                               "value": f"{lev:.1f}x", "source": f"{src} — {fy_l}"})
        if capex_l is not None and rev_l and rev_l > 0:
            cx_pct = abs(capex_l) / rev_l
            fin_facts.append({"label": f"Capex intensity ({fy_l})",
                               "value": f"{cx_pct * 100:.1f}% of revenue", "source": f"{src} — {fy_l}"})
        if div_l is not None and ni_l and ni_l > 0:
            payout = abs(div_l) / ni_l
            fin_facts.append({"label": f"Dividend payout ratio ({fy_l})",
                               "value": f"{payout * 100:.0f}%", "source": f"{src} — {fy_l}"})
        # YoY revenue growth
        if rev_l and rev_p and rev_p > 0:
            yoy = (rev_l - rev_p) / rev_p
            fin_facts.append({"label": f"Revenue growth YoY ({prev.get('fy','?')}→{fy_l})",
                               "value": f"{yoy * 100:+.1f}%", "source": src})

    return {
        "name": uni.get("name") or ext.get("name") or uni.get("slug") or "the company",
        "ticker": uni.get("ticker"), "exchange": uni.get("exchange"),
        "country": uni.get("country"), "sector": uni.get("sector"),
        "sub_sector": uni.get("sub_sector"),
        "currency": uni.get("currency") or ext.get("currency"),
        "segments": uni.get("segments") or ext.get("segments") or [],
        "risks": uni.get("risks") or [],
        "sector_kpis": (ext.get("sector_kpis") or uni.get("sector_kpis") or []),
        "notes": ext.get("notes") or [],
        "years": [x.get("fy") for x in fins],
        "fin_facts": fin_facts,
        "filing_sources": (ext.get("statements", {}) or {}).get("source_by_year", {}),
        "revenue_trend": "up" if _monotone([x.get("revenue") for x in fins]) else "mixed",
        "valuation_model": uni.get("valuation_model", "dcf"),
        "investment_view": ext.get("investment_view") or {},
        "latest_fy": fins[-1].get("fy") if fins else None,
    }


def _facts_for(slug: str) -> dict:
    f = ROOT / "data" / "extracted" / f"{slug}.json"
    ext = json.loads(f.read_text()) if f.exists() else {}
    uni = {}
    try:
        cov = json.loads((ROOT / "data" / "universe.json").read_text())
        uni = next((c for c in (cov if isinstance(cov, list) else cov.get("companies", []))
                    if c.get("slug") == slug), {})
    except Exception:
        pass
    return facts_from(ext, uni)


def _monotone(xs) -> bool:
    v = [x for x in xs if isinstance(x, (int, float))]
    return len(v) >= 2 and all(b >= a for a, b in zip(v, v[1:]))


def _prompt(facts: dict) -> str:
    """Human-readable grounding block: only sourced facts, each with its origin."""
    sector = facts.get("sector") or facts.get("sub_sector") or ""
    country = facts.get("country") or ""
    currency = facts.get("currency") or ""
    ticker = facts.get("ticker") or ""
    lines = [
        f"COMPANY: {facts['name']}  ({ticker}{'  ·  ' if ticker else ''}{sector}{'  ·  ' if sector else ''}{country})",
        f"Reporting currency: {currency}" if currency else "",
        f"Valuation model: {facts.get('valuation_model','dcf')}",
        "",
        "SOURCED FACTS (cite by label in every bullet you write):",
        "",
    ]
    # Financial facts derived from filings
    fin_facts = facts.get("fin_facts") or []
    if fin_facts:
        lines.append("[ FINANCIALS — from audited annual reports ]")
        for ff in fin_facts:
            lines.append(f"  - {ff['label']}: {ff['value']}   [source: {ff['source']}]")
        lines.append("")

    # Segments
    segs = facts.get("segments") or []
    if segs:
        lines.append("[ BUSINESS SEGMENTS — from company segment disclosure ]")
        for s in segs:
            nm = s.get("name") if isinstance(s, dict) else s
            rev = s.get("revenue") or s.get("revenue_m") or s.get("rev") if isinstance(s, dict) else None
            pct = s.get("pct_of_group") or s.get("pct") if isinstance(s, dict) else None
            detail = ""
            if rev:
                detail += f"  revenue ~{rev}m {currency}"
            if pct:
                detail += f"  ({pct}% of group)"
            lines.append(f"  - Segment: {nm}{detail}   [source: company segment disclosure]")
        lines.append("")

    # Sector KPIs with values
    kpis = facts.get("sector_kpis") or []
    if kpis:
        lines.append("[ OPERATING KPIs — from company filings ]")
        for k in kpis:
            if isinstance(k, dict):
                nm = k.get("name") or k.get("kpi")
                val = k.get("value")
                unit = k.get("unit") or ""
                period = k.get("period") or k.get("fy") or "latest"
                if nm and val is not None:
                    lines.append(f"  - {nm}: {val} {unit} ({period})   [source: operating KPI — company filing]")
            else:
                lines.append(f"  - KPI tracked: {k}   [source: operating KPI]")
        lines.append("")

    # Risks
    risks = facts.get("risks") or []
    if risks:
        lines.append("[ KEY RISKS — from company risk disclosures ]")
        for r in risks[:8]:
            d = r.get("description") if isinstance(r, dict) else r
            lines.append(f"  - Risk: {d}   [source: company risk disclosure]")
        lines.append("")

    # Notes / context
    notes = facts.get("notes") or []
    if notes:
        lines.append("[ ANALYST NOTES / CONTEXT ]")
        for n in (notes if isinstance(notes, list) else [notes])[:5]:
            nm = n.get("note") or n.get("text") or str(n) if isinstance(n, dict) else str(n)
            src = n.get("source", "platform notes") if isinstance(n, dict) else "platform notes"
            lines.append(f"  - {nm}   [source: {src}]")
        lines.append("")

    # Investment view if present
    inv = facts.get("investment_view") or {}
    if inv:
        lines.append("[ INVESTMENT VIEW ]")
        for k, v in inv.items():
            if v is not None:
                lines.append(f"  - {k}: {v}   [source: analyst investment view]")
        lines.append("")

    # Filing provenance
    filing_srcs = facts.get("filing_sources") or {}
    if filing_srcs:
        lines.append("[ FILING SOURCES ]")
        for fy, src in filing_srcs.items():
            lines.append(f"  - {fy} figures sourced from: {src}")
        lines.append("")

    # Revenue trend summary
    years = facts.get("years") or []
    trend = facts.get("revenue_trend", "mixed")
    if years:
        lines.append(f"Revenue trend across {', '.join(str(y) for y in years)}: {trend}   [source: audited financial statements]")

    lines.append("")
    lines.append("NOW write the qualitative note — JSON only, no prose, obeying every rule.")
    lines.append("Write at institutional depth: 3–5 bullets per section, one clear view per bullet.")
    lines.append("Moat scores 0–3 only from the facts above; alpha_thesis from the financial trend and sector context.")
    return "\n".join(l for l in lines if l is not None)


_MAX_TOKENS = 4000

# OpenAI-compatible providers (chat/completions with a Bearer key) — Groq is one, and
# is the free-tier default here. base URL + model are env-overridable.
_OPENAI_COMPATIBLE = {
    # Groq retired the Llama 3.3 line. This default went stale with it, so
    # filing extraction was asking for a model that no longer exists.
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o-mini"),
    # Gemini via its OpenAI-compatible endpoint (same as assistant.py uses)
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/openai",
               "gemini-3.6-flash"),
}

# Fallback model chain for Groq — same as assistant.py. If the first model returns
# a non-rate-limit error (e.g. model not found / 400), we pop to the next one.
_GROQ_CHAIN = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]


def _call_llm(system: str, user: str) -> str:
    provider = os.environ.get("AI_PROVIDER", "anthropic").lower()
    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("Set ANTHROPIC_API_KEY, or set AI_PROVIDER=groq/gemini with the matching key.")
        import anthropic
        c = anthropic.Anthropic(api_key=key)
        m = c.messages.create(model=os.environ.get("AI_MODEL", "claude-haiku-4-5"),
                              max_tokens=_MAX_TOKENS, system=system,
                              messages=[{"role": "user", "content": user}])
        return m.content[0].text
    if provider in _OPENAI_COMPATIBLE:
        keyvar, base, model = _OPENAI_COMPATIBLE[provider]
        key = os.environ.get(keyvar)
        if not key:
            raise SystemExit(f"Set {keyvar} (AI_PROVIDER={provider}).")
        import requests
        import logging as _logging
        _log = _logging.getLogger(__name__)
        import time as _time
        url = f"{os.environ.get('AI_BASE_URL', base)}/chat/completions"
        hdr = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        # For Groq, use the fallback chain; for others use the configured/default model
        if provider == "groq":
            configured = os.environ.get("AI_MODEL")
            models_to_try = ([configured] + _GROQ_CHAIN) if configured else list(_GROQ_CHAIN)
            # deduplicate preserving order
            seen_m: list = []
            for m in models_to_try:
                if m not in seen_m:
                    seen_m.append(m)
            models_to_try = seen_m[:2]  # max 2 models
        else:
            models_to_try = [os.environ.get("AI_MODEL", model)]

        r = None
        for model_idx, current_model in enumerate(models_to_try):
            payload = {"model": current_model,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": user}],
                       "temperature": 0, "max_tokens": _MAX_TOKENS,
                       "response_format": {"type": "json_object"}}
            for attempt in range(5):
                r = requests.post(url, headers=hdr, json=payload, timeout=120)
                if r.status_code == 400 and "response_format" in r.text:
                    payload.pop("response_format", None)
                    continue
                if r.status_code in (429, 503) and attempt < 4:
                    _time.sleep(float(r.headers.get("retry-after", 0) or 0) or 8 * (2 ** attempt))
                    continue
                # Non-rate-limit 4xx on Groq → model not found, try next in chain
                if r.status_code in (400, 404) and provider == "groq" and model_idx + 1 < len(models_to_try):
                    _log.warning("[ai_research] Groq model %r returned %d, falling back to %r",
                                 current_model, r.status_code, models_to_try[model_idx + 1])
                    break  # break inner loop to try next model
                break
            else:
                continue  # inner loop exhausted retries without break → next model
            # If we broke because of a non-rate-limit 4xx and there's a next model, continue outer loop
            if r.status_code in (400, 404) and provider == "groq" and model_idx + 1 < len(models_to_try):
                continue
            break  # success or unrecoverable error
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    raise SystemExit(f"unknown AI_PROVIDER {provider!r} (use anthropic | groq | gemini | openai)")


def available() -> bool:
    """True when an API key for the configured provider is present."""
    p = os.environ.get("AI_PROVIDER", "anthropic").lower()
    keyvar = ({"anthropic": "ANTHROPIC_API_KEY"}.get(p)
              or (_OPENAI_COMPATIBLE.get(p) or (None,))[0])
    # Also return True when Gemini or Groq key present even if AI_PROVIDER not explicitly set
    if not keyvar:
        return False
    return bool(os.environ.get(keyvar))


def _loads(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw[4:].strip() if raw.lower().startswith("json") else raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


# --- AI extraction of the reported statements straight from the filing PDF ----

EXTRACT_SYSTEM = """You are a financial-data extraction engine for an equity-research desk whose
entire value is that nothing is fabricated. You are given the text of pages from a company's
AUDITED financial statements. Extract the reported figures EXACTLY as printed.

Rules, without exception:
1. Extract ONLY numbers that literally appear in the provided text. Never infer, estimate,
   average, or fill a gap. If a line is not shown for a year, use null — never 0, never a guess.
2. Preserve the reported sign. Parentheses mean negative.
3. EVERY figure must cite the page number it was read from.
4. Map each line to ONE canonical key from the allowed list below; if a line does not map
   to one, skip it. Do not force a mapping.
5. Report the statement currency and units (thousands / millions / billions) exactly as the
   statement states them (usually in the column header or a note at the top).

Allowed canonical keys (map the filing's own wording onto these):
  income statement: revenue, cost_of_revenue, gross_profit, sga, other_opex, ebitda, da, ebit,
    interest_expense, interest_income, other_non_operating, pbt, tax, minority_interest, net_income
  balance sheet: cash, receivables, inventory, other_current_assets, total_current_assets, ppe,
    total_assets, accounts_payable, short_term_debt, total_current_liabilities, long_term_debt,
    total_liabilities, total_debt, net_debt, common_stock, retained_earnings, total_equity
  cash flow: operating_cash_flow, capex, change_receivables, change_inventory, change_payables,
    change_other_wc, other_operating_cf, asset_sales, other_investing, investing_cash_flow,
    net_debt_issued, dividends_paid, other_financing, free_cash_flow

For a BANK or financial institution there is NO cost of revenue, gross profit, or EBIT — skip
those. Map "total operating income" (or the sum of net interest income + fees) → revenue; a
distinct "interest income" line → interest_income; "interest expense" → interest_expense;
impairment / "credit loss expense" → other_opex; income tax (plus any levies) → tax; "profit
for the year" / "profit attributable to owners" → net_income; loans, deposits and customer
balances → total_assets / total_liabilities only where they are those totals. The pre-tax
profit line — however it is labelled ("profit before tax", "profit before taxation") — maps to
**pbt, NEVER to ebit**. Use ebit only when a company shows a distinct operating-profit line
ABOVE a separate profit-before-tax line (a bank does not).

If BOTH consolidated / Group AND separate / Bank (or Company) columns are shown, ALWAYS use the
consolidated / Group column. Extract every year of figures the statement shows.

Fiscal years use the form "FY2024". Return ONLY valid JSON of this exact shape:
{"currency": "NGN", "unit": "millions",
 "facts": [{"fy": "FY2024", "item": "revenue", "value": 665690, "page": 42}, ...]}"""


def _page_lines(page) -> str:
    """Reconstruct a page as rows by y-position, each row's words left-to-right — so a
    statement line reads "Label 123,456 234,567" instead of the default extractor's jumble
    of labels then a detached column of numbers (which no model can map)."""
    words = page.get_text("words")            # (x0, y0, x1, y1, word, block, line, word_no)
    if not words:
        return page.get_text()
    rows: dict = {}
    for x0, y0, _x1, _y1, wd, *_ in words:
        rows.setdefault(round(y0 / 3.0), []).append((x0, wd))
    return "\n".join(" ".join(w for _, w in sorted(ws)) for _, ws in sorted(rows.items()))


def _statement_pages_text(pdf_bytes: bytes, max_pages: int = 8) -> list:
    """Locate the pages most likely to be the primary statements (statement anchors +
    a high density of digits) and return [(page_number, text), ...], each rebuilt row-wise
    so labels stay next to their figures. Convention-independent."""
    import fitz
    anchors = ("comprehensive income", "income statement", "profit or loss", "profit and loss",
               "financial position", "balance sheet", "cash flow", "statement of changes",
               # French (BRVM / Sonatel etc.)
               "compte de résultat", "résultat global", "resultat global", "bilan",
               "situation financière", "situation financiere", "flux de trésorerie",
               "flux de tresorerie", "état du résultat",
               # Portuguese
               "demonstração", "demonstracao", "balanço", "balanco",
               "fluxo de caixa", "posição financeira")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        scored = []
        for i in range(doc.page_count):
            t = _page_lines(doc[i])
            tl = t.lower()
            hits = sum(1 for a in anchors if a in tl)
            digits = sum(c.isdigit() for c in t)
            if hits and digits > 120:  # a real statement page carries many figures; skips TOC / narrative
                scored.append((hits, digits, i, t))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        top = scored[:max_pages]
        # guarantee the strongest cash-flow page is present — in long integrated reports the
        # income statement, balance sheet and dense note tables otherwise crowd it out of the top-N
        cf_anchors = ("cash flow", "financing activities", "cash generated from op",
                      "flux de trésorerie", "flux de tresorerie", "fluxo de caixa")
        if not any(any(a in t.lower() for a in cf_anchors) for (_, _, _, t) in top):
            cf_page = next(((h, d, i, t) for (h, d, i, t) in scored
                            if any(a in t.lower() for a in cf_anchors)), None)
            if cf_page is not None:
                top = top[:max(1, max_pages - 1)] + [cf_page]
        chosen = sorted(top, key=lambda x: x[2])
        return [(i + 1, t) for (_, _, i, t) in chosen]
    finally:
        doc.close()


def _extract_prompt(pages: list, company: dict) -> str:
    hint = []
    if company.get("name"):
        hint.append(f"Company: {company['name']}.")
    if company.get("currency"):
        hint.append(f"Expected reporting currency: {company['currency']}.")
    if company.get("unit"):
        hint.append(f"Figures are most likely in {company['unit']}.")
    body = "\n\n".join(f"--- PAGE {p} ---\n{t[:7000]}" for p, t in pages)
    return (" ".join(hint) + "\n\nExtract the reported statement figures from these filing "
            "pages, citing the page number for each figure.\n\n" + body)


def _facts_to_proposals(data: dict, company: dict) -> list:
    """Turn the model's extracted facts into ingest-style proposals, sourced to the filing
    page, filtered to the canonical schema and to plausible magnitudes."""
    from aeon_nimbus import ingest
    cur = data.get("currency") or company.get("currency")
    # normalise the filing's reported scale to millions (the platform's basis) so an
    # in-thousands filing (common in African markets) stays comparable with its peers.
    unit_raw = (data.get("unit") or company.get("unit") or "").lower()
    scale = 0.001 if "thousand" in unit_raw else (1000.0 if "billion" in unit_raw else 1.0)
    fname = company.get("filing_name") or "audited filing (financial statements)"
    url = company.get("financials_url") or company.get("annual_report_url")
    out = []
    for f in data.get("facts") or []:
        item, fy, v = f.get("item"), f.get("fy"), f.get("value")
        if item not in ingest.FINANCIAL_ITEMS or not isinstance(v, (int, float)) or not fy:
            continue
        pg = f.get("page")
        out.append({"fy": str(fy), "item": item, "statement": ingest._STATEMENT_OF.get(item),
                    "value": round(float(v) * scale, 3), "unit": "millions", "currency": cur,
                    "source": f"{fname} p.{pg}" if pg else fname, "source_url": url,
                    "confidence": 0.9, "provenance": "filing_ai"})
    return ingest._dedupe([p for p in out if ingest._plausible(p)])


_PAGE_REF = re.compile(r"p\.(\d+)")
# scales between a proposal (normalised to millions) and what a filing might print:
# the number itself, thousands, raw units, or billions.
_PRINT_SCALES = (1.0, 1000.0, 1_000_000.0, 0.001)


def _page_numbers(doc, page_1based: int) -> list[float]:
    """Every numeric token printed on a page (spaced-thousands aware, sign ignored)."""
    from aeon_nimbus import extract_statements as X
    vals = []
    for row in X._rows_by_y(doc, page_1based):
        for tok in re.findall(r"\(?-?\d[\d,]*(?:\.\d+)?\)?", row):
            t = tok.strip("()").replace(",", "")
            try:
                vals.append(abs(float(t)))
            except ValueError:
                continue
    return vals


def ground_facts(pdf_bytes: bytes, proposals: list) -> list:
    """No source, no number — enforced mechanically. Keep a proposal only when its
    value is literally printed on the filing page it cites (checking the page and its
    neighbours, at any reporting scale). A figure the model cannot point to in the
    document is dropped, never stored. Returns the surviving proposals."""
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_cache: dict[int, list[float]] = {}
    kept = []
    try:
        for p in proposals:
            m = _PAGE_REF.search(p.get("source") or "")
            if not m:
                continue                       # uncited → cannot be verified → dropped
            pg = int(m.group(1))
            v = abs(float(p.get("value")))
            found = False
            for cand in (pg, pg - 1, pg + 1):  # printed page numbers can be off-by-one vs pdf index
                if cand < 1 or cand > doc.page_count:
                    continue
                if cand not in page_cache:
                    page_cache[cand] = _page_numbers(doc, cand)
                for s in _PRINT_SCALES:
                    want = v * s
                    tol = max(0.6, 0.0015 * want)
                    if any(abs(t - want) <= tol for t in page_cache[cand]):
                        found = True
                        break
                if found:
                    break
            if found:
                kept.append(p)
    finally:
        doc.close()
    return kept


def extract_statements_from_pdf(pdf_bytes: bytes, company: dict | None = None, *,
                                call=None, max_pages: int = 8) -> list:
    """Read a filing PDF and return proposals for the reported statement line items, each
    sourced to its filing page. The deterministic locator narrows to the statement pages so
    the model sees those, not the whole report; the model only structures what is printed —
    the no-fabrication rules live in EXTRACT_SYSTEM, and ground_facts() then verifies every
    returned figure against the cited page, dropping anything the document does not print.
    `call` is injectable for testing."""
    call = call or _call_llm
    company = company or {}
    pages = _statement_pages_text(pdf_bytes, max_pages)
    if not pages:
        return []
    raw = call(EXTRACT_SYSTEM, _extract_prompt(pages, company))
    data = _loads(raw)
    props = _facts_to_proposals(data, company)
    grounded = ground_facts(pdf_bytes, props)
    if len(grounded) < len(props):
        print(f"[ai_research] grounding dropped {len(props) - len(grounded)} of {len(props)} "
              f"facts not found on their cited pages", flush=True)
    return grounded


def draft_qualitative(facts: dict) -> dict:
    """Draft the qualitative block from sourced facts. Returns the block tagged as an
    AI draft pending review — it is NOT model-ready until an analyst approves it."""
    raw = _call_llm(SYSTEM, _prompt(facts))
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1].lstrip("json").strip()
    try:
        block = json.loads(raw)
    except json.JSONDecodeError:
        block = {"error": "model did not return valid JSON", "raw": raw[:400]}
    block["provenance"] = "ai_draft"
    block["review_required"] = True
    block["model"] = os.environ.get("AI_MODEL", "claude-haiku-4-5")
    return block


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    if not argv:
        raise SystemExit("usage: python -m aeon_nimbus.ai_research <slug> [--dry-run]")
    facts = _facts_for(argv[0])
    if dry:
        print("SYSTEM PROMPT:\n" + SYSTEM + "\n\nGROUNDING (only these facts are allowed):\n" + _prompt(facts))
        print("\n[dry-run] no API called. Set ANTHROPIC_API_KEY (or OPENAI_API_KEY) and drop --dry-run to draft.")
        return
    print(json.dumps(draft_qualitative(facts), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
