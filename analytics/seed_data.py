"""
Sample data seeding script - Pre-populate database with realistic events
"""
import sqlite3
from datetime import datetime, timedelta
import json
import os

DB_PATH = os.path.expanduser("~/.aeon/terminal.db")

SAMPLE_EVENTS = [
    {
        "title": "Apple Q4 Earnings Report",
        "category": "earnings",
        "event_date": (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d"),
        "source": "demo",
        "raw_text": "Apple Inc. scheduled to report Q4 earnings. Analysts expect EPS of $1.54 on revenue of $89.5B. iPhone 15 sales and Services growth key metrics.",
        "affected_assets": ["AAPL", "AAPL"],
    },
    {
        "title": "Federal Reserve FOMC Meeting - Interest Rate Decision",
        "category": "macro",
        "event_date": (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"),
        "source": "demo",
        "raw_text": "Federal Reserve FOMC meeting scheduled. Market expects 25bps rate cut to 4.50-4.75%. Powell press conference at 2:30 PM ET. Major impact on SPY, QQQ, bonds.",
        "affected_assets": ["SPY", "QQQ", "TLT", "GLD"],
    },
    {
        "title": "NVIDIA GTC Conference - New AI Chip Announcement",
        "category": "product",
        "event_date": (datetime.now() + timedelta(days=14)).strftime("%Y-%m-%d"),
        "source": "demo",
        "raw_text": "NVIDIA GTC conference featuring Jensen Huang keynote. Expected to unveil next-gen Blackwell AI chips. Competitive response to AMD MI300 series.",
        "affected_assets": ["NVDA", "AMD", "INTC", "SMCI"],
    },
    {
        "title": "US Non-Farm Payrolls (NFP) Report",
        "category": "macro",
        "event_date": (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d"),
        "source": "demo",
        "raw_text": "Bureau of Labor Statistics releases October employment data at 8:30 AM ET. Forecast: 180K jobs added, unemployment 3.8%. Critical for Fed policy outlook.",
        "affected_assets": ["SPY", "DXY", "GLD", "TLT"],
    },
    {
        "title": "Tesla Q3 Delivery Numbers",
        "category": "earnings",
        "event_date": (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d"),
        "source": "demo",
        "raw_text": "Tesla to report Q3 vehicle deliveries. Wall Street expects 435K deliveries vs 422K in Q2. Shanghai factory ramp and Cybertruck production key focus.",
        "affected_assets": ["TSLA", "RIVN", "LCID", "F"],
    },
    {
        "title": "CPI Inflation Data Release",
        "category": "macro",
        "event_date": (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d"),
        "source": "demo",
        "raw_text": "Consumer Price Index for October released at 8:30 AM ET. Expected: +3.3% YoY headline, +4.1% core. Higher than expected could delay Fed rate cuts.",
        "affected_assets": ["SPY", "TLT", "GLD", "UUP"],
    },
    {
        "title": "Microsoft AI Event - Copilot Enterprise Launch",
        "category": "product",
        "event_date": (datetime.now() + timedelta(days=12)).strftime("%Y-%m-%d"),
        "source": "demo",
        "raw_text": "Microsoft announces pricing and availability for Copilot Enterprise. $30/user/month. Competing directly with Google Workspace AI. Azure integration key differentiator.",
        "affected_assets": ["MSFT", "GOOGL", "CRM", "NOW"],
    },
    {
        "title": "China GDP Growth Report Q3",
        "category": "macro",
        "event_date": (datetime.now() + timedelta(days=8)).strftime("%Y-%m-%d"),
        "source": "demo",
        "raw_text": "China National Bureau of Statistics releases Q3 GDP. Expected: 4.8% YoY vs 6.3% in Q2. Property sector weakness and consumption data closely watched.",
        "affected_assets": ["FXI", "BABA", "KWEB", "ASHR"],
    },
    {
        "title": "Amazon Prime Day Sales Results",
        "category": "earnings",
        "event_date": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"),
        "source": "demo",
        "raw_text": "Amazon reports Prime Day sales figures. Expected to exceed $12.9B (Oct 2023 record). E-commerce strength indicator ahead of holiday season.",
        "affected_assets": ["AMZN", "WMT", "SHOP", "EBAY"],
    },
    {
        "title": "Oil Inventories Report - EIA",
        "category": "commodity",
        "event_date": (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%d"),
        "source": "demo",
        "raw_text": "Energy Information Administration weekly petroleum status report at 10:30 AM ET. Crude inventories expected -1.2M barrels. OPEC+ cuts still in effect.",
        "affected_assets": ["USO", "XLE", "XOM", "CVX"],
    },
]

def seed_database():
    """Populate database with sample events for demo purposes"""

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Clear existing demo data
    cursor.execute("DELETE FROM market_events WHERE source = 'demo'")

    # Insert sample events
    inserted = 0
    for event in SAMPLE_EVENTS:
        cursor.execute("""
            INSERT INTO market_events
            (title, category, event_date, source, raw_text, affected_assets, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            event["title"],
            event["category"],
            event["event_date"],
            event["source"],
            event["raw_text"],
            json.dumps(event["affected_assets"]),
            datetime.now().isoformat()
        ))
        inserted += 1

    conn.commit()
    conn.close()

    print(f"✓ Seeded {inserted} sample events into database")
    print(f"✓ Events range from D-1 to D-14")
    print(f"✓ Categories: earnings, macro, product, commodity")
    print(f"✓ Database: {DB_PATH}")

if __name__ == "__main__":
    seed_database()
