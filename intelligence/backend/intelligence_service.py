"""
Aeon Nimbus Intelligence - Dedicated Market Event Monitoring Service
Port: 8001
Purpose: Real-time event aggregation, countdown tracking, sentiment analysis
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import List, Optional
from contextlib import contextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Aeon Nimbus Intelligence API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5175", "http://127.0.0.1:5175"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = os.path.expanduser("~/.aeon/intelligence.db")

# ─── Database Setup ──────────────────────────────────────────
# NOTE: the `events` and `news_feed` schemas below match what's actually on
# disk (created long ago by calendar_sync.py's INSERT shape / an older
# service variant) — not the richer schema this file used to declare
# (event_date/category/affected_assets/source/...), which never matched the
# real table and made every query here raise "no such column" at request
# time. `economic_calendar` and `alert_rules` were already consistent.

@contextmanager
def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                date TEXT NOT NULL,
                event_type TEXT,
                phase TEXT,
                affected_tickers TEXT,
                recommendation TEXT,
                impact_score REAL
            );

            CREATE TABLE IF NOT EXISTS news_feed (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT,
                source TEXT,
                url TEXT,
                timestamp TEXT NOT NULL,
                affected_tickers TEXT,
                sentiment TEXT
            );

            CREATE TABLE IF NOT EXISTS economic_calendar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                country TEXT,
                event_date TEXT NOT NULL,
                event_time TEXT,
                previous_value TEXT,
                forecast_value TEXT,
                actual_value TEXT,
                importance INTEGER,
                affected_currencies TEXT,
                synced_at TEXT
            );

            CREATE TABLE IF NOT EXISTS alert_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER DEFAULT 1,
                event_category TEXT,
                min_days_before INTEGER,
                max_days_before INTEGER,
                assets_filter TEXT,
                notification_channels TEXT,
                is_active BOOLEAN DEFAULT 1,
                created_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
            CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
            CREATE INDEX IF NOT EXISTS idx_news_timestamp ON news_feed(timestamp);
        """)

init_db()

def _tickers(raw: Optional[str]) -> List[str]:
    """affected_tickers is stored as a plain comma-joined string, not JSON."""
    return [t.strip() for t in raw.split(",") if t.strip()] if raw else []

def _phase_for(days_until: int):
    if days_until <= 0:
        return "live", "#ef4444"
    if days_until <= 2:
        return "danger", "#ef4444"
    if days_until <= 9:
        return "euforia", "#f59e0b"
    if days_until <= 20:
        return "accumulation", "#10b981"
    return "pre-rumor", "#3b82f6"

# ─── Request/Response Models ─────────────────────────────────

class CreateEventRequest(BaseModel):
    title: str
    event_type: str
    date: str
    description: Optional[str] = None
    affected_tickers: Optional[List[str]] = None
    impact_score: Optional[float] = None

class AlertRuleRequest(BaseModel):
    event_category: Optional[str] = None
    min_days_before: int
    max_days_before: int
    assets_filter: Optional[List[str]] = None
    notification_channels: List[str] = ["push"]

# ─── Core API Endpoints ──────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "healthy", "service": "aeon-intelligence", "version": "1.0.0"}

@app.get("/api/events/live")
def get_live_events(timeframe: str = "30days"):
    """Get all events within timeframe with D-X countdown"""

    days = {"7days": 7, "30days": 30, "90days": 90}.get(timeframe, 30)
    end_date = datetime.now() + timedelta(days=days)

    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM events
            WHERE date(date) BETWEEN date('now') AND date(?)
            ORDER BY date ASC
        """, (end_date.date().isoformat(),)).fetchall()

        events = []
        for row in rows:
            event = dict(row)
            event_date = datetime.fromisoformat(event['date'])
            days_until = (event_date.date() - datetime.now().date()).days
            phase, phase_color = _phase_for(days_until)

            event['days_until'] = days_until
            event['phase'] = phase
            event['phase_color'] = phase_color
            event['affected_tickers'] = _tickers(event['affected_tickers'])

            events.append(event)

    return {
        "timeframe": timeframe,
        "total_events": len(events),
        "events": events,
        "phases": {
            "danger": len([e for e in events if e['phase'] == 'danger']),
            "euforia": len([e for e in events if e['phase'] == 'euforia']),
            "accumulation": len([e for e in events if e['phase'] == 'accumulation']),
            "pre_rumor": len([e for e in events if e['phase'] == 'pre-rumor'])
        }
    }

@app.get("/api/events/{event_id}")
def get_event_detail(event_id: int):
    """Get detailed analysis of single event"""

    with get_db() as conn:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Event not found")

        event = dict(row)
        event_date = datetime.fromisoformat(event['date'])
        days_until = (event_date.date() - datetime.now().date()).days

        event['days_until'] = days_until
        tickers = _tickers(event['affected_tickers'])
        event['affected_tickers'] = tickers

        # Related news: no FK between events and news_feed in the real schema,
        # so match on ticker overlap instead.
        related_news = []
        if tickers:
            like_clauses = " OR ".join(["affected_tickers LIKE ?"] * len(tickers))
            news_rows = conn.execute(f"""
                SELECT * FROM news_feed
                WHERE {like_clauses}
                ORDER BY timestamp DESC
                LIMIT 10
            """, tuple(f"%{t}%" for t in tickers)).fetchall()
            related_news = [dict(n) for n in news_rows]

        event['related_news'] = related_news

        # Calculate phase progress
        if days_until <= 0:
            phase_progress = 100
        elif days_until <= 2:
            phase_progress = 90 + (days_until * 5)
        elif days_until <= 9:
            phase_progress = 50 + ((9 - days_until) * 5)
        elif days_until <= 20:
            phase_progress = 20 + ((20 - days_until) * 3)
        else:
            phase_progress = max(0, 20 - (days_until - 20))

        event['phase_progress'] = phase_progress

        return event

@app.post("/api/events")
def create_event(req: CreateEventRequest):
    """Create new event (manual or from data source)"""

    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO events
            (title, description, date, event_type, affected_tickers, impact_score)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            req.title,
            req.description,
            req.date,
            req.event_type,
            ",".join(req.affected_tickers) if req.affected_tickers else None,
            req.impact_score,
        ))
        conn.commit()
        event_id = cursor.lastrowid

    return {"id": event_id, "status": "created"}

@app.get("/api/news/feed")
def get_news_feed(limit: int = 50):
    """Get raw news feed from all sources"""

    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM news_feed
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,)).fetchall()

        return {"feed": [dict(r) for r in rows], "count": len(rows)}

@app.post("/api/news/feed")
def add_news_item(source: str, message: str, title: Optional[str] = None, url: Optional[str] = None):
    """Add news item to feed (called by Telegram monitor or a fetcher)"""

    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO news_feed (title, content, source, url, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (title or message[:80], message, source, url, datetime.now().isoformat()))
        conn.commit()
        news_id = cursor.lastrowid

    return {"id": news_id, "status": "added"}

@app.get("/api/calendar")
def get_calendar(view: str = "month"):
    """Get calendar view of events"""

    days = {"week": 7, "month": 30}.get(view, 90)
    end_date = datetime.now() + timedelta(days=days)

    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM events
            WHERE date(date) BETWEEN date('now') AND date(?)
            ORDER BY date ASC
        """, (end_date.date().isoformat(),)).fetchall()

        # Group by date
        calendar = {}
        for row in rows:
            event = dict(row)
            date_key = event['date']
            if date_key not in calendar:
                calendar[date_key] = []

            event['affected_tickers'] = _tickers(event['affected_tickers'])
            calendar[date_key].append(event)

    return {"view": view, "calendar": calendar}

@app.post("/api/alerts")
def create_alert_rule(req: AlertRuleRequest):
    """Create alert rule for event notifications"""

    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO alert_rules
            (event_category, min_days_before, max_days_before, assets_filter, notification_channels, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            req.event_category,
            req.min_days_before,
            req.max_days_before,
            json.dumps(req.assets_filter) if req.assets_filter else None,
            json.dumps(req.notification_channels),
            datetime.now().isoformat()
        ))
        conn.commit()
        alert_id = cursor.lastrowid

    return {"id": alert_id, "status": "created"}

@app.get("/api/alerts/triggered")
def get_triggered_alerts():
    """Get events matching active alert rules"""

    with get_db() as conn:
        # Get active rules
        rules = conn.execute("""
            SELECT * FROM alert_rules WHERE is_active = 1
        """).fetchall()

        triggered = []
        for rule in rules:
            rule_dict = dict(rule)
            min_days = rule_dict['min_days_before']
            max_days = rule_dict['max_days_before']

            # Find matching events
            events = conn.execute("""
                SELECT * FROM events
                WHERE date(date) BETWEEN date('now', ? || ' days') AND date('now', ? || ' days')
            """, (f"+{min_days}", f"+{max_days}")).fetchall()

            for event in events:
                event_dict = dict(event)
                event_date = datetime.fromisoformat(event_dict['date'])
                days_until = (event_date.date() - datetime.now().date()).days

                triggered.append({
                    "rule_id": rule_dict['id'],
                    "event": event_dict,
                    "days_until": days_until,
                    "message": f"{event_dict['title']} in {days_until} days"
                })

    return {"triggered": triggered, "count": len(triggered)}

@app.get("/api/stats")
def get_stats():
    """Get dashboard statistics"""

    with get_db() as conn:
        total_events = conn.execute("SELECT COUNT(*) as cnt FROM events").fetchone()['cnt']
        upcoming_7d = conn.execute("""
            SELECT COUNT(*) as cnt FROM events
            WHERE date(date) BETWEEN date('now') AND date('now', '+7 days')
        """).fetchone()['cnt']
        total_news = conn.execute("SELECT COUNT(*) as cnt FROM news_feed").fetchone()['cnt']

        return {
            "total_events": total_events,
            "upcoming_7days": upcoming_7d,
            "total_news_items": total_news,
        }

if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting Aeon Nimbus Intelligence API on port 8001")
    uvicorn.run(app, host="127.0.0.1", port=8001)
