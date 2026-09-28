"""SQLite storage for documents, extracted fields, financials, and review flags."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .config import DEFAULT_DATABASE_PATH
from .schemas import DocumentRecord, FinancialField


SCHEMA_SQL = """
create table if not exists companies (
    company_id text primary key,
    company_name text not null,
    ticker text,
    exchange text,
    country text,
    sector text,
    subsector text,
    currency text,
    listed_or_private text,
    website text,
    coverage_status text,
    analyst_owner text
);

create table if not exists documents (
    document_id text primary key,
    company_id text,
    title text,
    document_type text,
    period text,
    source_url text,
    file_path text,
    page_count integer,
    currency text,
    unit text,
    upload_date text,
    extraction_status text,
    review_status text,
    confidence real,
    metadata_json text
);

create table if not exists financials (
    company_id text,
    year text,
    period_type text,
    payload_json text not null,
    source_document_id text,
    source_page integer,
    confidence_score real,
    review_status text,
    primary key (company_id, year, period_type)
);

create table if not exists extracted_fields (
    field_id text primary key,
    company_id text,
    document_id text,
    statement_type text,
    raw_label text,
    standard_label text,
    period text,
    value real,
    currency text,
    unit text,
    page_number integer,
    source_bbox text,
    extractor text,
    extraction_confidence real,
    validation_status text,
    review_status text,
    payload_json text
);

create table if not exists validation_checks (
    check_id text primary key,
    company_id text,
    year text,
    check_name text,
    passed integer,
    severity text,
    message text,
    payload_json text
);

create table if not exists risk_flags (
    risk_id text primary key,
    company_id text,
    date text,
    risk_type text,
    risk_level text,
    description text,
    source_document_id text,
    source_page integer,
    analyst_comment text,
    status text
);

create table if not exists research_notes (
    note_id text primary key,
    company_id text,
    title text,
    author text,
    note_date text,
    body text,
    source_path text,
    tags text
);

create table if not exists audit_log (
    audit_id integer primary key autoincrement,
    user_id text,
    action text,
    object_id text,
    timestamp text,
    details_json text
);

create table if not exists dashboard_contexts (
    context_id text primary key,
    company_id text,
    as_of text,
    status text not null,
    context_json text not null,
    source_hash text,
    created_by text,
    created_at text
);
"""


def connect(database_path: str | Path = DEFAULT_DATABASE_PATH) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def initialise_database(database_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
    with connect(database_path) as conn:
        conn.executescript(SCHEMA_SQL)


def create_company_record(
    company_name: str,
    ticker: str | None = None,
    country: str | None = None,
    sector: str | None = None,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    **extra: Any,
) -> str:
    """Create a company profile in the database."""

    initialise_database(database_path)
    company_id = extra.get("company_id") or _slug(company_name)
    payload = {
        "company_id": company_id,
        "company_name": company_name,
        "ticker": ticker,
        "exchange": extra.get("exchange"),
        "country": country,
        "sector": sector,
        "subsector": extra.get("subsector"),
        "currency": extra.get("currency"),
        "listed_or_private": extra.get("listed_or_private"),
        "website": extra.get("website"),
        "coverage_status": extra.get("coverage_status", "watchlist"),
        "analyst_owner": extra.get("analyst_owner"),
    }
    with connect(database_path) as conn:
        conn.execute(
            """
            insert into companies values (
                :company_id, :company_name, :ticker, :exchange, :country, :sector,
                :subsector, :currency, :listed_or_private, :website, :coverage_status,
                :analyst_owner
            )
            on conflict(company_id) do update set
                company_name=excluded.company_name,
                ticker=excluded.ticker,
                exchange=excluded.exchange,
                country=excluded.country,
                sector=excluded.sector,
                subsector=excluded.subsector,
                currency=excluded.currency,
                listed_or_private=excluded.listed_or_private,
                website=excluded.website,
                coverage_status=excluded.coverage_status,
                analyst_owner=excluded.analyst_owner
            """,
            payload,
        )
    return company_id


def update_financials(
    company_id: str,
    year: str,
    financial_data: dict[str, Any],
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    period_type: str = "FY",
) -> None:
    """Add or update standardised financial data."""

    initialise_database(database_path)
    with connect(database_path) as conn:
        conn.execute(
            """
            insert into financials values (?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(company_id, year, period_type) do update set
                payload_json=excluded.payload_json,
                source_document_id=excluded.source_document_id,
                source_page=excluded.source_page,
                confidence_score=excluded.confidence_score,
                review_status=excluded.review_status
            """,
            (
                company_id,
                year,
                period_type,
                json.dumps(financial_data),
                financial_data.get("source_document_id"),
                financial_data.get("source_page"),
                financial_data.get("confidence_score"),
                financial_data.get("review_status", "draft"),
            ),
        )


def save_document_record(document: DocumentRecord, database_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
    """Persist a document record produced by ingestion."""

    initialise_database(database_path)
    metadata = _document_file_metadata(document.file_path)
    with connect(database_path) as conn:
        conn.execute(
            """
            insert into documents values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(document_id) do update set
                company_id=excluded.company_id,
                title=excluded.title,
                document_type=excluded.document_type,
                period=excluded.period,
                source_url=excluded.source_url,
                file_path=excluded.file_path,
                page_count=excluded.page_count,
                currency=excluded.currency,
                unit=excluded.unit,
                upload_date=excluded.upload_date,
                extraction_status=excluded.extraction_status,
                review_status=excluded.review_status,
                confidence=excluded.confidence,
                metadata_json=excluded.metadata_json
            """,
            (
                document.document_id,
                document.company_id,
                document.title,
                document.document_type,
                document.period,
                document.source_url,
                document.file_path,
                document.page_count,
                document.currency,
                document.unit,
                document.upload_date,
                document.extraction_status,
                document.review_status,
                document.confidence,
                json.dumps(metadata),
            ),
        )


def save_extracted_field(
    company_id: str,
    document_id: str,
    statement_type: str,
    field: FinancialField,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> None:
    """Persist one source-linked extracted financial field."""

    initialise_database(database_path)
    source = field.source
    with connect(database_path) as conn:
        conn.execute(
            """
            insert into extracted_fields values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(field_id) do update set
                company_id=excluded.company_id,
                document_id=excluded.document_id,
                statement_type=excluded.statement_type,
                raw_label=excluded.raw_label,
                standard_label=excluded.standard_label,
                period=excluded.period,
                value=excluded.value,
                currency=excluded.currency,
                unit=excluded.unit,
                page_number=excluded.page_number,
                source_bbox=excluded.source_bbox,
                extractor=excluded.extractor,
                extraction_confidence=excluded.extraction_confidence,
                validation_status=excluded.validation_status,
                review_status=excluded.review_status,
                payload_json=excluded.payload_json
            """,
            (
                field.field_id,
                company_id,
                document_id,
                statement_type,
                source.raw_label,
                field.label,
                source.period,
                field.value,
                source.currency,
                source.unit,
                source.source_page,
                source.source_bbox,
                source.extraction_method,
                source.confidence,
                source.validation,
                source.review_status,
                json.dumps(field.to_dict()),
            ),
        )


def approve_extracted_field(
    field_id: str,
    reviewer_id: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    min_confidence: float = 0.90,
) -> None:
    """Approve a single extracted field after source, validation, and confidence checks."""

    initialise_database(database_path)
    approved_at = datetime.utcnow().isoformat(timespec="seconds")
    with connect(database_path) as conn:
        row = conn.execute("select * from extracted_fields where field_id=?", (field_id,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown extracted field: {field_id}")
        confidence = float(row["extraction_confidence"] or 0)
        if confidence < min_confidence:
            raise ValueError(f"Cannot approve {field_id}: confidence {confidence:.2f} is below {min_confidence:.2f}")
        required = {
            "document_id": row["document_id"],
            "page_number": row["page_number"],
            "currency": row["currency"],
            "unit": row["unit"],
            "raw_label": row["raw_label"],
            "standard_label": row["standard_label"],
        }
        missing = [key for key, value in required.items() if value in (None, "", "UNKNOWN")]
        if missing:
            raise ValueError(f"Cannot approve {field_id}: missing {', '.join(missing)}")
        validation_status = str(row["validation_status"] or "").lower()
        if validation_status not in {"passed", "computed from sourced fields"}:
            raise ValueError(f"Cannot approve {field_id}: validation status is {row['validation_status']}")
        conn.execute("update extracted_fields set review_status='approved' where field_id=?", (field_id,))
        conn.execute(
            "insert into audit_log (user_id, action, object_id, timestamp, details_json) values (?, ?, ?, ?, ?)",
            (
                reviewer_id,
                "approve_extracted_field",
                field_id,
                approved_at,
                json.dumps({"min_confidence": min_confidence, "previous_review_status": row["review_status"]}),
            ),
        )


def get_company_history(company_id: str, database_path: str | Path = DEFAULT_DATABASE_PATH) -> list[dict[str, Any]]:
    """Return multi-year financial history."""

    initialise_database(database_path)
    with connect(database_path) as conn:
        rows = conn.execute(
            "select year, period_type, payload_json from financials where company_id=? order by year",
            (company_id,),
        ).fetchall()
    return [{"year": row["year"], "period_type": row["period_type"], **json.loads(row["payload_json"])} for row in rows]


def get_latest_company_snapshot(company_id: str, database_path: str | Path = DEFAULT_DATABASE_PATH) -> dict[str, Any]:
    """Return latest financials, valuation, liquidity, and risk flags."""

    initialise_database(database_path)
    with connect(database_path) as conn:
        company = conn.execute("select * from companies where company_id=?", (company_id,)).fetchone()
        financial = conn.execute(
            "select year, payload_json from financials where company_id=? order by year desc limit 1",
            (company_id,),
        ).fetchone()
        risks = conn.execute(
            "select * from risk_flags where company_id=? and status != 'closed' order by date desc",
            (company_id,),
        ).fetchall()
    return {
        "company": dict(company) if company else None,
        "latest_financials": json.loads(financial["payload_json"]) if financial else None,
        "latest_year": financial["year"] if financial else None,
        "risk_flags": [dict(row) for row in risks],
    }


def compare_company_to_peers(
    company_id: str,
    peer_group_id: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
) -> list[dict[str, Any]]:
    """Produce peer comparison data from stored financial records."""

    initialise_database(database_path)
    with connect(database_path) as conn:
        rows = conn.execute("select company_id, year, payload_json from financials order by company_id, year").fetchall()
    peers = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        if payload.get("peer_group_id") == peer_group_id or row["company_id"] == company_id:
            peers.append({"company_id": row["company_id"], "year": row["year"], **payload})
    return peers


def create_audit_log(
    user_id: str,
    action: str,
    object_id: str,
    timestamp: str,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    details: dict[str, Any] | None = None,
) -> None:
    """Record every material user action."""

    initialise_database(database_path)
    with connect(database_path) as conn:
        conn.execute(
            "insert into audit_log (user_id, action, object_id, timestamp, details_json) values (?, ?, ?, ?, ?)",
            (user_id, action, object_id, timestamp, json.dumps(details or {})),
        )


def lock_reviewed_data(company_id: str, year: str, database_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
    """Lock approved financial data to prevent accidental overwrite."""

    with connect(database_path) as conn:
        conn.execute(
            "update financials set review_status='locked' where company_id=? and year=? and review_status='approved'",
            (company_id, year),
        )


def compare_new_extraction_to_previous(previous: dict[str, float], new: dict[str, float], threshold: float = 0.05) -> list[dict[str, Any]]:
    """Flag large changes versus prior extracted data."""

    flags = []
    for key, new_value in new.items():
        if key not in previous:
            continue
        old_value = previous[key]
        if old_value in (None, 0) or new_value is None:
            continue
        change = (new_value - old_value) / old_value
        if abs(change) > threshold:
            flags.append({"field": key, "previous": old_value, "new": new_value, "change": change})
    return flags


def require_analyst_approval(field_id: str, database_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
    """Prevent low-confidence values from entering final outputs."""

    with connect(database_path) as conn:
        conn.execute("update extracted_fields set review_status='pending' where field_id=?", (field_id,))


def save_dashboard_context(
    context_id: str,
    company_id: str,
    as_of: str,
    context: dict[str, Any],
    status: str = "draft",
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    source_hash: str | None = None,
    created_by: str = "system",
    created_at: str | None = None,
) -> None:
    """Persist a complete dashboard context after review or approval."""

    import datetime

    initialise_database(database_path)
    errors = validate_dashboard_context_for_status(context, status)
    if errors:
        raise ValueError("Cannot save dashboard context with this status: " + "; ".join(errors))
    with connect(database_path) as conn:
        conn.execute(
            """
            insert into dashboard_contexts values (?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(context_id) do update set
                company_id=excluded.company_id,
                as_of=excluded.as_of,
                status=excluded.status,
                context_json=excluded.context_json,
                source_hash=excluded.source_hash,
                created_by=excluded.created_by,
                created_at=excluded.created_at
            """,
            (
                context_id,
                company_id,
                as_of,
                status,
                json.dumps(context),
                source_hash,
                created_by,
                created_at or datetime.datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )


def validate_dashboard_context_for_status(context: dict[str, Any], status: str) -> list[str]:
    """Return approval-gate errors for a dashboard context status."""

    errors: list[str] = []
    if status in {"approved", "reviewed_public_seed"} and _context_has_unapproved_values(context):
        errors.append("context contains sample, unverified, or placeholder markers")
    if status == "approved":
        export_status = context.get("export_status") or {}
        if str(export_status.get("client_ready", "")).lower() != "yes":
            errors.append("approved context requires export_status.client_ready to equal yes")
        blocking_gates = [gate.get("gate", "approval gate") for gate in context.get("approval_gates", []) if gate.get("blocks_client_export")]
        if blocking_gates:
            errors.append(f"approved context has blocking approval gates: {', '.join(map(str, blocking_gates))}")
        open_review_items = [
            item
            for company in context.get("companies", [])
            for item in company.get("review_queue", [])
        ]
        if open_review_items:
            errors.append("approved context cannot contain open review queue items")
        bad_statuses = sorted(
            {
                str(value).lower()
                for value in _collect_key_values(context, "review_status")
                if str(value).lower() not in {"approved", "locked", "reviewed and approved"}
            }
        )
        if bad_statuses:
            errors.append(f"unapproved review status values present: {', '.join(bad_statuses)}")
        if _collect_key_values(context, "data_caveat"):
            errors.append("approved context cannot contain unresolved data caveats")
    if status == "reviewed_public_seed":
        companies = context.get("companies") or []
        if not companies:
            errors.append("reviewed public seed requires at least one company")
        official_docs = [
            doc
            for company in companies
            for doc in company.get("documents", [])
            if doc.get("document_type") == "annual_report"
        ]
        if not any(doc.get("sha256") and doc.get("source_url") for doc in official_docs):
            errors.append("reviewed public seed requires an official annual report URL and SHA-256 hash")
        for company in companies:
            for field in _iter_financial_fields(company):
                source = field.get("source") or {}
                missing = [
                    key
                    for key in ("source_document", "source_page", "period", "currency", "unit", "confidence", "last_update", "source_url", "source_hash")
                    if source.get(key) in (None, "")
                ]
                if missing:
                    errors.append(f"{field.get('field_id', 'field')} is missing source fields: {', '.join(missing)}")
                if float(source.get("confidence") or 0) < 0.90:
                    errors.append(f"{field.get('field_id', 'field')} confidence is below reviewed seed threshold")
                if str(source.get("review_status", "")).lower() not in {"reviewed public filing seed", "approved", "locked"}:
                    errors.append(f"{field.get('field_id', 'field')} is not reviewed as public filing seed")
    return errors


def _context_has_unapproved_values(context: dict[str, Any]) -> bool:
    text = json.dumps(context).lower()
    blocked_markers = ("sample not approved", "missing verified source", "add verified", "placeholder")
    return any(marker in text for marker in blocked_markers)


def _collect_key_values(value: Any, key: str) -> list[Any]:
    matches: list[Any] = []
    if isinstance(value, dict):
        for item_key, item_value in value.items():
            if item_key == key:
                matches.append(item_value)
            matches.extend(_collect_key_values(item_value, key))
    elif isinstance(value, list):
        for item in value:
            matches.extend(_collect_key_values(item, key))
    return matches


def _iter_financial_fields(company: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for year in company.get("financial_history", []):
        for value in year.values():
            if isinstance(value, dict) and "field_id" in value and "source" in value:
                fields.append(value)
    return fields


def _document_file_metadata(file_path: str | None) -> dict[str, Any]:
    if not file_path:
        return {}
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return {}
    hasher = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return {"sha256": hasher.hexdigest(), "file_size_bytes": path.stat().st_size}


def _slug(text: str) -> str:
    return "".join(char.lower() if char.isalnum() else "_" for char in text).strip("_")
