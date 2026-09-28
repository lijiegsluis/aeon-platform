# Aeon Nimbus Research — Equity Research Automation System

Status of the implementation against the automation spec. This documents the
working system: what exists, what was added, and the roadmap. Honest about
scope — the spec describes a multi-release production platform; the working
**core pipeline and the keystone Excel model compiler are implemented and
tested end-to-end** on a non-financial pilot (Bamburi Cement).

## 1. Existing architecture (inherited / reused)

The platform is a Python data-pipeline + engine that produces a self-contained
interactive HTML dashboard and now a downloadable Excel model. No separate live
web backend existed; per the spec's fallback, new logic is built as importable
Python services (a FastAPI layer is a clean add — see roadmap).

- **Entity registry** — `data/universe.json`: 30 African listed companies with
  legal name, ticker, exchange, secondary listings, country, sector, sub-sector,
  reporting currency, valuation model, latest fiscal year, data-availability.
  (ISIN / fiscal-year-end / accounting-standard fields are supported by the
  resolver schema; populated for the pilot, backfill pending — §1.)
- **Document collection** — `sources.py` + `fetch.py`: browser-UA + curl-fallback
  downloader (beats Cloudflare 403s), World Bank macro, free FX (open.er-api).
  73 filings on disk under `data/raw_docs/`. Source-waterfall priority is encoded
  in the acquisition order (IR site → exchange → aggregator). (§2)
- **Extraction pipeline** — `extract_statements.py` (PyMuPDF statement locator +
  layout-aware line-item extractor) and the agent extraction that produced
  `data/extracted/*.json`: all 30 companies with **source-linked 5-year IS/BS/CF
  + notes + KPI values + market data**, each figure carrying source + confidence.
  (§3, §5)
- **Financial taxonomy** — `standardisation.py`: IFRS label synonyms (turnover →
  revenue, borrowings → gross_debt, additions to PPE → capex …), currency + unit
  detection (KES/ZAR/NGN/EGP/MAD/XOF …), fiscal-year normalisation, bracket/
  negative parsing. (§4)
- **Sector model modules** — `sector_analytics.py` (bank justified-P/B, residual
  income, DDM, bank KPIs), `analytics.py` (DCF, WACC build, liquidity),
  `valuation.py` (blended-terminal DCF, comparables, football field, sensitivity),
  `forecast.py` (5-year projection). (§6, §15)
- **Data-quality / controls** — `coverage.json` + the new `controls.py`. (§10, §17)
- **Rendering** — `report_initiation.py` (initiation report), `build_platform.py`
  + `templates/platform.html` (Plotly dashboard). (§15, §16)

## 2. Code added this release

| Module | Purpose | Spec |
|---|---|---|
| `aeon_nimbus/controls.py` | 3-level control suite (critical/warning/info) + 0-100 data-quality score that gates export | §10, §17 |
| `aeon_nimbus/excel_model.py` | **Model compiler** → fully-linked `.xlsx` (named ranges, native formulas, 13 sheets, sources, control log) | §8, §9, §11 |
| `aeon_nimbus/valuation.py` | FCFF DCF (blended terminal), WACC build, comparables, sensitivity, football field | §8, §15 |
| `aeon_nimbus/forecast.py` | 5-year forward projection of P&L from history + assumptions | §5, §8 |
| `build_excel.py` | CLI: generate a model for a company | — |
| `tests/test_{controls,excel_model,valuation,forecast}.py` | 25 new tests | §20 |
| `aeon_nimbus/ingest.py` | Deterministic ingestion: xlsx/csv/pdf parsers + open-web collector → sourced *proposals* (never trusted facts) | §6, §7 |
| `aeon_nimbus/studio_core.py` | Staging, confidence-threshold auto-accept, merge into the working copy | §10 |
| `aeon_nimbus/studio_api.py` | Data Studio router: upload / collect / proposed / review / recompute / export | §16 |
| `aeon_nimbus/platform_data.py`, `render.py` | Shared assembly (static + live) and one-template render | §16 |
| `tests/test_{ingest,studio_api}.py` | 11 new tests | §20 |

## 2A. Data Studio — interactive data update (this release)

Served live (`./run.sh` → `GET /`, `live=True`), the dashboard gains a **Data
Studio** tab per company with four actions wired to `/api/studio`:

1. **Collect from web** — `POST /companies/{cid}/collect` (background job). Fetches
   the official filing (PDF at `annual_report_url`) plus public financial pages by
   ticker, parses statements, and proposes figures — each tagged with its **source
   URL + a confidence**. The filing is the reliable path; aggregators are
   best-effort and degrade to nothing rather than guessing.
2. **Upload file** — `POST /companies/{cid}/upload`. Deterministic parse of
   xlsx/xlsm/csv (openpyxl) and pdf (PyMuPDF statement locator), mapped into the
   template via the standardisation vocabulary. No LLM, no API key.
3. **Recalculate** — `POST /companies/{cid}/recompute`. Re-runs forecast +
   valuation + controls + data-quality on the approved working copy.
4. **Export Excel** — `GET /companies/{cid}/export`. Compiles the linked workbook
   (export-gated by the critical controls) and streams it.

**The honesty invariant.** Collected/uploaded figures are written to a
`ProposedFact` staging table, never straight into the model. A user-set
**confidence threshold** decides the split: proposals at or above it auto-merge
into `Company.extracted` (still sourced, logged, reversible); the rest wait in a
review table for one-click Approve/Reject. An unsourced or unparseable value is
dropped, not shown — so even open-web collection cannot put a fabricated figure
into the model.

**Source citations.** Every reported figure in the dashboard (static snapshot and
live app) carries a numbered superscript tracing it to a filing; hovering reveals
the source card, and a per-company **Sources** panel lists them. Derived ratios are
left uncited by design.

## 3. Excel model — structure & the "not a static export" guarantee

`compile_model(company, universe, out_path)` writes a workbook where the forecast
is driven by **editable assumptions through native Excel formulas and named
ranges**, so it recalculates when opened (or via a Windows `xlwings`/`pywin32`
worker server-side).

Sheets: `Cover, Assumptions, Historical_Financials, Revenue_Build,
Three_Statements, Valuation, Operating_KPIs, Comparables, Sources, Controls,
Control_Log, Change_Log, Charts` (+ sector schedules).

The linkage chain (all live formulas):
```
Assumptions (Rev_Growth, EBITDA_Margin, Capex_Pct, WACC build, Terminal_Growth, Exit_Multiple)
    -> Revenue_Build (forecast Revenue = prior * (1+Rev_Growth); cement volume x price)
    -> Three_Statements  IS:  EBITDA = Revenue*EBITDA_Margin ; EBIT = EBITDA - D&A ;
                               Tax = -MAX(0,PBT)*Tax_Rate ; Net income = PBT + Tax
                         FCFF: EBITDA + CashTax + Capex + dWC
    -> Valuation  PV(FCFF)=FCFF/(1+WACC)^t ; TV = 50% perpetuity + 50% exit multiple ;
                  EV = SumPV + PV(TV) ; Equity = EV - net debt ; Value_Per_Share = Equity/Shares_Out
```
Historical columns are hardcoded reported values that link from
`Historical_Financials` (each with a `Sources` row: currency, unit, source, page,
confidence, reported/derived). Named ranges include `Historical_Revenue_FYxx`,
`WACC`, `WACC_Risk_Free_Rate`, `WACC_Country_Risk_Premium`, `Terminal_Growth_Rate`,
`Exit_Multiple`, `Shares_Out`, `Value_Per_Share`.

## 4. Formula library (tested)

| Item | Formula |
|---|---|
| Cement revenue | Sales volume × realised price per tonne (else prior × (1+growth)) |
| Telecom revenue | Subscribers × ARPU × 12 |
| EBITDA (forecast) | Revenue × EBITDA margin |
| Tax | max(0, PBT) × tax rate |
| FCFF | EBITDA − cash tax − capex − Δ working capital |
| WACC | COE·(1−Wd) + Kd·(1−t)·Wd ;  COE = Rf + β·ERP + CRP |
| Terminal value | 50% · FCFF₅(1+g)/(WACC−g) + 50% · exit multiple · EBITDA₅ |
| Enterprise value | Σ PV(FCFF) + PV(terminal value) |
| Value per share | (EV − net debt) / shares outstanding |
| Justified P/B (banks) | (ROE − g) / (COE − g) applied to book value per share |

## 5. Controls & data-quality (§10, §17)

`run_controls()` returns critical / warning / informational checks with
expected-vs-actual detail (C01 coverage, C02 sourcing, C03 units, C04 net-debt
reconciliation, C05 balance-sheet integrity, C06 market data; W01-W05 warnings;
I01-I02 info). `data_quality_score()` → 0-100, band A/B/C/D, output policy, and
`export_allowed` (a critical failure blocks export). Both are written into the
`Controls` and `Control_Log` sheets with a full audit row per check.

## 6. Sector templates & country modules

- **Sector** is driven by `universe.sector`; the Revenue_Build and (roadmap)
  sector schedule sheets branch on it. Cement → volume/price/energy; Telecom →
  subs/ARPU/network capex; Bank → justified-P/B template (valuation logic done in
  `sector_analytics.py`; a dedicated bank workbook template is roadmap).
- **Country** cost-of-capital lives in `build_platform.WACC_BY_COUNTRY` /
  `BANK_COE_BY_COUNTRY` (frontier-realistic) and the WACC build cells; macro is
  fetched to `data/macro/<ISO>.json`. The model stays in reporting currency;
  USD/GBP/EUR converted views use the free FX layer (average-rate P&L, closing-rate
  balance sheet) — converted-view sheet is roadmap.

### Adding a sector
1. Add label mappings to `standardisation.LINE_ITEM_SYNONYMS` if the sector uses
   unusual labels. 2. Add a Revenue_Build branch + schedule in `excel_model.py`.
   3. Add the sector KPI list to the extraction prompt. 4. Add a valuation model
   in `sector_analytics.py` if it is not a DCF.

### Adding a country
Add its cost-of-capital to `WACC_BY_COUNTRY` / `BANK_COE_BY_COUNTRY`, its ISO to
`sources.COUNTRY_ISO3`, and (optional) a risk-free/bond-yield source.

### Adding a data provider
Implement a small adapter returning `{value, source, timestamp, currency,
exchange, confidence}` and register it in the source-priority list in
`sources.py` (FX and macro adapters are the templates).

## 7. Test results

`python3 -m pytest` → **34 passing** (engine, bank valuation, forecast,
valuation, controls, Excel integrity, platform). Excel integrity test reloads the
generated workbook and asserts required sheets, named ranges, live forecast
formulas and real historical hardcodes.

## 8. Known limitations

- **FastAPI service layer is live** (`api.py`) over a SQLAlchemy DB (SQLite dev /
  Postgres via `DATABASE_URL`) with an in-process job runner. **Celery+Redis** is
  a drop-in swap for the runner but not wired here; connecting the generated HTML
  dashboard to the API for in-browser download is the remaining front-end step.
- Excel recalculation is on-open; a server-side `xlwings`/`pywin32` recalc worker
  is Windows-only and not wired in this macOS dev env.
- **Banks now have a dedicated template** (justified P/B + Loan_Book / Deposits /
  NIM / Capital). The non-financial balance sheet / cash flow are still a
  simplified roll-forward (IS + FCFF fully linked); full 3-statement balancing
  (balance = 0 auto-plug) is the remaining modelling refinement.
- OCR for scanned PDFs and Arabic/Portuguese extraction rely on the agent layer;
  a dedicated OCR stage is roadmap.
- Analyst review / override workflow is modelled in the controls + Control_Log
  but the approval UI + persistence is roadmap.
- 11/29 companies are export-blocked by a critical control — expected behaviour;
  they need a data fix or an approved override.

## 9. Roadmap (next releases)

- **A. Service layer** — FastAPI app exposing search/resolve, project, extraction
  results + review/approve, generate-model, download-xlsx, controls, KPIs, audit;
  Celery+Redis for extraction/model jobs; Postgres for the trusted-research layer
  (raw layer already on disk).
- **B. Review & override** — analyst queue for low-confidence/critical items,
  formal override request→approve→log workflow persisted and stamped into
  Control_Log.
- **C. Bank workbook template** + full 3-statement balancing + converted-currency
  views.
- **D. Market-data adapters** (price series, peers, bond yields) behind the
  provider-priority interface; peer engine with the transparent weighted score.
- **E. Research library** (country/sector/company items with confidence + review).
- **F. PowerPoint football-field export** (python-pptx).
