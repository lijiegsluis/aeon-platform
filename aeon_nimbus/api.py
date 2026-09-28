"""FastAPI service layer (spec §16) — the click-through automation surface.

Wraps the trusted-research DB, the job runner, the controls/data-quality engine
and the Excel model compiler behind a REST API: search/resolve a company, open a
project, run extraction, review facts, request/approve overrides, generate the
linked Excel model, poll job status, and download the workbook.

Run:  uvicorn aeon_nimbus.api:app --port 8100
Docs: /docs (OpenAPI)
"""

from __future__ import annotations

import os
from pathlib import Path as _Path

# Load .env from project root if present (development convenience)
_env_file = _Path(__file__).resolve().parent.parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from aeon_nimbus import auth, auth_api
from aeon_nimbus import controls as ctrl
from aeon_nimbus import db as D
from aeon_nimbus import jobs, studio_api, bulk_api
from aeon_nimbus import change_store, user_store
from aeon_nimbus.platform_data import assemble_live
from aeon_nimbus.render import render_platform
from aeon_nimbus import screener_api


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    log = logging.getLogger("aeon_nimbus")
    D.init_db()
    # Ensure auth_users table exists (without username column first)
    from aeon_nimbus.auth import User
    from aeon_nimbus.db import engine
    import sqlalchemy as sa
    # Create table without the new column if it doesn't exist
    with engine.connect() as conn:
        tables = conn.execute(sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name='auth_users'")).fetchall()
        if not tables:
            User.metadata.create_all(bind=engine)
    # Add username column if it doesn't exist yet (schema migration)
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("ALTER TABLE auth_users ADD COLUMN username VARCHAR"))
            conn.commit()
        log.info("auth: added username column to auth_users")
    except Exception:
        pass  # column already exists
    # Now create any missing tables (picks up username column now)
    User.metadata.create_all(bind=engine)
    # Restore users and change log from GCS mirror (Cloud Run cold-start resilience)
    with D.SessionLocal() as db:
        n = user_store.load(db)
    if n:
        log.warning("auth: restored %d accounts from register", n)
    msg = auth.ensure_first_admin()
    if msg:
        log.warning("auth: %s", msg)
        with D.SessionLocal() as db:
            user_store.save(db)
    with D.SessionLocal() as db:
        c = change_store.load(db)
    if c:
        log.warning("history: restored %d change-log entries", c)
    with D.SessionLocal() as db:
        if not db.query(D.Company).first():
            D.seed_from_files()

    # Background task: warm the filing-alert cache every 24 hours
    import asyncio as _asyncio

    async def _filing_alert_loop():
        while True:
            await _asyncio.sleep(86400)  # 24 hours
            try:
                from aeon_nimbus import filing_alerts as _fa
                with D.SessionLocal() as _db:
                    _companies = [{"slug": c.slug, "ticker": c.ticker, "exchange": c.exchange,
                                   "country": c.country, "universe": c.universe or {}}
                                  for c in _db.query(D.Company).all()]
                _result = _fa.check_all_companies(_companies)
                studio_api._filing_alert_cache["result"] = _result
                import datetime as _dt3
                studio_api._filing_alert_cache["ts"] = _dt3.datetime.now(_dt3.timezone.utc)
            except Exception:
                pass  # silent — never block the event loop

    _asyncio.create_task(_filing_alert_loop())

    # Background task: run consistency check every 24 hours and cache result
    async def _consistency_loop():
        while True:
            await _asyncio.sleep(86400)
            try:
                from aeon_nimbus import platform_data as _pdata
                import openpyxl as _opx
                from pathlib import Path as _Path
                with D.SessionLocal() as _db:
                    _companies = _db.query(D.Company).all()
                    _out = []
                    for _co in _companies:
                        _platform_target = None
                        try:
                            _ext = _pdata.merged_extracted(_co.extracted or {})
                            _deep = _pdata.deep_from_extracted(_ext, _co.universe or {})
                            if _deep and _deep.get("rating"):
                                _platform_target = _deep["rating"].get("target")
                        except Exception:
                            pass
                        if _platform_target is None:
                            continue
                        _wb_path = _Path("output") / "models" / f"{_co.slug}_model.xlsx"
                        _wb_target = None
                        if _wb_path.exists():
                            try:
                                _wb = _opx.load_workbook(str(_wb_path), data_only=True)
                                _ws = _wb["Valuation"] if "Valuation" in _wb.sheetnames else None
                                if _ws:
                                    for _row in _ws.iter_rows():
                                        for _cell in _row:
                                            if _cell.value and isinstance(_cell.value, str) and "weighted target" in _cell.value.lower():
                                                _vc = _ws.cell(row=_cell.row, column=4)
                                                if isinstance(_vc.value, (int, float)):
                                                    _wb_target = float(_vc.value)
                                                break
                                        if _wb_target is not None:
                                            break
                            except Exception:
                                pass
                        _pct = abs(_platform_target - _wb_target) / abs(_wb_target) if _wb_target else None
                        _out.append({"slug": _co.slug, "name": _co.name,
                                     "platform_target": round(_platform_target, 4),
                                     "workbook_target": round(_wb_target, 4) if _wb_target else None,
                                     "pct_diff": round(_pct, 4) if _pct is not None else None,
                                     "status": "diverged" if (_pct and _pct > 0.05) else ("ok" if _wb_target else "workbook_unreadable")})
                import datetime as _dt4
                studio_api._consistency_cache = {"result": _out, "ts": _dt4.datetime.now(_dt4.timezone.utc)}
            except Exception:
                pass

    _asyncio.create_task(_consistency_loop())
    yield


app = FastAPI(title="Aeon Nimbus Research — Equity Research Automation API",
              version="1.0", lifespan=lifespan)
from fastapi.responses import HTMLResponse, JSONResponse
from aeon_nimbus import compare_api, watchlist_api, export_api
from aeon_nimbus.json_utils import clean_for_json

app.include_router(studio_api.router)
app.include_router(auth_api.router)
auth.install(app)

# Public APIs - installed after auth to bypass login requirement
app.include_router(bulk_api.router)
app.include_router(screener_api.router)
app.include_router(compare_api.router)
app.include_router(watchlist_api.router)
app.include_router(export_api.router)


# JSON response wrapper to handle NaN/Inf
@app.middleware("http")
async def clean_json_middleware(request, call_next):
    response = await call_next(request)
    if isinstance(response, JSONResponse):
        # Already handled by FastAPI's JSON encoding
        pass
    return response


def get_db():
    db = D.SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    user = auth.current_user(request)
    if user:
        # signed-in users go straight to the platform
        return render_platform(assemble_live(db), live=True, user=user)
    # anonymous visitors go straight to login — marketing lives on aeonnimbus.com now
    return RedirectResponse("/login", status_code=303)


@app.get("/platform", response_class=HTMLResponse)
def platform(request: Request, db: Session = Depends(get_db)):
    """Live dashboard — requires authentication (handled by middleware)."""
    user = auth.current_user(request)
    return render_platform(assemble_live(db), live=True, user=user)


def _404(what: str):
    raise HTTPException(status_code=404, detail=f"{what} not found")


def _brief(c: D.Company) -> dict:
    return {"id": c.id, "slug": c.slug, "name": c.name, "ticker": c.ticker, "isin": c.isin,
            "exchange": c.exchange, "country": c.country, "sector": c.sector,
            "currency": c.currency, "valuation_model": c.valuation_model}


# ---- schemas -------------------------------------------------------------

class ProjectIn(BaseModel):
    company_id: int


class ReviewIn(BaseModel):
    decision: str  # approved | rejected


class OverrideIn(BaseModel):
    control_id: str
    rationale: str
    evidence: Optional[str] = None


class DecisionIn(BaseModel):
    decision: str  # approved | rejected
    approver: str = "senior_analyst"


# ---- health & search -----------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "aeon-nimbus-er-automation"}


@app.get("/api/companies")
def search_companies(q: str = Query("", description="name, ticker, ISIN or exchange"),
                     sector: str = Query(None, description="filter by sector"),
                     country: str = Query(None, description="filter by country"),
                     min_market_cap: float = Query(None, description="minimum market cap in millions"),
                     max_market_cap: float = Query(None, description="maximum market cap in millions"),
                     rating: str = Query(None, description="filter by rating: buy, hold, sell"),
                     limit: int = Query(500, description="max results"),
                     offset: int = Query(0, description="pagination offset"),
                     db: Session = Depends(get_db)):
    """Enhanced search with multi-criteria filtering and pagination."""
    query = db.query(D.Company)

    # Text search
    if q:
        like = f"%{q}%"
        query = query.filter(or_(D.Company.name.ilike(like), D.Company.ticker.ilike(like),
                                 D.Company.isin.ilike(like), D.Company.exchange.ilike(like),
                                 D.Company.sector.ilike(like)))

    # Sector filter
    if sector:
        query = query.filter(D.Company.sector.ilike(f"%{sector}%"))

    # Country filter
    if country:
        query = query.filter(D.Company.country.ilike(f"%{country}%"))

    results = query.order_by(D.Company.name).offset(offset).limit(limit).all()

    # Post-filter by market cap and rating (requires extracted data)
    filtered = []
    for c in results:
        if min_market_cap or max_market_cap or rating:
            from aeon_nimbus.platform_data import merged_extracted, deep_from_extracted
            ext = merged_extracted(c.extracted or {})
            deep = deep_from_extracted(ext, c.universe or {}) if ext.get("financials") else None

            if min_market_cap:
                mcap = deep.get("keystats", {}).get("market_cap_m") if deep else None
                if not mcap or mcap < min_market_cap:
                    continue

            if max_market_cap:
                mcap = deep.get("keystats", {}).get("market_cap_m") if deep else None
                if not mcap or mcap > max_market_cap:
                    continue

            if rating and deep:
                stance = deep.get("rating", {}).get("stance", "").lower()
                if rating.lower() not in stance:
                    continue

        filtered.append(_brief(c))

    return {"results": filtered, "total": len(filtered), "offset": offset, "limit": limit}


import json as _json
import functools as _ft


@_ft.lru_cache(maxsize=1)
def _reference_universe() -> list:
    """Curated, searchable directory of listed companies (real ticker + exchange),
    any market. Selecting one pre-fills the add form and drives the web collector;
    a wrong ticker just returns no data — never a fabricated figure."""
    path = D.ROOT / "data" / "reference_universe.json"
    if not path.exists():
        return []
    raw = _json.loads(path.read_text())
    out = []
    for r in raw.get("reference", []):
        out.append({**r, "covered": False})
    for c in raw.get("covered", []):
        out.append({**c, "covered": True})
    return out


@app.get("/api/reference/search")
def reference_search(q: str = Query("", description="company name or ticker"),
                     db: Session = Depends(get_db)):
    """Type-ahead over the equities directory. Marks entries already in coverage
    (open them) vs new (add + auto-research)."""
    ql = (q or "").strip().lower()
    if len(ql) < 2:
        return {"results": []}
    companies = db.query(D.Company).all()
    have = {(c.slug or "").lower(): c.id for c in companies}
    have_names = {(c.name or "").lower(): c.id for c in companies}
    have_tickers = {(c.ticker or "").upper().split(";")[0].split("/")[0].strip(): c.id
                    for c in companies if c.ticker}
    seen, results = set(), []
    for r in _reference_universe():
        name, tk = (r.get("name") or ""), (r.get("ticker") or "")
        if ql not in name.lower() and ql not in tk.lower():
            continue
        key = (r.get("slug") or name).lower()
        if key in seen:
            continue
        seen.add(key)
        cid = (have.get((r.get("slug") or "").lower()) or have_names.get(name.lower())
               or (have_tickers.get(tk.upper()) if tk else None))
        results.append({"name": name, "ticker": tk, "exchange": r.get("exchange"),
                        "country": r.get("country"), "sector": r.get("sector"),
                        "currency": r.get("currency"), "in_coverage": bool(cid), "company_id": cid})
    # exact/prefix matches first, then covered ones, then alphabetical
    results.sort(key=lambda x: (not x["name"].lower().startswith(ql), x["in_coverage"], x["name"]))
    return {"results": results[:12]}


@app.get("/api/companies/{cid}")
def get_company(cid: int, db: Session = Depends(get_db)):
    c = db.get(D.Company, cid) or _404("company")
    ext = c.extracted or {}
    return {**_brief(c), "sub_sector": c.sub_sector, "fiscal_year_end": c.fiscal_year_end,
            "accounting_standard": c.accounting_standard, "ir_url": c.ir_url,
            "years": [f["fy"] for f in ext.get("financials", [])],
            "data_quality": ctrl.data_quality_score(ext)}


# ---- projects & jobs -----------------------------------------------------

@app.post("/api/projects")
def create_project(body: ProjectIn, db: Session = Depends(get_db)):
    c = db.get(D.Company, body.company_id) or _404("company")
    p = D.Project(company_id=c.id, status="draft")
    db.add(p)
    db.commit()
    D.audit(db, "project_created", "project", p.id, company=c.slug)
    return {"project_id": p.id, "company": _brief(c), "status": p.status}


@app.get("/api/projects/{pid}")
def get_project(pid: int, db: Session = Depends(get_db)):
    p = db.get(D.Project, pid) or _404("project")
    model = db.query(D.GeneratedModel).filter_by(project_id=pid).order_by(D.GeneratedModel.id.desc()).first()
    return {"project_id": p.id, "company": _brief(p.company), "status": p.status,
            "model": ({"model_id": model.model_id, "version": model.version, "dq_score": model.dq_score,
                       "export_allowed": model.export_allowed} if model else None)}


@app.post("/api/projects/{pid}/extract")
def start_extract(pid: int, db: Session = Depends(get_db)):
    db.get(D.Project, pid) or _404("project")
    jid = jobs.start_extraction(pid)
    return {"job_id": jid, "kind": "extraction"}


@app.get("/api/jobs/{jid}")
def get_job(jid: int, db: Session = Depends(get_db)):
    j = db.get(D.Job, jid) or _404("job")
    return {"job_id": j.id, "kind": j.kind, "status": j.status, "progress": j.progress,
            "result": j.result, "error": j.error}


@app.get("/api/projects/{pid}/facts")
def get_facts(pid: int, db: Session = Depends(get_db)):
    p = db.get(D.Project, pid) or _404("project")
    facts = db.query(D.FinancialFact).filter_by(company_id=p.company_id).all()
    return {"facts": [{"id": f.id, "fy": f.fy, "item": f.item, "statement": f.statement_type,
                       "value": f.reported_value, "currency": f.currency, "unit": f.unit,
                       "confidence": f.confidence, "review_status": f.review_status,
                       "source": (f.source or "")[:200]} for f in facts]}


@app.post("/api/facts/{fid}/review")
def review_fact(fid: int, body: ReviewIn, db: Session = Depends(get_db)):
    f = db.get(D.FinancialFact, fid) or _404("fact")
    if body.decision not in {"approved", "rejected"}:
        raise HTTPException(400, "decision must be approved or rejected")
    f.review_status = body.decision
    db.add(f)
    db.commit()
    D.audit(db, "fact_reviewed", "fact", fid, decision=body.decision)
    return {"fact_id": fid, "review_status": f.review_status}


# ---- overrides -----------------------------------------------------------

@app.post("/api/projects/{pid}/overrides")
def request_override(pid: int, body: OverrideIn, db: Session = Depends(get_db)):
    db.get(D.Project, pid) or _404("project")
    o = D.Override(project_id=pid, control_id=body.control_id, rationale=body.rationale, evidence=body.evidence)
    db.add(o)
    db.commit()
    D.audit(db, "override_requested", "override", o.id, control=body.control_id)
    return {"override_id": o.id, "status": o.status}


@app.post("/api/overrides/{oid}/decide")
def decide_override(oid: int, body: DecisionIn, db: Session = Depends(get_db)):
    o = db.get(D.Override, oid) or _404("override")
    if body.decision not in {"approved", "rejected"}:
        raise HTTPException(400, "decision must be approved or rejected")
    o.status = body.decision
    o.approved_by = body.approver
    o.decided_at = datetime.now(timezone.utc)
    db.add(o)
    db.commit()
    D.audit(db, "override_decided", "override", oid, decision=body.decision, approver=body.approver)
    return {"override_id": oid, "status": o.status, "approved_by": o.approved_by}


# ---- model generation, controls, download --------------------------------

@app.post("/api/projects/{pid}/generate-model")
def generate_model(pid: int, db: Session = Depends(get_db)):
    db.get(D.Project, pid) or _404("project")
    jid = jobs.start_model_generation(pid)
    return {"job_id": jid, "kind": "model_generation"}


@app.get("/api/projects/{pid}/controls")
def get_controls(pid: int, db: Session = Depends(get_db)):
    p = db.get(D.Project, pid) or _404("project")
    rows = db.query(D.Control).filter_by(project_id=pid).all()
    dq = ctrl.data_quality_score(p.company.extracted or {})
    return {"data_quality": dq,
            "controls": [{"control_id": c.control_id, "category": c.category, "severity": c.severity,
                          "status": c.status, "description": c.description,
                          "expected": c.expected, "actual": c.actual} for c in rows]}


@app.get("/api/projects/{pid}/model/download")
def download_model(pid: int, db: Session = Depends(get_db)):
    p = db.get(D.Project, pid) or _404("project")
    model = db.query(D.GeneratedModel).filter_by(project_id=pid).order_by(D.GeneratedModel.id.desc()).first()
    if not model:
        raise HTTPException(409, "no model generated yet — call generate-model first")
    if not model.export_allowed and p.status != "approved_for_export":
        raise HTTPException(423, "export blocked by a critical control; request and approve an override first")
    path = Path(model.path)
    if not path.exists():
        raise HTTPException(410, "model file missing; regenerate")
    D.audit(db, "model_downloaded", "project", pid, model_id=model.model_id)
    return FileResponse(str(path), filename=f"{p.company.slug}_model.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/projects/{pid}/approve-export")
def approve_export(pid: int, db: Session = Depends(get_db)):
    p = db.get(D.Project, pid) or _404("project")
    p.status = "approved_for_export"
    db.add(p)
    db.commit()
    D.audit(db, "export_approved", "project", pid)
    return {"project_id": pid, "status": p.status}


# ---- KPIs, comparables, audit --------------------------------------------

@app.get("/api/projects/{pid}/kpis")
def get_kpis(pid: int, db: Session = Depends(get_db)):
    p = db.get(D.Project, pid) or _404("project")
    return {"sector_kpis": (p.company.extracted or {}).get("sector_kpis", [])}


@app.get("/api/projects/{pid}/comparables")
def get_comparables(pid: int, db: Session = Depends(get_db)):
    p = db.get(D.Project, pid) or _404("project")
    return {"peers": (p.company.universe or {}).get("peers", [])}


@app.get("/api/projects/{pid}/audit")
def get_audit(pid: int, db: Session = Depends(get_db)):
    rows = db.query(D.AuditLog).filter(
        (D.AuditLog.entity == "project") & (D.AuditLog.entity_id == str(pid))
    ).order_by(D.AuditLog.id.desc()).limit(100).all()
    return {"audit": [{"ts": a.ts.isoformat(), "actor": a.actor, "action": a.action,
                       "detail": a.detail} for a in rows]}


# ── Upload & analyse financial statements ──────────────────────────────────

@app.post("/api/upload-analyze")
async def upload_analyze(file: UploadFile = File(...)):
    """Extract key financials from an uploaded PDF, Excel, or CSV file."""
    import io, re, os

    suffix = (file.filename or "").rsplit(".", 1)[-1].lower()
    raw = await file.read()

    financials: list[dict] = []
    notes = ""
    company_name = None
    confidence = 0.6

    if suffix == "pdf":
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(stream=raw, filetype="pdf")
            text = "\n".join(p.get_text() for p in doc)
        except Exception as e:
            raise HTTPException(422, f"Could not read PDF: {e}")

        # Extract company name from first page
        first_page = doc[0].get_text()
        for line in first_page.split("\n")[:10]:
            line = line.strip()
            if len(line) > 5 and not re.search(r'\d{4}', line):
                company_name = line[:80]
                break

        # Parse year blocks
        year_pattern = re.compile(r'\b(20[12]\d)\b')
        years = sorted(set(int(y) for y in year_pattern.findall(text)))[-5:]
        revenue_pat = re.compile(r'(?:revenue|net revenue|total revenue)[^\d\-]*([\-\d,\.]+)', re.I)
        ebit_pat    = re.compile(r'(?:operating income|ebit)[^\d\-]*([\-\d,\.]+)', re.I)
        ni_pat      = re.compile(r'(?:net income|net earnings|profit)[^\d\-]*([\-\d,\.]+)', re.I)
        fcf_pat     = re.compile(r'(?:free cash flow|fcf)[^\d\-]*([\-\d,\.]+)', re.I)

        def first_num(pat, txt):
            m = pat.search(txt)
            if not m:
                return None
            try:
                return float(m.group(1).replace(",", ""))
            except Exception:
                return None

        for yr in years:
            # Slice text around year mentions for context
            idx = text.find(str(yr))
            chunk = text[max(0, idx - 200): idx + 800] if idx >= 0 else text
            financials.append({
                "year": str(yr),
                "revenue":        first_num(revenue_pat, chunk),
                "ebit":           first_num(ebit_pat, chunk),
                "net_income":     first_num(ni_pat, chunk),
                "free_cash_flow": first_num(fcf_pat, chunk),
            })
        notes = f"Extracted from PDF ({doc.page_count} pages). Figures are pattern-matched estimates — verify against source tables."
        confidence = 0.55

    elif suffix in ("xlsx", "xls"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
            ws = wb.active
            rows = [[c.value for c in row] for row in ws.iter_rows()]
        except Exception as e:
            raise HTTPException(422, f"Could not read Excel: {e}")

        year_row_idx = None
        years = []
        for i, row in enumerate(rows[:20]):
            years_found = [int(v) for v in row if isinstance(v, (int, float)) and 2010 < v < 2030]
            if years_found:
                years = years_found
                year_row_idx = i
                break

        key_rows = {"revenue": None, "ebit": None, "net_income": None, "free_cash_flow": None}
        keywords = {
            "revenue": ["revenue", "net revenue", "total revenue", "sales"],
            "ebit":    ["operating income", "ebit", "operating profit"],
            "net_income": ["net income", "net earnings", "net profit"],
            "free_cash_flow": ["free cash flow", "fcf"],
        }
        for row in rows:
            label = str(row[0] or "").lower().strip()
            for key, kws in keywords.items():
                if key_rows[key] is None and any(kw in label for kw in kws):
                    key_rows[key] = row

        for i, yr in enumerate(years):
            col = i + 1  # first data column after label
            def get_val(row):
                if row is None or col >= len(row): return None
                v = row[col]
                return float(v) if isinstance(v, (int, float)) else None
            financials.append({
                "year": str(yr),
                "revenue":        get_val(key_rows["revenue"]),
                "ebit":           get_val(key_rows["ebit"]),
                "net_income":     get_val(key_rows["net_income"]),
                "free_cash_flow": get_val(key_rows["free_cash_flow"]),
            })
        company_name = wb.properties.title or None
        notes = "Extracted from Excel workbook. Verify figures against source tables."
        confidence = 0.7

    elif suffix == "csv":
        import csv
        reader = csv.reader(io.StringIO(raw.decode("utf-8", errors="replace")))
        rows = list(reader)
        notes = f"CSV file — {len(rows)} rows detected. Manual mapping required."
        confidence = 0.4

    else:
        raise HTTPException(415, "Unsupported file type. Please upload PDF, XLSX, or CSV.")

    return {
        "company_name": company_name,
        "financials": financials,
        "notes": notes,
        "confidence": confidence,
    }


# ── On-demand company research ─────────────────────────────────────────────

class ResearchRequest(BaseModel):
    query: str  # ticker or company name


@app.post("/api/research-company")
async def research_company(req: ResearchRequest):
    """Fetch live financials and run a quick DCF for any global company."""
    import re, os

    query = req.query.strip()
    if not query:
        raise HTTPException(400, "query is required")

    # Try yfinance if available (no API key needed)
    try:
        import yfinance as yf
        ticker_guess = re.sub(r'[^A-Z0-9.\-]', '', query.upper().split()[0])
        t = yf.Ticker(ticker_guess)
        info = t.info or {}
        fin = t.financials  # DataFrame, annual, rows=items, cols=dates

        if not info.get("longName"):
            raise ValueError("ticker not found")

        # Build 5-year financials from yfinance
        financials = []
        if fin is not None and not fin.empty:
            for col in fin.columns[:5]:
                yr_str = str(col.year) if hasattr(col, 'year') else str(col)[:4]
                def _get(label):
                    candidates = [k for k in fin.index if label.lower() in k.lower()]
                    if not candidates: return None
                    v = fin.loc[candidates[0], col]
                    return None if v != v else float(v) / 1e6  # USD millions
                rev  = _get("Total Revenue")
                ebit = _get("Operating Income")
                ni   = _get("Net Income")
                da   = _get("Depreciation")
                ebitda = (ebit + da) if ebit is not None and da is not None else None
                financials.append({"year": yr_str, "revenue": rev, "ebit": ebit,
                                   "ebitda": ebitda, "net_income": ni,
                                   "free_cash_flow": None})

        # Cashflow for FCF
        cf = t.cashflow
        if cf is not None and not cf.empty:
            for i, col in enumerate(cf.columns[:min(5, len(financials))]):
                ocf = None; capex = None
                for k in cf.index:
                    if "operating" in k.lower() and "cash" in k.lower():
                        v = cf.loc[k, col]; ocf = float(v)/1e6 if v == v else None
                    if "capital" in k.lower() and "expenditure" in k.lower():
                        v = cf.loc[k, col]; capex = float(v)/1e6 if v == v else None
                if i < len(financials) and ocf is not None:
                    financials[i]["free_cash_flow"] = (ocf + (capex or 0))

        price  = info.get("currentPrice") or info.get("regularMarketPrice")
        shares = (info.get("sharesOutstanding") or 0) / 1e6
        mktcap_bn = (price * shares / 1000) if price and shares else (
            (info.get("marketCap") or 0) / 1e9)

        # Simple trailing EV/EBITDA
        ebitda_ttm = info.get("ebitda")
        ev = info.get("enterpriseValue")
        ev_ebitda = (ev / ebitda_ttm) if ev and ebitda_ttm else None

        from aeon_nimbus.country_risk import implied_wacc
        country = info.get("country", "Unknown")
        wacc = implied_wacc(country)

        # Very rough DCF: avg last 3yr FCF → 5yr growth → terminal
        fcfs = [f["free_cash_flow"] for f in financials[:3] if f.get("free_cash_flow")]
        target_price = None
        if fcfs and price:
            avg_fcf = sum(fcfs) / len(fcfs)
            g = 0.08; terminal_g = 0.025
            dcf_val = sum(avg_fcf * (1 + g)**i / (1 + wacc)**i for i in range(1, 6))
            tv = (avg_fcf * (1 + g)**5 * (1 + terminal_g) / (wacc - terminal_g)) / (1 + wacc)**5
            equity_val = (dcf_val + tv) * 1e6  # back to $
            if shares > 0:
                target_price = round(equity_val / (shares * 1e6), 2)

        upside = ((target_price / price) - 1) if target_price and price else None
        rating = "Buy" if upside and upside > 0.15 else ("Reduce" if upside and upside < -0.05 else "Hold")

        return {
            "name":    info.get("longName", query),
            "ticker":  ticker_guess,
            "exchange":info.get("exchange", ""),
            "sector":  info.get("sector", ""),
            "country": country,
            "currency":info.get("currency", "USD"),
            "financials": financials,
            "market":  {"price": price, "market_cap_usd_bn": round(mktcap_bn, 2),
                        "price_date": "live", "currency": info.get("currency","USD")},
            "ev_ebitda":   round(ev_ebitda, 1) if ev_ebitda else None,
            "pe":          info.get("trailingPE"),
            "wacc":        wacc,
            "target_price":target_price,
            "upside":      round(upside, 3) if upside else None,
            "rating":      rating,
            "summary":     info.get("longBusinessSummary","")[:600] if info.get("longBusinessSummary") else "",
            "bull_case":   f"Strong FCF generation supports a target of ${target_price:.2f}. WACC of {wacc*100:.1f}% assumes a {country} risk-free premium." if target_price else None,
            "bear_case":   "Execution risk, macro headwinds, and potential re-rating of growth multiples present downside.",
            "sources":     [f"Yahoo Finance / yfinance ({ticker_guess})", "Aeon Nimbus DCF framework"],
        }

    except ImportError:
        pass  # yfinance not installed, fall through to stub
    except Exception as e:
        # yfinance failed (e.g. unknown ticker) — return a stub with error context
        return {
            "name": query, "ticker": query.upper(), "exchange": "", "sector": "",
            "country": "", "currency": "USD", "financials": [], "market": {},
            "summary": f"Could not retrieve live data for '{query}'. Please verify the ticker symbol or try a full company name.",
            "sources": [],
        }

    # Fallback if yfinance not installed
    return {
        "name": query, "ticker": query.upper(), "exchange": "", "sector": "",
        "country": "", "currency": "USD", "financials": [], "market": {},
        "summary": "Live research requires the yfinance package. Install it with: pip install yfinance",
        "sources": [],
    }


class AddCompanyRequest(BaseModel):
    name: str
    ticker: str
    exchange: str = ""
    sector: str = ""
    country: str = ""
    currency: str = "USD"
    market_cap_usd_bn: float | None = None
    financials: list = []
    market: dict = {}
    summary: str = ""


@app.post("/api/add-company")
def add_company(req: AddCompanyRequest, db: Session = Depends(get_db),
                _user=Depends(auth.require_admin)):
    """Add a researched company to the coverage universe (admin only)."""
    import re as _re
    slug = _re.sub(r'[^a-z0-9]+', '_', req.name.lower()).strip('_')
    # Check not already in DB
    existing = db.query(D.Company).filter(D.Company.slug == slug).first()
    if existing:
        return {"ok": True, "slug": slug, "created": False, "message": "Already in coverage"}
    # Build universe entry
    uni_entry = {
        "name": req.name, "ticker": req.ticker, "exchange": req.exchange,
        "sector": req.sector, "country": req.country, "currency": req.currency,
        "valuation_model": "dcf", "market_cap_usd_bn": req.market_cap_usd_bn,
        "slug": slug, "source": "research",
    }
    # Build extracted entry from research result
    # Convert financials: research_company returns {year, revenue, ebitda, net_income, free_cash_flow}
    fins = []
    for f in (req.financials or []):
        yr = str(f.get("year") or "")
        fins.append({
            "fy": f"FY{yr}" if yr and not yr.startswith("FY") else yr,
            "revenue": f.get("revenue"), "ebitda": f.get("ebitda"),
            "net_income": f.get("net_income"), "fcf": f.get("free_cash_flow"),
            "free_cash_flow": f.get("free_cash_flow"),
            "source": f"yfinance / {req.ticker}",
        })
    market_entry = {
        "share_price": req.market.get("price"), "price_currency": req.currency,
        "price_date": req.market.get("price_date", "live"),
        "shares_outstanding_m": None,
        "market_cap_m": (req.market_cap_usd_bn or 0) * 1000 if req.market_cap_usd_bn else None,
        "source": "yfinance",
    }
    extracted_entry = {
        "name": req.name, "ticker": req.ticker, "currency": req.currency,
        "country": req.country, "sector": req.sector,
        "financials": fins, "market": market_entry,
        "sector_kpis": [], "sources": [f"yfinance ({req.ticker})", "Aeon Nimbus research engine"],
        "qualitative": {
            "business_description": req.summary,
            "market_overview": [], "revenue_drivers": [], "cost_pressures": [],
        } if req.summary else {},
        "data_quality": {"confidence": "medium", "caveats": ["Auto-collected via yfinance — verify against filings"]},
    }
    co = D.Company(slug=slug, name=req.name, ticker=req.ticker,
                   universe=uni_entry, extracted=extracted_entry)
    db.add(co); db.commit()
    return {"ok": True, "slug": slug, "created": True, "message": f"Added {req.name} ({slug}) to coverage universe"}


# ────────────────────────────────────────────────────────────────────────────
# yfinance ticker helper — shared by market-events, insiders, and live-prices
# ────────────────────────────────────────────────────────────────────────────
_YF_SUFFIX = {
    "jse": ".JO", "johannesburg": ".JO",
    "nse": ".NS", "bse": ".BO", "india": ".NS",
    "lse": ".L", "london": ".L",
    "tsx": ".TO", "toronto": ".TO",
    "asx": ".AX", "australia": ".AX",
    "hkex": ".HK", "hong kong": ".HK",
    "tse": ".T", "tokyo": ".T",
    "krx": ".KS", "korea": ".KS",
    "sgx": ".SI", "singapore": ".SI",
    "xetra": ".DE", "deutsche": ".DE",
    "euronext": ".PA",
    "nairobi": ".NR", "nse kenya": ".NR",
    "dar es salaam": ".DSE",
    "nigeria": ".LG", "nigerian": ".LG",
    "ghana": ".GH",
    "casablanca": ".CS",
    "egypt": ".CA", "egyptian": ".CA",
}


def _yf_ticker(co) -> str:
    """Build a yfinance-resolvable ticker for a Company ORM object."""
    ticker = (co.ticker or "").upper()
    yf_t = (co.universe or {}).get("yf_ticker") or ticker
    if yf_t and "." not in yf_t:
        exchange = ((co.universe or {}).get("exchange") or co.exchange or "").lower()
        for key, suffix in _YF_SUFFIX.items():
            if key in exchange:
                yf_t = yf_t + suffix
                break
    return yf_t


# ────────────────────────────────────────────────────────────────────────────
# Market events: news feed, earnings calendar, price history
# ────────────────────────────────────────────────────────────────────────────
_market_events_cache: dict = {}  # keyed by slug → {"ts": datetime, "data": dict}
_MARKET_EVENTS_TTL = 900  # 15 minutes


@app.get("/api/companies/{slug}/market-events")
def market_events(slug: str, db: Session = Depends(get_db)):
    """News feed, earnings calendar, and 1-year price history for a company."""
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    cached = _market_events_cache.get(slug)
    if cached and (now - cached["ts"]).total_seconds() < _MARKET_EVENTS_TTL:
        return cached["data"]

    company = db.query(D.Company).filter(D.Company.slug == slug).first()
    if not company:
        raise HTTPException(404, "Company not found")

    try:
        import yfinance as yf
        yf_t = _yf_ticker(company)
        tkr = yf.Ticker(yf_t)

        # ── News ──────────────────────────────────────────────────────────────
        news = []
        try:
            for item in (tkr.news or [])[:10]:
                content = item.get("content") or {}
                if content:
                    pub_at = content.get("pubDate") or ""
                    url = (content.get("canonicalUrl") or {})
                    url = url.get("url") if isinstance(url, dict) else str(url or "")
                    provider = content.get("provider") or {}
                    publisher = provider.get("displayName") if isinstance(provider, dict) else str(provider or "")
                    news.append({"title": content.get("title") or "",
                                 "url": url,
                                 "publisher": publisher,
                                 "published_at": pub_at})
                else:
                    # older yfinance shape
                    news.append({"title": item.get("title") or "",
                                 "url": item.get("link") or "",
                                 "publisher": item.get("publisher") or "",
                                 "published_at": item.get("providerPublishTime") or ""})
        except Exception:
            pass

        # ── Calendar ──────────────────────────────────────────────────────────
        calendar = {}
        try:
            cal = tkr.calendar or {}
            for k, v in cal.items():
                import datetime as _dt2
                if isinstance(v, (_dt2.date, _dt2.datetime)):
                    cal[k] = v.isoformat()
            key_map = {
                "Earnings Date": "earnings_date",
                "EPS Estimate Low": "earnings_eps_low",
                "EPS Estimate High": "earnings_eps_high",
                "EPS Estimate Average": "earnings_eps_avg",
                "Revenue Low": "revenue_low",
                "Revenue High": "revenue_high",
                "Ex-Dividend Date": "ex_dividend_date",
                "Dividend Date": "dividend_date",
            }
            for src, dst in key_map.items():
                if src in cal:
                    calendar[dst] = cal[src]
            # yfinance sometimes returns lists for date fields — take first element
            for k in ("earnings_date",):
                if isinstance(calendar.get(k), list):
                    calendar[k] = calendar[k][0] if calendar[k] else None
        except Exception:
            pass

        # ── Price history ─────────────────────────────────────────────────────
        price_history = []
        try:
            hist = tkr.history(period="1y")
            if hist is not None and not hist.empty:
                for dt_idx, row in hist.iterrows():
                    price_history.append({
                        "date": dt_idx.date().isoformat() if hasattr(dt_idx, "date") else str(dt_idx)[:10],
                        "open": round(float(row.get("Open") or 0), 4),
                        "high": round(float(row.get("High") or 0), 4),
                        "low": round(float(row.get("Low") or 0), 4),
                        "close": round(float(row.get("Close") or 0), 4),
                        "volume": int(row.get("Volume") or 0),
                    })
        except Exception:
            pass

        result = {"news": news, "calendar": calendar, "price_history": price_history}
    except ImportError:
        result = {"news": [], "calendar": {}, "price_history": [], "error": "yfinance not installed"}
    except Exception as e:
        result = {"news": [], "calendar": {}, "price_history": [], "error": str(e)[:200]}

    _market_events_cache[slug] = {"ts": now, "data": result}
    return result


# ────────────────────────────────────────────────────────────────────────────
# Macro impact sensitivity analysis
# ────────────────────────────────────────────────────────────────────────────
@app.get("/api/companies/{slug}/macro-impact")
def macro_impact(slug: str, db: Session = Depends(get_db)):
    """Sensitivity analysis: estimated revenue / target-price impact of a ±10% move
    in each macro driver relevant to this company."""
    company = db.query(D.Company).filter(D.Company.slug == slug).first()
    if not company:
        raise HTTPException(404, "Company not found")

    uni = company.universe or {}
    ext = company.extracted or {}
    merged_fins = ext.get("financials") or []
    latest = merged_fins[-1] if merged_fins else {}

    revenue = float(latest.get("revenue") or 0)
    net_debt = float(latest.get("net_debt") or 0)
    net_income = float(latest.get("net_income") or 0)

    # Approximate market cap from keystats if available
    from aeon_nimbus import platform_data as _pdata
    _merged = _pdata.merged_extracted(ext)
    _deep = _pdata.deep_from_extracted(_merged, uni)
    ks = (_deep or {}).get("keystats") or {}
    market_cap_m = float(ks.get("market_cap_m") or 0)

    macro_sources = uni.get("macro_sources") or []
    if not isinstance(macro_sources, list):
        macro_sources = []
    items = []

    for driver in macro_sources:
        dl = driver.lower()
        if any(k in dl for k in ("fx", "usd", "exchange rate", "currency")):
            fx_pct = float(ext.get("fx_revenue_pct") or 0.3)
            rev_impact = round(revenue * fx_pct * 0.1, 2)  # ±10% move on fx_pct of revenue
            category = "fx"
        elif any(k in dl for k in ("rate", "benchmark", "cbk", "cbn", "mpc", "interest")):
            is_bank = bool(_merged.get("bank"))
            loan_book = float(latest.get("total_assets") or 0)
            nim = float(ext.get("nim") or 0.04)
            if is_bank and loan_book:
                rev_impact = round(loan_book * nim * 0.001, 2)  # 10bp NIM move
            else:
                rev_impact = round(abs(net_debt) * 0.1, 2)  # 10% of interest cost
            category = "rate"
        elif any(k in dl for k in ("oil", "crude", "cement", "copper", "coal", "commodity")):
            rev_impact = round(revenue * 0.15 * 0.1, 2)
            category = "commodity"
        else:
            rev_impact = round(revenue * 0.1 * 0.1, 2)
            category = "other"

        target_impact_pct = round(rev_impact / market_cap_m, 4) if market_cap_m else None
        items.append({
            "driver": driver,
            "move": "±10%",
            "category": category,
            "revenue_impact_m": rev_impact,
            "target_impact_pct": target_impact_pct,
        })

    # Always include two standard macro items
    dxy_impact = round(revenue * 0.1 * 0.1, 2)
    vix_impact = round(market_cap_m * 0.05, 2) if market_cap_m else None
    items.append({
        "driver": "USD broad index (DXY)",
        "move": "±10%",
        "category": "fx",
        "revenue_impact_m": dxy_impact,
        "target_impact_pct": round(dxy_impact / market_cap_m, 4) if market_cap_m else None,
        "note": "Generic DXY sensitivity — adjust for company's actual USD exposure.",
    })
    items.append({
        "driver": "Global risk sentiment (VIX)",
        "move": "±10%",
        "category": "sentiment",
        "revenue_impact_m": None,
        "target_impact_pct": round(vix_impact / market_cap_m, 4) if (vix_impact and market_cap_m) else None,
        "note": "Multiple re-rating risk — VIX spike typically compresses EM multiples 5-15%.",
    })

    # Fetch live macro rates via yfinance
    _MACRO_TICKERS = {
        "DXY":  ("DX-Y.NYB", "USD broad index (DXY)", "index"),
        "VIX":  ("^VIX",     "CBOE Volatility Index (VIX)", "index"),
        "WTI":  ("CL=F",     "WTI Crude Oil (USD/bbl)", "commodity"),
        "GOLD": ("GC=F",     "Gold (USD/oz)", "commodity"),
        "US10Y":("^TNX",     "US 10Y Treasury yield (%)", "rate"),
    }
    country = (uni.get("country") or "").lower()
    # Add local FX pair based on country
    _LOCAL_FX = {
        "kenya": ("KESUSD=X", "KES/USD spot rate"),
        "nigeria": ("NGNUSD=X", "NGN/USD spot rate"),
        "ghana": ("GHSUSD=X", "GHS/USD spot rate"),
        "egypt": ("EGPUSD=X", "EGP/USD spot rate"),
        "south africa": ("ZARUSD=X", "ZAR/USD spot rate"),
        "tanzania": ("TZSUSD=X", "TZS/USD spot rate"),
        "ethiopia": ("ETBUSD=X", "ETB/USD spot rate"),
    }
    live_rates = {}
    _fetch_tickers = list(_MACRO_TICKERS.keys())
    for _country_key, (_fx_ticker, _fx_label) in _LOCAL_FX.items():
        if _country_key in country:
            _MACRO_TICKERS["LOCAL_FX"] = (_fx_ticker, _fx_label, "fx")
            break
    try:
        import yfinance as _yf2
        _syms = [v[0] for v in _MACRO_TICKERS.values()]
        _raw = _yf2.download(_syms, period="2d", auto_adjust=True, progress=False)
        _close = _raw["Close"] if "Close" in _raw else _raw
        for _key, (_sym, _label, _cat) in _MACRO_TICKERS.items():
            try:
                _col = _close[_sym] if _sym in _close.columns else None
                if _col is not None and not _col.dropna().empty:
                    _vals = _col.dropna()
                    _last = float(_vals.iloc[-1])
                    _prev = float(_vals.iloc[-2]) if len(_vals) >= 2 else _last
                    live_rates[_key] = {
                        "label": _label, "value": round(_last, 4),
                        "chg_pct": round((_last - _prev) / _prev * 100, 2) if _prev else None,
                        "category": _cat,
                    }
            except Exception:
                pass
    except Exception:
        pass

    return {"slug": slug, "drivers": items, "live_rates": live_rates}


# ────────────────────────────────────────────────────────────────────────────
# Live market prices
# ────────────────────────────────────────────────────────────────────────────
@app.get("/api/market/live-prices")
def live_prices(tickers: str = Query(...)):
    """Return live price + change% for a comma-separated list of tickers."""
    from aeon_nimbus.live_prices import get_live_prices
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        return {"prices": []}

    with D.SessionLocal() as db:
        companies = db.query(D.Company).all()
        co_by_ticker: dict[str, D.Company] = {}
        for c in companies:
            if c.ticker:
                co_by_ticker[c.ticker.upper()] = c

    co_dicts = []
    for ticker in ticker_list[:60]:
        co = co_by_ticker.get(ticker)
        if co:
            co_dicts.append({
                "ticker": ticker,
                "slug": co.slug,
                "exchange": (co.universe or {}).get("exchange", ""),
                "currency": (co.extracted or {}).get("currency") or (co.universe or {}).get("currency", ""),
                "yf_ticker": (co.universe or {}).get("yf_ticker"),
                "extracted": co.extracted or {},
            })
        else:
            co_dicts.append({"ticker": ticker, "slug": ticker.lower(), "exchange": "", "currency": ""})

    results = get_live_prices(co_dicts)
    return {"prices": results, "as_of": __import__("datetime").datetime.utcnow().isoformat()}


@app.get("/api/companies/{slug}/live-quote")
def company_live_quote(slug: str, db: Session = Depends(get_db)):
    """Real-time price, change%, market cap, and 52-week range for one company."""
    from aeon_nimbus.live_prices import resolve_yf_ticker
    co = db.query(D.Company).filter(D.Company.slug == slug).first()
    if not co:
        raise HTTPException(404, "Company not found")

    ticker = (co.ticker or "").upper()
    exchange = (co.universe or {}).get("exchange", "")
    yft = (co.universe or {}).get("yf_ticker") or resolve_yf_ticker(ticker, exchange)
    mkt = (co.extracted or {}).get("market") or {}
    val = (co.extracted or {}).get("valuation") or {}
    currency = (co.extracted or {}).get("currency") or (co.universe or {}).get("currency", "")

    result = {
        "ticker": ticker,
        "slug": slug,
        "currency": currency,
        "price": None,
        "change_pct": None,
        "market_cap_m": None,
        "week52_high": None,
        "week52_low": None,
        "volume": None,
        "source": "stored",
        "stored_price": mkt.get("share_price") or mkt.get("price"),
        "stored_price_date": mkt.get("price_date", ""),
        "valuation": {
            "target": (val.get("rating") or {}).get("target_price"),
            "bull": (val.get("rating") or {}).get("bull_price"),
            "bear": (val.get("rating") or {}).get("bear_price"),
            "stance": (val.get("rating") or {}).get("stance"),
            "upside": (val.get("base") or {}).get("upside_pct"),
            "wacc": (val.get("base") or {}).get("wacc"),
        },
    }

    if yft:
        try:
            import yfinance as yf
            info = yf.Ticker(yft).fast_info
            price = getattr(info, "last_price", None)
            prev = getattr(info, "previous_close", None)
            shares = getattr(info, "shares", None)
            hi52 = getattr(info, "year_high", None)
            lo52 = getattr(info, "year_low", None)
            vol = getattr(info, "last_volume", None)
            if price:
                chg = ((price - prev) / prev * 100) if prev and prev != 0 else None
                result.update({
                    "price": round(float(price), 4),
                    "change_pct": round(float(chg), 2) if chg is not None else None,
                    "market_cap_m": round(float(price * shares / 1e6), 1) if shares else None,
                    "week52_high": round(float(hi52), 4) if hi52 else None,
                    "week52_low": round(float(lo52), 4) if lo52 else None,
                    "volume": int(vol) if vol else None,
                    "source": "live",
                    "yf_ticker": yft,
                })
        except Exception:
            pass

    if result["price"] is None and result["stored_price"]:
        result["price"] = result["stored_price"]
        result["source"] = "stored"

    return result


# ────────────────────────────────────────────────────────────────────────────
# Insider activity (SEC EDGAR Form 4)
# ────────────────────────────────────────────────────────────────────────────
@app.get("/api/companies/{slug}/insiders")
def insider_activity(slug: str, db: Session = Depends(get_db)):
    company = db.query(D.Company).filter(D.Company.slug == slug).first()
    if not company:
        raise HTTPException(404, "Company not found")
    ticker = (company.ticker or "").upper()
    if not ticker:
        return {"insiders": [], "error": "No ticker for this company"}

    try:
        import urllib.request, json as _json
        # EDGAR full-text search for recent Form 4 filings
        url = (f"https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22"
               f"&forms=4&dateRange=custom&startdt=2024-01-01&hits.hits._source=period_of_report,display_names,file_date")
        req = urllib.request.Request(url, headers={"User-Agent": "AeonNimbus glasmikgamer@gmail.com"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read())
        hits = (data.get("hits") or {}).get("hits") or []
        insiders = []
        for h in hits[:10]:
            src = h.get("_source") or {}
            names = src.get("display_names") or []
            insiders.append({
                "name": names[0] if names else "Unknown",
                "title": "",
                "type": "BUY",  # Form 4 doesn't have type without parsing
                "shares": "—",
                "date": src.get("period_of_report") or src.get("file_date") or "—",
            })
        return {"insiders": insiders}
    except Exception as e:
        insiders = []

    # Fallback to yfinance for non-US companies when EDGAR returned nothing
    country = (company.country or "").lower()
    is_us = "united states" in country or "usa" in country or country in ("us", "u.s.")
    if not insiders and not is_us:
        try:
            import yfinance as yf
            _yft = _yf_ticker(company)
            tkr = yf.Ticker(_yft)
            df = tkr.insider_transactions
            if df is not None and not df.empty:
                for _, row in df.head(20).iterrows():
                    tx_type = str(row.get("Transaction") or row.get("transaction") or "")
                    tx_lower = tx_type.lower()
                    insiders.append({
                        "name": str(row.get("Insider") or row.get("insider") or row.get("name") or "Unknown"),
                        "date": str(row.get("Start Date") or row.get("date") or row.get("startDate") or "—"),
                        "transaction": "buy" if "purchase" in tx_lower or "buy" in tx_lower else "sell",
                        "shares": row.get("Shares") or row.get("shares"),
                        "value": row.get("Value") or row.get("value"),
                        "source": "yfinance",
                    })
        except Exception:
            pass

    if not insiders:
        return {"insiders": [], "source": "none",
                "note": "No insider data available for this exchange."}
    return {"insiders": insiders}


# ────────────────────────────────────────────────────────────────────────────
# Investor Persona Lenses (Gemini)
# ────────────────────────────────────────────────────────────────────────────
class LensRequest(BaseModel):
    pass  # slug comes from path

@app.post("/api/companies/{slug}/lenses")
def investor_lenses(slug: str, db: Session = Depends(get_db)):
    import os
    api_key = os.environ.get("GEMINI_API_KEY", "")
    company = db.query(D.Company).filter(D.Company.slug == slug).first()
    if not company:
        raise HTTPException(404, "Company not found")

    # Build a compact financial summary
    from aeon_nimbus.platform_data import assemble_live
    data = assemble_live(db)
    co = next((c for c in data["companies"] if c["slug"] == slug), None)
    d = (co or {}).get("deep") or {}
    fin_summary = ""
    if d:
        k = d.get("keystats") or {}
        fin_summary = (
            f"Ticker: {company.ticker}, Sector: {company.sector}, Country: {company.country}. "
            f"EV/EBITDA: {k.get('ev_ebitda','—')}x, P/E: {k.get('pe','—')}x. "
            f"Rating: {(d.get('rating') or {}).get('stance','—')}, "
            f"Upside: {(d.get('rating') or {}).get('upside','—')}. "
            f"EBITDA margin (last yr): {((d.get('ebitda_margin') or [None])[-1] or 'n/a')}."
        )

    PERSONAS = [
        ("Buffett", "Warren Buffett: moat, predictable earnings, ROE, owner-earnings, price paid"),
        ("Graham", "Benjamin Graham: margin of safety, net asset value, P/E below 15, no speculation"),
        ("Lynch", "Peter Lynch: PEG ratio, growth at reasonable price, understand the business"),
        ("Munger", "Charlie Munger: quality businesses at fair prices, mental models, incentives"),
        ("Marks", "Howard Marks: risk first, cycle positioning, downside before upside"),
    ]

    if not api_key:
        # Return plausible stub without AI
        lenses = []
        for name, _ in PERSONAS:
            lenses.append({"persona": name, "verdict": "HOLD",
                            "analysis": f"Configure GEMINI_API_KEY to get {name}'s lens on {company.name}.",
                            "key_metric": "—"})
        return {"lenses": lenses}

    try:
        import urllib.request, json as _json, time as _time
        lenses = []
        for name, philosophy in PERSONAS:
            prompt = (
                f"You are {name} ({philosophy}). Assess {company.name} ({company.ticker}) "
                f"as an investment. Financials: {fin_summary} "
                f"Give: verdict (BUY/HOLD/AVOID), one-paragraph analysis in first person as {name}, "
                f"and the single most important metric. Reply ONLY as valid JSON with no markdown fences: "
                f'{{\"verdict\":\"...\",\"analysis\":\"...\",\"key_metric\":\"...\"}}'
            )
            body = _json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                                 "generationConfig": {"maxOutputTokens": 600}}).encode()
            req = urllib.request.Request(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={api_key}",
                data=body, headers={"Content-Type": "application/json"}, method="POST")
            import ssl as _ssl; ctx=_ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=_ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                result = _json.loads(resp.read())
            text = result["candidates"][0]["content"]["parts"][0]["text"]
            import re
            clean = re.sub(r'```(?:json)?', '', text).strip()
            parsed = {}
            try:
                parsed = _json.loads(clean)
            except Exception:
                mx = re.search(r'\{[^{}]*\}', clean, re.DOTALL)
                if mx:
                    try: parsed = _json.loads(mx.group())
                    except Exception: pass
            lenses.append({"persona": name,
                            "verdict": parsed.get("verdict", "HOLD"),
                            "analysis": parsed.get("analysis", text[:300]),
                            "key_metric": parsed.get("key_metric", "—")})
            _time.sleep(1)
        return {"lenses": lenses}
    except Exception as e:
        return {"error": f"Gemini unavailable: {str(e)[:120]}"}


# ────────────────────────────────────────────────────────────────────────────
# Competitive Moat Rating (Gemini)
# ────────────────────────────────────────────────────────────────────────────
@app.post("/api/companies/{slug}/moat")
def moat_rating(slug: str, db: Session = Depends(get_db)):
    import os
    api_key = os.environ.get("GEMINI_API_KEY", "")
    company = db.query(D.Company).filter(D.Company.slug == slug).first()
    if not company:
        raise HTTPException(404, "Company not found")

    if not api_key:
        return {"moat": "Unknown", "rationale": "Configure GEMINI_API_KEY to enable moat assessment.",
                "durability": "—", "key_sources": "—"}

    try:
        import urllib.request, json as _json, re
        prompt = (
            f"Assess the competitive moat of {company.name} ({company.ticker}), "
            f"a {company.sector} company in {company.country}. "
            f"Choose: Wide (durable structural advantage ≥10yr), Narrow (some advantage, eroding), or None. "
            f"Also state durability (High/Medium/Low) and key sources of advantage. "
            f'Reply ONLY as valid JSON with no markdown fences: {{"moat":"Wide|Narrow|None","rationale":"...","durability":"High|Medium|Low","key_sources":"..."}}'
        )
        body = _json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                             "generationConfig": {"maxOutputTokens": 400}}).encode()
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={api_key}",
            data=body, headers={"Content-Type": "application/json"}, method="POST")
        import ssl as _ssl; ctx=_ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=_ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
            result = _json.loads(resp.read())
        text = result["candidates"][0]["content"]["parts"][0]["text"]
        clean = re.sub(r'```(?:json)?', '', text).strip()
        parsed = {}
        try:
            parsed = _json.loads(clean)
        except Exception:
            m = re.search(r'\{[^{}]*\}', clean, re.DOTALL)
            if m:
                try: parsed = _json.loads(m.group())
                except Exception: pass
        return {
            "moat": parsed.get("moat", "Unknown"),
            "rationale": parsed.get("rationale", clean[:200]),
            "durability": parsed.get("durability", "—"),
            "key_sources": parsed.get("key_sources", "—"),
        }
    except Exception as e:
        return {"error": f"Gemini unavailable: {str(e)[:120]}"}


# ────────────────────────────────────────────────────────────────────────────
# Multi-model AI Consensus (Research page)
# ────────────────────────────────────────────────────────────────────────────
class ConsensusRequest(BaseModel):
    query: str

@app.post("/api/research-consensus")
def research_consensus(req: ConsensusRequest):
    import os, urllib.request, json as _json, re
    api_key = os.environ.get("GEMINI_API_KEY", "")
    query = req.query.strip()

    MODELS = [
        ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite"),
        ("gemini-flash-lite-latest", "Gemini Flash Lite"),
        ("gemini-flash-latest", "Gemini Flash"),
    ]

    if not api_key:
        return {
            "name": query, "ticker": query.upper(),
            "consensus_rating": "Hold", "agreement_score": 0.5,
            "consensus_rationale": "Configure GEMINI_API_KEY for multi-model consensus.",
            "models": [{"model_name": m[1], "rating": "Hold", "summary": "API key required.", "target": None}
                       for m in MODELS],
            "generated_at": datetime.now(timezone.utc).date().isoformat(),
        }

    results = []
    for model_id, model_name in MODELS:
        try:
            prompt = (
                f"You are an institutional equity analyst. Research {query}. "
                f"Provide: rating (Buy/Hold/Reduce), a 2-sentence investment summary, and a 12-month price target. "
                f'Reply ONLY as valid JSON with no markdown fences: {{"rating":"Buy|Hold|Reduce","summary":"...","target":number_or_null}}'
            )
            body = _json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                                 "generationConfig": {"maxOutputTokens": 400}}).encode()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={api_key}"
            http_req = urllib.request.Request(url, data=body,
                                              headers={"Content-Type": "application/json"}, method="POST")
            import ssl as _ssl; _ctx=_ssl.create_default_context(); _ctx.check_hostname=False; _ctx.verify_mode=_ssl.CERT_NONE
            with urllib.request.urlopen(http_req, timeout=15, context=_ctx) as resp:
                result = _json.loads(resp.read())
            text = result["candidates"][0]["content"]["parts"][0]["text"]
            _c = re.sub(r'```(?:json)?', '', text).strip()
            parsed = {}
            try:
                parsed = _json.loads(_c)
            except Exception:
                _mx = re.search(r'\{[^{}]*\}', _c, re.DOTALL)
                if _mx:
                    try: parsed = _json.loads(_mx.group())
                    except Exception: pass
            results.append({"model_name": model_name, "rating": parsed.get("rating","Hold"),
                             "summary": parsed.get("summary", text[:200]),
                             "target": parsed.get("target")})
        except Exception as e:
            results.append({"model_name": model_name, "rating": "Hold",
                             "summary": f"Model unavailable: {str(e)[:80]}", "target": None})

    ratings = [r["rating"] for r in results]
    from collections import Counter
    top = Counter(ratings).most_common(1)[0]
    agreement = top[1] / len(ratings)
    consensus = top[0]

    return {
        "name": query, "ticker": query.upper(),
        "consensus_rating": consensus, "agreement_score": round(agreement, 2),
        "consensus_rationale": f"{top[1]} of {len(ratings)} models agree on {consensus}.",
        "models": results,
        "generated_at": datetime.now(timezone.utc).date().isoformat(),
    }


# ────────────────────────────────────────────────────────────────────────────
# Direct Excel model download (no project workflow needed)
# ────────────────────────────────────────────────────────────────────────────
@app.get("/api/companies/{slug}/model/download")
def download_company_model(slug: str, db: Session = Depends(get_db)):
    """Generate and stream an Excel model for a company by slug (on-demand)."""
    from aeon_nimbus.excel_model import compile_model
    from aeon_nimbus.platform_data import deep_from_extracted
    co = db.query(D.Company).filter_by(slug=slug).first()
    if not co:
        raise HTTPException(404, "Company not found")
    ext = co.extracted or {}
    uni = co.universe or {}
    if not ext.get("financials"):
        raise HTTPException(422, "No financial data for this company yet")
    # Build a minimal company dict for the model compiler
    company_dict = {**uni, **ext, "slug": slug, "name": co.name, "ticker": co.ticker,
                    "country": co.country, "sector": co.sector, "currency": co.currency,
                    "exchange": co.exchange, "valuation_model": co.valuation_model or "dcf"}
    out_dir = Path("output") / "models"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{slug}_model.xlsx"
    try:
        compile_model(company_dict, str(out_path))
    except Exception as e:
        raise HTTPException(500, f"Model compilation failed: {str(e)[:200]}")
    if not out_path.exists():
        raise HTTPException(500, "Model file not created")
    return FileResponse(str(out_path), filename=f"{slug}_model.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ────────────────────────────────────────────────────────────────────────────
# Live initiation report — regenerated on demand using current Studio assumptions
# ────────────────────────────────────────────────────────────────────────────
@app.get("/api/report/{slug}", response_class=HTMLResponse)
def live_report(slug: str, db: Session = Depends(get_db)):
    """Regenerate the initiation report on demand using live studio assumptions."""
    from aeon_nimbus import report_initiation, platform_data as pdata
    co = db.query(D.Company).filter_by(slug=slug).first()
    if not co:
        raise HTTPException(404, "Company not found")
    ext = co.extracted or {}
    uni = co.universe or {}
    # Build a minimal seed rec from stored data
    merged = pdata.merged_extracted(ext)
    deep = pdata.deep_from_extracted(merged, uni)
    if not deep:
        raise HTTPException(422, "Insufficient data to generate report for this company")
    # Build a minimal company dict resembling the seed contract so build_initiation works
    from aeon_nimbus import scenarios as scn
    fins = sorted(merged.get("financials", []), key=lambda f: f.get("fy", ""))
    mkt = merged.get("market", {}) or {}
    rating = deep.get("rating") or {}
    keystats = deep.get("keystats") or {}
    iv = {
        "target_price": rating.get("target"),
        "current_price": rating.get("price") or mkt.get("share_price"),
        "upside_downside": rating.get("upside"),
        "conviction": rating.get("conviction", "Indicative"),
        "catalysts": (uni.get("catalysts") or []),
        "key_debate": (uni.get("key_debate") or merged.get("key_debate") or "—"),
        "variant_view": (uni.get("variant_view") or merged.get("variant_view") or "—"),
        "data_caveat": (uni.get("data_caveat") or merged.get("data_caveat") or ""),
        "stop_loss": None, "position_size_pct": None, "horizon_months": None,
    }
    sc = scn.normalise(merged.get("scenarios"), fins,
                       country=uni.get("country"),
                       is_bank=bool(merged.get("bank")), bank=merged.get("bank"))
    latest = fins[-1] if fins else {}
    ebitda, rev, mcap, ev_m = (latest.get("ebitda"), latest.get("revenue"),
                                keystats.get("market_cap_m"), keystats.get("ev_m"))
    peers_raw = pdata.comps_for(uni)
    company_seed = {
        "name": co.name, "ticker": co.ticker or "", "exchange": co.exchange or "",
        "country": co.country or "", "sector": co.sector or "",
        "currency": co.currency or merged.get("currency", "USD"),
        "latest_filing": uni.get("latest_filing") or "",
        "website": uni.get("website") or co.ir_url or "",
        "market_cap": round(mcap, 1) if mcap else None,
        "enterprise_value": round(ev_m, 1) if ev_m else None,
        "investment_view": iv,
        "valuation": {
            "current_multiples": {
                "ev_ebitda": keystats.get("ev_ebitda"),
                "pe": keystats.get("pe"),
                "dividend_yield": keystats.get("div_yield"),
            },
            "peers": [{"name": p.get("name"), "ticker": p.get("ticker")} for p in (peers_raw or [])],
            "scenarios": {"sets": {sname: sv for sname, sv in (sc.get("sets") or {}).items()}},
            "peer_median_ev_ebitda": None,
            "wacc_build": {},
            "valuation_bridge": [],
        },
        "memo": {
            "executive_summary": (merged.get("executive_summary") or
                                  uni.get("executive_summary") or
                                  f"{co.name} — live report"),
            "business_model": uni.get("business_model") or "—",
            "sector_context": uni.get("sector_context") or "—",
            "country_context": uni.get("country_context") or "—",
            "historical_financials": uni.get("historical_financials") or "—",
            "valuation": uni.get("valuation_commentary") or "—",
            "liquidity": uni.get("liquidity") or "—",
            "capital_structure": uni.get("capital_structure") or "—",
        },
        "market_data": mkt,
        "financial_history": fins,
        "risks": uni.get("risks") or [],
        "documents": uni.get("documents") or [],
    }
    try:
        report_vm = report_initiation.build_initiation(
            company_seed, rec=merged, universe_entry=uni, db=db)
    except Exception as e:
        raise HTTPException(500, f"Report generation failed: {e}")
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from aeon_nimbus.config import TEMPLATE_DIR
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(("html", "xml")),
        trim_blocks=True, lstrip_blocks=True,
    )
    env.filters["money"] = report_initiation.money
    env.filters["price"] = report_initiation.price
    env.filters["pct"] = report_initiation.pct
    env.filters["mult"] = report_initiation.mult
    html = env.get_template("report.html").render(r=report_vm)
    # Write sidecar JSON for last-report-meta endpoint
    try:
        _report_rating = report_vm.get("rating") or {}
        _sidecar = {
            "slug": slug,
            "target": _report_rating.get("target_price") or _report_rating.get("target"),
            "rating": _report_rating.get("stance") or _report_rating.get("recommendation"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        _sidecar_dir = Path("output") / "reports"
        _sidecar_dir.mkdir(parents=True, exist_ok=True)
        (_sidecar_dir / f"{slug}_last.json").write_text(_json.dumps(_sidecar))
    except Exception:
        pass  # sidecar is best-effort
    return HTMLResponse(content=html)


# ────────────────────────────────────────────────────────────────────────────
# Last report metadata sidecar
# ────────────────────────────────────────────────────────────────────────────
@app.get("/api/companies/{slug}/last-report-meta")
def last_report_meta(slug: str):
    """Return the most recently generated report's target/rating from the sidecar JSON."""
    path = Path("output") / "reports" / f"{slug}_last.json"
    if not path.exists():
        return {"target": None}
    try:
        return _json.loads(path.read_text())
    except Exception:
        return {"target": None}


# ────────────────────────────────────────────────────────────────────────────
# AI Analyst Chatbot — comprehensive built-in query engine, no API key needed
# ────────────────────────────────────────────────────────────────────────────
class AnalystChatRequest(BaseModel):
    message: str
    context_slug: Optional[str] = None

def _safe_float(v):
    try: return float(v) if v is not None else None
    except: return None

def _analyst_build_summaries(companies):
    rows = []
    for c in companies:
        d = c.get("deep") or {}
        rating = d.get("rating") or {}
        ks = d.get("keystats") or {}
        rev = [_safe_float(x) for x in (d.get("revenue") or [])]
        ebitda = [_safe_float(x) for x in (d.get("ebitda") or [])]
        ni = [_safe_float(x) for x in (d.get("net_income") or [])]
        fcf = [_safe_float(x) for x in (d.get("fcf") or [])]
        net_debt = [_safe_float(x) for x in (d.get("net_debt") or [])]
        capex = [_safe_float(x) for x in (d.get("capex") or [])]
        divs = [_safe_float(x) for x in (d.get("dividends_paid") or [])]
        roe = [_safe_float(x) for x in (d.get("roe") or [])]
        ebitda_margin = [_safe_float(x) for x in (d.get("ebitda_margin") or [])]
        net_margin = [_safe_float(x) for x in (d.get("net_margin") or [])]
        yrs = d.get("years") or []
        forecast = d.get("forecast") or {}
        risks = (d.get("risks") or [])[:4]
        segments = d.get("business_segments") or d.get("segments") or []
        memo = d.get("memo") or {}

        def last(lst): return lst[-1] if lst else None
        def valid_last(lst): return next((v for v in reversed(lst) if v is not None), None)

        lrev = valid_last(rev); lebda = valid_last(ebitda); lni = valid_last(ni)
        margin = round(lebda / lrev * 100, 1) if (lrev and lebda and lrev != 0) else None
        net_mg = round(lni / lrev * 100, 1) if (lrev and lni and lrev != 0) else None
        rev_growth = None
        if len(rev) >= 2 and rev[-1] and rev[-2] and rev[-2] != 0:
            rev_growth = round((rev[-1] - rev[-2]) / abs(rev[-2]) * 100, 1)
        nd_ebitda = None
        if valid_last(net_debt) and lebda and lebda != 0:
            nd_ebitda = round(valid_last(net_debt) / lebda, 1)

        upside = _safe_float(rating.get("upside"))
        rows.append({
            "slug": c.get("slug"), "name": c.get("name"), "ticker": c.get("ticker"),
            "country": c.get("country"), "sector": c.get("sector"),
            "market_cap_usd_bn": _safe_float(c.get("market_cap_usd_bn")),
            "currency": c.get("currency") or "USD",
            "rating": rating.get("stance"), "upside_pct": upside,
            "conviction": rating.get("conviction"),
            "dcf_value": _safe_float(rating.get("value_per_share") or rating.get("target")),
            "current_price": _safe_float(rating.get("price") or ks.get("share_price")),
            "ev_ebitda": _safe_float(ks.get("ev_ebitda")), "pe": _safe_float(ks.get("pe")),
            "pb": _safe_float(ks.get("pb")), "fcf_yield": _safe_float(ks.get("fcf_yield")),
            "div_yield": _safe_float(ks.get("div_yield")),
            "revenue": rev, "ebitda": ebitda, "net_income": ni,
            "fcf": fcf, "net_debt": net_debt, "capex": capex, "dividends": divs,
            "roe": roe, "ebitda_margin_series": ebitda_margin, "net_margin_series": net_margin,
            "years": yrs,
            "latest_revenue": lrev, "latest_ebitda": lebda, "latest_ni": lni,
            "latest_fcf": valid_last(fcf), "latest_net_debt": valid_last(net_debt),
            "latest_roe": valid_last(roe), "latest_fy": last(yrs),
            "ebitda_margin_pct": margin, "net_margin_pct": net_mg,
            "rev_growth_pct": rev_growth, "nd_ebitda": nd_ebitda,
            "forecast": forecast, "risks": risks, "segments": segments, "memo": memo,
        })
    return rows

def _fmt_m(v, cur=""):
    if v is None: return "—"
    try: v = float(v)
    except: return "—"
    sign = "-" if v < 0 else ""
    av = abs(v)
    if av >= 1_000_000: return f"{sign}{cur}{av/1_000_000:.2f}tn"
    if av >= 1_000: return f"{sign}{cur}{av/1_000:.1f}bn"
    return f"{sign}{cur}{av:.0f}m"

def _fmt_price(v):
    if v is None: return "—"
    try: return f"{float(v):.2f}"
    except: return "—"

def _analyst_answer(msg: str, rows: list, focused_slug) -> str:
    import re
    from collections import defaultdict, Counter
    q = msg.lower().strip()
    focused = next((r for r in rows if r["slug"] == focused_slug), None) if focused_slug else None

    def has(*kw): return any(k in q for k in kw)
    def tbl(header, pairs, n=15):
        lines = [f"<b>{header}</b>"]
        for i, (label, val) in enumerate(pairs[:n], 1):
            label = label[:38]
            lines.append(f"{i:>2}. {label:<38} {val}")
        return "<br>".join(lines)

    def company_card(r):
        cur = r["currency"]
        lines = [f"<b>{r['name']}</b> · {r['ticker'] or ''} · {r['country']} · {r['sector']}"]
        if r["market_cap_usd_bn"]: lines.append(f"Market cap: ${r['market_cap_usd_bn']:.1f}bn USD")
        if r["latest_revenue"]: lines.append(f"Revenue ({r['latest_fy']}): {_fmt_m(r['latest_revenue'], cur)}")
        if r["ebitda_margin_pct"]: lines.append(f"EBITDA margin: {r['ebitda_margin_pct']:.1f}%")
        if r["net_margin_pct"]: lines.append(f"Net margin: {r['net_margin_pct']:.1f}%")
        if r["latest_fcf"]: lines.append(f"FCF: {_fmt_m(r['latest_fcf'], cur)}")
        if r["nd_ebitda"] is not None: lines.append(f"Net debt/EBITDA: {r['nd_ebitda']:.1f}x")
        if r["pe"]: lines.append(f"P/E: {r['pe']:.1f}x · EV/EBITDA: {r['ev_ebitda']:.1f}x" if r["ev_ebitda"] else f"P/E: {r['pe']:.1f}x")
        if r["dcf_value"]: lines.append(f"DCF target: {cur} {_fmt_price(r['dcf_value'])}")
        if r["current_price"]: lines.append(f"Current price: {cur} {_fmt_price(r['current_price'])}")
        if r["upside_pct"] is not None: lines.append(f"Upside: {r['upside_pct']*100:.0f}% · Rating: {r['rating'] or 'unrated'}")
        if r["risks"]: lines.append(f"Key risks: {'; '.join(r['risks'][:2])}")
        return "<br>".join(lines)

    def year_series(r, field, label):
        vals = r.get(field) or []
        yrs = r.get("years") or []
        if not vals or not yrs: return f"No {label} data for {r['name']}."
        cur = r["currency"]
        lines = [f"<b>{r['name']} — {label} ({cur})</b>"]
        for yr, v in zip(yrs, vals):
            lines.append(f"  {yr}: {_fmt_m(v, '') if v is not None else '—'}")
        return "<br>".join(lines)

    # ── HELP ─────────────────────────────────────────────────────────────────
    if has("help", "what can you", "what do you", "capabilities", "commands"):
        return ("<b>I can answer:</b><br>"
                "• Rank by EBITDA margin, net margin, revenue, net income, FCF, market cap<br>"
                "• P/E, EV/EBITDA, P/B, FCF yield, dividend yield rankings<br>"
                "• Revenue / EBITDA / FCF / net debt by year for any company<br>"
                "• DCF fair values, upside %, buy/hold/sell ratings<br>"
                "• Fastest growing (revenue), most leveraged (net debt/EBITDA)<br>"
                "• Sector comparison (avg margins, avg multiples)<br>"
                "• Region filter: Africa, Asia, Europe, North America<br>"
                "• Company lookup by name or ticker<br>"
                "• Forecast / scenario projections<br>"
                "• Risks, segments, business description<br>"
                "• Coverage stats, recent data, cheapest / most expensive<br><br>"
                "Just ask naturally — e.g. <i>Which tech companies have the best margins?</i>")

    # ── REGION QUICK CHECK (before coverage-stats so "Europe coverage" routes here) ──
    _AFRICA_C = {"Kenya","Nigeria","South Africa","Egypt","Ghana","Ethiopia","Tanzania","Rwanda","Uganda","Ivory Coast","Senegal","Cameroon","Morocco","Tunisia","Botswana","Zambia","Zimbabwe","Mozambique","Mauritius","Namibia","Cote d'Ivoire"}
    _EUROPE_C = {"Netherlands","Denmark","Germany","France","United Kingdom","Switzerland","Sweden","Norway","Italy","Spain","Portugal","Belgium","Austria","Poland","Finland"}
    _ASIA_C = {"Taiwan","South Korea","Japan","China","India","Singapore","Hong Kong","Indonesia","Thailand","Vietnam","Malaysia","Philippines"}
    _NA_C = {"United States","Canada","Mexico"}
    def _region_block(name, cset):
        fil = [r for r in rows if r.get("country") in cset]
        if not fil: return f"No {name} companies in coverage yet."
        buys_ = [r for r in fil if r["rating"] and "buy" in r["rating"].lower()]
        lines_ = [f"<b>{name} coverage: {len(fil)} companies · {len(buys_)} Buy ratings</b>"]
        for r in sorted(fil, key=lambda x: -(x["upside_pct"] or -1) if x["upside_pct"] is not None else -1):
            up = f"{r['upside_pct']*100:.0f}% upside" if r["upside_pct"] is not None else "unrated"
            mg = f" · {r['ebitda_margin_pct']:.0f}% margin" if r["ebitda_margin_pct"] else ""
            lines_.append(f"  <b>{r['name']}</b> ({r['sector'] or ''}){mg} · {r['rating'] or 'NR'} · {up}")
        return "<br>".join(lines_)
    if has("africa", "african") and not has("non-africa"): return _region_block("Africa", _AFRICA_C)
    if has("europe", "european"): return _region_block("Europe", _EUROPE_C)
    if has("asia", "asian"): return _region_block("Asia", _ASIA_C)
    if has("north america", "usa company", "us company", "american company", "united states company"): return _region_block("North America", _NA_C)

    # ── COVERAGE STATS ───────────────────────────────────────────────────────
    if has("how many", "coverage", "universe", "total compan", "how big"):
        regions = Counter(r["country"] for r in rows if r.get("country"))
        sectors = Counter(r["sector"] for r in rows if r.get("sector"))
        buys = sum(1 for r in rows if r["rating"] and "buy" in r["rating"].lower())
        top_s = ", ".join(f"{s} ({n})" for s, n in sectors.most_common(5))
        return (f"Coverage: <b>{len(rows)} companies</b> across {len(regions)} countries · {buys} with Buy rating.<br>"
                f"Top sectors: {top_s}.<br>"
                f"Regions: Africa, Europe, North America, Asia covered.")

    # ── DCF / FAIR VALUE ─────────────────────────────────────────────────────
    if has("dcf", "fair value", "intrinsic", "target price", "value per share", "price target"):
        if focused:
            return company_card(focused)
        rated = [(f"{r['name']} ({r['ticker'] or r['country']})",
                  f"{r['currency']} {_fmt_price(r['dcf_value'])} · {r['upside_pct']*100:.0f}% upside · {r['rating'] or ''}")
                 for r in sorted(rows, key=lambda x: -(x["upside_pct"] or 0) if x["upside_pct"] is not None else 0)
                 if r["dcf_value"]]
        if not rated: return "No DCF values computed. Run the valuation model for companies first."
        return tbl("DCF fair values — sorted by upside", rated, 20)

    # ── UPSIDE / BUY RATINGS ─────────────────────────────────────────────────
    if has("upside", "best pick", "top pick", "buy rating", "buy list", "highest upside", "most upside"):
        buys = sorted([r for r in rows if r["upside_pct"] is not None], key=lambda r: -(r["upside_pct"] or 0))
        if not buys: return "No rated companies yet."
        pairs = [(f"{r['name']} ({r['sector'] or r['country']})",
                  f"{r['upside_pct']*100:.0f}% · {r['rating'] or ''}") for r in buys]
        return tbl("Ranked by DCF upside ↓", pairs, 20)

    # ── EBITDA MARGIN ────────────────────────────────────────────────────────
    if has("ebitda margin", "operating margin", "margin rank"):
        direction = "lowest" if has("lowest", "worst", "thin") else "highest"
        ranked = sorted([r for r in rows if r["ebitda_margin_pct"] is not None],
                        key=lambda r: (r["ebitda_margin_pct"] or 0), reverse=(direction == "highest"))
        sector_filter = next((w for w in ["tech", "financ", "energy", "health", "telecom", "consumer", "material", "industri", "real estate", "util"]
                              if w in q), None)
        if sector_filter:
            ranked = [r for r in ranked if sector_filter in (r["sector"] or "").lower()]
        pairs = [(f"{r['name']} ({r['sector'] or ''})", f"{r['ebitda_margin_pct']:.1f}%") for r in ranked]
        return tbl(f"EBITDA margin — {direction} first", pairs, 20)

    # ── NET MARGIN ───────────────────────────────────────────────────────────
    if has("net margin", "profit margin", "net profit margin"):
        ranked = sorted([r for r in rows if r["net_margin_pct"] is not None],
                        key=lambda r: -(r["net_margin_pct"] or 0))
        pairs = [(f"{r['name']} ({r['sector'] or ''})", f"{r['net_margin_pct']:.1f}%") for r in ranked]
        return tbl("Net margin — highest first", pairs, 20)

    # ── MARGIN (generic) ─────────────────────────────────────────────────────
    if has("margin") and not has("net margin", "ebitda margin", "fcf margin"):
        ranked = sorted([r for r in rows if r["ebitda_margin_pct"] is not None],
                        key=lambda r: -(r["ebitda_margin_pct"] or 0))
        pairs = [(f"{r['name']} ({r['sector'] or ''})", f"{r['ebitda_margin_pct']:.1f}%") for r in ranked]
        return tbl("EBITDA margin — highest first", pairs, 20)

    # ── REVENUE ──────────────────────────────────────────────────────────────
    if has("revenue", "sales", "top line") and not has("growth"):
        if focused: return year_series(focused, "revenue", "Revenue")
        # Company name lookup in revenue context
        name_match = next((r for r in rows if r["name"].lower() in q or (r["ticker"] or "").lower() in q), None)
        if name_match: return year_series(name_match, "revenue", "Revenue")
        ranked = sorted([r for r in rows if r["latest_revenue"] is not None],
                        key=lambda r: -(r["latest_revenue"] or 0))
        pairs = [(f"{r['name']} ({r['latest_fy'] or ''})", f"{r['currency']} {_fmt_m(r['latest_revenue'])}") for r in ranked]
        return tbl("Latest revenue — largest first", pairs, 20)

    # ── REVENUE GROWTH ────────────────────────────────────────────────────────
    if has("growth", "fastest grow", "revenue growth", "growing"):
        ranked = sorted([r for r in rows if r["rev_growth_pct"] is not None],
                        key=lambda r: -(r["rev_growth_pct"] or 0))
        pairs = [(f"{r['name']} ({r['sector'] or ''})", f"{r['rev_growth_pct']:+.1f}% YoY") for r in ranked]
        return tbl("Revenue growth YoY — fastest first", pairs, 20)

    # ── NET INCOME / EARNINGS ─────────────────────────────────────────────────
    if has("net income", "earnings", "profit", "bottom line"):
        if focused: return year_series(focused, "net_income", "Net Income")
        name_match = next((r for r in rows if r["name"].lower() in q or (r["ticker"] or "").lower() in q), None)
        if name_match: return year_series(name_match, "net_income", "Net Income")
        ranked = sorted([r for r in rows if r["latest_ni"] is not None],
                        key=lambda r: -(r["latest_ni"] or 0))
        pairs = [(f"{r['name']}", f"{r['currency']} {_fmt_m(r['latest_ni'])}") for r in ranked]
        return tbl("Net income — highest first", pairs, 20)

    # ── FCF YIELD (before generic FCF) ───────────────────────────────────────
    if has("fcf yield", "free cash flow yield"):
        ranked = sorted([r for r in rows if r["fcf_yield"] is not None], key=lambda r: -(r["fcf_yield"] or 0))
        if not ranked: return "No FCF yield data available."
        pairs = [(f"{r['name']} ({r['sector'] or ''})", f"{r['fcf_yield']*100:.1f}%") for r in ranked]
        return tbl("FCF yield — highest first", pairs, 20)

    # ── FCF ───────────────────────────────────────────────────────────────────
    if has("free cash flow", "fcf", "cash flow", "cash generation"):
        if focused: return year_series(focused, "fcf", "Free Cash Flow")
        name_match = next((r for r in rows if r["name"].lower() in q or (r["ticker"] or "").lower() in q), None)
        if name_match: return year_series(name_match, "fcf", "Free Cash Flow")
        ranked = sorted([r for r in rows if r["latest_fcf"] is not None],
                        key=lambda r: -(r["latest_fcf"] or 0))
        pairs = [(f"{r['name']} ({r['sector'] or ''})", f"{r['currency']} {_fmt_m(r['latest_fcf'])}") for r in ranked]
        return tbl("Free cash flow — highest first", pairs, 20)

    # ── FCF YIELD ─────────────────────────────────────────────────────────────
    if has("fcf yield", "free cash flow yield"):
        ranked = sorted([r for r in rows if r["fcf_yield"] is not None],
                        key=lambda r: -(r["fcf_yield"] or 0))
        pairs = [(f"{r['name']} ({r['sector'] or ''})", f"{r['fcf_yield']*100:.1f}%") for r in ranked]
        return tbl("FCF yield — highest first", pairs, 20)

    # ── LEVERAGE / NET DEBT ───────────────────────────────────────────────────
    if has("net debt", "leverage", "leveraged", "most debt", "nd/ebitda", "debt/ebitda", "balance sheet"):
        if focused: return year_series(focused, "net_debt", "Net Debt")
        name_match = next((r for r in rows if r["name"].lower() in q or (r["ticker"] or "").lower() in q), None)
        if name_match: return year_series(name_match, "net_debt", "Net Debt")
        if has("leverage", "leveraged", "most debt", "net debt ebitda", "nd/ebitda"):
            ranked = sorted([r for r in rows if r["nd_ebitda"] is not None],
                            key=lambda r: -(r["nd_ebitda"] or 0))
            pairs = [(f"{r['name']} ({r['sector'] or ''})", f"{r['nd_ebitda']:.1f}x ND/EBITDA") for r in ranked]
            return tbl("Net debt/EBITDA — most leveraged first", pairs, 20)
        ranked = sorted([r for r in rows if r["latest_net_debt"] is not None],
                        key=lambda r: (r["latest_net_debt"] or 0), reverse=True)
        pairs = [(f"{r['name']}", f"{r['currency']} {_fmt_m(r['latest_net_debt'])}") for r in ranked]
        return tbl("Net debt — highest first (negative = net cash)", pairs, 20)

    # ── CAPEX ─────────────────────────────────────────────────────────────────
    if has("capex", "capital expenditure", "investment spending"):
        if focused: return year_series(focused, "capex", "CapEx")
        ranked = sorted([r for r in rows if (r["capex"] or []) and r["capex"][-1] is not None],
                        key=lambda r: -(r["capex"][-1] if r["capex"] else 0))
        pairs = [(f"{r['name']} ({r['sector'] or ''})", f"{r['currency']} {_fmt_m(r['capex'][-1])}") for r in ranked if r["capex"]]
        return tbl("CapEx — highest first", pairs, 20)

    # ── DIVIDENDS ─────────────────────────────────────────────────────────────
    if has("dividend", "yield", "income stock", "pays dividend"):
        ranked = sorted([r for r in rows if r["div_yield"] is not None],
                        key=lambda r: -(r["div_yield"] or 0))
        if not ranked: return "No dividend yield data available for the current coverage."
        pairs = [(f"{r['name']} ({r['sector'] or ''})", f"{r['div_yield']*100:.1f}% yield") for r in ranked]
        return tbl("Dividend yield — highest first", pairs, 20)

    # ── ROE ───────────────────────────────────────────────────────────────────
    if has("roe", "return on equity", "return on capital"):
        ranked = sorted([r for r in rows if r["latest_roe"] is not None],
                        key=lambda r: -(r["latest_roe"] or 0))
        pairs = [(f"{r['name']} ({r['sector'] or ''})",
                  f"{r['latest_roe']*100:.1f}%" if r["latest_roe"] < 10 else f"{r['latest_roe']:.1f}%") for r in ranked]
        return tbl("Return on equity — highest first", pairs, 20)

    # ── P/E ───────────────────────────────────────────────────────────────────
    if has("p/e", "pe ratio", "pe multiple", "price to earn", "earnings multiple"):
        direction = "lowest" if has("lowest", "cheap", "value") else "highest"
        ranked = sorted([r for r in rows if r["pe"] is not None],
                        key=lambda r: (r["pe"] or 0), reverse=(direction == "highest"))
        pairs = [(f"{r['name']} ({r['sector'] or ''})", f"{r['pe']:.1f}x") for r in ranked]
        return tbl(f"P/E multiple — {direction} first", pairs, 20)

    # ── EV/EBITDA ─────────────────────────────────────────────────────────────
    if has("ev/ebitda", "ev ebitda", "enterprise value"):
        direction = "lowest" if has("lowest", "cheap", "value") else "highest"
        ranked = sorted([r for r in rows if r["ev_ebitda"] is not None],
                        key=lambda r: (r["ev_ebitda"] or 0), reverse=(direction == "highest"))
        pairs = [(f"{r['name']} ({r['sector'] or ''})", f"{r['ev_ebitda']:.1f}x") for r in ranked]
        return tbl(f"EV/EBITDA — {direction} first", pairs, 20)

    # ── P/B ───────────────────────────────────────────────────────────────────
    if has("p/b", "pb ratio", "price to book", "book value"):
        ranked = sorted([r for r in rows if r["pb"] is not None], key=lambda r: (r["pb"] or 0))
        pairs = [(f"{r['name']} ({r['sector'] or ''})", f"{r['pb']:.1f}x") for r in ranked]
        return tbl("P/B — lowest first (cheapest to book)", pairs, 20)

    # ── CHEAPEST / VALUE ─────────────────────────────────────────────────────
    if has("cheapest", "most value", "best value", "undervalued", "value stock"):
        scored = []
        for r in rows:
            score = 0
            if r["pe"] and r["pe"] < 15: score += 2
            elif r["pe"] and r["pe"] < 25: score += 1
            if r["ev_ebitda"] and r["ev_ebitda"] < 10: score += 2
            elif r["ev_ebitda"] and r["ev_ebitda"] < 15: score += 1
            if r["upside_pct"] and r["upside_pct"] > 0.3: score += 2
            if r["fcf_yield"] and r["fcf_yield"] > 0.05: score += 1
            if score > 0: scored.append((r, score))
        scored.sort(key=lambda x: -x[1])
        if not scored: return "Not enough valuation data to score value stocks."
        pairs = [(f"{r['name']} ({r['sector'] or ''})",
                  f"score {s} · PE {r['pe']:.0f}x · EV {r['ev_ebitda']:.0f}x" if r["pe"] and r["ev_ebitda"] else f"score {s}")
                 for r, s in scored]
        return tbl("Value screen (low PE + low EV/EBITDA + upside)", pairs, 15)

    # ── MOST EXPENSIVE ───────────────────────────────────────────────────────
    if has("most expensive", "expensive", "overvalued", "richest valuation"):
        ranked = sorted([r for r in rows if r["pe"] is not None], key=lambda r: -(r["pe"] or 0))
        pairs = [(f"{r['name']} ({r['sector'] or ''})", f"{r['pe']:.0f}x PE") for r in ranked]
        return tbl("Highest P/E (most expensive)", pairs, 15)

    # ── MARKET CAP ────────────────────────────────────────────────────────────
    if has("market cap", "largest", "biggest company", "size", "biggest"):
        ranked = sorted([r for r in rows if r["market_cap_usd_bn"] is not None],
                        key=lambda r: -(r["market_cap_usd_bn"] or 0))
        pairs = [(f"{r['name']} ({r['country'] or ''})", f"${r['market_cap_usd_bn']:.1f}bn USD") for r in ranked]
        return tbl("Market cap — largest first", pairs, 20)

    # ── SECTOR COMPARISON ─────────────────────────────────────────────────────
    if has("sector", "compar", "industry", "by sector"):
        secs = defaultdict(list)
        for r in rows:
            if r["sector"]: secs[r["sector"]].append(r)
        lines = [f"<b>Sector breakdown ({len(rows)} companies)</b><br>"]
        for sec, cos in sorted(secs.items()):
            margins = [c["ebitda_margin_pct"] for c in cos if c["ebitda_margin_pct"] is not None]
            evs = [c["ev_ebitda"] for c in cos if c["ev_ebitda"] is not None]
            pes = [c["pe"] for c in cos if c["pe"] is not None]
            am = f"{sum(margins)/len(margins):.0f}%" if margins else "—"
            aev = f"{sum(evs)/len(evs):.1f}x" if evs else "—"
            ape = f"{sum(pes)/len(pes):.0f}x" if pes else "—"
            buys = sum(1 for c in cos if c["rating"] and "buy" in c["rating"].lower())
            lines.append(f"  <b>{sec}</b> ({len(cos)}) · margin {am} · EV/EBITDA {aev} · PE {ape} · {buys} buys")
        return "<br>".join(lines)

    # ── FORECAST / PROJECTIONS ───────────────────────────────────────────────
    if has("forecast", "projection", "estimate", "bull", "bear", "base case", "scenario"):
        r = focused
        if not r:
            name_match = next((x for x in rows if x["name"].lower() in q or (x["ticker"] or "").lower() in q), None)
            r = name_match
        if r and r["forecast"]:
            f = r["forecast"]
            cur = r["currency"]
            lines = [f"<b>{r['name']} — Forecast scenarios</b>"]
            for scenario in ["bull", "base", "bear"]:
                sc = f.get(scenario) or f.get(f"{scenario}_case") or {}
                if sc:
                    rev_f = sc.get("revenue") or sc.get("rev")
                    ebitda_f = sc.get("ebitda")
                    val = sc.get("value") or sc.get("value_per_share")
                    wacc = sc.get("wacc")
                    tg = sc.get("terminal") or sc.get("terminal_growth")
                    parts = [scenario.title()]
                    if rev_f: parts.append(f"rev {_fmt_m(rev_f, cur)}")
                    if ebitda_f: parts.append(f"EBITDA {_fmt_m(ebitda_f, cur)}")
                    if val: parts.append(f"DCF {_fmt_price(val)}")
                    if wacc: parts.append(f"WACC {wacc*100:.1f}%")
                    if tg: parts.append(f"TG {tg*100:.1f}%")
                    lines.append("  " + " · ".join(parts))
            return "<br>".join(lines) if len(lines) > 1 else f"Forecast data not available for {r['name'] if r else 'this company'}."
        if not r:
            rated_with_forecast = [x for x in rows if x["forecast"] and (x["forecast"].get("bull") or x["forecast"].get("base"))]
            if rated_with_forecast:
                lines = [f"<b>Companies with forecast scenarios ({len(rated_with_forecast)})</b>"]
                for x in rated_with_forecast[:10]:
                    f = x["forecast"]; base = f.get("base") or f.get("base_case") or {}
                    val = base.get("value") or base.get("value_per_share")
                    lines.append(f"  {x['name']} · base DCF: {x['currency']} {_fmt_price(val)}" if val else f"  {x['name']}")
                return "<br>".join(lines)
        return "Specify a company to see its forecast — e.g. 'ASML forecast' or 'Safaricom scenarios'."

    # ── RISKS ────────────────────────────────────────────────────────────────
    if has("risk", "risks", "downside risk", "what could go wrong"):
        if focused:
            r = focused
            if r["risks"]: return f"<b>{r['name']} — Key risks</b><br>" + "<br>".join(f"• {x}" for x in r["risks"])
            return f"No risk data stored for {r['name']}."
        name_match = next((r for r in rows if r["name"].lower() in q or (r["ticker"] or "").lower() in q), None)
        if name_match:
            r = name_match
            if r["risks"]: return f"<b>{r['name']} — Key risks</b><br>" + "<br>".join(f"• {x}" for x in r["risks"])
        # Generic risk summary
        all_risks = [(r["name"], risk) for r in rows for risk in r["risks"]]
        common = ["geopolit", "fx", "currency", "rate", "regulat", "compet", "credit", "macro"]
        counts = Counter()
        for _, risk in all_risks:
            for c in common:
                if c in risk.lower(): counts[c] += 1
        lines = [f"<b>Most common risk themes across {len(rows)} companies</b>"]
        for k, n in counts.most_common(8):
            lines.append(f"  {k.title()}: {n} companies")
        return "<br>".join(lines)

    # ── SEGMENTS / BUSINESS DESCRIPTION ──────────────────────────────────────
    if has("segment", "business", "division", "what does", "what is", "describe"):
        r = focused
        if not r:
            name_match = next((x for x in rows if x["name"].lower() in q or (x["ticker"] or "").lower() in q), None)
            r = name_match
        if r:
            lines = [f"<b>{r['name']}</b> · {r['country']} · {r['sector']}"]
            if r["segments"]:
                segs = r["segments"]
                lines.append(f"Business segments ({len(segs)}):")
                for s in (segs if isinstance(segs, list) else [])[:8]:
                    if isinstance(s, str): lines.append(f"  • {s}")
                    elif isinstance(s, dict): lines.append(f"  • {s.get('name','')} {s.get('value','')}")
            else:
                lines.append("Segment data not available — check the company page for details.")
            return "<br>".join(lines)
        return "Name a company to see its business description — e.g. 'What does ASML do?'"

    # ── RECENT / CHANGED ─────────────────────────────────────────────────────
    if has("recent", "latest", "changed", "new", "update", "current"):
        fy_ranked = sorted([r for r in rows if r["latest_fy"]], key=lambda r: r["latest_fy"], reverse=True)
        if not fy_ranked: return "No fiscal year data available."
        lines = [f"<b>Most recently updated companies</b> (by latest FY in dataset)"]
        for r in fy_ranked[:15]:
            rev_str = f" · rev {r['currency']} {_fmt_m(r['latest_revenue'])}" if r["latest_revenue"] else ""
            lines.append(f"  {r['latest_fy']}: <b>{r['name']}</b>{rev_str}")
        return "<br>".join(lines)

    # ── REGION FILTERS (generic "America" fallback) ───────────────────────────
    if has("america", "us company", "usa company", "united states", "american"): return _region_block("North America", _NA_C)

    # ── SECTOR-SPECIFIC ───────────────────────────────────────────────────────
    SECTOR_KWORDS = {
        "Technology": ["tech", "software", "semiconductor", "chip"],
        "Financials": ["financ", "bank", "banking", "insurance"],
        "Energy": ["energy", "oil", "gas", "petroleum"],
        "Healthcare": ["health", "pharma", "medical", "biotech"],
        "Telecommunications": ["telecom", "mobile", "wireless"],
        "Consumer Staples": ["consumer staple", "staple", "food", "beverage"],
        "Consumer Discretionary": ["consumer disc", "retail", "luxury", "automobile", "auto"],
        "Industrials": ["industri", "manufacturing", "aerospace"],
        "Materials": ["material", "mining", "chemical"],
        "Real Estate": ["real estate", "reit", "property"],
        "Utilities": ["util"],
        "Communication Services": ["communication", "media", "streaming"],
    }
    for sector_name, keywords in SECTOR_KWORDS.items():
        if any(k in q for k in keywords):
            filtered = [r for r in rows if sector_name.lower() in (r["sector"] or "").lower()]
            if not filtered: continue
            margins = sorted([r for r in filtered if r["ebitda_margin_pct"] is not None],
                             key=lambda r: -(r["ebitda_margin_pct"] or 0))
            lines = [f"<b>{sector_name} — {len(filtered)} companies</b>"]
            for r in filtered:
                m = f"{r['ebitda_margin_pct']:.0f}% margin" if r["ebitda_margin_pct"] else "—"
                up = f"{r['upside_pct']*100:.0f}% upside" if r["upside_pct"] is not None else "unrated"
                lines.append(f"  <b>{r['name']}</b> · {r['country']} · {m} · {r['rating'] or 'NR'} · {up}")
            return "<br>".join(lines)

    # ── RESEARCH NOTES (knowledge base) — check before name match ────────────
    if has("what do we know", "prior research", "research on", "notes on", "what have we", "knowledge base"):
        return "__RESEARCH_NOTES_QUERY__"

    # ── THESIS GATES — check before name match ────────────────────────────────
    if has("thesis gate", "kill condition", "thesis check", "gate status", "position gate"):
        return "__THESIS_GATE_QUERY__"

    # ── COMPANY LOOKUP ────────────────────────────────────────────────────────
    # Try partial match on name or ticker
    name_match = next((r for r in rows
                       if any(word in r["name"].lower() for word in q.split() if len(word) > 2)
                       or (r["ticker"] and r["ticker"].lower() in q)), None)
    if name_match:
        return company_card(name_match)

    # ── EBITDA by year ────────────────────────────────────────────────────────
    if has("ebitda"):
        if focused: return year_series(focused, "ebitda", "EBITDA")
        ranked = sorted([r for r in rows if r["latest_ebitda"] is not None],
                        key=lambda r: -(r["latest_ebitda"] or 0))
        pairs = [(f"{r['name']}", f"{r['currency']} {_fmt_m(r['latest_ebitda'])}") for r in ranked]
        return tbl("Latest EBITDA — highest first", pairs, 20)

    # ── NET CASH / CASH-RICH ─────────────────────────────────────────────────
    if has("cash rich", "net cash", "cash position"):
        cash_cos = sorted([r for r in rows if r["latest_net_debt"] is not None and r["latest_net_debt"] < 0],
                          key=lambda r: r["latest_net_debt"] or 0)
        if not cash_cos: return "No companies with net cash positions in the dataset."
        pairs = [(f"{r['name']} ({r['sector'] or ''})", f"{r['currency']} {_fmt_m(abs(r['latest_net_debt']))} net cash") for r in cash_cos]
        return tbl("Net cash positions (net debt < 0)", pairs, 15)

    # ── FALLBACK ─────────────────────────────────────────────────────────────
    return (f"I couldn't match a specific query for: <i>{msg[:80]}</i><br><br>"
            "Try: <b>margin ranking</b> · <b>DCF upside</b> · <b>sector comparison</b> · "
            "<b>revenue by year</b> · <b>Africa picks</b> · <b>fastest growing</b> · "
            "<b>most leveraged</b> · <b>cheapest PE</b> · <b>FCF ranking</b> · <b>help</b>")


@app.post("/api/analyst-chat")
def analyst_chat(req: AnalystChatRequest, db: Session = Depends(get_db)):
    import os, urllib.request, json as _json
    message = req.message.strip()
    if not message:
        return {"reply": "Ask me anything about the coverage universe.", "grounded": True}

    from aeon_nimbus.platform_data import assemble_live
    data = assemble_live(db)
    rows = _analyst_build_summaries(data.get("companies", []))

    answer = _analyst_answer(message, rows, req.context_slug)

    # ── Research notes KB lookup ──────────────────────────────────────────────
    if answer == "__RESEARCH_NOTES_QUERY__":
        from aeon_nimbus.db import ResearchNote
        q = message.lower()
        # extract any ticker-like words (2-5 uppercase chars) from the original message
        import re as _re
        _STOP = {"WHAT","DO","WE","KNOW","ABOUT","PRIOR","RESEARCH","ON","NOTES","HAVE","THE",
                 "AND","FOR","OR","IN","AT","OF","A","AN","IS","IT","TO","FROM","ALL","ANY",
                 "SHOW","PAST","COVERED","COMPANIES","COMPANY","BASE","KNOWLEDGE","US","ME","MY"}
        tickers_mentioned = [t for t in _re.findall(r'\b([A-Z]{2,5})\b', message.upper())
                             if t not in _STOP]
        notes_q = db.query(ResearchNote).order_by(ResearchNote.created_at.desc())
        if tickers_mentioned:
            # filter to notes whose tickers field contains any mentioned ticker
            from sqlalchemy import or_
            filters = [ResearchNote.tickers.ilike(f"%{t}%") for t in tickers_mentioned]
            notes_q = notes_q.filter(or_(*filters))
        notes = notes_q.limit(10).all()
        if not notes:
            scope = f" for {', '.join(tickers_mentioned)}" if tickers_mentioned else ""
            answer = (f"No research notes found{scope}. "
                      "Notes are added via the Knowledge Base as research is completed.")
        else:
            lines = [f"<b>Research notes{' — ' + ', '.join(tickers_mentioned) if tickers_mentioned else ''}:</b><br>"]
            for n in notes:
                date_str = n.date or n.created_at.strftime("%Y-%m-%d")
                task_str = f"[{n.task}] " if n.task else ""
                tickers_str = f"<b>{n.tickers}</b> — " if n.tickers else ""
                lines.append(f"<b>{date_str}</b> {task_str}{tickers_str}{n.finding[:300]}")
            answer = "<br>".join(lines)

    # ── Thesis gate lookup ────────────────────────────────────────────────────
    if answer == "__THESIS_GATE_QUERY__":
        import pathlib
        gates_dir = pathlib.Path(__file__).resolve().parents[3] / "outputs" / "thesis_gates"
        if not gates_dir.exists() or not list(gates_dir.glob("*.md")):
            answer = ("No thesis gates on file yet. Write a four-line gate (Edge · Catalyst · "
                      "Numbers · Kill condition) and save it to <b>outputs/thesis_gates/</b> before "
                      "initiating research on any new position.")
        else:
            gate_files = sorted(gates_dir.glob("*.md"))
            lines = ["<b>Thesis gate status:</b><br>"]
            for gf in gate_files[:15]:
                text = gf.read_text(encoding="utf-8", errors="ignore")
                ticker = gf.stem.split("-")[0].upper()
                kill = ""
                for ln in text.splitlines():
                    if "kill condition" in ln.lower() or "line 4" in ln.lower():
                        kill = ln.strip("# ").replace("LINE 4 — KILL CONDITION", "").strip()
                        break
                kill_display = (kill[:120] + "…") if len(kill) > 120 else kill
                lines.append(f"<b>{ticker}</b> — Kill: {kill_display or '(see gate file)'}")
            answer = "<br>".join(lines)

    # Optional Gemini enrichment for open-ended questions
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if api_key and "couldn't match" in answer:
        try:
            import ssl as _ssl
            compact = [{k: v for k, v in r.items()
                        if k not in ("revenue","ebitda","net_income","fcf","net_debt","capex","dividends",
                                     "roe","ebitda_margin_series","net_margin_series","years","forecast","memo","segments")}
                       for r in rows]
            context = _json.dumps({"companies": compact}, default=str)[:10000]
            sys_p = ("You are the Aeon Nimbus AI Analyst. Answer ONLY from the platform data. "
                     "Never fabricate figures. Say 'not in coverage' if absent.\n\nDATA:\n" + context)
            body = _json.dumps({
                "system_instruction": {"parts": [{"text": sys_p}]},
                "contents": [{"parts": [{"text": message}]}],
                "generationConfig": {"maxOutputTokens": 500, "temperature": 0.1},
            }).encode()
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={api_key}"
            r2 = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
            ctx = _ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = _ssl.CERT_NONE
            with urllib.request.urlopen(r2, timeout=20, context=ctx) as resp:
                result = _json.loads(resp.read())
            answer = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception:
            pass

    return {"reply": answer, "grounded": True}


class ResearchNoteRequest(BaseModel):
    date: str = ""
    task: str = ""
    tickers: str = ""
    finding: str
    file_path: str = ""
    links_to: str = ""


@app.post("/api/research-notes")
def add_research_note(req: ResearchNoteRequest, db: Session = Depends(get_db)):
    """Save a research finding to the knowledge base."""
    from aeon_nimbus.db import ResearchNote
    note = ResearchNote(
        date=req.date or None,
        task=req.task or None,
        tickers=req.tickers or None,
        finding=req.finding,
        file_path=req.file_path or None,
        links_to=req.links_to or None,
    )
    db.add(note)
    db.commit()
    return {"id": note.id, "status": "saved"}


@app.get("/api/research-notes")
def list_research_notes(ticker: str = "", limit: int = 20, db: Session = Depends(get_db)):
    """List research notes, optionally filtered by ticker."""
    from aeon_nimbus.db import ResearchNote
    q = db.query(ResearchNote).order_by(ResearchNote.created_at.desc())
    if ticker:
        q = q.filter(ResearchNote.tickers.ilike(f"%{ticker}%"))
    notes = q.limit(min(limit, 100)).all()
    return {"notes": [
        {"id": n.id, "date": n.date, "task": n.task, "tickers": n.tickers,
         "finding": n.finding, "created_at": n.created_at.isoformat()}
        for n in notes
    ]}

