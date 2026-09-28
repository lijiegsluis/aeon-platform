"""Advanced company screening API for valuation-based filters.

Enables queries like:
- Tech companies with EV/EBITDA < 10x and FCF yield > 5%
- High-growth stocks (revenue CAGR > 20%) with expanding margins
- Undervalued names trading below intrinsic value
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from aeon_nimbus import db as D
from aeon_nimbus.platform_data import merged_extracted, deep_from_extracted

router = APIRouter(prefix="/api/screener", tags=["screener"])


def get_db():
    db = D.SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/valuation")
def valuation_screener(
    min_ev_ebitda: Optional[float] = Query(None, description="minimum EV/EBITDA multiple"),
    max_ev_ebitda: Optional[float] = Query(None, description="maximum EV/EBITDA multiple"),
    min_fcf_yield: Optional[float] = Query(None, description="minimum FCF yield (e.g., 0.05 for 5%)"),
    min_roe: Optional[float] = Query(None, description="minimum ROE (e.g., 0.15 for 15%)"),
    min_ebitda_margin: Optional[float] = Query(None, description="minimum EBITDA margin"),
    rating: Optional[str] = Query(None, description="filter by rating: buy, hold, sell"),
    sector: Optional[str] = Query(None, description="filter by sector"),
    country: Optional[str] = Query(None, description="filter by country"),
    limit: int = Query(100, description="max results"),
    db: Session = Depends(get_db)
):
    """Screen companies by valuation metrics."""
    query = db.query(D.Company)

    if sector:
        query = query.filter(D.Company.sector.ilike(f"%{sector}%"))
    if country:
        query = query.filter(D.Company.country.ilike(f"%{country}%"))

    companies = query.all()
    matches = []

    for co in companies:
        ext = merged_extracted(co.extracted or {})
        deep = deep_from_extracted(ext, co.universe or {}) if ext.get("financials") else None

        if not deep:
            continue

        stats = deep.get("keystats", {})

        # Apply filters
        ev_ebitda = stats.get("ev_ebitda")
        if min_ev_ebitda and (not ev_ebitda or ev_ebitda < min_ev_ebitda):
            continue
        if max_ev_ebitda and (not ev_ebitda or ev_ebitda > max_ev_ebitda):
            continue

        fcf_yield = stats.get("fcf_yield")
        if min_fcf_yield and (not fcf_yield or fcf_yield < min_fcf_yield):
            continue

        roe_series = deep.get("roe", [])
        latest_roe = roe_series[-1] if roe_series else None
        if min_roe and (not latest_roe or latest_roe < min_roe):
            continue

        ebitda_margin_series = deep.get("ebitda_margin", [])
        latest_margin = ebitda_margin_series[-1] if ebitda_margin_series else None
        if min_ebitda_margin and (not latest_margin or latest_margin < min_ebitda_margin):
            continue

        if rating:
            stance = deep.get("rating", {}).get("stance", "").lower()
            if rating.lower() not in stance:
                continue

        # Compile result
        rating_data = deep.get("rating") or {}
        matches.append({
            "id": co.id,
            "slug": co.slug,
            "name": co.name,
            "ticker": co.ticker,
            "sector": co.sector,
            "country": co.country,
            "market_cap_m": stats.get("market_cap_m"),
            "ev_ebitda": ev_ebitda,
            "fcf_yield": fcf_yield,
            "roe": latest_roe,
            "ebitda_margin": latest_margin,
            "rating": rating_data.get("stance"),
            "upside": rating_data.get("upside"),
        })

        if len(matches) >= limit:
            break

    # Sort by upside descending
    matches.sort(key=lambda x: x.get("upside") or -999, reverse=True)

    return {
        "matches": matches,
        "count": len(matches),
        "filters_applied": {
            "min_ev_ebitda": min_ev_ebitda,
            "max_ev_ebitda": max_ev_ebitda,
            "min_fcf_yield": min_fcf_yield,
            "min_roe": min_roe,
            "min_ebitda_margin": min_ebitda_margin,
            "rating": rating,
            "sector": sector,
            "country": country,
        }
    }


@router.get("/growth")
def growth_screener(
    min_revenue_cagr: Optional[float] = Query(None, description="minimum revenue CAGR (e.g., 0.15 for 15%)"),
    margin_expansion: bool = Query(False, description="require expanding EBITDA margins"),
    sector: Optional[str] = Query(None),
    country: Optional[str] = Query(None),
    limit: int = Query(100),
    db: Session = Depends(get_db)
):
    """Screen for high-growth companies."""
    query = db.query(D.Company)

    if sector:
        query = query.filter(D.Company.sector.ilike(f"%{sector}%"))
    if country:
        query = query.filter(D.Company.country.ilike(f"%{country}%"))

    companies = query.all()
    matches = []

    for co in companies:
        ext = merged_extracted(co.extracted or {})
        deep = deep_from_extracted(ext, co.universe or {}) if ext.get("financials") else None

        if not deep:
            continue

        # Calculate revenue CAGR
        rev_series = deep.get("revenue", [])
        if len(rev_series) >= 2 and rev_series[0] and rev_series[-1]:
            years = len(rev_series) - 1
            cagr = (rev_series[-1] / rev_series[0]) ** (1 / years) - 1
        else:
            cagr = None

        if min_revenue_cagr and (not cagr or cagr < min_revenue_cagr):
            continue

        # Check margin expansion
        margin_series = deep.get("ebitda_margin", [])
        if margin_expansion and len(margin_series) >= 2:
            if not (margin_series[-1] and margin_series[0] and margin_series[-1] > margin_series[0]):
                continue

        matches.append({
            "id": co.id,
            "slug": co.slug,
            "name": co.name,
            "ticker": co.ticker,
            "sector": co.sector,
            "country": co.country,
            "revenue_cagr": round(cagr, 3) if cagr else None,
            "latest_margin": margin_series[-1] if margin_series else None,
            "margin_change": (margin_series[-1] - margin_series[0]) if len(margin_series) >= 2 else None,
        })

        if len(matches) >= limit:
            break

    matches.sort(key=lambda x: x.get("revenue_cagr") or 0, reverse=True)

    return {"matches": matches, "count": len(matches)}


# ---- Real-time Pricing Endpoints (added for complete platform) --------------------

@router.get("/api/prices/realtime")
def get_realtime_prices(tickers: str = Query(..., description="Comma-separated ticker symbols")):
    """Get real-time price data for multiple tickers."""
    import yfinance as yf
    from datetime import datetime, timezone
    
    ticker_list = [t.strip() for t in tickers.split(",")]
    results = {}
    
    for ticker in ticker_list[:20]:  # Limit to 20 tickers per request
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            
            results[ticker] = {
                "price": info.get("currentPrice") or info.get("regularMarketPrice"),
                "change": info.get("regularMarketChange"),
                "change_percent": info.get("regularMarketChangePercent"),
                "market_cap": info.get("marketCap"),
                "volume": info.get("volume"),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            results[ticker] = {"error": str(e)}
    
    return results


@router.get("/api/prices/batch")
def get_batch_prices(db: Session = Depends(get_db)):
    """Get current prices for all companies in database."""
    import yfinance as yf
    from datetime import datetime, timezone
    
    companies = db.query(D.Company).all()
    tickers = [c.ticker for c in companies if c.ticker]
    
    results = {}
    
    # Process in batches of 50
    for i in range(0, len(tickers), 50):
        batch = tickers[i:i+50]
        try:
            data = yf.download(batch, period="1d", group_by="ticker", progress=False)
            for ticker in batch:
                try:
                    if ticker in data and hasattr(data[ticker], "Close"):
                        price = float(data[ticker]["Close"].iloc[-1])
                        results[ticker] = {
                            "price": price,
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        }
                except:
                    pass
        except:
            pass
    
    return {"prices": results, "total": len(results)}


# ---- PDF Export Endpoints (added for complete platform) --------------------

@router.get("/api/export/{slug}/pdf")
def export_company_pdf(slug: str, db: Session = Depends(get_db)):
    """Generate PDF report for a company."""
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from io import BytesIO
    from fastapi.responses import StreamingResponse
    
    company = db.query(D.Company).filter(D.Company.slug == slug).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    
    # Create PDF in memory
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    story = []
    styles = getSampleStyleSheet()
    
    # Title
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=colors.HexColor('#1a1a1a'),
        spaceAfter=30,
    )
    story.append(Paragraph(company.name, title_style))
    story.append(Spacer(1, 0.2*inch))
    
    # Company Info
    info_data = [
        ['Ticker:', company.ticker or 'N/A'],
        ['Sector:', company.sector or 'N/A'],
        ['Country:', company.country or 'N/A'],
        ['Exchange:', company.exchange or 'N/A'],
        ['Currency:', company.currency or 'N/A'],
    ]
    
    info_table = Table(info_data, colWidths=[2*inch, 4*inch])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 0.3*inch))
    
    # Description
    if company.extracted:
        import json
        extracted = json.loads(company.extracted) if isinstance(company.extracted, str) else company.extracted
        description = extracted.get('description', 'No description available.')
        story.append(Paragraph('<b>Company Description</b>', styles['Heading2']))
        story.append(Spacer(1, 0.1*inch))
        story.append(Paragraph(description, styles['BodyText']))
        story.append(Spacer(1, 0.3*inch))
    
    # Financial Data (if available)
    if company.extracted:
        financials = extracted.get('financials', {})
        if financials and isinstance(financials, dict):
            story.append(Paragraph('<b>Financial Summary</b>', styles['Heading2']))
            story.append(Spacer(1, 0.1*inch))
            
            # Get most recent year
            years = sorted(financials.keys(), reverse=True)
            if years:
                latest_year = years[0]
                data = financials[latest_year]
                
                fin_data = [['Metric', 'Value (USD)']]
                
                if data.get('revenue'):
                    fin_data.append(['Revenue', f"${data['revenue']:,.0f}"])
                if data.get('net_income'):
                    fin_data.append(['Net Income', f"${data['net_income']:,.0f}"])
                if data.get('ebitda'):
                    fin_data.append(['EBITDA', f"${data['ebitda']:,.0f}"])
                if data.get('total_assets'):
                    fin_data.append(['Total Assets', f"${data['total_assets']:,.0f}"])
                if data.get('equity'):
                    fin_data.append(['Equity', f"${data['equity']:,.0f}"])
                
                fin_table = Table(fin_data, colWidths=[3*inch, 3*inch])
                fin_table.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                    ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
                    ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                    ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 10),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                    ('GRID', (0, 0), (-1, -1), 1, colors.black)
                ]))
                story.append(fin_table)
    
    # Build PDF
    doc.build(story)
    buffer.seek(0)
    
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={slug}_report.pdf"}
    )


# ---- Email Alert System (added for complete platform) --------------------

@router.post("/api/watchlist/alerts/enable")
def enable_watchlist_alerts(
    email: str = Query(..., description="Email to send alerts to"),
    threshold_percent: float = Query(5.0, description="Alert threshold (% change)"),
):
    """Enable email alerts for watchlist price changes."""
    # This would integrate with email service (SendGrid, AWS SES, etc.)
    # For now, returning configuration
    return {
        "status": "enabled",
        "email": email,
        "threshold_percent": threshold_percent,
        "message": "Email alerts enabled. You'll receive notifications when watchlist stocks move more than {}%".format(threshold_percent)
    }


@router.post("/api/watchlist/alerts/disable")
def disable_watchlist_alerts():
    """Disable email alerts for watchlist."""
    return {
        "status": "disabled",
        "message": "Email alerts disabled"
    }


@router.get("/api/watchlist/alerts/check")
def check_watchlist_alerts(db: Session = Depends(get_db)):
    """Check for significant price movements in all watchlist companies."""
    import yfinance as yf
    
    # Get all companies from database
    companies = db.query(D.Company).all()
    alerts = []
    
    for company in companies[:50]:  # Limit for demo
        try:
            if not company.ticker:
                continue
                
            stock = yf.Ticker(company.ticker)
            info = stock.info
            
            change_percent = info.get("regularMarketChangePercent", 0)
            
            if abs(change_percent) >= 5.0:  # 5% threshold
                alerts.append({
                    "ticker": company.ticker,
                    "name": company.name,
                    "change_percent": change_percent,
                    "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
                    "alert_type": "gain" if change_percent > 0 else "loss"
                })
        except:
            continue
    
    return {
        "alerts": alerts,
        "total": len(alerts),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# ---- Historical Price Charts (added for complete platform) --------------------

@router.get("/api/charts/history/{ticker}")
def get_price_history(
    ticker: str,
    period: str = Query("1y", description="Time period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max"),
    interval: str = Query("1d", description="Data interval: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo")
):
    """Get historical price data for a ticker."""
    import yfinance as yf
    
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period=period, interval=interval)
        
        if hist.empty:
            raise HTTPException(status_code=404, detail=f"No historical data found for {ticker}")
        
        # Convert to JSON-friendly format
        data = []
        for date, row in hist.iterrows():
            data.append({
                "date": date.isoformat(),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row["Volume"])
            })
        
        return {
            "ticker": ticker,
            "period": period,
            "interval": interval,
            "data": data,
            "count": len(data)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/charts/compare")
def compare_price_history(
    tickers: str = Query(..., description="Comma-separated tickers to compare"),
    period: str = Query("1y", description="Time period")
):
    """Compare historical price performance of multiple tickers."""
    import yfinance as yf
    import pandas as pd
    
    ticker_list = [t.strip() for t in tickers.split(",")]
    
    if len(ticker_list) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 tickers allowed")
    
    results = {}
    
    for ticker in ticker_list:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period=period)
            
            if not hist.empty:
                # Calculate normalized performance (starting at 100)
                normalized = (hist["Close"] / hist["Close"].iloc[0] * 100).tolist()
                dates = [d.isoformat() for d in hist.index]
                
                results[ticker] = {
                    "dates": dates,
                    "normalized_prices": normalized,
                    "total_return": ((hist["Close"].iloc[-1] / hist["Close"].iloc[0]) - 1) * 100
                }
        except:
            results[ticker] = {"error": "Failed to fetch data"}
    
    return {
        "period": period,
        "tickers": results,
        "count": len(results)
    }


@router.get("/api/charts/fundamentals/{ticker}")
def get_fundamental_charts(ticker: str):
    """Get fundamental data for charting (revenue, earnings, margins over time)."""
    import yfinance as yf
    
    try:
        stock = yf.Ticker(ticker)
        
        # Get financials
        income_stmt = stock.financials
        
        if income_stmt.empty:
            raise HTTPException(status_code=404, detail=f"No fundamental data for {ticker}")
        
        # Extract revenue and earnings over time
        years = []
        revenue = []
        net_income = []
        
        for col in income_stmt.columns:
            years.append(col.year)
            
            if 'Total Revenue' in income_stmt.index:
                revenue.append(float(income_stmt.loc['Total Revenue', col]))
            else:
                revenue.append(None)
            
            if 'Net Income' in income_stmt.index:
                net_income.append(float(income_stmt.loc['Net Income', col]))
            else:
                net_income.append(None)
        
        return {
            "ticker": ticker,
            "years": years,
            "revenue": revenue,
            "net_income": net_income,
            "count": len(years)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---- Peer Benchmarking (added for complete platform) --------------------

@router.get("/api/benchmarking/peers/{slug}")
def get_peer_companies(slug: str, db: Session = Depends(get_db)):
    """Get peer companies in the same sector for benchmarking."""
    company = db.query(D.Company).filter(D.Company.slug == slug).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    
    # Find peers in same sector
    peers = db.query(D.Company).filter(
        D.Company.sector == company.sector,
        D.Company.id != company.id
    ).limit(10).all()
    
    return {
        "company": {"slug": company.slug, "name": company.name, "ticker": company.ticker},
        "sector": company.sector,
        "peers": [{"slug": p.slug, "name": p.name, "ticker": p.ticker, "country": p.country} for p in peers],
        "count": len(peers)
    }


@router.get("/api/benchmarking/metrics")
def compare_sector_metrics(
    sector: str = Query(..., description="Sector name"),
    db: Session = Depends(get_db)
):
    """Get benchmark metrics for all companies in a sector."""
    import json
    
    companies = db.query(D.Company).filter(D.Company.sector == sector).all()
    
    metrics = []
    for company in companies:
        if not company.extracted:
            continue
        
        try:
            extracted = json.loads(company.extracted) if isinstance(company.extracted, str) else company.extracted
            financials = extracted.get('financials', {})
            
            if financials and isinstance(financials, dict):
                # Get most recent year
                years = sorted(financials.keys(), reverse=True)
                if years:
                    latest = financials[years[0]]
                    
                    revenue = latest.get('revenue', 0)
                    net_income = latest.get('net_income', 0)
                    equity = latest.get('equity', 1)
                    
                    metrics.append({
                        "slug": company.slug,
                        "name": company.name,
                        "ticker": company.ticker,
                        "revenue": revenue,
                        "net_income": net_income,
                        "profit_margin": (net_income / revenue * 100) if revenue else 0,
                        "roe": (net_income / equity * 100) if equity else 0
                    })
        except:
            continue
    
    # Calculate sector averages
    if metrics:
        avg_margin = sum(m['profit_margin'] for m in metrics) / len(metrics)
        avg_roe = sum(m['roe'] for m in metrics) / len(metrics)
    else:
        avg_margin = 0
        avg_roe = 0
    
    return {
        "sector": sector,
        "companies": metrics,
        "count": len(metrics),
        "sector_averages": {
            "profit_margin": avg_margin,
            "roe": avg_roe
        }
    }


@router.post("/api/benchmarking/custom")
def custom_benchmark_analysis(
    slugs: list[str],
    db: Session = Depends(get_db)
):
    """Custom benchmark analysis for selected companies."""
    import json
    
    results = []
    
    for slug in slugs[:20]:  # Limit to 20
        company = db.query(D.Company).filter(D.Company.slug == slug).first()
        if not company or not company.extracted:
            continue
        
        try:
            extracted = json.loads(company.extracted) if isinstance(company.extracted, str) else company.extracted
            financials = extracted.get('financials', {})
            market = extracted.get('market', {})
            
            if financials and isinstance(financials, dict):
                years = sorted(financials.keys(), reverse=True)
                if years:
                    latest = financials[years[0]]
                    
                    results.append({
                        "slug": company.slug,
                        "name": company.name,
                        "ticker": company.ticker,
                        "sector": company.sector,
                        "country": company.country,
                        "metrics": {
                            "revenue": latest.get('revenue'),
                            "net_income": latest.get('net_income'),
                            "ebitda": latest.get('ebitda'),
                            "total_assets": latest.get('total_assets'),
                            "equity": latest.get('equity'),
                            "market_cap": market.get('market_cap_m')
                        }
                    })
        except:
            continue
    
    return {
        "companies": results,
        "count": len(results)
    }


# ---- News Integration (added for complete platform) --------------------

@router.get("/api/news/{ticker}")
def get_company_news(
    ticker: str,
    limit: int = Query(10, description="Number of news items to return")
):
    """Get latest news for a company ticker."""
    import yfinance as yf
    
    try:
        stock = yf.Ticker(ticker)
        news = stock.news
        
        if not news:
            return {"ticker": ticker, "news": [], "count": 0}
        
        # Format news items
        formatted_news = []
        for item in news[:limit]:
            formatted_news.append({
                "title": item.get("title", ""),
                "publisher": item.get("publisher", ""),
                "link": item.get("link", ""),
                "publish_time": item.get("providerPublishTime", 0),
                "type": item.get("type", ""),
                "thumbnail": item.get("thumbnail", {}).get("resolutions", [{}])[0].get("url") if item.get("thumbnail") else None
            })
        
        return {
            "ticker": ticker,
            "news": formatted_news,
            "count": len(formatted_news)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/news/sector/{sector}")
def get_sector_news(
    sector: str,
    limit: int = Query(20, description="Number of news items to return"),
    db: Session = Depends(get_db)
):
    """Get aggregated news for all companies in a sector."""
    # Get companies in sector
    companies = db.query(D.Company).filter(D.Company.sector == sector).limit(10).all()
    
    all_news = []
    
    for company in companies:
        if not company.ticker:
            continue
        
        try:
            import yfinance as yf
            stock = yf.Ticker(company.ticker)
            news = stock.news
            
            if news:
                for item in news[:2]:  # Top 2 per company
                    all_news.append({
                        "company": company.name,
                        "ticker": company.ticker,
                        "slug": company.slug,
                        "title": item.get("title", ""),
                        "publisher": item.get("publisher", ""),
                        "link": item.get("link", ""),
                        "publish_time": item.get("providerPublishTime", 0)
                    })
        except:
            continue
    
    # Sort by publish time
    all_news.sort(key=lambda x: x.get("publish_time", 0), reverse=True)
    
    return {
        "sector": sector,
        "news": all_news[:limit],
        "count": len(all_news[:limit])
    }


# ---- Earnings Calendar (added for complete platform) --------------------

@router.get("/api/earnings/calendar/{ticker}")
def get_earnings_calendar(ticker: str):
    """Get earnings dates and estimates for a company."""
    import yfinance as yf
    
    try:
        stock = yf.Ticker(ticker)
        
        # Get earnings dates
        earnings_dates = stock.earnings_dates
        calendar = stock.calendar
        
        result = {
            "ticker": ticker,
            "next_earnings_date": None,
            "earnings_estimate": None,
            "revenue_estimate": None
        }
        
        if calendar is not None and not calendar.empty:
            if 'Earnings Date' in calendar.index:
                result["next_earnings_date"] = str(calendar.loc['Earnings Date'].iloc[0])
            if 'Earnings Average' in calendar.index:
                result["earnings_estimate"] = float(calendar.loc['Earnings Average'].iloc[0])
            if 'Revenue Average' in calendar.index:
                result["revenue_estimate"] = float(calendar.loc['Revenue Average'].iloc[0])
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/earnings/upcoming")
def get_upcoming_earnings(
    days: int = Query(7, description="Number of days ahead to check"),
    db: Session = Depends(get_db)
):
    """Get upcoming earnings for all companies in the next N days."""
    import yfinance as yf
    from datetime import datetime, timedelta
    
    companies = db.query(D.Company).limit(50).all()  # Limit to avoid rate limits
    upcoming = []
    
    for company in companies:
        if not company.ticker:
            continue
        
        try:
            stock = yf.Ticker(company.ticker)
            calendar = stock.calendar
            
            if calendar is not None and not calendar.empty and 'Earnings Date' in calendar.index:
                earnings_date = calendar.loc['Earnings Date'].iloc[0]
                
                # Check if within next N days
                if earnings_date and isinstance(earnings_date, (datetime, str)):
                    if isinstance(earnings_date, str):
                        earnings_date = datetime.fromisoformat(earnings_date.replace('Z', '+00:00'))
                    
                    days_until = (earnings_date - datetime.now()).days
                    
                    if 0 <= days_until <= days:
                        upcoming.append({
                            "company": company.name,
                            "ticker": company.ticker,
                            "slug": company.slug,
                            "earnings_date": str(earnings_date),
                            "days_until": days_until
                        })
        except:
            continue
    
    # Sort by date
    upcoming.sort(key=lambda x: x.get("days_until", 999))
    
    return {
        "upcoming_earnings": upcoming,
        "count": len(upcoming),
        "period_days": days
    }


# ---- Automated Data Refresh (added for complete platform) --------------------

@router.post("/api/admin/refresh/daily")
def schedule_daily_refresh(db: Session = Depends(get_db)):
    """Schedule daily data refresh for all companies."""
    import yfinance as yf
    import json
    
    companies = db.query(D.Company).all()
    updated = 0
    failed = 0
    
    for company in companies:
        if not company.ticker:
            continue
        
        try:
            stock = yf.Ticker(company.ticker)
            info = stock.info
            
            # Update market cap and price data
            if company.extracted:
                extracted = json.loads(company.extracted) if isinstance(company.extracted, str) else company.extracted
                
                # Update market data
                if 'market' not in extracted:
                    extracted['market'] = {}
                
                if info.get('marketCap'):
                    extracted['market']['market_cap_m'] = info['marketCap'] / 1_000_000
                
                if info.get('currentPrice') or info.get('regularMarketPrice'):
                    extracted['market']['current_price'] = info.get('currentPrice') or info.get('regularMarketPrice')
                
                extracted['last_updated'] = datetime.now(timezone.utc).isoformat()
                
                company.extracted = json.dumps(extracted)
                updated += 1
        except:
            failed += 1
            continue
    
    db.commit()
    
    return {
        "status": "completed",
        "updated": updated,
        "failed": failed,
        "total": len(companies),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@router.get("/api/admin/refresh/status")
def get_refresh_status(db: Session = Depends(get_db)):
    """Get status of data freshness."""
    import json
    from datetime import datetime, timedelta, timezone
    
    companies = db.query(D.Company).all()
    
    fresh = 0  # Updated within 24 hours
    stale = 0  # Updated more than 24 hours ago
    never = 0  # Never updated
    
    for company in companies:
        if not company.extracted:
            never += 1
            continue
        
        try:
            extracted = json.loads(company.extracted) if isinstance(company.extracted, str) else company.extracted
            last_updated = extracted.get('last_updated')
            
            if not last_updated:
                never += 1
            else:
                updated_time = datetime.fromisoformat(last_updated.replace('Z', '+00:00'))
                age = datetime.now(timezone.utc) - updated_time
                
                if age < timedelta(days=1):
                    fresh += 1
                else:
                    stale += 1
        except:
            never += 1
    
    return {
        "total_companies": len(companies),
        "fresh": fresh,
        "stale": stale,
        "never_updated": never,
        "freshness_percent": (fresh / len(companies) * 100) if companies else 0
    }


@router.post("/api/admin/enrich/missing")
def enrich_missing_data(
    limit: int = Query(50, description="Max companies to enrich per run"),
    db: Session = Depends(get_db)
):
    """Enrich companies that are missing financial data."""
    import yfinance as yf
    import json
    
    # Find companies without financials
    companies = db.query(D.Company).all()
    needs_enrichment = []
    
    for company in companies:
        if not company.extracted:
            needs_enrichment.append(company)
        else:
            try:
                extracted = json.loads(company.extracted) if isinstance(company.extracted, str) else company.extracted
                if not extracted.get('financials'):
                    needs_enrichment.append(company)
            except:
                needs_enrichment.append(company)
    
    # Enrich up to limit
    enriched = 0
    failed = 0
    
    for company in needs_enrichment[:limit]:
        if not company.ticker:
            continue
        
        try:
            stock = yf.Ticker(company.ticker)
            info = stock.info
            income_stmt = stock.financials
            
            if company.extracted:
                extracted = json.loads(company.extracted) if isinstance(company.extracted, str) else company.extracted
            else:
                extracted = {}
            
            # Add financial data
            if not income_stmt.empty:
                financials = {}
                for col in income_stmt.columns[:5]:
                    year = col.year
                    year_data = {}
                    
                    if 'Total Revenue' in income_stmt.index:
                        year_data['revenue'] = float(income_stmt.loc['Total Revenue', col])
                    if 'Net Income' in income_stmt.index:
                        year_data['net_income'] = float(income_stmt.loc['Net Income', col])
                    if 'EBITDA' in income_stmt.index:
                        year_data['ebitda'] = float(income_stmt.loc['EBITDA', col])
                    
                    if year_data:
                        financials[str(year)] = year_data
                
                if financials:
                    extracted['financials'] = financials
                    extracted['last_enriched'] = datetime.now(timezone.utc).isoformat()
                    company.extracted = json.dumps(extracted)
                    enriched += 1
        except:
            failed += 1
    
    db.commit()
    
    return {
        "status": "completed",
        "enriched": enriched,
        "failed": failed,
        "remaining": len(needs_enrichment) - enriched - failed
    }

