"""Data Studio core — staging, threshold and merge logic shared by the API
router and the background collector.

Kept free of any import of jobs/api so both can depend on it without a cycle.
The contract: collected/uploaded figures are written to ProposedFact; those at or
above the user's confidence threshold auto-merge into the company's working copy
(Company.extracted) and are logged; the rest wait in the review table. Every
proposal — auto-accepted or not — keeps its source, so nothing unsourced ever
reaches the model.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from aeon_nimbus import db as D
from aeon_nimbus import ingest


def company_view(co: "D.Company") -> dict:
    """The merged dict ingest expects (universe metadata + working financials)."""
    u = co.universe or {}
    ext = co.extracted or {}
    return {
        "ticker": co.ticker, "name": co.name, "country": co.country,
        "exchange": co.exchange or u.get("exchange"),
        "currency": ext.get("currency") or co.currency,
        "unit": ext.get("unit") or u.get("unit"),
        "financials_url": ext.get("financials_url") or u.get("financials_url"),
        "annual_report_url": (ext.get("annual_report_url") or u.get("annual_report_url")
                              or co.ir_url or u.get("ir_url")),
        "financials": ext.get("financials", []),
    }


def project_for(db, co: "D.Company") -> "D.Project":
    """Get or create the working project for a company (the studio hides projects
    from the user; internally every company has exactly one)."""
    p = (db.query(D.Project).filter_by(company_id=co.id)
         .order_by(D.Project.id.desc()).first())
    if p is None:
        p = D.Project(company_id=co.id, status="draft")
        db.add(p)
        db.commit()
        D.audit(db, "project_created", "project", p.id, company=co.slug, via="studio")
    return p


def _existing_value(co: "D.Company", fy: str, item: str) -> Optional[float]:
    for f in (co.extracted or {}).get("financials", []):
        if f.get("fy") == fy:
            v = f.get(item)
            return float(v) if isinstance(v, (int, float)) else None
    return None


def _existing_conf(co: "D.Company", fy: str) -> Optional[float]:
    for f in (co.extracted or {}).get("financials", []):
        if f.get("fy") == fy:
            c = f.get("confidence")
            return float(c) if isinstance(c, (int, float)) else None
    return None


def _serialize(pf: "D.ProposedFact") -> dict[str, Any]:
    return {"id": pf.id, "batch": pf.batch, "origin": pf.origin, "fy": pf.fy, "item": pf.item,
            "statement": pf.statement, "value": pf.value, "prior_value": pf.prior_value,
            "unit": pf.unit, "currency": pf.currency, "source": pf.source, "source_url": pf.source_url,
            "confidence": pf.confidence, "status": pf.status}


def stage_proposals(db, co: "D.Company", proposals: list[dict], origin: str,
                    threshold: float, actor: str = "system") -> dict[str, Any]:
    """Persist proposals, auto-merge those at/above threshold, return a summary."""
    batch = f"{origin}-{uuid.uuid4().hex[:8]}"
    auto: list[dict] = []
    rows: list[D.ProposedFact] = []
    for p in proposals:
        conf = p.get("confidence") or 0.0
        # For automated WEB collection, never let a lower-confidence figure silently
        # overwrite an existing higher-confidence (e.g. audited) one — it waits for
        # review. Uploads are user-initiated (explicit trust), so they are not gated
        # this way.
        prior = _existing_value(co, p["fy"], p["item"])
        prior_conf = _existing_conf(co, p["fy"])
        downgrade = (origin == "web" and prior is not None and prior_conf is not None
                     and conf < prior_conf - 1e-9)
        # A figure that CONTRADICTS the one already on file (>2%) never auto-merges,
        # whatever its confidence — two sources disagreeing is exactly the case that
        # needs an analyst's eyes. Identical re-collections still flow through.
        conflict = (origin in ("web", "filing_ai") and prior is not None
                    and isinstance(p.get("value"), (int, float))
                    and abs(abs(p["value"]) - abs(prior)) > max(1.5, 0.02 * max(abs(p["value"]), abs(prior))))
        status = "auto_approved" if (conf >= threshold and not downgrade and not conflict) else "pending"
        pf = D.ProposedFact(
            company_id=co.id, batch=batch, origin=origin, fy=p["fy"], item=p["item"],
            statement=p.get("statement"), value=p.get("value"),
            prior_value=prior,
            unit=p.get("unit"), currency=p.get("currency"), source=p.get("source"),
            source_url=p.get("source_url"), confidence=conf, status=status)
        db.add(pf)
        rows.append(pf)
        if status == "auto_approved":
            auto.append(p)
    db.commit()
    if auto:
        co.extracted = ingest.merge_facts(co.extracted or {}, auto)
        db.add(co)
        db.commit()
    D.audit(db, "studio_stage", "company", co.id, origin=origin, batch=batch,
            proposed=len(rows), auto_accepted=len(auto), pending=len(rows) - len(auto),
            threshold=threshold, actor=actor)
    return {"batch": batch, "proposed": len(rows), "auto_accepted": len(auto),
            "pending": len(rows) - len(auto), "items": [_serialize(pf) for pf in rows]}


def approve(db, pf: "D.ProposedFact", actor: str = "system") -> None:
    """Approve a single staged proposal and merge it into the working copy."""
    co = db.get(D.Company, pf.company_id)
    co.extracted = ingest.merge_facts(co.extracted or {}, [{
        "fy": pf.fy, "item": pf.item, "value": pf.value, "unit": pf.unit,
        "currency": pf.currency, "source": pf.source, "confidence": pf.confidence}])
    pf.status = "approved"
    db.add_all([co, pf])
    db.commit()
    D.audit(db, "studio_fact_approved", "company", co.id, fy=pf.fy, item=pf.item,
            value=pf.value, actor=actor)


def reject(db, pf: "D.ProposedFact", actor: str = "system") -> None:
    pf.status = "rejected"
    db.add(pf)
    db.commit()
    D.audit(db, "studio_fact_rejected", "company", pf.company_id, fy=pf.fy,
            item=pf.item, actor=actor)
