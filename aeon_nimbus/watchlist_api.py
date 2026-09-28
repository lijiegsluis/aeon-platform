"""Portfolio tracking and watchlists."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from aeon_nimbus import db as D
from aeon_nimbus import auth

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

def get_db():
    db = D.SessionLocal()
    try:
        yield db
    finally:
        db.close()

class WatchlistCreate(BaseModel):
    name: str
    slugs: list[str] = []

def _get_user_id(request: Request):
    """Extract user ID from request."""
    user = auth.current_user(request)
    return user.id if user else 1  # Default to user 1 for demo

@router.get("/")
def list_watchlists(request: Request, db: Session = Depends(get_db)):
    """Get all watchlists for current user."""
    user_id = _get_user_id(request)
    wls = db.query(D.Watchlist).filter(D.Watchlist.user_id == user_id).all()
    return [{"id": w.id, "name": w.name, "slug_count": len(w.slugs.split(",") if w.slugs else [])} for w in wls]

@router.post("/")
def create_watchlist(req: WatchlistCreate, request: Request, db: Session = Depends(get_db)):
    """Create new watchlist."""
    user_id = _get_user_id(request)
    wl = D.Watchlist(user_id=user_id, name=req.name, slugs=",".join(req.slugs))
    db.add(wl)
    db.commit()
    return {"id": wl.id, "name": wl.name}

@router.get("/{wl_id}")
def get_watchlist(wl_id: int, request: Request, db: Session = Depends(get_db)):
    """Get watchlist details with company data."""
    user_id = _get_user_id(request)
    wl = db.query(D.Watchlist).filter(D.Watchlist.id == wl_id, D.Watchlist.user_id == user_id).first()
    if not wl:
        raise HTTPException(404, "Watchlist not found")

    from aeon_nimbus.platform_data import merged_extracted, deep_from_extracted

    slugs = wl.slugs.split(",") if wl.slugs else []
    companies = []
    for slug in slugs:
        co = db.query(D.Company).filter(D.Company.slug == slug).first()
        if co:
            ext = merged_extracted(co.extracted or {})
            deep = deep_from_extracted(ext, co.universe or {}) if ext.get("financials") else None
            companies.append({
                "slug": co.slug,
                "name": co.name,
                "ticker": co.ticker,
                "rating": deep.get("rating", {}).get("stance") if deep else None,
                "upside": deep.get("rating", {}).get("upside") if deep else None,
            })

    return {"id": wl.id, "name": wl.name, "companies": companies}

@router.put("/{wl_id}/add/{slug}")
def add_to_watchlist(wl_id: int, slug: str, request: Request, db: Session = Depends(get_db)):
    """Add company to watchlist."""
    user_id = _get_user_id(request)
    wl = db.query(D.Watchlist).filter(D.Watchlist.id == wl_id, D.Watchlist.user_id == user_id).first()
    if not wl:
        raise HTTPException(404)

    slugs = set(wl.slugs.split(",") if wl.slugs else [])
    slugs.add(slug)
    wl.slugs = ",".join(s for s in slugs if s)
    db.commit()
    return {"ok": True}

@router.delete("/{wl_id}")
def delete_watchlist(wl_id: int, request: Request, db: Session = Depends(get_db)):
    """Delete watchlist."""
    user_id = _get_user_id(request)
    wl = db.query(D.Watchlist).filter(D.Watchlist.id == wl_id, D.Watchlist.user_id == user_id).first()
    if wl:
        db.delete(wl)
        db.commit()
    return {"ok": True}
