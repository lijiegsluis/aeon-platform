# Getting Started with Aeon Nimbus

Welcome to Aeon Nimbus! This guide will help you get up and running in minutes.

---

## Quick Start

### 1. Access the Platform

Visit: **http://localhost:5174** (or your deployed URL)

**Demo Credentials:**
- Email: `admin@aeon.local`
- Password: `admin`

### 2. Explore the Dashboard

After login, you'll see:
- **Interactive Map:** Global view of all 319 companies
- **Search Bar:** Find companies by name or ticker
- **Filters:** Narrow by sector, country, or market cap

### 3. Search for a Company

Try searching for "Apple" or "AAPL":
1. Type in the search bar at the top
2. Click the company card
3. View comprehensive financial data

---

## Key Features

### 📊 Financial Data
- **Revenue, Net Income, EBITDA** for 5+ years
- **Market Cap** updated in real-time (Professional plan)
- **Employee count, sector, exchange** metadata

### 🗺️ Map Visualization
- Click pins to view company details
- Zoom to explore regional markets
- Color-coded by sector

### 📈 Charts & Analytics
- Historical price charts (1 day to 10 years)
- Compare up to 10 stocks side-by-side
- Revenue and earnings trends

### 🔔 Watchlist & Alerts
- Add companies to your personal watchlist
- Set price movement alerts (5%, 10%, custom)
- Email notifications (Professional plan)

### 📄 Export Options
- **PDF:** Professional reports with charts
- **CSV:** Raw data for Excel/analysis
- **JSON:** API access for developers

### 🏆 Peer Benchmarking
- Compare companies in the same sector
- Profit margins, ROE, and custom metrics
- Industry averages and outliers

### 📰 News & Events
- Latest news for each company
- Sector-wide news aggregation
- Earnings calendar with estimates

---

## Common Tasks

### Add a Company to Your Watchlist

1. Search for the company
2. Click the **"Add to Watchlist"** button
3. Access your watchlist from the top navigation

### Set Up Price Alerts

1. Go to your watchlist
2. Click **"Set Alert"** on any company
3. Choose threshold (5%, 10%, or custom)
4. Confirm your email for notifications

### Compare Multiple Companies

1. Select first company
2. Click **"Compare"** button
3. Add up to 9 more companies
4. View side-by-side metrics

### Export a Company Report

1. View company details
2. Click **"Export"** dropdown
3. Choose format:
   - **PDF** for presentations
   - **CSV** for spreadsheet analysis
   - **JSON** for developers

### Use Historical Charts

1. Open company details
2. Navigate to **"Charts"** tab
3. Select time period (1D, 1M, 1Y, 5Y, etc.)
4. Toggle between price, volume, fundamentals

---

## Navigation Guide

### Top Navigation Bar
- **Home:** Return to dashboard
- **Watchlist:** Your tracked companies
- **Screener:** Advanced filtering tools
- **API Docs:** Developer documentation

### Company Detail View
- **Overview:** Summary and key metrics
- **Financials:** Multi-year data tables
- **Charts:** Price and fundamental visualizations
- **News:** Latest articles and announcements
- **Benchmarking:** Peer comparisons

### Search & Filters
- **Text Search:** Company name or ticker
- **Sector Filter:** Technology, Financials, Healthcare, etc.
- **Country Filter:** US, China, UK, Germany, etc.
- **Market Cap:** Small, Mid, Large cap

---

## Tips & Tricks

### 🎯 Power User Features

**Keyboard Shortcuts:**
- `/` - Focus search bar
- `Esc` - Close modals
- `Ctrl+K` - Quick command palette (coming soon)

**Advanced Search:**
- Use ticker symbols for exact matches
- Filter by sector before searching
- Sort results by market cap or revenue

**Bulk Analysis:**
- Export entire sector to CSV
- Use API for programmatic access
- Create custom watchlists by theme (e.g., "African Tech")

### 📱 Mobile Access

The platform is fully responsive:
- All features work on mobile
- Touch-friendly interface
- Optimized charts for small screens

### 🔗 API Access (Professional+)

Generate your API key:
1. Go to **Settings** → **API Keys**
2. Click **"Generate New Key"**
3. Copy and store securely

Example API call:
```bash
curl -H "Authorization: Bearer YOUR_KEY" \
  https://api.aeon-nimbus.com/api/export/aapl/json
```

---

## Data Coverage

### Companies: 319
- United States: 178
- China: 20
- United Kingdom: 19
- Germany: 12
- France: 12
- South Africa: 11
- And 19 more countries...

### Sectors Covered
- Technology (40 companies)
- Financials (40 companies)
- Consumer Cyclical (31 companies)
- Healthcare (31 companies)
- Telecommunications (22 companies)
- And 10+ more sectors

### Financial Coverage: 97.3%
- 252 companies with complete financials
- 5-year historical data
- Quarterly updates

---

## Upgrade Your Plan

### Free (Explorer)
- Access all 319 companies
- Basic financial data
- 5 watchlist companies
- CSV export

### Professional ($29/month)
- Real-time pricing
- Unlimited watchlist
- Email alerts
- PDF exports
- Historical charts
- News integration

### Enterprise ($199/month)
- API access (1000 calls/day)
- Team collaboration
- Custom integrations
- Priority support

[View Full Pricing →](pricing.html)

---

## Need Help?

### Documentation
- **API Docs:** http://localhost:5174/docs
- **User Guide:** This document
- **Video Tutorials:** Coming soon

### Support
- **Email:** support@aeon-nimbus.com
- **Live Chat:** Available on Professional+ plans
- **Response Time:** 24 hours (free), 2 hours (paid)

### Community
- **GitHub:** Report issues and feature requests
- **Twitter:** @AeonNimbus for updates
- **Blog:** Latest features and market insights

---

## What's Next?

1. ✅ Explore the dashboard and map
2. ✅ Search for 3-5 companies you follow
3. ✅ Add them to your watchlist
4. ✅ Set up a price alert
5. ✅ Export your first PDF report
6. ✅ Try peer benchmarking
7. ✅ Check the news feed

---

**Ready to dive deeper?** Check out our [Demo Video](demo/DEMO_SCRIPT.md) or explore the [API Documentation](http://localhost:5174/docs).

**Questions?** Contact us at demo@aeon-nimbus.com

---

*Last Updated: September 26, 2026*
