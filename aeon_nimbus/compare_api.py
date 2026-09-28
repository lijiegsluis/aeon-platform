"""Company comparison API - side-by-side analysis."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from aeon_nimbus import db as D
from aeon_nimbus.platform_data import merged_extracted, deep_from_extracted

router = APIRouter(prefix="/api/compare", tags=["compare"])

def get_db():
    db = D.SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/")
def compare_companies(
    slugs: str = Query(..., description="comma-separated slugs"),
    db: Session = Depends(get_db)
):
    """Compare 2-5 companies side-by-side."""
    slug_list = [s.strip() for s in slugs.split(",")][:5]

    results = []
    for slug in slug_list:
        co = db.query(D.Company).filter(D.Company.slug == slug).first()
        if not co:
            continue

        ext = merged_extracted(co.extracted or {})
        deep = deep_from_extracted(ext, co.universe or {}) if ext.get("financials") else None

        if not deep:
            results.append({"slug": slug, "name": co.name, "error": "no_data"})
            continue

        stats = deep.get("keystats", {})
        rev = deep.get("revenue", [])
        ebitda_m = deep.get("ebitda_margin", [])

        results.append({
            "slug": co.slug,
            "name": co.name,
            "ticker": co.ticker,
            "sector": co.sector,
            "country": co.country,
            "market_cap_m": stats.get("market_cap_m"),
            "ev_ebitda": stats.get("ev_ebitda"),
            "fcf_yield": stats.get("fcf_yield"),
            "revenue_latest": rev[-1] if rev else None,
            "revenue_cagr_3y": _cagr(rev[-4:]) if len(rev) >= 4 else None,
            "ebitda_margin_latest": ebitda_m[-1] if ebitda_m else None,
            "rating": (deep.get("rating") or {}).get("stance"),
            "upside": (deep.get("rating") or {}).get("upside"),
        })

    # Add sector medians
    if results and results[0].get("sector"):
        sector = results[0]["sector"]
        sector_cos = db.query(D.Company).filter(D.Company.sector.ilike(f"%{sector}%")).all()

        ev_ebitdas = []
        fcf_yields = []
        for sc in sector_cos:
            ext = merged_extracted(sc.extracted or {})
            deep = deep_from_extracted(ext, sc.universe or {}) if ext.get("financials") else None
            if deep:
                stats = deep.get("keystats", {})
                if stats.get("ev_ebitda"):
                    ev_ebitdas.append(stats["ev_ebitda"])
                if stats.get("fcf_yield"):
                    fcf_yields.append(stats["fcf_yield"])

        sector_medians = {
            "ev_ebitda": sorted(ev_ebitdas)[len(ev_ebitdas)//2] if ev_ebitdas else None,
            "fcf_yield": sorted(fcf_yields)[len(fcf_yields)//2] if fcf_yields else None,
        }
    else:
        sector_medians = {}

    return {
        "companies": results,
        "sector_medians": sector_medians,
        "count": len(results)
    }

def _cagr(series):
    if len(series) < 2 or not series[0] or not series[-1]:
        return None
    return round((series[-1] / series[0]) ** (1 / (len(series) - 1)) - 1, 3)
