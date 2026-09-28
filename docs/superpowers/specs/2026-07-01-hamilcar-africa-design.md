# Hamilcar Africa — Pan-African Equity Research Platform

**Design spec — 2026-07-01**
Location: `Hamilcar_project/ER_Safaricom_algo/`
Status: draft for review (universe section pending the data-availability workflow)

---

## 1. Goal & framing

A source-linked equity-research platform that produces an **institutional-grade, initiation-of-coverage report + interactive dashboard per company** for a curated universe of African blue-chips, with **Safaricom (NSE: SCOM) as the flagship**. Built by reusing and extending the Hamilcar `Sample_ER_algorithm` engine.

This is a **portfolio piece for ER/PE recruiters** — so the governing constraint is credibility: it must read as analyst work, not AI output. Concretely that means:

- **Every figure is source-linked** — each number carries `{document, page, period, currency, unit, confidence, review_status, source_url}`. This traceability is the core credibility mechanism, inherited from the existing engine.
- **No invented numbers.** Where data is unavailable (common in African markets), the platform shows an explicit "not disclosed" state and a confidence score, never a fabricated figure. (User standing constraint: never hallucinate figures on real documents.)
- **Sector-correct methodology.** Banks are valued as banks, not DCF'd — see §5.
- No em-dashes / no semicolons in authored prose; verified facts only.

"All African companies" is the *roadmap*. v1 ships a scalable platform + methodology plus a fully-built curated universe; every additional name is a repeatable "add a source-linked seed" operation.

## 2. Scope decomposition

| Layer | v1 (this build) | Roadmap |
|---|---|---|
| Platform / engine | Reuse + extend Hamilcar engine (multi-company, sector-aware, initiation reports) | — |
| Flagship | Safaricom — deepest, fully source-linked (FY2025 seed already exists) | Update each reporting cycle |
| Universe | Curated cross-sector set (see §3), each with a source-linked initiation report + dashboard | Widen coverage, more exchanges |
| Data | Filings (source-linked) + free live macro/FX | Live market data, more automation |
| Delivery | Self-contained HTML reports + dashboard + universe screener | Optional web deployment |

## 3. Launch universe

**Resolved 2026-07-01** via two research workflows (`africa-universe-harden`, `africa-universe-expand`) plus a deterministic acquisition run. The verified universe is **30 companies** in `data/universe.json`; filings + macro/FX are on disk (`data/raw_docs/` — 73 PDFs / 536MB), coverage in `data/coverage.json`.

**The 30** — Telecom (8): Safaricom *(flagship)*, MTN Group, Airtel Africa, Vodacom, MTN Nigeria, MTN Ghana, Sonatel, Maroc Telecom. Banks (10): Equity Group, KCB, Co-operative Bank, Standard Bank, FirstRand, Absa, Capitec, CIB Egypt, GTCO, Zenith. Consumer (6): EABL, Shoprite, Tiger Brands, Nigerian Breweries, BUA Foods, Bidcorp. Materials (3): Dangote Cement, BUA Cement, Bamburi. Energy (2): Sasol, Seplat. Tech/holding (1): Naspers *(sum-of-parts)*. Exchanges: NSE, JSE, NGX, EGX, BRVM, Casablanca, GSE, LSE. Valuation model: 19 DCF, 10 bank P/B–DDM, 1 sum-of-parts.

**Data coverage (per `data/coverage.json`)** — 30/30 for investor presentations, sector KPIs (5–12 each), peers (5–6 each), 5yr-financials source and macro; **27/30 annual reports + notes** downloaded (3 — CIB, BUA Cement, Seplat — behind JS/bot walls, resolvable via a real browser or an alternate host such as the LSE NSM / NGX / EGX repositories); market data partial by design (market cap + ticker + FX sourced; live intraday price snapshotted, not freely available on African exchanges). Source-linked statement extraction is proven on both a telecom (Safaricom) and a bank (Equity Group), across statements-only PDFs and 298-page integrated reports.

Two tiers for report depth: a **Deep tier** (Safaricom + strongest-data names → full initiation reports) and a **Coverage tier** (the rest → source-linked coverage that deepens iteratively).

## 4. Architecture — reuse map

New standalone project at `ER_Safaricom_algo/`, forking the reusable Hamilcar core and extending it. What is inherited vs new:

**Reused largely as-is**
- `schemas.py` — `SourceReference`, `FinancialField` (native + `usd_value`), `DocumentRecord`, `RiskFlag`, `ValidationResult`. The source-linked data model is exactly right.
- `analytics.py` — `run_dcf`, `run_scenario_analysis`, `calculate_valuation_multiples`, `build_wacc_from_components` (already has country-risk + FX premia), the liquidity/block-trade suite, `calculate_fx_sensitivity`, recovery waterfall.
- `standardisation.py` — IFRS label synonyms, currency patterns (already covers KES/ZAR/NGN/EGP/MAD/XOF/TZS/ZMW), `normalise_currency`, fiscal-year alignment, bracket/negative parsing.
- `database.py` — SQLite `dashboard_contexts` (status-gated: `approved` / `reviewed_public_seed`), generic multi-company schema.
- `report.py` — `build_dashboard_context`, `render_self_contained_html` (Jinja2 + embedded Plotly), blocked empty state.
- `parsing.py` / `extraction.py` / `ingestion.py` — PDF/Excel/Word parsing (PyMuPDF) for source extraction.
- `validation.py` — accounting cross-checks (balance sheet balances, cash-flow ties).
- The Safaricom `real_seed.py` — the flagship's verified FY2025 data and the seed contract every company follows.

**New / extended (the real work)**
1. `sector_analytics.py` (new) — **bank valuation + KPIs** and a sector router (see §5).
2. `report_initiation.py` (new) — the **full initiation-of-coverage report generator** (see §6), extending `memo.py`.
3. `universe.py` (new) — the **multi-company universe screener** context (cross-company comps table, sector & country heatmaps); lifts the single-context `limit 1` to assemble all approved companies.
4. `fx.py` + `macro.py` (new) — free live **FX (Frankfurter, no key)** and **macro (World Bank / IMF)** adapters, wiring `data_sources.py`'s catalogued adapters; populate `usd_value` for cross-company comparability.
5. `seeds/` (new package) — one **source-linked seed module per company** (Safaricom migrated in first), each producing the company data contract.
6. `templates/` — extend `dashboard.html` for the universe screener + sector-aware panels; add `report.html` for the printable initiation report.
7. `pdf_export.py` (new) — a **real** PDF renderer for the initiation report (weasyprint or the `pdf` skill), replacing the honest "PDF blocked" stub in `memo.py`.
8. `cli.py` / `run_platform.py` — multi-company build (`--seed <company>`, `--seed-all`, `--report <company>`, `--universe`).

## 5. Sector-aware analytics & valuation (credibility-critical)

The single biggest tell of amateur/AI research is DCF-ing a bank. Analytics route by `company.sector` → `valuation_model`:

**Non-financials (telecom, consumer, materials, energy) → `dcf`**
- Revenue → EBITDA → EBIT → NOPAT → FCF, discounted at a country-risk-adjusted WACC (existing `run_dcf` + `build_wacc_from_components`).
- Comps: EV/EBITDA, EV/Sales, P/E, FCF yield vs peer median.
- Energy (Sasol): add commodity (oil/chemical spread) sensitivity to scenarios.

**Banks (e.g. Equity Group, Standard Bank, CIB) → `bank_pb_ddm`** *(new in `sector_analytics.py`)*
- KPIs: net interest margin, cost/income, ROE, ROTE, CET1 / capital adequacy ratio, NPL ratio, cost of risk, loan/deposit ratio, dividend payout.
- Valuation, two cross-checking methods:
  - **Justified P/B** = (ROE − g) / (COE − g), applied to book value per share.
  - **Excess-return / DDM**: value = BVPS + Σ discounted (ROE − COE)·BVPS, or a Gordon dividend-discount value; COE from CAPM + country-risk premium.
- Comps: P/B vs ROE scatter (the correct bank comp), P/E, dividend yield. **No EV/EBITDA, no DCF.**

**Insurers (if any verify) → `insurer_ev`** — embedded value / P/EV. Only if the universe includes one.

The report and dashboard templates branch on `valuation_model` to show the right valuation section, KPI set, and comps chart.

## 6. Initiation-of-coverage report generator

Extends `memo.py`'s section/table scaffolding into a full sell-side-style report, rendered as a **self-contained HTML file per company** (`output/reports/<company>_initiation.html`) and exported to **PDF**. Source-linked throughout; a rating box up top.

Sections:
1. **Cover / rating box** — recommendation (Buy/Hold/Sell), target price, current price, upside/downside, market cap, key stats, report date. Data caveat if applicable.
2. **Investment thesis** — 3-5 pillars, each tied to evidence.
3. **Business & segment overview** — model, segments, geographic exposure (Safaricom: Kenya vs Ethiopia; Airtel/MTN: multi-country).
4. **Industry & country context** — sector dynamics + macro (GDP, inflation, rates, FX) from the free macro layer.
5. **Financial analysis** — 3-5yr history, margins, returns, cash generation, leverage; sector-appropriate (bank KPIs for banks).
6. **Valuation** — DCF (or bank model) + comps; scenarios (base/upside/downside); WACC/COE build; sensitivity matrix; valuation bridge to target price.
7. **Risks** — ranked register (FX, country/regulatory, governance, liquidity, refinancing) with source references.
8. **Governance & ESG** — ownership, board, state exposure, ESG flags (material, not boilerplate).
9. **Catalysts & what-to-watch** — next results, corporate actions, regulatory events.
10. **Appendix** — source-linked financial statements + document register (page-referenced).

Prose lives in the seed's `memo` block (authored, source-grounded); numbers/tables/charts come from analytics. This keeps the generator deterministic and the content verifiable.

## 7. Data strategy — "use all available data"

Per company, aggregate every free source and tag each with a confidence score:

| Source | Type | Use | Access |
|---|---|---|---|
| Audited annual report (PDF) | Primary | Financial statements, segments, debt notes, governance — page-referenced | Company IR site |
| Latest interim / half-year results | Primary | Most recent trading, guidance | Company IR / exchange |
| Exchange announcements (NSE/JSE SENS/NGX/EGX) | Primary | Corporate actions, trading updates | Exchange sites |
| World Bank / IMF | Macro | GDP, inflation, policy rate per country | Free API, no key |
| Frankfurter / exchangerate.host | FX | Currency normalization to USD | Free API, no key |
| Central banks (CBK, SARB, CBN, CBE) | Macro | Policy rates, FX reference | Free |
| FMP / AlphaVantage (free tier) | Market | Price, shares, market cap where African coverage exists | Free tier, patchy |

**Scarcity is handled honestly:** confidence scores per field, explicit "not disclosed" states, and market data falling back to the filing when APIs do not cover the exchange. Multi-currency values are stored in native currency (with source) and normalized to USD via the FX layer for cross-company comps; both are shown.

## 8. Deliverables

Per company: (a) initiation-of-coverage report (HTML + PDF), (b) dashboard tab.
Platform-level: (c) universe screener / overview — cross-company comps table, sector & country heatmaps, ranking.
All self-contained HTML (embedded data + Plotly), openable offline.

## 9. Project structure (target)

```
ER_Safaricom_algo/
  aeon_nimbus/            # the engine package (forked + extended)
    schemas.py analytics.py sector_analytics.py  standardisation.py
    database.py report.py report_initiation.py universe.py
    fx.py macro.py parsing.py extraction.py validation.py cli.py config.py
    seeds/                    # one source-linked module per company
      safaricom.py mtn_group.py equity_group.py ...
    templates/
      dashboard.html report.html
  data/
    raw_docs/<company>/       # source filings (PDF)
    aeon_nimbus.sqlite
  output/
    aeon_nimbus.html      # universe dashboard
    reports/<company>_initiation.{html,pdf}
  docs/superpowers/specs/     # this spec + plan
  tests/                      # pytest on analytics + bank model + FX + validation
  requirements.txt run_platform.py README.md
```

## 10. Build phasing

Given depth × N companies, the extraction is the bottleneck — parallelized with a research/extraction workflow (concurrent per-company seed-building).

1. **Engine fork + extensions** — stand up `aeon_nimbus/` from the Hamilcar core; add `sector_analytics.py` (bank model), `report_initiation.py`, `universe.py`, `fx.py`/`macro.py`; tests for the new analytics.
2. **Flagship** — migrate Safaricom seed; produce its full initiation report + dashboard end-to-end (proves the pipeline).
3. **Universe extraction (parallel)** — per company: fetch filing → extract source-linked financials → build seed. Deep tier to full depth, coverage tier to genuine (lighter) depth.
4. **Universe screener + polish** — cross-company comps, heatmaps; run the design skill on report + dashboard; verify.
5. **(Optional) deploy** — host the dashboard + reports.

## 11. Testing & verification

- pytest on: DCF math, the new bank valuation (justified P/B, excess-return), FX normalization, validation checks (balance-sheet balances, cash-flow ties), report generation (no missing sections, no `None` leaking into prose).
- Per company: automated source-integrity check — every rendered figure resolves to a `SourceReference`; fail the build if a number lacks a source.
- Visual verification of the flagship report + dashboard in the browser before claiming done.

## 12. Risks & open questions

- **Data availability** — the gating risk; mitigated by the verification workflow up front and honest "not disclosed" states. Some coverage-tier names may stay lighter than the deep tier.
- **Depth vs breadth** — "as many as possible" trades against per-name depth. Tiering resolves this; Safaricom + deep tier carry the credibility.
- **Bank vs non-bank rigor** — the bank model must be genuinely correct (justified P/B, COE), or it undercuts credibility. Covered by tests + methodology transparency.
- **PDF fidelity** — initiation reports must print cleanly; weasyprint vs the pdf skill to be chosen in the plan.
- **FY alignment** — companies have different fiscal year-ends; comps normalize to latest-reported FY with the period shown.
```
