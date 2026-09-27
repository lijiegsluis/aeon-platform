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
import yfinance as yf

app = FastAPI(title="Aeon Nimbus Intelligence API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5175", "http://127.0.0.1:5175"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = os.path.expanduser("~/.aeon/intelligence.db")

# ─── Database Setup ──────────────────────────────────────────

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
                category TEXT NOT NULL,
                event_date TEXT NOT NULL,
                event_time TEXT,
                source TEXT NOT NULL,
                source_channel TEXT,
                raw_text TEXT,
                affected_assets TEXT,
                sentiment_score INTEGER,
                confidence_level REAL,
                phase TEXT,
                analysis_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS news_feed (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                channel TEXT,
                message TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                event_id INTEGER,
                processed BOOLEAN DEFAULT 0,
                FOREIGN KEY (event_id) REFERENCES events(id)
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

            CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date);
            CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
            CREATE INDEX IF NOT EXISTS idx_news_timestamp ON news_feed(timestamp);
        """)

init_db()

# ─── Request/Response Models ─────────────────────────────────

class CreateEventRequest(BaseModel):
    title: str
    category: str
    event_date: str
    event_time: Optional[str] = None
    source: str
    raw_text: Optional[str] = None
    affected_assets: Optional[List[str]] = None

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

    if timeframe == "30days":
        end_date = datetime.now() + timedelta(days=30)
    elif timeframe == "7days":
        end_date = datetime.now() + timedelta(days=7)
    elif timeframe == "90days":
        end_date = datetime.now() + timedelta(days=90)
    else:
        end_date = datetime.now() + timedelta(days=30)

    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM events
            WHERE date(event_date) BETWEEN date('now') AND date(?)
            ORDER BY event_date ASC
        """, (end_date.date().isoformat(),)).fetchall()

        events = []
        for row in rows:
            event = dict(row)
            event_date = datetime.fromisoformat(event['event_date'])
            days_until = (event_date.date() - datetime.now().date()).days

            # Calculate phase
            if days_until <= 0:
                phase = "live"
                phase_color = "#ef4444"
            elif days_until <= 2:
                phase = "danger"
                phase_color = "#ef4444"
            elif days_until <= 9:
                phase = "euforia"
                phase_color = "#f59e0b"
            elif days_until <= 20:
                phase = "accumulation"
                phase_color = "#10b981"
            else:
                phase = "pre-rumor"
                phase_color = "#3b82f6"

            event['days_until'] = days_until
            event['phase'] = phase
            event['phase_color'] = phase_color
            event['affected_assets'] = json.loads(event['affected_assets']) if event['affected_assets'] else []

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
        event_date = datetime.fromisoformat(event['event_date'])
        days_until = (event_date.date() - datetime.now().date()).days

        event['days_until'] = days_until
        event['affected_assets'] = json.loads(event['affected_assets']) if event['affected_assets'] else []

        # Get related news
        news_rows = conn.execute("""
            SELECT * FROM news_feed
            WHERE event_id = ?
            ORDER BY timestamp DESC
            LIMIT 10
        """, (event_id,)).fetchall()

        event['related_news'] = [dict(n) for n in news_rows]

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
            (title, category, event_date, event_time, source, raw_text, affected_assets, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            req.title,
            req.category,
            req.event_date,
            req.event_time,
            req.source,
            req.raw_text,
            json.dumps(req.affected_assets) if req.affected_assets else None,
            datetime.now().isoformat()
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
def add_news_item(source: str, channel: str, message: str):
    """Add news item to feed (called by Telegram monitor)"""

    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO news_feed (source, channel, message, timestamp, processed)
            VALUES (?, ?, ?, ?, 0)
        """, (source, channel, message, datetime.now().isoformat()))
        conn.commit()
        news_id = cursor.lastrowid

    return {"id": news_id, "status": "added"}

@app.get("/api/calendar")
def get_calendar(view: str = "month"):
    """Get calendar view of events"""

    if view == "week":
        end_date = datetime.now() + timedelta(days=7)
    elif view == "month":
        end_date = datetime.now() + timedelta(days=30)
    else:
        end_date = datetime.now() + timedelta(days=90)

    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM events
            WHERE date(event_date) BETWEEN date('now') AND date(?)
            ORDER BY event_date ASC
        """, (end_date.date().isoformat(),)).fetchall()

        # Group by date
        calendar = {}
        for row in rows:
            event = dict(row)
            date_key = event['event_date']
            if date_key not in calendar:
                calendar[date_key] = []

            event['affected_assets'] = json.loads(event['affected_assets']) if event['affected_assets'] else []
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
                WHERE date(event_date) BETWEEN date('now', ? || ' days') AND date('now', ? || ' days')
            """, (f"+{min_days}", f"+{max_days}")).fetchall()

            for event in events:
                event_dict = dict(event)
                event_date = datetime.fromisoformat(event_dict['event_date'])
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
            WHERE date(event_date) BETWEEN date('now') AND date('now', '+7 days')
        """).fetchone()['cnt']
        total_news = conn.execute("SELECT COUNT(*) as cnt FROM news_feed").fetchone()['cnt']
        unprocessed_news = conn.execute("""
            SELECT COUNT(*) as cnt FROM news_feed WHERE processed = 0
        """).fetchone()['cnt']

        return {
            "total_events": total_events,
            "upcoming_7days": upcoming_7d,
            "total_news_items": total_news,
            "unprocessed_news": unprocessed_news
        }

if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting Aeon Nimbus Intelligence API on port 8001")
    uvicorn.run(app, host="127.0.0.1", port=8001)
