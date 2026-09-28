"""Export endpoints for data downloads."""
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from aeon_nimbus import db as D
from aeon_nimbus.platform_data import merged_extracted, deep_from_extracted
import json

router = APIRouter(prefix="/api/export", tags=["export"])

def get_db():
    db = D.SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/{slug}/json")
def export_json(slug: str, db: Session = Depends(get_db)):
    """Export full company data as JSON."""
    co = db.query(D.Company).filter(D.Company.slug == slug).first()
    if not co:
        raise HTTPException(404)

    ext = merged_extracted(co.extracted or {})
    deep = deep_from_extracted(ext, co.universe or {}) if ext.get("financials") else None

    return {
        "company": {
            "slug": co.slug,
            "name": co.name,
            "ticker": co.ticker,
            "sector": co.sector,
            "country": co.country,
        },
        "financials": ext.get("financials", {}),
        "analysis": deep or {},
        "extracted": ext
    }

@router.get("/{slug}/csv")
def export_csv(slug: str, db: Session = Depends(get_db)):
    """Export financials as CSV."""
    co = db.query(D.Company).filter(D.Company.slug == slug).first()
    if not co:
        raise HTTPException(404)

    ext = merged_extracted(co.extracted or {})
    financials = ext.get("financials", {})

    # Build CSV from financials
    rows = ["Year,Revenue,EBITDA,Net Income,Total Assets,Total Equity"]
    for fy, data in financials.items():
        if isinstance(data, dict):
            rows.append(f"{fy},{data.get('revenue', '')},{data.get('ebitda', '')},{data.get('net_income', '')},{data.get('total_assets', '')},{data.get('total_equity', '')}")

    csv_content = "\n".join(rows)

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={slug}_financials.csv"}
    )
