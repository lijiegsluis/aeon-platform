# Screenshot Capture Guide

Complete this checklist to finalize the marketing package.

## Required Screenshots (10)

### 1. Dashboard Overview
**File:** `assets/screenshots/01_dashboard.png`
**Steps:**
1. Login to platform at http://localhost:5174
2. Ensure map shows all company pins
3. Zoom to show global coverage
4. Capture full browser window (1920x1080)

### 2. Company Details - Apple
**File:** `assets/screenshots/02_company_apple.png`
**Steps:**
1. Search "Apple" or "AAPL"
2. Click company card
3. Scroll to show financial table (Revenue, Net Income, EBITDA)
4. Ensure 5-year data visible

### 3. Historical Price Chart
**File:** `assets/screenshots/03_chart_history.png`
**Steps:**
1. Open AAPL details
2. Navigate to "Charts" tab
3. Select 1Y period
4. Show price chart with volume

### 4. Peer Comparison Chart
**File:** `assets/screenshots/04_chart_compare.png`
**Steps:**
1. Click "Compare Peers"
2. Select AAPL, MSFT, GOOGL
3. Show normalized performance chart (all starting at 100)
4. Include 1Y timeframe

### 5. Benchmarking Table
**File:** `assets/screenshots/05_benchmarking.png`
**Steps:**
1. Navigate to Benchmarking
2. Filter to Technology sector
3. Show table with profit margins, ROE
4. Highlight top performers

### 6. Watchlist View
**File:** `assets/screenshots/06_watchlist.png`
**Steps:**
1. Add 5-7 companies to watchlist
2. Open watchlist view
3. Show price changes, alerts column
4. Include "Add Alert" button visible

### 7. PDF Export Preview
**File:** `assets/screenshots/07_pdf_export.png`
**Steps:**
1. Open any company details
2. Click "Export" → "PDF"
3. Capture generated PDF in viewer
4. Show first page with company header

### 8. Mobile View - iPhone
**File:** `assets/screenshots/08_mobile_iphone.png`
**Steps:**
1. Open Chrome DevTools
2. Toggle device toolbar (Cmd+Shift+M)
3. Select iPhone 12 Pro
4. Capture responsive layout
5. Show search + company cards stacked vertically

### 9. News Feed
**File:** `assets/screenshots/09_news_feed.png`
**Steps:**
1. Navigate to News tab
2. Show company news with thumbnails
3. Ensure timestamps visible
4. Include at least 5 news items

### 10. Earnings Calendar
**File:** `assets/screenshots/10_earnings_calendar.png`
**Steps:**
1. Open Earnings Calendar
2. Show upcoming earnings dates
3. Include EPS estimates
4. Filter to next 30 days

---

## Screenshot Standards

### Resolution
- Desktop: 1920x1080 minimum
- Mobile: Device-specific (375x812 for iPhone 12 Pro)
- Retina: 2x resolution preferred

### Format
- PNG format (lossless)
- Optimize with ImageOptim or TinyPNG
- Max file size: 500KB per image

### Browser Setup
- Use Chrome with clean profile
- Hide bookmarks bar
- Use incognito mode (no extensions)
- Zoom: 100%

### Content Guidelines
- Use real data (not "Lorem ipsum")
- Show populated watchlists (not empty states)
- Include realistic market data
- Ensure timestamps are recent

### Annotations (Optional)
- Add arrows/callouts in post-processing
- Use brand colors (#667eea primary)
- Keep annotations minimal
- Export annotated versions separately

---

## Tools

### Screenshot Capture
- **Mac:** Cmd+Shift+4, Cmd+Shift+5
- **Chrome DevTools:** Cmd+Shift+P → "Capture screenshot"
- **Firefox:** Shift+F2 → "screenshot --fullpage"

### Editing
- **Figma:** For annotations and layouts
- **Photoshop:** Professional editing
- **Sketch:** Mac-specific design tool
- **GIMP:** Free alternative

### Optimization
- **ImageOptim** (Mac): Drag-and-drop compression
- **TinyPNG** (Web): https://tinypng.com
- **Squoosh** (Web): https://squoosh.app

---

## Post-Processing Checklist

- [ ] All 10 screenshots captured
- [ ] Filenames match convention (01-10)
- [ ] Images optimized (<500KB each)
- [ ] Sensitive data removed (if any)
- [ ] Consistent browser chrome (or cropped)
- [ ] Color-corrected for consistency
- [ ] Alt text written for accessibility
- [ ] Added to marketing materials

---

## Usage in Marketing Materials

### Landing Page
- Hero section: `01_dashboard.png`
- Features section: `03_chart_history.png`, `05_benchmarking.png`
- Mobile section: `08_mobile_iphone.png`

### Pricing Page
- No screenshots needed (feature lists instead)

### Demo Video
- Use all 10 as transitions or overlays

### Social Media
- Twitter/X: `01_dashboard.png`, `04_chart_compare.png`
- LinkedIn: `05_benchmarking.png`, `02_company_apple.png`
- Product Hunt: `01_dashboard.png` (main), others in gallery

### Press Kit
- Include all 10 in zip file
- Add high-res versions (2x)
- Provide usage guidelines

---

## Quick Capture Script

```bash
#!/bin/bash
# Save as: capture_screenshots.sh

echo "Starting screenshot capture process..."
echo ""
echo "1. Login to http://localhost:5174"
echo "2. Use Chrome DevTools for mobile views"
echo "3. Save each screenshot with correct filename"
echo ""
echo "Screenshots needed:"
echo "  01_dashboard.png - Dashboard overview"
echo "  02_company_apple.png - Apple company details"
echo "  03_chart_history.png - Historical price chart"
echo "  04_chart_compare.png - Peer comparison"
echo "  05_benchmarking.png - Benchmarking table"
echo "  06_watchlist.png - Watchlist view"
echo "  07_pdf_export.png - PDF export preview"
echo "  08_mobile_iphone.png - Mobile responsive"
echo "  09_news_feed.png - News integration"
echo "  10_earnings_calendar.png - Earnings calendar"
echo ""
echo "Save to: marketing/assets/screenshots/"
```

---

**Status:** Ready for capture  
**Deadline:** Before launch day  
**Owner:** Marketing team
