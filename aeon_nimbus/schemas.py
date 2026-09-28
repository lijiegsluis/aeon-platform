"""Typed records used across ingestion, extraction, analytics, and reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any


@dataclass
class SourceReference:
    source_document: str
    source_document_id: str
    source_page: int | None
    period: str
    currency: str
    unit: str
    confidence: float
    last_update: str
    raw_label: str | None = None
    extraction_method: str = "sample"
    source_bbox: str | None = None
    source_table_id: str | None = None
    validation: str = "not run"
    review_status: str = "draft"
    source_url: str | None = None


@dataclass
class FinancialField:
    field_id: str
    label: str
    value: float | None
    source: SourceReference
    usd_value: float | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["source"] = asdict(self.source)
        return out


@dataclass
class DocumentRecord:
    document_id: str
    company_id: str
    title: str
    document_type: str
    period: str
    source_url: str | None
    file_path: str | None
    page_count: int | None
    currency: str | None
    unit: str | None
    upload_date: str = field(default_factory=lambda: date.today().isoformat())
    extraction_status: str = "not started"
    review_status: str = "not reviewed"
    confidence: float = 0.0


@dataclass
class RiskFlag:
    risk_id: str
    company_id: str
    risk_type: str
    risk_level: str
    description: str
    source_document_id: str | None = None
    source_page: int | None = None
    analyst_comment: str | None = None
    status: str = "open"


@dataclass
class ValidationResult:
    check_name: str
    passed: bool
    expected: float | None
    actual: float | None
    difference: float | None
    severity: str
    message: str


def today_iso() -> str:
    return date.today().isoformat()
