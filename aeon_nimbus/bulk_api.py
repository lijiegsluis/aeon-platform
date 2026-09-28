"""Bulk enrichment API for scaling qualitative data across thousands of companies.

Processes companies in batches, detects thin qualitative content, and auto-enriches
to Safaricom-level depth using the auto_enrich module.
"""
from pathlib import Path
from typing import Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from aeon_nimbus import db as D
from aeon_nimbus.auto_enrich import needs_enrichment, enrich_on_click

router = APIRouter(prefix="/api/bulk", tags=["bulk-operations"])


def get_db():
    db = D.SessionLocal()
    try:
        yield db
    finally:
        db.close()


class BulkEnrichRequest(BaseModel):
    slugs: list[str] | None = None  # Specific companies, or None for all
    min_quality_threshold: int = 6  # Enrichment threshold


class BulkEnrichResponse(BaseModel):
    total: int
    enriched: int
    already_complete: int
    errors: list[dict]


@router.post("/enrich", response_model=BulkEnrichResponse)
def bulk_enrich(req: BulkEnrichRequest, db: Session = Depends(get_db)):
    """Enrich multiple companies in one request."""
    data_dir = Path("data")

    # Determine which companies to process
    if req.slugs:
        slugs = req.slugs
    else:
        # All companies in database
        companies = db.query(D.Company).all()
        slugs = [c.slug for c in companies if c.slug]

    total = len(slugs)
    enriched_count = 0
    complete_count = 0
    errors = []

    for slug in slugs:
        try:
            result = enrich_on_click(slug, data_dir)
            if result.get("status") == "enriched":
                enriched_count += 1
            elif result.get("status") == "already_complete":
                complete_count += 1
            elif "error" in result:
                errors.append({"slug": slug, "error": result["error"]})
        except Exception as e:
            errors.append({"slug": slug, "error": str(e)})

    return BulkEnrichResponse(
        total=total,
        enriched=enriched_count,
        already_complete=complete_count,
        errors=errors
    )


@router.get("/quality-report")
def quality_report(db: Session = Depends(get_db)):
    """Generate data quality report across all companies."""
    from aeon_nimbus import platform_data as pdata

    companies = db.query(D.Company).all()
    thin_companies = []
    adequate_companies = []

    data_dir = Path("data")

    for co in companies:
        extracted_path = data_dir / "extracted" / f"{co.slug}.json"
        if needs_enrichment(extracted_path):
            thin_companies.append({
                "slug": co.slug,
                "name": co.name,
                "ticker": co.ticker
            })
        else:
            adequate_companies.append(co.slug)

    return {
        "total_companies": len(companies),
        "adequate_quality": len(adequate_companies),
        "needs_enrichment": len(thin_companies),
        "thin_companies": thin_companies[:50],  # First 50 for display
        "coverage_pct": round(len(adequate_companies) / len(companies) * 100, 1)
    }
