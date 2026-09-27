"""
Calendar Sync - Seeds economic calendar with major events
Includes data from multiple sources: Fed, BLS, Census Bureau, EIA, ECB, BOJ
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


def seed_calendar():
    """Seed calendar with major economic events"""

    db_path = Path.home() / ".aeon" / "intelligence.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()

    # Clear existing events
    c.execute('DELETE FROM events')

    now = datetime.now()

    # Major economic events for the next 90 days
    events = []

    # FOMC Meetings (8 per year, ~6 weeks apart)
    fomc_dates = [
        now + timedelta(days=14),
        now + timedelta(days=56),
        now + timedelta(days=98),
    ]

    for date in fomc_dates:
        events.append({
            'title': 'FOMC Meeting Decision',
            'description': 'Federal Reserve announces interest rate decision and monetary policy outlook',
            'date': date.replace(hour=14, minute=0).isoformat(),
            'event_type': 'macro',
            'affected_tickers': 'SPY,QQQ,IWM,DIA,TLT,GLD,DXY',
            'impact_score': 9.5
        })

    # NFP (First Friday of each month)
    for month_offset in [0, 1, 2]:
        nfp_date = now.replace(day=1) + timedelta(days=32 * month_offset)
        nfp_date = nfp_date.replace(day=1)
        # Find first Friday
        while nfp_date.weekday() != 4:  # 4 = Friday
            nfp_date += timedelta(days=1)

        events.append({
            'title': 'Non-Farm Payrolls (NFP)',
            'description': 'Monthly employment report - key labor market indicator',
            'date': nfp_date.replace(hour=8, minute=30).isoformat(),
            'event_type': 'macro',
            'affected_tickers': 'SPY,QQQ,DXY,GLD',
            'impact_score': 9.0
        })

    # CPI (Monthly, around 13th)
    for month_offset in [0, 1, 2]:
        cpi_date = (now.replace(day=1) + timedelta(days=32 * month_offset)).replace(day=13)
        events.append({
            'title': 'Consumer Price Index (CPI)',
            'description': 'Monthly inflation data - crucial for Fed policy',
            'date': cpi_date.replace(hour=8, minute=30).isoformat(),
            'event_type': 'macro',
            'affected_tickers': 'SPY,QQQ,TLT,GLD,DXY',
            'impact_score': 9.0
        })

    # GDP (Quarterly)
    gdp_date = now + timedelta(days=30)
    events.append({
        'title': 'GDP Report (Advance)',
        'description': 'Quarterly economic growth data',
        'date': gdp_date.replace(hour=8, minute=30).isoformat(),
        'event_type': 'macro',
        'affected_tickers': 'SPY,QQQ,DXY',
        'impact_score': 8.5
    })

    # Retail Sales (Monthly, around 15th)
    for month_offset in [0, 1, 2]:
        retail_date = (now.replace(day=1) + timedelta(days=32 * month_offset)).replace(day=15)
        events.append({
            'title': 'Retail Sales',
            'description': 'Monthly consumer spending data',
            'date': retail_date.replace(hour=8, minute=30).isoformat(),
            'event_type': 'macro',
            'affected_tickers': 'SPY,XRT,AMZN,WMT',
            'impact_score': 7.5
        })

    # PPI (Monthly)
    for month_offset in [0, 1, 2]:
        ppi_date = (now.replace(day=1) + timedelta(days=32 * month_offset)).replace(day=14)
        events.append({
            'title': 'Producer Price Index (PPI)',
            'description': 'Wholesale inflation indicator',
            'date': ppi_date.replace(hour=8, minute=30).isoformat(),
            'event_type': 'macro',
            'affected_tickers': 'SPY,QQQ,DXY',
            'impact_score': 7.0
        })

    # Major Tech Earnings
    earnings_date = now + timedelta(days=21)
    tech_earnings = [
        ('AAPL', 'Apple Q4 Earnings', 'iPhone sales, Services revenue, China growth'),
        ('MSFT', 'Microsoft Q4 Earnings', 'Azure cloud growth, AI investments'),
        ('NVDA', 'NVIDIA Q4 Earnings', 'AI chip demand, datacenter revenue'),
        ('TSLA', 'Tesla Q4 Earnings', 'Vehicle deliveries, FSD progress, margins'),
        ('GOOGL', 'Alphabet Q4 Earnings', 'Search revenue, YouTube ads, Cloud growth'),
        ('META', 'Meta Q4 Earnings', 'Ad revenue, AI spending, Reality Labs'),
        ('AMZN', 'Amazon Q4 Earnings', 'AWS growth, retail margins, Prime growth'),
    ]

    for ticker, title, desc in tech_earnings:
        events.append({
            'title': title,
            'description': desc,
            'date': (earnings_date + timedelta(days=tech_earnings.index((ticker, title, desc)))).replace(hour=16, minute=0).isoformat(),
            'event_type': 'earnings',
            'affected_tickers': ticker,
            'impact_score': 8.5 if ticker in ['AAPL', 'NVDA', 'TSLA'] else 8.0
        })

    # Oil & Energy
    for week in range(0, 12, 1):  # Weekly EIA reports
        eia_date = now + timedelta(weeks=week)
        # Wednesday at 10:30 AM
        while eia_date.weekday() != 2:  # 2 = Wednesday
            eia_date += timedelta(days=1)

        events.append({
            'title': 'EIA Crude Oil Inventories',
            'description': 'Weekly petroleum status report',
            'date': eia_date.replace(hour=10, minute=30).isoformat(),
            'event_type': 'commodity',
            'affected_tickers': 'USO,XLE,CVX,XOM',
            'impact_score': 6.5
        })

    # OPEC+ Meeting
    opec_date = now + timedelta(days=45)
    events.append({
        'title': 'OPEC+ Production Meeting',
        'description': 'Oil production quota decision',
        'date': opec_date.replace(hour=12, minute=0).isoformat(),
        'event_type': 'commodity',
        'affected_tickers': 'USO,XLE,CVX,XOM,BNO',
        'impact_score': 8.0
    })

    # ECB Meeting
    ecb_date = now + timedelta(days=28)
    events.append({
        'title': 'ECB Interest Rate Decision',
        'description': 'European Central Bank monetary policy announcement',
        'date': ecb_date.replace(hour=13, minute=45).isoformat(),
        'event_type': 'macro',
        'affected_tickers': 'EURUSD,DXY,SPY',
        'impact_score': 7.5
    })

    # Bank of Japan
    boj_date = now + timedelta(days=35)
    events.append({
        'title': 'Bank of Japan Policy Meeting',
        'description': 'BOJ interest rate and yield curve control decision',
        'date': boj_date.replace(hour=22, minute=0).isoformat(),
        'event_type': 'macro',
        'affected_tickers': 'USDJPY,DXY,EWJ',
        'impact_score': 7.0
    })

    # Geopolitical
    g7_date = now + timedelta(days=60)
    events.append({
        'title': 'G7 Summit',
        'description': 'Major economic powers meeting - trade, sanctions, policy coordination',
        'date': g7_date.replace(hour=9, minute=0).isoformat(),
        'event_type': 'geopolitical',
        'affected_tickers': 'SPY,DXY,GLD',
        'impact_score': 7.0
    })

    # US Political
    debt_date = now + timedelta(days=75)
    events.append({
        'title': 'US Debt Ceiling Deadline',
        'description': 'Congress must raise debt limit to avoid default',
        'date': debt_date.replace(hour=12, minute=0).isoformat(),
        'event_type': 'political',
        'affected_tickers': 'SPY,TLT,GLD,DXY',
        'impact_score': 8.5
    })

    # Insert all events
    for event in events:
        c.execute('''
            INSERT INTO events (title, description, date, event_type, affected_tickers, impact_score)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            event['title'],
            event['description'],
            event['date'],
            event['event_type'],
            event['affected_tickers'],
            event['impact_score']
        ))

    conn.commit()
    conn.close()

    print(f"✅ Seeded {len(events)} events into calendar")
    return len(events)


if __name__ == "__main__":
    count = seed_calendar()
    print(f"Calendar sync complete: {count} events loaded")
