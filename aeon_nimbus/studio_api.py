"""Data Studio API (spec §16, interactive) — the four-button workflow.

Endpoints the dashboard's Data Studio panel drives:
  * POST /api/studio/companies/{cid}/upload   — parse an uploaded xlsx/csv/pdf
  * POST /api/studio/companies/{cid}/collect   — kick off open-web collection (job)
  * GET  /api/studio/companies/{cid}/proposed  — the review table
  * POST /api/studio/proposed/{pid}/review     — approve / reject one proposal
  * POST /api/studio/companies/{cid}/recompute — re-run valuation on approved data
  * GET  /api/studio/companies/{cid}/export    — download the linked Excel model

Collected/uploaded figures land as ProposedFact and only merge into the working
copy when they clear the caller's confidence threshold or are approved here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from aeon_nimbus import config as cfg
from aeon_nimbus import controls as ctrl
from aeon_nimbus import auth as A
from aeon_nimbus import db as D
from aeon_nimbus import jobs, studio_core
from aeon_nimbus import ingest
from aeon_nimbus import platform_data as pdata
from aeon_nimbus import scenarios as scn
from aeon_nimbus.excel_model import compile_model
from aeon_nimbus.platform_data import deep_from_extracted
from aeon_nimbus import filing_alerts as FA

router = APIRouter(prefix="/api/studio", tags=["data-studio"])

# Cache for expensive operations
_cache = {
    "deep_computations": {},  # slug -> (timestamp, deep_data)
    "multiples": None,  # cached multiples table
    "multiples_ts": None,
}
_cache_ttl = 3600  # 1 hour


def get_db():
    db = D.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _company(db: Session, cid: int) -> "D.Company":
    co = db.get(D.Company, cid)
    if co is None:
        raise HTTPException(404, "company not found")
    return co


def _fresh(co: "D.Company") -> dict:
    """Recomputed panes + data-quality from the current working copy, with any analyst
    overrides layered on (sourced baseline stays untouched)."""
    ext = pdata.merged_extracted(co.extracted or {})
    return {"deep": deep_from_extracted(ext, co.universe or {}) if ext.get("financials") else None,
            "data_quality": ctrl.data_quality_score(ext),
            "overrides": (co.extracted or {}).get("overrides") or {}}


class CollectIn(BaseModel):
    threshold: float = 0.85


class ReviewIn(BaseModel):
    decision: str  # approved | rejected


class ScenariosIn(BaseModel):
    scenarios: dict


def _scenarios_for(co: "D.Company") -> tuple:
    ext = pdata.merged_extracted(co.extracted or {})  # DCF reflects analyst edits
    fins = sorted(ext.get("financials", []), key=lambda f: f.get("fy", ""))
    scen = scn.normalise(ext.get("scenarios"), fins, country=(co.universe or {}).get("country"),
                         is_bank=ext.get("bank") is not None, bank=ext.get("bank"))
    return scen, fins


def _peers_for_sector(sector: str, country: Optional[str], exclude_name: str, limit: int = 5) -> list:
    """Same-sector comparables from the covered universe (they carry data for the comps
    table), same country first. Gives a new company a peer set so the peer multiples and
    the comps table populate — deterministic, no fabrication."""
    import json as _json
    try:
        cov = _json.loads((D.ROOT / "data" / "universe.json").read_text())
        cov = cov if isinstance(cov, list) else cov.get("companies", [])
    except Exception:
        return []
    same = [c for c in cov if c.get("sector") == sector and c.get("name") != exclude_name]
    same.sort(key=lambda c: (c.get("country") != country, c.get("name")))
    return [{"name": c["name"], "ticker": c.get("ticker")} for c in same[:limit]]


class NewCompanyIn(BaseModel):
    name: str
    ticker: str = ""
    exchange: str = ""
    country: str = ""
    sector: str = "Other"
    currency: str = "USD"
    ir_url: str = ""            # optional official filing / IR page for the collector
    collect: bool = False       # kick off web research immediately after creating
    threshold: float = 0.85


@router.post("/companies")
def create_company(body: NewCompanyIn, db: Session = Depends(get_db), _user=Depends(A.require_analyst)):
    """Add a company to the platform (modular coverage). Creates a source-disciplined
    shell; when ``collect`` is set, immediately starts real web research (the official
    filing + public financial sources) and returns a job_id to poll."""
    import re as _re
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "company name is required")
    base = _re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "company"
    slug, i = base, 2
    while db.query(D.Company).filter_by(slug=slug).first():
        slug, i = f"{base}_{i}", i + 1
    ccy = (body.currency or "USD").strip() or "USD"
    ir = (body.ir_url or "").strip() or None
    uni = {"slug": slug, "name": name, "ticker": (body.ticker or None), "exchange": (body.exchange or None),
           "country": (body.country or None), "sector": (body.sector or "Other"), "currency": ccy,
           "ir_url": ir, "annual_report_url": ir,
           "peers": _peers_for_sector(body.sector or "Other", body.country or None, name),
           "segments": [], "risks": [], "sector_kpis": []}
    co = D.Company(slug=slug, name=name, ticker=(body.ticker or None), exchange=(body.exchange or None),
                   country=(body.country or None), sector=(body.sector or "Other"), currency=ccy,
                   ir_url=ir, universe=uni, extracted={"currency": ccy, "unit": "millions", "financials": []})
    db.add(co); db.commit(); db.refresh(co)
    D.audit(db, "studio_company_created", "company", co.id, name=name, ticker=body.ticker, actor=_user.email)
    job_id = jobs.start_collection(co.id, body.threshold) if body.collect else None
    return {"id": co.id, "slug": slug, "name": name, "job_id": job_id}


@router.delete("/companies/{cid}")
def delete_company(cid: int, db: Session = Depends(get_db), _user=Depends(A.require_analyst)):
    """Remove a company and everything staged for it (proposals, projects, jobs,
    generated models). Irreversible; the seeded universe can be restored by re-seeding."""
    co = _company(db, cid)
    name = co.name
    db.query(D.ProposedFact).filter_by(company_id=cid).delete()
    for proj in db.query(D.Project).filter_by(company_id=cid).all():
        db.query(D.Job).filter_by(project_id=proj.id).delete()
        db.query(D.GeneratedModel).filter_by(project_id=proj.id).delete()
        for tbl in ("Control", "FinancialFact"):
            m = getattr(D, tbl, None)
            if m is not None:
                db.query(m).filter_by(**({"project_id": proj.id} if tbl == "Control" else {"company_id": cid})).delete()
        db.delete(proj)
    db.delete(co)
    db.commit()
    D.audit(db, "studio_company_deleted", "company", cid, name=name, actor=_user.email)
    return {"deleted": cid, "name": name}


@router.post("/companies/{cid}/draft-analysis")
def draft_analysis(cid: int, db: Session = Depends(get_db), _user=Depends(A.require_analyst)):
    """AI qualitative layer — draft the narrative sections (market overview, revenue
    drivers, cost pressures, non-financial notes, outlook) from the company's OWN
    sourced facts. The draft is marked for review and never auto-merged; the numbers
    stay deterministic. Needs an API key configured (see aeon_nimbus/ai_research.py)."""
    co = _company(db, cid)
    try:
        from aeon_nimbus import ai_research
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"AI module unavailable: {e}")
    facts = ai_research.facts_from(co.extracted or {}, co.universe or {})
    try:
        draft = ai_research.draft_qualitative(facts)
    except SystemExit as e:                    # no API key configured
        raise HTTPException(503, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"AI drafting failed: {e}")
    # Auto-persist the draft into extracted.qualitative so the Report tab shows it immediately.
    # Marked review_required=True — the platform renders it with a draft banner until approved.
    import copy as _copy
    ext = _copy.deepcopy(co.extracted or {})
    ext["qualitative"] = draft
    co.extracted = ext
    db.add(co)
    db.commit()
    D.audit(db, "ai_draft_analysis", "company", cid, model=draft.get("model"), actor=_user.email)
    return {"company_id": cid, "draft": draft, "note": "AI draft saved — visible in Report tab. Pending analyst review."}


@router.post("/companies/{cid}/upload")
async def upload_file(cid: int, file: UploadFile = File(...), threshold: float = Form(0.85),
                      db: Session = Depends(get_db), _user=Depends(A.require_analyst)):
    from aeon_nimbus import ai_research
    co = _company(db, cid)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    fname = file.filename or "upload"
    # a PDF is a filing → read it with the AI (same engine as the auto path), each figure
    # sourced to its page. Spreadsheets/CSV parse deterministically. AI falls back to the
    # deterministic PDF parser when no key is configured.
    if fname.lower().endswith(".pdf") and ai_research.available():
        view = {**studio_core.company_view(co), "filing_name": fname}
        proposals = ai_research.extract_statements_from_pdf(data, view)
        origin = "filing_ai"
    else:
        try:
            proposals = ingest.parse_file(fname, data, studio_core.company_view(co))
        except ValueError as e:
            raise HTTPException(415, str(e))
        origin = "upload"
    if not proposals:
        raise HTTPException(422, "no recognisable financial line items found in the file")
    summary = studio_core.stage_proposals(db, co, proposals, origin, threshold,
                                          actor=_user.email)
    return {**summary, **_fresh(co)}


@router.post("/companies/{cid}/extract-filing")
async def extract_filing(cid: int, file: UploadFile = File(None), url: str = Form(None),
                         threshold: float = Form(0.85), db: Session = Depends(get_db),
                         _user=Depends(A.require_analyst)):
    """Filing-first, AI-sourced path: read the company's audited filing PDF (uploaded or by
    URL) and extract the reported statements with the LLM, each figure staged with its filing
    page for review. This is the primary source; the aggregator stays a fallback for what a
    filing does not carry (e.g. the live share price)."""
    from aeon_nimbus import ai_research
    co = _company(db, cid)
    if not ai_research.available():
        raise HTTPException(503, "AI extraction needs an API key — set ANTHROPIC_API_KEY (or "
                                 "AI_PROVIDER=openai + OPENAI_API_KEY) on the server, then retry.")
    view = studio_core.company_view(co)
    if file is not None:
        data = await file.read()
        view = {**view, "filing_name": file.filename or "audited filing"}
    elif url:
        try:
            content, ctype = ingest._http_get(url)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"could not fetch the filing: {e}")
        is_pdf = "pdf" in (ctype or "").lower() or url.lower().endswith(".pdf")
        if not content or not is_pdf:
            raise HTTPException(415, "the URL did not return a PDF filing")
        data = content
        view = {**view, "filing_name": url.rsplit("/", 1)[-1] or "audited filing", "financials_url": url}
        u = dict(co.universe or {})            # remember the filing as this company's source of record
        u["financials_url"] = url
        co.universe = u
        db.add(co)
        db.commit()
    else:
        raise HTTPException(400, "provide a filing PDF (file) or a url")
    if not data:
        raise HTTPException(400, "empty filing")
    try:
        proposals = ai_research.extract_statements_from_pdf(data, view)
    except SystemExit as e:  # no key surfaced from deep in the stack
        raise HTTPException(503, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"extraction failed: {e}")
    if not proposals:
        raise HTTPException(422, "no statement figures could be extracted from this filing")
    summary = studio_core.stage_proposals(db, co, proposals, "filing_ai", threshold,
                                          actor=_user.email)
    D.audit(db, "filing_extracted", "company", cid, facts=len(proposals),
            source=view.get("filing_name"), actor=_user.email)
    return {**summary, **_fresh(co)}


@router.post("/companies/{cid}/collect")
def collect_web(cid: int, body: CollectIn, db: Session = Depends(get_db), _user=Depends(A.require_analyst)):
    _company(db, cid)
    jid = jobs.start_collection(cid, body.threshold)
    return {"job_id": jid, "kind": "web_collection"}


@router.get("/companies/{cid}/proposed")
def list_proposed(cid: int, batch: Optional[str] = Query(None),
                  status: Optional[str] = Query(None), db: Session = Depends(get_db)):
    _company(db, cid)
    q = db.query(D.ProposedFact).filter_by(company_id=cid)
    if batch:
        q = q.filter_by(batch=batch)
    if status:
        q = q.filter_by(status=status)
    rows = q.order_by(D.ProposedFact.id.desc()).limit(500).all()
    return {"proposed": [studio_core._serialize(r) for r in rows]}


@router.post("/proposed/{pid}/review")
def review_proposed(pid: int, body: ReviewIn, db: Session = Depends(get_db), _user=Depends(A.require_analyst)):
    pf = db.get(D.ProposedFact, pid)
    if pf is None:
        raise HTTPException(404, "proposal not found")
    if body.decision not in {"approved", "rejected"}:
        raise HTTPException(400, "decision must be approved or rejected")
    if body.decision == "approved":
        studio_core.approve(db, pf, actor=_user.email)
    else:
        studio_core.reject(db, pf, actor=_user.email)
    return {"id": pid, "status": pf.status, **_fresh(db.get(D.Company, pf.company_id))}


@router.post("/companies/{cid}/recompute")
def recompute(cid: int, db: Session = Depends(get_db), _user=Depends(A.require_analyst)):
    co = _company(db, cid)
    D.audit(db, "studio_recompute", "company", cid, actor=_user.email)
    return _fresh(co)


# --- analyst edits (modular / dynamic) ------------------------------------
# The analyst can override any sourced value; the collected data is the immutable
# baseline (never mutated) and edits layer on top via platform_data.merged_extracted,
# so a reset simply drops the overrides. Everything downstream — the deep panes, the
# DCF, the KPIs and the Excel export — reads through the merged view and updates at once.

class EditIn(BaseModel):
    section: str                      # "financials" | "market"
    item: str                         # line-item key (financials) or field (market)
    fy: Optional[str] = None          # required for a financials edit
    value: Optional[float] = None     # None clears this one override


class ResetIn(BaseModel):
    section: Optional[str] = None      # "financials" | "market"; None resets everything
    fy: Optional[str] = None           # optional: reset just one financials year
    item: Optional[str] = None         # optional: reset just one field


def current_value(co: "D.Company", section: str, item: str, fy: Optional[str]):
    """What a field reads as right now, before a write replaces it.

    A change log that records only the new value tells a reader what a figure
    became and not what it was, which is the half that matters when deciding
    whether to put it back. The effective value is the sourced baseline with any
    existing override already layered on, which is what the analyst was actually
    looking at when they typed.
    """
    rec = pdata.merged_extracted(co.extracted or {})
    if section == "financials":
        for f in rec.get("financials") or []:
            if f.get("fy") == fy:
                return f.get(item)
        return None
    if section == "market":
        return (rec.get("market") or {}).get(item)
    if section == "narrative":
        return ((co.extracted or {}).get("overrides") or {}).get("narrative", {}).get(item)
    return None


def where_of(co: "D.Company", section: str, item: str = "", fy: str = "") -> str:
    """A readable path to the thing that changed, for the change log.

    "Safaricom PLC / Financials / revenue / FY2026" reads at a glance and tells
    an analyst which screen to open. Knowing that something changed without
    knowing where is not much better than not knowing.
    """
    label = {"financials": "Financials", "market": "Market data",
             "narrative": "Commentary", "meta": "Company details",
             "scenarios": "Assumptions", "all": "All analyst edits"}
    parts = [co.name if co is not None else "", label.get(section, section or "")]
    if item:
        parts.append(item)
    if fy:
        parts.append(fy)
    return " / ".join(p for p in parts if p)


def _apply_edit(extracted: dict, section: str, item: str, fy: Optional[str], value) -> dict:
    ext = dict(extracted or {})
    ov = {k: (dict(v) if isinstance(v, dict) else v) for k, v in (ext.get("overrides") or {}).items()}
    if section == "financials":
        if not fy:
            raise HTTPException(422, "fy is required to edit a financial line")
        fin = {yr: dict(items) for yr, items in (ov.get("financials") or {}).items()}
        yr = dict(fin.get(fy) or {})
        if value is None:
            yr.pop(item, None)
        else:
            yr[item] = float(value)
        if yr:
            fin[fy] = yr
        else:
            fin.pop(fy, None)
        ov["financials"] = fin
    elif section == "market":
        m = dict(ov.get("market") or {})
        if value is None:
            m.pop(item, None)
        else:
            m[item] = float(value)
        ov["market"] = m
    else:
        raise HTTPException(422, f"unknown section {section!r} (use 'financials' or 'market')")
    ov = {k: v for k, v in ov.items() if v}      # drop emptied sections
    if ov:
        ext["overrides"] = ov
    else:
        ext.pop("overrides", None)
    return ext


@router.patch("/companies/{cid}/edit")
def edit_field(cid: int, body: EditIn, db: Session = Depends(get_db), _user=Depends(A.require_analyst)):
    """Override one sourced value (a financial line for a year, or a market field).
    value=null clears that single edit. The sourced baseline is never touched; returns
    the freshly recomputed panes so the dashboard reflects the edit immediately."""
    co = _company(db, cid)
    before = current_value(co, body.section, body.item, body.fy)
    where = where_of(co, body.section, body.item, body.fy or "")
    co.extracted = _apply_edit(co.extracted or {}, body.section, body.item, body.fy, body.value)
    db.add(co)
    db.commit()
    D.audit(db, "analyst_edit", "company", cid, section=body.section, item=body.item,
            fy=body.fy, value=body.value, before=before, after=body.value,
            where=where, revertible=True, actor=_user.email)
    return _fresh(co)


@router.post("/companies/{cid}/reset")
def reset_edits(cid: int, body: ResetIn, db: Session = Depends(get_db), _user=Depends(A.require_analyst)):
    """Delete analyst edits — one field, one year, a whole section, or all of them —
    restoring the sourced defaults. This is the 'reset to default' control."""
    co = _company(db, cid)
    before = (current_value(co, body.section, body.item, body.fy)
              if (body.section and body.item) else None)
    ext = dict(co.extracted or {})
    ov = {k: (dict(v) if isinstance(v, dict) else v) for k, v in (ext.get("overrides") or {}).items()}
    if body.section == "financials" and body.fy and body.item:      # one cell
        yr = dict((ov.get("financials") or {}).get(body.fy) or {})
        yr.pop(body.item, None)
        fin = {y: v for y, v in (ov.get("financials") or {}).items()}
        if yr:
            fin[body.fy] = yr
        else:
            fin.pop(body.fy, None)
        ov["financials"] = fin
    elif body.section == "market" and body.item:                    # one market field
        m = dict(ov.get("market") or {})
        m.pop(body.item, None)
        ov["market"] = m
    elif body.section:                                              # a whole section
        ov.pop(body.section, None)
    else:                                                           # everything
        ov = {}
    ov = {k: v for k, v in ov.items() if v}
    if ov:
        ext["overrides"] = ov
    else:
        ext.pop("overrides", None)
    co.extracted = ext
    db.add(co)
    db.commit()
    after = (current_value(co, body.section, body.item, body.fy)
             if (body.section and body.item) else None)
    D.audit(db, "analyst_reset", "company", cid, section=body.section or "all",
            fy=body.fy, item=body.item, before=before, after=after,
            where=where_of(co, body.section or "all", body.item or "", body.fy or ""),
            revertible=bool(body.section and body.item), actor=_user.email)
    return _fresh(co)


class NarrativeIn(BaseModel):
    key: str                       # report section: business_overview, market_overview, risks, swot_*, …
    text: Optional[str] = None     # empty/None clears it back to the scaffold


@router.patch("/companies/{cid}/narrative")
def edit_narrative(cid: int, body: NarrativeIn, db: Session = Depends(get_db), _user=Depends(A.require_analyst)):
    """Write the analyst's prose for a Report section (business overview, market, risks, SWOT,
    country risk, …). Stored in the overrides layer; empty text clears it back to the scaffold."""
    co = _company(db, cid)
    ext = dict(co.extracted or {})
    ov = {k: (dict(v) if isinstance(v, dict) else v) for k, v in (ext.get("overrides") or {}).items()}
    narr = dict(ov.get("narrative") or {})
    _before_txt = narr.get(body.key)
    txt = (body.text or "").strip()
    if txt:
        narr[body.key] = txt
    else:
        narr.pop(body.key, None)
    ov["narrative"] = narr
    ov = {k: v for k, v in ov.items() if v}
    if ov:
        ext["overrides"] = ov
    else:
        ext.pop("overrides", None)
    co.extracted = ext
    db.add(co)
    db.commit()
    D.audit(db, "analyst_narrative", "company", cid, key=body.key,
            before=_before_txt, after=txt or None,
            where=where_of(co, "narrative", body.key), revertible=True,
            actor=_user.email)
    return _fresh(co)


class MetaIn(BaseModel):
    field: str            # name | sector | currency | unit | exchange | country
    value: str


_META_FIELDS = {"name", "sector", "currency", "unit", "exchange", "country"}


@router.patch("/companies/{cid}/meta")
def edit_meta(cid: int, body: MetaIn, db: Session = Depends(get_db), _user=Depends(A.require_analyst)):
    """Edit a company detail — name, sector, currency, unit, exchange or country. These are
    the analyst's descriptive fields; the change updates the record and flows to the deep
    panes, the currency/units basis and the Excel export."""
    if body.field not in _META_FIELDS:
        raise HTTPException(422, f"unknown detail {body.field!r}")
    co = _company(db, cid)
    _meta_before = (co.name if body.field == "name"
                    else (co.extracted or {}).get("unit") if body.field == "unit"
                    else getattr(co, body.field, None))
    val = (body.value or "").strip()
    u = dict(co.universe or {})
    ext = dict(co.extracted or {})
    if body.field == "name":
        if val:
            co.name = val
        u["name"] = co.name
    elif body.field == "unit":                 # not a Company column — lives in the dataset
        u["unit"] = val
        if ext:
            ext["unit"] = val
    else:                                      # sector / currency / exchange / country are columns
        setattr(co, body.field, val)
        u[body.field] = val
        if body.field == "currency" and ext:
            ext["currency"] = val
    co.universe = u
    if ext:
        co.extracted = ext
    db.add(co)
    db.commit()
    D.audit(db, "meta_edit", "company", cid, field=body.field, value=val,
            before=_meta_before, after=val,
            where=where_of(co, "meta", body.field), revertible=True,
            actor=_user.email)
    return {**_fresh(co), "meta": {"name": co.name, "sector": co.sector, "currency": co.currency,
                                   "exchange": co.exchange, "country": co.country,
                                   "unit": (ext.get("unit") if ext else None) or u.get("unit")}}


@router.get("/companies/{cid}/scenarios")
def get_scenarios(cid: int, db: Session = Depends(get_db), _user=Depends(A.require_viewer)):
    """The Bull/Base/Bear assumption matrix + value per share for each."""
    co = _company(db, cid)
    scen, fins = _scenarios_for(co)
    mkt = pdata.merged_extracted(co.extracted or {}).get("market", {})
    return {"scenarios": scen, "keys": scn.keys_for(scen["kind"]),
            "valuation": pdata.scenario_values(fins, mkt, scen)}


@router.post("/companies/{cid}/scenarios")
def save_scenarios(cid: int, body: ScenariosIn, db: Session = Depends(get_db), _user=Depends(A.require_analyst)):
    """Persist edited assumptions / active scenario and return the fresh valuation.
    The dashboard, this studio and the Excel export all read the same stored set."""
    co = _company(db, cid)
    ext = dict(co.extracted or {})                     # raw baseline, for persisting scenarios
    eff = pdata.merged_extracted(co.extracted or {})   # merged view, for the computation
    fins = sorted(eff.get("financials", []), key=lambda f: f.get("fy", ""))
    scen = scn.normalise(body.scenarios, fins, country=(co.universe or {}).get("country"),
                         is_bank=eff.get("bank") is not None, bank=eff.get("bank"))
    ext["scenarios"] = scen
    co.extracted = ext
    db.add(co)
    db.commit()
    D.audit(db, "scenarios_saved", "company", cid, selected=scen["selected"], actor=_user.email)
    # Push the saved assumptions back into the stored Excel workbook (if it exists).
    # Runs in a background thread so the API responds immediately.
    _push_scenarios_to_excel(co.slug, scen)
    return {"scenarios": scen, "keys": scn.keys_for(scen["kind"]),
            "valuation": pdata.scenario_values(fins, eff.get("market", {}), scen)}


class ApplyIn(BaseModel):
    action: str
    assumption: str | None = None
    slug: str | None = None
    field: str | None = None
    new_value: str | None = None
    name: str | None = None
    ticker: str | None = None
    exchange: str | None = None
    country: str | None = None
    sector: str | None = None
    why: str | None = None


# -------------------------------------------------------------------- history
# Actions worth showing an analyst. The log also records reads and downloads,
# which are noise in a page about what CHANGED.
# What the History tab shows, keyed by the action name the code ACTUALLY emits.
#
# Six of the eleven names here were never emitted by anything — company_created
# where the code writes studio_company_created, meta_edited where it writes
# meta_edit — and twenty-six real actions were missing, so the tab showed almost
# nothing and looked broken. tests/test_history.py compares this against every
# audit call in the package, so a renamed action cannot silently empty it again.
CHANGES = {
    # the company itself
    "studio_company_created": "company added",
    "assistant_company_added": "company added by the AI analyst",
    "bloomberg_row_added": "company added to the Bloomberg submission",
    "bloomberg_row_removed": "company removed from the Bloomberg submission",
    "bloomberg_cell_edited": "Bloomberg figure typed over by hand",
    "bloomberg_exported": "Bloomberg file exported",
    "studio_company_deleted": "company removed",
    "meta_edit": "company detail edited",
    # figures
    "analyst_edit": "figure edited",
    "assistant_change_applied": "figure edited by the AI analyst",
    "analyst_reset": "edit reverted",
    "studio_fact_approved": "figure approved",
    "studio_fact_rejected": "figure rejected",
    "fact_reviewed": "figure reviewed",
    # where figures come from
    "studio_stage": "file uploaded",
    "filing_extracted": "filing extracted",
    "web_collection": "data collected from the web",
    # the model
    "scenarios_saved": "assumptions changed",
    "studio_recompute": "model recomputed",
    "model_generated": "model rebuilt",
    "override_requested": "control override requested",
    "override_decided": "control override decided",
    "export_approved": "export approved",
    # commentary
    "analyst_narrative": "commentary edited",
    "ai_draft_analysis": "commentary drafted by the AI analyst",
    # access and conversation, which an admin needs to be able to account for
    "user_deleted": "user removed",
    "chat_history_cleared": "chat history cleared",
    "chat_retention_changed": "chat retention changed",
    "change_reverted": "change put back",
    "sector_forecast_run": "sector forecast run",
    "filing_alert_checked": "filing alerts checked",
}

# Module-level cache for filing-alerts (60-minute TTL)
_filing_alert_cache: dict = {}   # keys: "ts" (datetime), "result" (list)
_consistency_cache: dict = {}    # keys: "ts" (datetime), "result" (list) — warmed by lifespan loop


@router.get("/history")
def history(limit: int = Query(200, le=1000), company: str = "",
            db: Session = Depends(get_db), _user=Depends(A.require_viewer)):
    """What changed, who changed it, and what it was before.

    An audit row exists for every write already. This turns it into something an
    analyst can read: newest first, one line each, with the before and after
    where the action recorded them.
    """
    q = db.query(D.AuditLog).filter(D.AuditLog.action.in_(list(CHANGES)))
    if company:
        co = db.query(D.Company).filter(
            (D.Company.slug == company) | (D.Company.id == str(company))).first()
        if co:
            q = q.filter(D.AuditLog.entity == "company",
                         D.AuditLog.entity_id == str(co.id))
    rows = q.order_by(D.AuditLog.ts.desc()).limit(limit).all()

    names = {str(c.id): c.name for c in db.query(D.Company).all()}
    # An entry can only be put back if it is still the change in force. A later
    # edit to the same field supersedes it, and reverting the older one would
    # silently discard the newer. Walk newest first and let the first entry
    # touching a given field claim it.
    seen: set = set()
    out = []
    for r in rows:
        d = r.detail or {}
        target = (r.entity_id, d.get("section") or d.get("field") or d.get("key"),
                  d.get("item"), d.get("fy"))
        superseded = target in seen
        if d.get("revertible"):
            seen.add(target)
        out.append({
            "id": r.id,
            "when": r.ts.isoformat() if r.ts else None,
            "who": r.actor,
            "what": CHANGES.get(r.action, r.action.replace("_", " ")),
            "company": names.get(str(r.entity_id)) if r.entity == "company" else None,
            "where": d.get("where") or "",
            "field": d.get("field") or d.get("item") or d.get("key") or d.get("name") or "",
            "before": d.get("before"),
            "after": d.get("after") if "after" in d else d.get("new_value"),
            "why": d.get("why") or "",
            "by_assistant": d.get("proposed_by") == "assistant",
            "revertible": bool(d.get("revertible")) and not superseded,
            "superseded": superseded,
        })
    return {"results": out, "count": len(out)}


class RevertIn(BaseModel):
    why: Optional[str] = None


# Which detail keys an entry needs before it can be put back, per action. An
# action absent from here cannot be reverted from the log at all, which is the
# safe default: a revert that guesses is worse than a revert that refuses.
REVERTIBLE = {
    "analyst_edit": ("section", "item"),
    "analyst_reset": ("section", "item"),
    "analyst_narrative": ("key",),
    "meta_edit": ("field",),
    # An assistant edit writes a scalar at a dotted path in the record, so it is
    # put back by writing the old value to the same path.
    "assistant_change_applied": ("path",),
}


@router.post("/history/{entry_id}/revert")
def revert_change(entry_id: int, body: RevertIn = RevertIn(),
                  db: Session = Depends(get_db), _user=Depends(A.require_analyst)):
    """Put one change back to what it was, and record that as its own change.

    A revert is an edit like any other, so it leaves its own row naming the
    entry it undid. Nothing is deleted from the log: the history is the record
    of what happened, and rewriting it would defeat the point of keeping one.
    """
    row = db.get(D.AuditLog, entry_id)
    if row is None:
        raise HTTPException(404, "no such change")
    d = row.detail or {}
    if not d.get("revertible") or row.action not in REVERTIBLE:
        raise HTTPException(422, "this change cannot be put back automatically")
    for k in REVERTIBLE[row.action]:
        if not d.get(k):
            raise HTTPException(422, f"the change did not record {k}, so it cannot be put back")
    if row.entity != "company":
        raise HTTPException(422, "only changes to a company can be put back")

    # Refuse if a later change has already moved the same field.
    later = db.query(D.AuditLog).filter(
        D.AuditLog.id > entry_id,
        D.AuditLog.entity == "company",
        D.AuditLog.entity_id == row.entity_id,
        D.AuditLog.action.in_(list(REVERTIBLE)),
    ).all()
    key = (d.get("section") or d.get("field") or d.get("key"), d.get("item"), d.get("fy"))
    for lr in later:
        ld = lr.detail or {}
        if (ld.get("section") or ld.get("field") or ld.get("key"),
                ld.get("item"), ld.get("fy")) == key:
            raise HTTPException(
                409, "a later change has already moved this field. Put that one back first.")

    co = _company(db, int(row.entity_id))
    before = d.get("before")
    if row.action in ("analyst_edit", "analyst_reset"):
        co.extracted = _apply_edit(co.extracted or {}, d["section"], d["item"],
                                   d.get("fy"), before)
    elif row.action == "analyst_narrative":
        ext = dict(co.extracted or {})
        ov = {k: (dict(v) if isinstance(v, dict) else v)
              for k, v in (ext.get("overrides") or {}).items()}
        narr = dict(ov.get("narrative") or {})
        if before:
            narr[d["key"]] = before
        else:
            narr.pop(d["key"], None)
        ov["narrative"] = narr
        ov = {k: v for k, v in ov.items() if v}
        ext["overrides"] = ov
        if not ov:
            ext.pop("overrides", None)
        co.extracted = ext
    elif row.action == "assistant_change_applied":
        ex = json.loads(json.dumps(co.extracted or {}))
        parts = [x for x in str(d["path"]).split(".") if x]
        node = ex
        try:
            for k in parts[:-1]:
                node = (next(x for x in node if str(x.get("fy")) == k)
                        if isinstance(node, list) else node[k])
            node[parts[-1]] = before
        except (StopIteration, KeyError, TypeError, AttributeError):
            raise HTTPException(422, "the field that changed is no longer on this record")
        co.extracted = ex
    elif row.action == "meta_edit":
        field = d["field"]
        u = dict(co.universe or {})
        ext = dict(co.extracted or {})
        if field == "name":
            if before:
                co.name = before
            u["name"] = co.name
        elif field == "unit":
            u["unit"] = before or ""
            if ext:
                ext["unit"] = before or ""
        else:
            setattr(co, field, before or "")
            u[field] = before or ""
            if field == "currency" and ext:
                ext["currency"] = before or ""
        co.universe = u
        co.extracted = ext
    db.add(co)
    db.commit()
    D.audit(db, "change_reverted", "company", co.id,
            reverted_entry=entry_id, reverted_action=row.action,
            reverted_from=d.get("after"), before=d.get("after"), after=before,
            where=d.get("where") or "", why=(body.why or "").strip(),
            original_actor=row.actor, revertible=False, actor=_user.email)
    return {"ok": True, "reverted": entry_id, "company": _fresh(co)}


# --------------------------------------------------------------- chat history
# Kept per user, so an analyst can pick up where they left off. Visible to an
# admin, because an assistant that quotes figures is something the desk has to
# be able to review. Deleted on a schedule the admin sets, because a transcript
# nobody reads is a liability rather than a record.
RETENTION = {"week": 7, "month": 30, "quarter": 92, "year": 365, "forever": 0}
RETENTION_KEY = "chat_retention"
RETENTION_DEFAULT = "month"


def _retention(db) -> str:
    row = db.get(D.Setting, RETENTION_KEY)
    return (row.value if row and row.value in RETENTION else RETENTION_DEFAULT)


def purge_old_chat(db) -> int:
    """Delete conversation older than the admin's setting. Returns how many."""
    days = RETENTION.get(_retention(db), 30)
    if not days:
        return 0
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    n = db.query(D.ChatMessage).filter(D.ChatMessage.ts < cutoff).delete(
        synchronize_session=False)
    db.commit()
    return n


def _mirror_chat(email: str, question: str, out: dict) -> None:
    """Copy one turn to Dataverse, if a tenant is configured.

    After the local commit, and deliberately silent. The local database is what
    the user reads back; this is the copy that survives the container. A tenant
    being unreachable is not a reason to fail a question that already succeeded.
    """
    from aeon_nimbus import teams_store
    if not teams_store.chat_enabled():
        return
    tools = ",".join(s["tool"] for s in out.get("steps", []))
    teams_store.save_chat(email, "user", question[:8000])
    teams_store.save_chat(email, "assistant", (out.get("reply") or "")[:8000], tools)


class RetentionIn(BaseModel):
    keep_for: str


@router.get("/assistant/history")
def chat_history(user: str = "", limit: int = Query(100, le=500),
                 db: Session = Depends(get_db), _user=Depends(A.require_viewer)):
    """Your own conversation. An admin may read anyone's by naming them."""
    whose = (_user.email or "").lower()
    if user and user.lower() != whose:
        if A.RANK.get(_user.role, 0) < A.RANK["admin"]:
            raise HTTPException(403, "Only an admin can read another person's conversation.")
        whose = user.lower()
    purge_old_chat(db)
    rows = (db.query(D.ChatMessage).filter(D.ChatMessage.user_email == whose)
            .order_by(D.ChatMessage.ts.desc()).limit(limit).all())
    return {"user": whose, "keep_for": _retention(db),
            "results": [{"when": r.ts.isoformat() if r.ts else None, "role": r.role,
                         "content": r.content, "tools": r.tools_used or {}}
                        for r in reversed(rows)]}


@router.delete("/assistant/history")
def clear_chat_history(user: str = "", db: Session = Depends(get_db),
                       _user=Depends(A.require_viewer)):
    """Clear your own. An admin may clear anyone's."""
    whose = (_user.email or "").lower()
    if user and user.lower() != whose:
        if A.RANK.get(_user.role, 0) < A.RANK["admin"]:
            raise HTTPException(403, "Only an admin can clear another person's conversation.")
        whose = user.lower()
    n = db.query(D.ChatMessage).filter(D.ChatMessage.user_email == whose).delete(
        synchronize_session=False)
    db.commit()
    D.audit(db, "chat_history_cleared", "user", whose, actor=_user.email, messages=n)
    return {"ok": True, "deleted": n, "user": whose}


@router.get("/assistant/who-has-chatted")
def chat_users(db: Session = Depends(get_db), _admin=Depends(A.require_admin)):
    """Everyone with a conversation on record, for the admin screen."""
    purge_old_chat(db)
    rows = (db.query(D.ChatMessage.user_email, func.count(D.ChatMessage.id),
                     func.max(D.ChatMessage.ts))
            .group_by(D.ChatMessage.user_email).all())
    return {"keep_for": _retention(db),
            "options": list(RETENTION),
            "results": [{"user": e, "messages": n,
                         "last": t.isoformat() if t else None} for e, n, t in rows]}


@router.get("/teams/check")
def teams_check(_admin=Depends(A.require_admin)):
    """Prove the Microsoft 365 configuration by using it.

    Client credentials fail in a handful of specific ways, and the HTTP status
    alone points at the wrong Azure blade for most of them, so each step says
    what to go and fix.
    """
    from aeon_nimbus import platform_data, teams_store
    out = dict(teams_store.check())
    # Said on the same screen as the SharePoint status, because that panel was
    # the one place an admin could read "Dormant" and conclude the change log
    # was going nowhere.
    out["change_log_home"] = platform_data.change_log_home()
    return out



# =====================================================================
# Bloomberg earnings-estimates contribution
#
# Admin only. Bloomberg agrees a column layout with each contributor once and
# rejects the file if the structure moves, so the master template is a fixed
# artefact in the repository and the platform only ever writes cell values into
# it. Everything the admin decides lives in the bloomberg_row table instead of
# in the workbook.

BB_TEMPLATE = Path(__file__).resolve().parent.parent / "data" / "bloomberg" / "ee_template.xlsx"


def _bb_template() -> bytes:
    if not BB_TEMPLATE.exists():
        raise HTTPException(503, "The Bloomberg template is not installed on this "
                                 "server. Upload it on the Bloomberg page.")
    return BB_TEMPLATE.read_bytes()


def _bb_deep(db, slug: str) -> dict:
    from aeon_nimbus import platform_data as pdata
    co = db.query(D.Company).filter(D.Company.slug == slug).first()
    if not co:
        return {}
    try:
        return pdata.deep_from_extracted(co.extracted or {}, co.universe or {}) or {}
    except Exception:
        return {}


def _bb_state(db, tpl: bytes) -> dict:
    """Everything the page renders: selected companies, values, gaps, audit."""
    from aeon_nimbus import bloomberg as BB
    layout = BB.read_layout(tpl)
    tmpl_rows = {r["row"]: r for r in BB.read_rows(tpl)}

    out = []
    for sel in db.query(D.BloombergRow).order_by(D.BloombergRow.template_row).all():
        co = db.query(D.Company).filter(D.Company.slug == sel.slug).first()
        meta = tmpl_rows.get(sel.template_row, {})
        deep = _bb_deep(db, sel.slug)
        values, gaps = BB.populate(deep, layout, meta.get("fye", ""))
        existing = BB.read_values(tpl, sel.template_row, layout)
        issues = BB.audit_existing(existing, deep, layout)
        # A typed value always wins, and is marked so the page can show which
        # figures are the platform's and which are a person's.
        overrides = dict(sel.overrides or {})
        merged = {**values, **overrides}
        out.append({
            "id": sel.id, "slug": sel.slug,
            "company": co.name if co else sel.slug,
            "template_row": sel.template_row,
            "template_name": meta.get("name", ""),
            "ticker": meta.get("ticker", ""), "currency": meta.get("currency", ""),
            "fye": meta.get("fye", ""), "analyst": meta.get("analyst", ""),
            "derived": values, "overrides": overrides, "values": merged,
            "gaps": gaps, "issues": issues,
            "blocked": any(g["kind"] == "blocking" for g in gaps),
            "added_by": sel.added_by,
        })
    cols = [{"header": h, "column": c, "kind": BB.field_kind(h)}
            for h, c in sorted(layout.items(), key=lambda kv: (len(kv[1]), kv[1]))]
    return {"rows": out, "columns": cols,
            "template_rows": len(tmpl_rows), "template_file": BB_TEMPLATE.name}


@router.get("/bloomberg")
def bloomberg_state(db: Session = Depends(get_db), _admin=Depends(A.require_admin)):
    """The working set, with every gap and every problem already in the file."""
    return _bb_state(db, _bb_template())


@router.get("/bloomberg/candidates")
def bloomberg_candidates(db: Session = Depends(get_db), _admin=Depends(A.require_admin)):
    """Coverage companies not yet in the submission, matched to a template row.

    Matching is on ticker then name, ignoring separators, because the template
    writes "SCOM KN" where the platform says "SCOM" and "Safaricom" where it
    says "Safaricom PLC".
    """
    from aeon_nimbus import bloomberg as BB
    tpl = _bb_template()
    rows = BB.read_rows(tpl)
    taken = {r.slug for r in db.query(D.BloombergRow).all()}

    def key(x):
        return "".join(ch for ch in (x or "").lower() if ch.isalnum())

    by_name = {key(r["name"]): r for r in rows}
    by_tick = {key(r["ticker"].split()[0]): r for r in rows if r["ticker"]}

    out = []
    for co in db.query(D.Company).order_by(D.Company.name).all():
        if co.slug in taken:
            continue
        m = by_name.get(key(co.name)) or by_tick.get(key(co.ticker))
        out.append({"slug": co.slug, "name": co.name, "ticker": co.ticker,
                    "match_row": m["row"] if m else None,
                    "match_name": m["name"] if m else None,
                    "match_fye": m["fye"] if m else None})
    return {"candidates": out, "next_free_row": BB.first_free_row(tpl)}


class BBAdd(BaseModel):
    slug: str
    template_row: int


@router.post("/bloomberg/rows")
def bloomberg_add(body: BBAdd, db: Session = Depends(get_db),
                  _admin=Depends(A.require_admin)):
    co = db.query(D.Company).filter(D.Company.slug == body.slug).first()
    if not co:
        raise HTTPException(404, f"{body.slug!r} is not in coverage")
    if db.query(D.BloombergRow).filter(D.BloombergRow.slug == body.slug).first():
        raise HTTPException(409, f"{co.name} is already in the submission")
    clash = db.query(D.BloombergRow).filter(
        D.BloombergRow.template_row == body.template_row).first()
    if clash:
        raise HTTPException(409, f"template row {body.template_row} is already "
                                 f"used by {clash.slug}")
    row = D.BloombergRow(slug=body.slug, template_row=body.template_row,
                         overrides={}, added_by=_admin.email)
    db.add(row)
    db.commit()
    D.audit(db, "bloomberg_row_added", "company", co.id, actor=_admin.email,
            field="bloomberg", after=f"row {body.template_row}",
            where=f"Bloomberg / {co.name}", why="added to the submission")
    return {"ok": True, "id": row.id}


@router.delete("/bloomberg/rows/{row_id}")
def bloomberg_remove(row_id: int, db: Session = Depends(get_db),
                     _admin=Depends(A.require_admin)):
    row = db.get(D.BloombergRow, row_id)
    if not row:
        raise HTTPException(404, "not in the submission")
    slug, trow = row.slug, row.template_row
    db.delete(row)
    db.commit()
    D.audit(db, "bloomberg_row_removed", "company", "", actor=_admin.email,
            field="bloomberg", before=f"row {trow}",
            where=f"Bloomberg / {slug}", why="removed from the submission")
    return {"ok": True, "detail": f"{slug} removed from the submission"}


class BBCell(BaseModel):
    row_id: int
    column: str
    value: str | None = None


@router.patch("/bloomberg/cell")
def bloomberg_cell(body: BBCell, db: Session = Depends(get_db),
                   _admin=Depends(A.require_admin)):
    """Type over a derived figure, or clear the override to get it back.

    Recorded in the change log like any other write, because a figure a person
    typed into a Bloomberg submission is exactly the kind of thing that has to
    be explainable afterwards.
    """
    row = db.get(D.BloombergRow, body.row_id)
    if not row:
        raise HTTPException(404, "not in the submission")
    col = (body.column or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{1,3}", col):
        raise HTTPException(400, f"{body.column!r} is not a column letter")

    ov = dict(row.overrides or {})
    before = ov.get(col)
    raw = (body.value or "").strip()
    if raw == "":
        ov.pop(col, None)
        after = None
    else:
        try:
            after = float(raw.replace(",", ""))
        except ValueError:
            after = raw
        ov[col] = after
    row.overrides = ov
    db.commit()
    D.audit(db, "bloomberg_cell_edited", "company", "", actor=_admin.email,
            field=f"{col}{row.template_row}", before=before, after=after,
            where=f"Bloomberg / {row.slug} / {col}",
            why="typed over the derived figure" if after is not None
                else "override cleared, derived figure restored")
    return {"ok": True, "overrides": ov}


@router.get("/bloomberg/download")
def bloomberg_download(db: Session = Depends(get_db), _admin=Depends(A.require_admin)):
    """The template with the submission written into it, and nothing else moved."""
    from aeon_nimbus import bloomberg as BB
    tpl = _bb_template()
    state = _bb_state(db, tpl)
    edits = {r["template_row"]: r["values"] for r in state["rows"]
             if r["values"] and not r["blocked"]}
    if not edits:
        raise HTTPException(400, "Nothing to write. Add a company, and check "
                                 "no row is blocked on its fiscal year.")
    try:
        data = BB.write(tpl, edits)
    except ValueError as e:
        raise HTTPException(500, str(e))

    D.audit(db, "bloomberg_exported", actor=_admin.email,
            where="Bloomberg", why=f"{len(edits)} companies written",
            after=f"{len(edits)} rows")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    from fastapi.responses import Response
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f'attachment; filename="hamilcar_bloomberg_ee_{stamp}.xlsx"'})


@router.get("/assistant/health")
def assistant_health(_admin=Depends(A.require_admin)):
    """Which configured models the provider still serves.

    Providers retire models. When Groq retired the Llama 3.3 line the first
    sign was an analyst being shown raw provider JSON, so this makes the state
    of the chain readable before somebody asks a question.
    """
    from aeon_nimbus import assistant
    return assistant.health()


@router.post("/storage/self-test")
def storage_self_test(_admin=Depends(A.require_admin)):
    """Prove the change log survives a restart, by writing and reading it back.

    Separate from the SharePoint check below. That one tests a copy nobody
    restores from; this one tests the store the platform actually reloads.
    """
    from aeon_nimbus import gcs_mirror, platform_data
    out = dict(gcs_mirror.self_test())
    out["change_log_home"] = platform_data.change_log_home()
    return out


@router.post("/teams/self-test")
def teams_self_test(_admin=Depends(A.require_admin)):
    """Write a row to each list and a file to the library, then read one back.

    check() proves the configuration can be reached. This proves it can be
    written to, which is a different permission and the one that usually turns
    out to be the missing piece.
    """
    from aeon_nimbus import teams_store
    return teams_store.self_test()


@router.get("/teams/changes")
def teams_changes(company: str = "", limit: int = 500, _admin=Depends(A.require_admin)):
    """The change log as SharePoint holds it, rather than as this container does."""
    from aeon_nimbus import teams_store
    if not teams_store.enabled():
        raise HTTPException(400, "No SharePoint site is configured, so the change log "
                                 "lives in the platform database only.")
    return {"source": "sharepoint", "results": teams_store.fetch_changes(company, limit)}


@router.get("/teams/files")
def teams_files(folder: str = "", _admin=Depends(A.require_admin)):
    from aeon_nimbus import teams_store
    if not teams_store.enabled():
        raise HTTPException(400, "No SharePoint site is configured.")
    return {"source": "sharepoint", "results": teams_store.list_files(folder)}


@router.get("/teams/chat-history")
def teams_chat_history(user: str = "", limit: int = 200, _admin=Depends(A.require_admin)):
    """Read conversation back out of Dataverse rather than the local database.

    Worth having separately: it is how you confirm the mirror is real, and it
    reaches turns from containers that no longer exist.
    """
    from aeon_nimbus import teams_store
    if not teams_store.chat_enabled():
        raise HTTPException(400, "No SharePoint site is configured, so chat history "
                                 "lives in the platform database only.")
    return {"source": "sharepoint" if teams_store.enabled() else "dataverse",
            "results": teams_store.fetch_chat(user, limit)}


@router.post("/assistant/retention")
def set_retention(body: RetentionIn, db: Session = Depends(get_db),
                  _admin=Depends(A.require_admin)):
    """How long conversation is kept. Applied immediately, not only in future."""
    if body.keep_for not in RETENTION:
        raise HTTPException(400, f"Choose one of: {', '.join(RETENTION)}.")
    row = db.get(D.Setting, RETENTION_KEY) or D.Setting(key=RETENTION_KEY)
    before = row.value or RETENTION_DEFAULT
    row.value = body.keep_for
    db.add(row)
    db.commit()
    removed = purge_old_chat(db)
    D.audit(db, "chat_retention_changed", actor=_admin.email,
            before=before, after=body.keep_for, deleted=removed)
    return {"ok": True, "keep_for": body.keep_for, "deleted_now": removed}


# ------------------------------------------------------------------ assistant
class AskIn(BaseModel):
    question: str
    history: list = []


def _assistant_tools(db):
    """The tools the assistant may call. Read-only by construction: there is no
    write tool in here, so the model cannot change anything even if it tries.
    The write path is propose_change, which puts a card in front of a human."""
    from aeon_nimbus import assistant_tools
    return assistant_tools.build(db)


def _resolve_company(db, text: str):
    """Find a company by slug, ticker or name. The model uses whichever it saw."""
    t = (text or "").strip().lower()
    rows = db.query(D.Company).all()
    for c in rows:
        if t in ((c.slug or "").lower(), (c.ticker or "").lower()):
            return c
    return next((c for c in rows if t and t in (c.name or "").lower()), None)


def _resolve_field(rec: dict, field: str) -> str:
    """Turn a field the model named into a path that exists on the record.

    "market.share_price" is used as given. A bare "share_price" is searched for,
    so the analyst is not shown a card that fails on Approve for a naming
    difference they never saw. Resolving is not guessing: a name that matches
    nothing comes back empty and the change is refused.
    """
    field = (field or "").strip()
    if not field:
        return ""
    node = rec
    for part in field.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and any(str(x.get("fy")) == part
                                            for x in node if isinstance(x, dict)):
            node = next(x for x in node if str(x.get("fy")) == part)
        else:
            node = None
            break
    if node is not None:
        return field

    leaf = field.split(".")[-1]
    for parent, val in (rec or {}).items():
        if isinstance(val, dict) and leaf in val:
            return f"{parent}.{leaf}"
    if leaf in (rec or {}):
        return leaf
    return ""


@router.post("/assistant/apply")
def assistant_apply(body: ApplyIn, db: Session = Depends(get_db),
                    _user=Depends(A.require_analyst)):
    """Commit a proposal the analyst approved.

    The assistant cannot reach this: it has no tool that calls it. A human
    presses Approve, and the change is recorded against THEM, so the history
    answers "who changed this" with a person rather than with a model.
    """
    if body.action == "edit_field":
        if not (body.slug and body.field):
            raise HTTPException(400, "an edit needs a company and a field")
        # The model says "Safaricom" and "share_price"; the record wants
        # "safaricom_plc" and "market.share_price". Resolving both here means the
        # analyst is not shown a card that cannot be approved.
        c = _resolve_company(db, body.slug)
        if not c:
            raise HTTPException(404, f"no company matching {body.slug!r} is in coverage")
        field = _resolve_field(c.extracted or {}, body.field or "")
        if not field:
            raise HTTPException(400, f"{body.field!r} is not a field on this record")
        parts = [x for x in field.split(".") if x]
        ex = json.loads(json.dumps(c.extracted or {}))       # copy, so a failure changes nothing
        node, before = ex, None
        try:
            for k in parts[:-1]:
                if isinstance(node, list):
                    node = next(x for x in node if str(x.get("fy")) == k)
                else:
                    node = node.setdefault(k, {})
            before = (node or {}).get(parts[-1])
            raw = body.new_value
            try:
                node[parts[-1]] = float(str(raw).replace(",", ""))
            except (TypeError, ValueError):
                node[parts[-1]] = raw
        except (StopIteration, AttributeError, TypeError):
            raise HTTPException(400, f"{body.field} is not a field on this record")
        c.extracted = ex
        db.commit()
        D.audit(db, "assistant_change_applied", "company", c.id, actor=_user.email,
                field=field, before=before, after=body.new_value,
                where=where_of(c, "financials" if field.startswith("financials")
                               else "market" if field.startswith("market") else "record",
                               field), path=field, revertible=True,
                why=body.why or "", proposed_by="assistant")
        return {"ok": True,
                "detail": f"{field} changed from {before} to {body.new_value}, "
                          f"recorded against {_user.email}."}

    if body.action == "add_company":
        if not (body.name and body.ticker):
            raise HTTPException(400, "a new company needs at least a name and a ticker")
        slug = re.sub(r"[^a-z0-9]+", "_", (body.name or "").lower()).strip("_")
        if db.query(D.Company).filter(D.Company.slug == slug).first():
            raise HTTPException(409, f"{body.name} is already in coverage")
        c = D.Company(slug=slug, name=body.name, ticker=body.ticker,
                      exchange=body.exchange or "", country=body.country or "",
                      sector=body.sector or "", extracted={}, universe={
                          "name": body.name, "slug": slug, "ticker": body.ticker,
                          "exchange": body.exchange or "", "country": body.country or "",
                          "sector": body.sector or ""})
        db.add(c)
        db.commit()
        D.audit(db, "assistant_company_added", "company", c.id, actor=_user.email,
                name=body.name, why=body.why or "", proposed_by="assistant")
        return {"ok": True, "detail": f"{body.name} added to coverage as {slug}. "
                                      f"It has no data yet — collect or upload next."}

    if body.action in ("set_assumption", "recompute"):
        c = _resolve_company(db, body.slug or "")
        if not c:
            raise HTTPException(404, f"no company matching {body.slug!r} is in coverage")
        before = None
        if body.action == "set_assumption":
            if not body.assumption:
                raise HTTPException(400, "which assumption?")
            ex = json.loads(json.dumps(c.extracted or {}))
            drivers = ex.setdefault("drivers", {})
            before = drivers.get(body.assumption)
            try:
                drivers[body.assumption] = float(str(body.new_value).replace(",", ""))
            except (TypeError, ValueError):
                raise HTTPException(400, f"{body.new_value!r} is not a number")
            c.extracted = ex
            db.commit()
        # rebuild the workbook so the change is in the file, not only the record
        try:
            from aeon_nimbus.excel_model import compile_model
            compile_model(c.extracted or {}, c.universe or {}, cfg.model_path(c.slug))
            built = "the model was rebuilt"
        except Exception as e:
            built = f"the model could not be rebuilt: {type(e).__name__}"
        D.audit(db, "assistant_change_applied", "company", c.id, actor=_user.email,
                field=(f"drivers.{body.assumption}" if body.assumption else "model"),
                before=before, after=body.new_value, why=body.why or "",
                where=where_of(c, "Assumptions",
                               body.assumption or "model"),
                proposed_by="assistant")
        return {"ok": True,
                "detail": (f"{body.assumption} set to {body.new_value}, {built}, "
                           f"recorded against {_user.email}."
                           if body.action == "set_assumption"
                           else f"{built}, recorded against {_user.email}.")}

    raise HTTPException(400, f"unknown action {body.action!r}")


@router.post("/assistant/ask")
def assistant_ask(body: AskIn, db: Session = Depends(get_db),
                  _user=Depends(A.require_viewer)):
    """One turn of conversation.

    A value the analyst supplied is applied straight away and comes back as a
    receipt they can put back. A value the model produced itself comes back as a
    proposal for them to approve. The AI analyst chooses between the two, and
    the choice is checked here: an apply is only possible at all because this
    hands it a callable, which a viewer's turn does not get.
    """
    from aeon_nimbus import assistant
    tools = _assistant_tools(db)

    # Two conditions, and both must hold. The role is who is asking; the switch
    # is whether the AI analyst may write at all. It reads per request rather
    # than at import, so turning it off takes effect on the next question
    # instead of the next deploy — which is what a safety switch has to do.
    allowed = os.getenv("AI_ANALYST_MAY_WRITE", "1").strip().lower()
    may_change = (A.RANK.get(_user.role, 0) >= A.RANK["analyst"]
                  and allowed not in ("0", "false", "no", "off"))

    def _apply(args: dict) -> dict:
        """Route the model's change through the same door a human's goes through.

        Deliberately calls assistant_apply rather than reimplementing it, so the
        resolution, the guards, the audit row and the put-back are the ones that
        are already tested. What differs is only who is named in the log.
        """
        if not may_change:
            raise PermissionError("Changing data needs analyst access.")
        fields = set(ApplyIn.model_fields)
        payload = ApplyIn(**{k: (str(v) if v is not None else None)
                             for k, v in args.items() if k in fields})
        out = assistant_apply(payload, db, _user)
        # The receipt needs the audit row's id so the chat can offer a put-back
        # next to what it just did. Read back rather than threaded through,
        # which keeps the shared apply path untouched.
        row = (db.query(D.AuditLog).filter(D.AuditLog.actor == _user.email)
                 .order_by(D.AuditLog.id.desc()).first())
        if row is not None and (row.detail or {}).get("revertible"):
            out = {**out, "entry_id": row.id,
                   "where": (row.detail or {}).get("where") or ""}
        return out

    out = assistant.answer(body.question, body.history or [], tools,
                           apply=_apply if may_change else None)
    nav = out.get("navigate") or {}
    if nav.get("slug"):
        nav["slug"] = tools["_resolve_slug"](nav["slug"]) or nav["slug"]
    db.add(D.ChatMessage(user_email=(_user.email or "").lower(), role="user",
                         content=body.question[:8000]))
    db.add(D.ChatMessage(user_email=(_user.email or "").lower(), role="assistant",
                         content=(out.get("reply") or "")[:8000],
                         tools_used={"tools": [s["tool"] for s in out.get("steps", [])],
                                     "navigated": bool(out.get("navigate")),
                                     "proposed": bool(out.get("proposal")),
                                     "applied": len(out.get("applied") or [])}))
    db.commit()
    purge_old_chat(db)
    _mirror_chat(_user.email, body.question, out)
    D.audit(db, "assistant_asked", actor=_user.email,
            question=body.question[:200],
            tools=[s["tool"] for s in out.get("steps", [])])
    # a viewer may read and navigate, but must not be offered a change to approve
    # Said by the platform, not left to the model. A refusal the model forgets
    # to mention reads as the change having gone through.
    if not may_change and (out.get("proposal") or out.get("applied")
                           or out.get("refused")):
        out["proposal"], out["applied"] = None, []
        why = ("The AI analyst is set to read-only on this platform, so nothing "
               "has been changed or proposed."
               if allowed in ("0", "false", "no", "off")
               else "Changing data needs analyst access, so nothing has been "
                    "changed or proposed.")
        out["reply"] = ((out.get("reply") or "").rstrip() + "\n\n" + why).strip()
    return out


class GridIn(BaseModel):
    rows: list = []
    widths: list = []
    freeze_at: str | None = None
    title: str | None = None


@router.post("/companies/{cid}/grid-export")
def grid_export(cid: int, body: GridIn, db: Session = Depends(get_db),
                _user=Depends(A.require_analyst)):
    """Finalise what the analyst built in the browser as a live workbook.

    Formulas go out as formulas, so Excel recomputes on open and the analyst
    receives a model they can keep working in rather than a snapshot.
    """
    from fastapi import Response

    from aeon_nimbus import data_sheet as ds
    co = _company(db, cid)
    data = ds.from_grid(body.model_dump(),
                        title=body.title or f"{co.name} — model",
                        currency=(co.extracted or {}).get("currency") or "USD")
    # File it in the Teams channel too, when one is configured. The analyst
    # still gets the download either way; this is so the team has the copy.
    filed = ""
    try:
        from aeon_nimbus import teams_store
        filed = teams_store.put_file(f"{co.slug}_model.xlsx", data)
    except Exception:                                # never block the download
        filed = ""
    D.audit(db, "grid_exported", "company", cid, actor=_user.email,
            rows=len(body.rows or []), filed_to_teams=bool(filed))
    headers = {"Content-Disposition": f'attachment; filename="{co.slug}_model.xlsx"'}
    if filed:
        headers["X-Teams-Url"] = filed
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers)


@router.get("/companies/{cid}/data-sheet")
def data_sheet(cid: int, db: Session = Depends(get_db), _user=Depends(A.require_analyst)):
    """Download the company's figures as an editable grid.

    The analyst edits it in real Excel — any function, any added row or column —
    and uploads it back through /upload, which stages each change against its
    current value for approval. No spreadsheet is reimplemented here and no
    formula engine runs in the browser: Excel does the arithmetic and the
    platform reads the values it saved.
    """
    from aeon_nimbus import data_sheet as ds
    co = _company(db, cid)
    data = ds.build(co.extracted or {}, co.universe or {})
    D.audit(db, "data_sheet_downloaded", "company", cid, actor=_user.email)
    from fastapi import Response
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{co.slug}_data.xlsx"'})


@router.get("/companies/{cid}/export")
def export_model(cid: int, db: Session = Depends(get_db), _user=Depends(A.require_viewer)):
    co = _company(db, cid)
    if not (co.extracted or {}).get("financials"):
        raise HTTPException(409, "no financials to model yet — collect or upload data first")
    project = studio_core.project_for(db, co)
    scen, _ = _scenarios_for(co)
    comps = pdata.comps_for(co.universe or {})
    out_path = cfg.model_path(co.slug)
    res = compile_model(pdata.merged_extracted(co.extracted or {}), co.universe or {}, out_path,
                        scenarios=scen, comps=comps)
    db.add(D.GeneratedModel(project_id=project.id, model_id=res["model_id"], version=res["version"],
                            path=res["path"], dq_score=res["data_quality"]["score"],
                            export_allowed=res["export_allowed"]))
    db.commit()
    if not res["export_allowed"]:
        crit = [c for c in res["controls"] if c["severity"] == "critical" and c["status"] == "fail"]
        raise HTTPException(423, {"message": "export blocked by a critical control",
                                  "controls": [{"id": c["control_id"], "description": c["description"]} for c in crit]})
    D.audit(db, "studio_export", "company", cid, model_id=res["model_id"], actor=_user.email)
    return FileResponse(str(Path(res["path"])), filename=f"{co.slug}_model.xlsx",
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _push_scenarios_to_excel(slug: str, scenarios: dict) -> None:
    """Push saved scenario assumptions into the company's stored Excel workbook.

    Non-blocking: spawned in a background thread. Skips silently if the workbook
    does not exist. Writes named ranges using openpyxl.
    """
    def _run():
        wb_path = Path("output") / "models" / f"{slug}_model.xlsx"
        if not wb_path.exists():
            log.info("excel-sync: no workbook at %s — skipping", wb_path)
            return
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(wb_path))
            # Determine scenario kind from the stored set
            kind = scenarios.get("kind", "dcf")
            named_map = scn.named_for(kind)
            # Use the Base scenario assumptions (the primary set)
            base_assum = (scenarios.get("sets") or {}).get("Base", {})
            wrote = 0
            for assum_key, xl_name in named_map.items():
                val = base_assum.get(assum_key)
                if val is None:
                    continue
                if xl_name in wb.defined_names:
                    dest = wb.defined_names[xl_name]
                    for sheet_name, cell_ref in dest.destinations:
                        ws = wb[sheet_name]
                        ws[cell_ref] = val
                        wrote += 1
            if wrote:
                wb.save(str(wb_path))
                log.info("excel-sync: wrote %d assumptions to %s", wrote, wb_path)
            else:
                log.info("excel-sync: no matching named ranges found in %s", wb_path)
        except Exception as exc:
            log.warning("excel-sync: failed for %s: %s", slug, exc)

    threading.Thread(target=_run, daemon=True).start()


@router.post("/reprice-all")
async def reprice_all(db: Session = Depends(get_db)):
    """Reprice all companies in the DB using yfinance. Returns a summary."""
    import datetime as _dt
    companies = db.query(D.Company).all()
    repriced = 0
    failed = 0
    results = []
    for co in companies:
        ticker = co.ticker or ""
        yf_ticker = (co.universe or {}).get("yf_ticker") or ticker
        if not yf_ticker:
            failed += 1
            results.append({"slug": co.slug, "ticker": ticker, "price": None,
                            "error": "no ticker configured"})
            continue
        # Apply exchange suffix (same logic as single reprice)
        if "." not in yf_ticker:
            exchange = ((co.universe or {}).get("exchange") or "").lower()
            _YF_SUFFIX = {
                "jse": ".JO", "johannesburg": ".JO", "nse": ".NS", "bse": ".BO",
                "india": ".NS", "lse": ".L", "london": ".L", "tsx": ".TO",
                "toronto": ".TO", "asx": ".AX", "australia": ".AX",
                "hkex": ".HK", "hong kong": ".HK", "tse": ".T", "tokyo": ".T",
                "krx": ".KS", "korea": ".KS", "sgx": ".SI", "singapore": ".SI",
                "xetra": ".DE", "deutsche": ".DE", "euronext": ".PA",
                "nairobi": ".NR", "nse kenya": ".NR", "dar es salaam": ".DSE",
                "nigeria": ".LG", "nigerian": ".LG", "ghana": ".GH",
                "casablanca": ".CS", "egypt": ".CA", "egyptian": ".CA",
            }
            for key, suffix in _YF_SUFFIX.items():
                if key in exchange:
                    yf_ticker = yf_ticker + suffix
                    break
        try:
            import yfinance as yf
            import requests, requests.adapters
            session = requests.Session()
            session.mount("https://", requests.adapters.HTTPAdapter(max_retries=2))
            tkr = yf.Ticker(yf_ticker, session=session)
            try:
                fast = tkr.fast_info
                price = getattr(fast, "last_price", None)
                shares = getattr(fast, "shares", None)
                mktcap = getattr(fast, "market_cap", None)
            except Exception:
                d = tkr.info
                price = d.get("currentPrice") or d.get("regularMarketPrice")
                shares = d.get("sharesOutstanding")
                mktcap = d.get("marketCap")
            if price is None:
                raise ValueError(f"no price returned for {yf_ticker}")
            today = _dt.date.today().isoformat()
            ext = dict(co.extracted or {})
            mkt = dict(ext.get("market", {}) or {})
            mkt["share_price"] = round(float(price), 4)
            mkt["price_date"] = today
            if shares:
                mkt["shares_outstanding"] = round(float(shares) / 1e6, 2)
            if mktcap:
                mkt["market_cap_usd"] = round(float(mktcap) / 1e9, 3)
            mkt["source"] = f"Yahoo Finance / yfinance ({yf_ticker})"
            mkt["repriced_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
            ext["market"] = mkt
            # Store last_repriced in universe (since the Company model doesn't have the column)
            uni = dict(co.universe or {})
            uni["last_repriced"] = mkt["repriced_at"]
            co.universe = uni
            co.extracted = ext
            db.add(co)
            repriced += 1
            results.append({"slug": co.slug, "ticker": yf_ticker, "price": price, "error": None})
        except Exception as e:
            failed += 1
            results.append({"slug": co.slug, "ticker": yf_ticker, "price": None, "error": str(e)})
        time.sleep(0.5)
    db.commit()
    return {"repriced": repriced, "failed": failed, "results": results}


@router.get("/consistency-check")
def consistency_check(db: Session = Depends(get_db)):
    """Compare platform DCF targets vs workbook targets for all companies."""
    import datetime as _dt4
    _now = _dt4.datetime.now(_dt4.timezone.utc)
    cached = _consistency_cache.get("result")
    cached_ts = _consistency_cache.get("ts")
    if cached is not None and cached_ts is not None:
        age = (_now - cached_ts).total_seconds()
        if age < 3600:
            return cached
    companies = db.query(D.Company).all()
    out = []
    for co in companies:
        wb_path = Path("output") / "models" / f"{co.slug}_model.xlsx"
        # Get platform target
        platform_target = None
        try:
            ext = pdata.merged_extracted(co.extracted or {})
            deep = pdata.deep_from_extracted(ext, co.universe or {})
            if deep and deep.get("rating"):
                platform_target = deep["rating"].get("target")
        except Exception:
            pass
        if platform_target is None:
            continue  # no platform target — skip
        if not wb_path.exists():
            out.append({"slug": co.slug, "name": co.name,
                        "platform_target": platform_target,
                        "workbook_target": None, "pct_diff": None,
                        "status": "workbook_unreadable"})
            continue
        # Read workbook target
        workbook_target = None
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(wb_path), data_only=True)
            # Look in the Valuation sheet for "WEIGHTED TARGET PRICE"
            ws = wb["Valuation"] if "Valuation" in wb.sheetnames else None
            if ws:
                for row in ws.iter_rows():
                    for cell in row:
                        if cell.value and isinstance(cell.value, str) and \
                                "weighted target" in cell.value.lower():
                            # The value is in column D (offset 3 from col A)
                            val_cell = ws.cell(row=cell.row, column=4)
                            if isinstance(val_cell.value, (int, float)):
                                workbook_target = float(val_cell.value)
                            break
                    if workbook_target is not None:
                        break
        except Exception:
            out.append({"slug": co.slug, "name": co.name,
                        "platform_target": platform_target,
                        "workbook_target": None, "pct_diff": None,
                        "status": "workbook_unreadable"})
            continue
        if workbook_target is None:
            out.append({"slug": co.slug, "name": co.name,
                        "platform_target": platform_target,
                        "workbook_target": None, "pct_diff": None,
                        "status": "workbook_unreadable"})
            continue
        pct_diff = abs(platform_target - workbook_target) / abs(workbook_target) if workbook_target else None
        status = "diverged" if (pct_diff is not None and pct_diff > 0.05) else "ok"
        out.append({"slug": co.slug, "name": co.name,
                    "platform_target": round(platform_target, 4),
                    "workbook_target": round(workbook_target, 4),
                    "pct_diff": round(pct_diff, 4) if pct_diff is not None else None,
                    "status": status})
    result = {"companies": out, "total": len(out),
              "diverged": sum(1 for c in out if c["status"] == "diverged"),
              "ok": sum(1 for c in out if c["status"] == "ok")}
    _consistency_cache["result"] = result
    _consistency_cache["ts"] = _dt4.datetime.now(_dt4.timezone.utc)
    return result


@router.post("/companies/{cid}/reprice")
def reprice_market(cid: int, db: Session = Depends(get_db), _user=Depends(A.require_analyst)):
    """Fetch a fresh live price for this company and update market data in extracted."""
    import datetime as _dt
    co = _company(db, cid)
    ticker = co.ticker or ""
    yf_ticker = (co.universe or {}).get("yf_ticker") or ticker
    if not yf_ticker:
        raise HTTPException(400, "No ticker configured for this company")
    # Append exchange suffix for non-US exchanges so yfinance can resolve the ticker
    if "." not in yf_ticker:
        exchange = ((co.universe or {}).get("exchange") or "").lower()
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
        for key, suffix in _YF_SUFFIX.items():
            if key in exchange:
                yf_ticker = yf_ticker + suffix
                break
    try:
        import yfinance as yf
        import requests, requests.adapters
        # Use a fresh session to avoid threading conflicts with yfinance's cache
        session = requests.Session()
        session.mount("https://", requests.adapters.HTTPAdapter(max_retries=2))
        tkr = yf.Ticker(yf_ticker, session=session)
        try:
            fast = tkr.fast_info
            price = getattr(fast, "last_price", None)
            shares = getattr(fast, "shares", None)
            mktcap = getattr(fast, "market_cap", None)
        except Exception:
            d = tkr.info
            price = d.get("currentPrice") or d.get("regularMarketPrice")
            shares = d.get("sharesOutstanding")
            mktcap = d.get("marketCap")
        if price is None:
            raise HTTPException(502, f"yfinance returned no price for {yf_ticker}")
        today = _dt.date.today().isoformat()
        ext = dict(co.extracted or {})
        mkt = dict(ext.get("market", {}) or {})
        mkt["share_price"] = round(float(price), 4)
        mkt["price_date"] = today
        if shares:
            mkt["shares_outstanding"] = round(float(shares) / 1e6, 2)  # millions
        if mktcap:
            mkt["market_cap_usd"] = round(float(mktcap) / 1e9, 3)  # billions
        mkt["source"] = f"Yahoo Finance / yfinance ({yf_ticker})"
        mkt["repriced_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        ext["market"] = mkt
        co.extracted = ext
        db.add(co)
        db.commit()
        D.audit(db, "market_repriced", "company", cid, price=price, date=today, actor=_user.email)
        return {"ok": True, "price": price, "price_date": today, "ticker": yf_ticker, **_fresh(co)}
    except ImportError:
        raise HTTPException(503, "yfinance not installed — run: pip install yfinance")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Price fetch failed: {e}")


@router.get("/companies/{cid}/sector-forecast")
def sector_forecast(cid: int, db: Session = Depends(get_db), _user=Depends(A.require_viewer)):
    """Run the sector-appropriate bottom-up forecast for this company.

    Detects the sector from ``co.sector``, pulls operating drivers from
    ``co.extracted["drivers"]`` if present, else derives reasonable defaults from
    the last two years of financials, then runs a 5-year forecast.
    """
    from aeon_nimbus.sector_engines import engine_for_sector

    co = _company(db, cid)
    sector = (co.sector or "").strip()
    EngineClass = engine_for_sector(sector)
    if EngineClass is None:
        return {"available": False, "reason": f"no sector engine for this sector: {sector!r}"}

    ext = pdata.merged_extracted(co.extracted or {})
    fins = sorted(ext.get("financials", []), key=lambda f: f.get("fy", ""))
    drivers = (co.extracted or {}).get("drivers") or {}

    # --- derive defaults from financials when no explicit drivers provided ---
    if not drivers and fins:
        last = fins[-1]
        prev = fins[-2] if len(fins) >= 2 else {}
        sector_lower = sector.lower()

        if "bank" in sector_lower:
            # NIM proxy: nii / loan_book; else use 8% default
            nii_l = last.get("nii") or last.get("net_interest_income")
            lb_l = last.get("loan_book") or last.get("loans")
            nii_p = prev.get("nii") or prev.get("net_interest_income")
            lb_p = prev.get("loan_book") or prev.get("loans")
            nim = None
            if nii_l and lb_l and lb_l > 0:
                nim = nii_l / lb_l
            elif nii_p and lb_p and lb_p > 0:
                nim = nii_p / lb_p
            # loan growth: yoy change in loan book
            loan_growth = None
            if lb_l and lb_p and lb_p > 0:
                loan_growth = (lb_l - lb_p) / lb_p
            drivers = {
                "loan_growth_pct": round(loan_growth, 4) if loan_growth is not None else 0.10,
                "nim_pct": round(nim, 4) if nim is not None else 0.08,
                "cost_income_ratio": 0.50,
                "loan_loss_provision_pct": 0.02,
                "tax_rate": round(last.get("tax_rate") or 0.30, 4),
            }

        elif "telecom" in sector_lower:
            rev_l = last.get("revenue") or 0
            rev_p = prev.get("revenue") or 0
            rev_growth = (rev_l - rev_p) / rev_p if rev_p else 0.05
            ebitda_l = last.get("ebitda") or 0
            ebitda_margin = ebitda_l / rev_l if rev_l else 0.40
            drivers = {
                "sub_growth_pct": round(rev_growth * 0.6, 4),  # approx: part of rev growth is volume
                "arpu_growth_pct": round(rev_growth * 0.4, 4),
                "ebitda_margin_pct": round(min(max(ebitda_margin, 0.15), 0.65), 4),
                "da_pct_revenue": 0.12,
                "tax_rate": round(last.get("tax_rate") or 0.30, 4),
                "capex_pct_revenue": 0.18,
            }

        elif any(k in sector_lower for k in ("cement", "mining", "oil", "materials")):
            rev_l = last.get("revenue") or 0
            rev_p = prev.get("revenue") or 0
            rev_growth = (rev_l - rev_p) / rev_p if rev_p else 0.05
            ebitda_l = last.get("ebitda") or 0
            ebitda_margin = ebitda_l / rev_l if rev_l else 0.30
            drivers = {
                "volume_growth_pct": round(rev_growth * 0.5, 4),
                "price_growth_pct": round(rev_growth * 0.5, 4),
                "cash_cost_per_tonne": 65.0,
                "fixed_costs_m": round(rev_l * 0.15, 2) if rev_l else 50.0,
                "da_pct_revenue": 0.08,
                "tax_rate": round(last.get("tax_rate") or 0.30, 4),
                "capex_m": abs(last.get("capex") or rev_l * 0.10 if rev_l else 30.0),
            }

        else:  # technology, consumer, healthcare, industrials, financials (non-bank), etc.
            rev_l = last.get("revenue") or 0
            rev_p = prev.get("revenue") or 0
            rev_growth = (rev_l - rev_p) / rev_p if rev_p else 0.08
            ebitda_l = last.get("ebitda") or 0
            ebitda_margin = ebitda_l / rev_l if rev_l else 0.20
            capex_l = abs(last.get("capex") or 0)
            capex_pct = capex_l / rev_l if rev_l else 0.04
            drivers = {
                "revenue_growth_pct": round(max(min(rev_growth, 0.50), -0.10), 4),
                "ebitda_margin_pct": round(max(min(ebitda_margin, 0.70), 0.05), 4),
                "da_pct_revenue": round(last.get("da_pct") or 0.05, 4),
                "tax_rate": round(last.get("tax_rate") or 0.25, 4),
                "capex_pct_revenue": round(min(capex_pct, 0.20), 4),
            }

    # --- base-year data ---
    base = {}
    if fins:
        last = fins[-1]
        base["fy"] = last.get("fy")
        for k in ("revenue", "loan_book", "loans", "non_interest_income", "subscribers_m", "arpu",
                  "volume_mt", "price_per_tonne", "cash_cost_per_tonne", "fixed_costs_m", "capex_m"):
            if last.get(k) is not None:
                base[k] = last[k]
        # fallback aliases
        if "loan_book" not in base and last.get("loans"):
            base["loan_book"] = last["loans"]
        if "non_interest_income" not in base:
            base["non_interest_income"] = last.get("non_interest_income") or (last.get("revenue", 1000) * 0.15)

    engine = EngineClass(base_year_data=base, drivers=drivers, years=5)
    forecast_rows = engine.forecast()

    D.audit(db, "sector_forecast_run", "company", cid, sector=sector, actor=_user.email)

    return {
        "available": True,
        "sector": sector,
        "engine": EngineClass.__name__,
        "drivers": drivers,
        "forecast": forecast_rows,
    }


# ---------------------------------------------------------------------------
# Filing alerts endpoint
# ---------------------------------------------------------------------------

@router.get("/filing-alerts")
def filing_alerts_endpoint(db: Session = Depends(get_db), _user=Depends(A.require_viewer)):
    """Check all companies for new IR filings newer than stored data.

    Results are cached for 60 minutes to avoid hammering IR pages on every call.
    """
    import datetime as _dt2

    # Serve cached result if fresh (cache is warmed by the 24h lifespan background loop)
    cached = _filing_alert_cache.get("result")
    cached_ts = _filing_alert_cache.get("ts")
    if cached is not None and cached_ts is not None:
        age = (_dt2.datetime.now(_dt2.timezone.utc) - cached_ts).total_seconds()
        if age < 3600:
            return cached

    # Cache cold or stale — run a quick check (max 10 companies with IR URLs to avoid timeout)
    companies = db.query(D.Company).all()
    company_list = []
    for co in companies:
        universe = co.universe or {}
        extracted = co.extracted or {}
        if universe.get("ir_url") or universe.get("annual_report_url"):
            company_list.append({
                "slug": co.slug,
                "name": (universe.get("name") or co.slug or ""),
                "universe": universe,
                "extracted": extracted,
            })
    # Limit inline check to 10 companies; background loop handles the full universe
    company_list = company_list[:10]

    alerts = FA.check_all_companies(company_list)
    now_iso = _dt2.datetime.now(_dt2.timezone.utc).isoformat()
    result = {
        "alerts": alerts,
        "checked": len(company_list),
        "found": len(alerts),
        "as_of": now_iso,
    }

    _filing_alert_cache["ts"] = _dt2.datetime.now(_dt2.timezone.utc)
    _filing_alert_cache["result"] = result

    return result
