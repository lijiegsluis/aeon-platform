"""Trusted-research data layer (spec §18).

SQLAlchemy models over SQLite in dev (no server needed) and PostgreSQL in
production via DATABASE_URL. The raw-source layer already lives on disk under
data/raw_docs and data/extracted; this is the trusted layer: companies,
projects, approved financial facts, controls, overrides, generated models, jobs
and an append-only audit log. Raw data is never overwritten — approvals and
overrides are new rows.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, create_engine, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker
from sqlalchemy.types import JSON

ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'platform.db'}")
_connect = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    ticker: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    isin: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    exchange: Mapped[Optional[str]] = mapped_column(String(120))
    country: Mapped[Optional[str]] = mapped_column(String(80))
    sector: Mapped[Optional[str]] = mapped_column(String(80))
    sub_sector: Mapped[Optional[str]] = mapped_column(String(160))
    currency: Mapped[Optional[str]] = mapped_column(String(8))
    fiscal_year_end: Mapped[Optional[str]] = mapped_column(String(20))
    accounting_standard: Mapped[Optional[str]] = mapped_column(String(20))
    valuation_model: Mapped[Optional[str]] = mapped_column(String(24))
    ir_url: Mapped[Optional[str]] = mapped_column(String(400))
    universe: Mapped[dict] = mapped_column(JSON, default=dict)     # full registry entry
    extracted: Mapped[dict] = mapped_column(JSON, default=dict)   # full extracted dataset (or {})


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    status: Mapped[str] = mapped_column(String(40), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    company: Mapped[Company] = relationship()


class FinancialFact(Base):
    __tablename__ = "financial_facts"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    fy: Mapped[str] = mapped_column(String(12))
    item: Mapped[str] = mapped_column(String(60))
    statement_type: Mapped[str] = mapped_column(String(24))
    reported_value: Mapped[Optional[float]] = mapped_column(Float)
    currency: Mapped[Optional[str]] = mapped_column(String(8))
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    source: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    is_derived: Mapped[bool] = mapped_column(default=False)
    review_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/approved/rejected


class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(100))
    slugs: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ProposedFact(Base):
    """A figure proposed by the Data Studio (web collection or file upload),
    staged for review. Trusted figures live in FinancialFact / Company.extracted;
    nothing here touches the model until it is approved (or auto-accepted above the
    user's confidence threshold). Every proposal carries its source so the
    never-fabricate contract holds even for open-web collection.
    """
    __tablename__ = "proposed_facts"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    batch: Mapped[str] = mapped_column(String(40), index=True)   # one collect/upload run
    origin: Mapped[str] = mapped_column(String(12))              # web | upload
    fy: Mapped[str] = mapped_column(String(12))
    item: Mapped[str] = mapped_column(String(60))
    statement: Mapped[Optional[str]] = mapped_column(String(24))
    value: Mapped[Optional[float]] = mapped_column(Float)
    prior_value: Mapped[Optional[float]] = mapped_column(Float)  # existing figure it would replace
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    currency: Mapped[Optional[str]] = mapped_column(String(8))
    source: Mapped[Optional[str]] = mapped_column(Text)          # human label (file / site + ref)
    source_url: Mapped[Optional[str]] = mapped_column(String(600))
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/approved/rejected/auto_approved
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Control(Base):
    __tablename__ = "controls"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    control_id: Mapped[str] = mapped_column(String(12))
    category: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(12))
    description: Mapped[str] = mapped_column(Text)
    expected: Mapped[Optional[str]] = mapped_column(Text)
    actual: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Override(Base):
    __tablename__ = "overrides"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    control_id: Mapped[str] = mapped_column(String(12))
    rationale: Mapped[str] = mapped_column(Text)
    evidence: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="requested")  # requested/approved/rejected
    requested_by: Mapped[str] = mapped_column(String(80), default="analyst")
    approved_by: Mapped[Optional[str]] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class GeneratedModel(Base):
    __tablename__ = "generated_models"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    model_id: Mapped[str] = mapped_column(String(40))
    version: Mapped[str] = mapped_column(String(16))
    path: Mapped[str] = mapped_column(String(400))
    dq_score: Mapped[Optional[float]] = mapped_column(Float)
    export_allowed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_now)
    actor: Mapped[str] = mapped_column(String(80), default="system")
    action: Mapped[str] = mapped_column(String(80))
    entity: Mapped[Optional[str]] = mapped_column(String(40))
    entity_id: Mapped[Optional[str]] = mapped_column(String(40))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


def audit(db, action: str, entity: str = "", entity_id: Any = "", actor: str = "system", **detail) -> None:
    db.add(AuditLog(actor=actor, action=action, entity=entity, entity_id=str(entity_id), detail=detail))
    db.commit()


class ResearchNote(Base):
    """Compound knowledge layer — past research that the analyst chatbot can query."""
    __tablename__ = "research_notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[Optional[str]] = mapped_column(String(12), index=True)   # YYYY-MM-DD
    task: Mapped[Optional[str]] = mapped_column(String(40))               # task5/doocey/houston/manual
    tickers: Mapped[Optional[str]] = mapped_column(String(200), index=True)  # comma-separated
    finding: Mapped[str] = mapped_column(Text)                            # the actual insight
    file_path: Mapped[Optional[str]] = mapped_column(String(600))         # source file on disk
    links_to: Mapped[Optional[str]] = mapped_column(String(400))          # related note ids/tickers
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class BloombergRow(Base):
    """One company selected for the Bloomberg earnings-estimates submission file."""
    __tablename__ = "bloomberg_row"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), index=True)
    template_row: Mapped[int] = mapped_column(Integer)
    overrides: Mapped[dict] = mapped_column(JSON, default=dict)
    added_by: Mapped[str] = mapped_column(String(160), default="")
    ts: Mapped[Optional[datetime]] = mapped_column(DateTime, default=_now)


class Setting(Base):
    """Small key/value settings an admin controls from the UI."""
    __tablename__ = "setting"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), default="")


_FACT_STATEMENT = {
    "revenue": "income_statement", "ebitda": "income_statement", "ebit": "income_statement",
    "net_income": "income_statement", "total_assets": "balance_sheet", "total_equity": "balance_sheet",
    "total_debt": "balance_sheet", "cash": "balance_sheet", "net_debt": "balance_sheet",
    "operating_cash_flow": "cash_flow", "capex": "cash_flow", "free_cash_flow": "cash_flow",
    "dividends_paid": "cash_flow",
}


def init_db() -> None:
    Base.metadata.create_all(engine)


def seed_from_files() -> dict[str, int]:
    """Seed companies + financial facts from the on-disk universe + extracted data."""
    init_db()
    uni = {u["slug"]: u for u in json.loads((ROOT / "data" / "universe.json").read_text())}
    extracted_dir = ROOT / "data" / "extracted"
    n_co = n_fact = 0
    with SessionLocal() as db:
        for slug, u in uni.items():
            if db.query(Company).filter_by(slug=slug).first():
                continue
            ext_path = extracted_dir / f"{slug}.json"
            ext = json.loads(ext_path.read_text()) if ext_path.exists() else {}
            if ext:
                ext["_peers"] = u.get("peers", [])  # so the peer control sees the universe peers
            co = Company(
                slug=slug, name=u.get("name"), ticker=u.get("ticker"), isin=u.get("isin"),
                exchange=u.get("exchange"), country=u.get("country"), sector=u.get("sector"),
                sub_sector=u.get("sub_sector"), currency=u.get("currency"),
                fiscal_year_end=u.get("fiscal_year_end", "December"),
                accounting_standard=u.get("accounting_standard", "IFRS"),
                valuation_model=u.get("valuation_model"), ir_url=u.get("ir_url"),
                universe=u, extracted=ext,
            )
            db.add(co)
            db.flush()
            n_co += 1
            for f in ext.get("financials", []):
                # Normalize alternate field names from agent-written files
                if "cash" not in f and "cash_and_equivalents" in f:
                    f["cash"] = f["cash_and_equivalents"]
                if "free_cash_flow" not in f and "fcf" in f:
                    f["free_cash_flow"] = f["fcf"]
                for item, stmt in _FACT_STATEMENT.items():
                    if item in f and f.get(item) is not None:
                        fy_val = f.get("fy") or (f"FY{f['fiscal_year']}" if f.get("fiscal_year") else None)
                        db.add(FinancialFact(
                            company_id=co.id, fy=fy_val, item=item, statement_type=stmt,
                            reported_value=f.get(item), currency=ext.get("currency"),
                            unit=ext.get("unit"), source=(f.get("source") or "")[:500],
                            confidence=f.get("confidence"), review_status="approved",
                        ))
                        n_fact += 1
        db.commit()
    return {"companies": n_co, "facts": n_fact}
