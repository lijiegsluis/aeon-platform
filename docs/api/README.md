# Aeon Nimbus - API Integration Guide

Complete guide for integrating the Aeon Nimbus API into external applications.

---

## Base URL

**Local Development:**
```
http://localhost:5174/api
```

**Production:**
```
https://api.aeon-nimbus.com/api
```

---

## Authentication

### API Key (Professional & Enterprise Plans)

**Header:**
```
Authorization: Bearer YOUR_API_KEY
```

**Example:**
```bash
curl -H "Authorization: Bearer sk_live_abc123..." \
  https://api.aeon-nimbus.com/api/export/aapl/json
```

### Session Token (Web App)

Automatically handled by browser cookies after login.

---

## Rate Limits

| Plan | Requests/Day | Requests/Hour | Burst |
|------|--------------|---------------|-------|
| Explorer (Free) | 100 | 10 | 5 |
| Professional | 1,000 | 100 | 20 |
| Enterprise | 10,000 | 1,000 | 50 |

**Rate Limit Headers:**
```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 995
X-RateLimit-Reset: 1640995200
```

---

## Endpoints

### 1. Company Data

#### Get All Companies
```http
GET /api/export/all/json
```

**Response:**
```json
{
  "companies": [
    {
      "slug": "aapl",
      "name": "Apple Inc.",
      "ticker": "AAPL",
      "sector": "Technology",
      "country": "United States",
      "market_cap": 2800000000000,
      "financials": [...]
    }
  ],
  "count": 319
}
```

#### Get Single Company
```http
GET /api/export/{slug}/json
```

**Example:**
```bash
curl https://api.aeon-nimbus.com/api/export/aapl/json
```

**Response:**
```json
{
  "slug": "aapl",
  "name": "Apple Inc.",
  "ticker": "AAPL",
  "sector": "Technology",
  "country": "United States",
  "headquarters": "Cupertino, CA",
  "employees": 164000,
  "market_cap": 2800000000000,
  "financials": [
    {
      "fy": "2023",
      "revenue": 383285000000,
      "net_income": 96995000000,
      "ebitda": 129659000000,
      "profit_margin": 0.2537
    }
  ]
}
```

---

### 2. Real-Time Pricing

#### Get Real-Time Price
```http
GET /api/prices/realtime?ticker={TICKER}
```

**Example:**
```bash
curl https://api.aeon-nimbus.com/api/prices/realtime?ticker=AAPL
```

**Response:**
```json
{
  "ticker": "AAPL",
  "price": 178.25,
  "change": 2.35,
  "change_percent": 1.34,
  "volume": 52341234,
  "timestamp": "2026-09-26T15:45:00Z",
  "market_status": "open"
}
```

#### Batch Pricing
```http
POST /api/prices/batch
Content-Type: application/json

{
  "tickers": ["AAPL", "MSFT", "GOOGL"]
}
```

**Response:**
```json
{
  "prices": [
    {"ticker": "AAPL", "price": 178.25, "change_percent": 1.34},
    {"ticker": "MSFT", "price": 335.50, "change_percent": 0.85},
    {"ticker": "GOOGL", "price": 142.80, "change_percent": -0.45}
  ],
  "timestamp": "2026-09-26T15:45:00Z"
}
```

---

### 3. Historical Charts

#### Price History
```http
GET /api/charts/history/{ticker}?period={PERIOD}
```

**Parameters:**
- `period`: `1d`, `5d`, `1m`, `3m`, `6m`, `1y`, `5y`, `max`

**Example:**
```bash
curl https://api.aeon-nimbus.com/api/charts/history/AAPL?period=1y
```

**Response:**
```json
{
  "ticker": "AAPL",
  "period": "1y",
  "data": [
    {"date": "2025-09-26", "open": 175.50, "high": 178.90, "low": 174.20, "close": 178.25, "volume": 52341234},
    {"date": "2025-09-27", "open": 178.30, "high": 180.15, "low": 177.50, "close": 179.85, "volume": 48234567}
  ]
}
```

#### Compare Multiple Stocks
```http
POST /api/charts/compare
Content-Type: application/json

{
  "tickers": ["AAPL", "MSFT", "GOOGL"],
  "period": "1y",
  "normalize": true
}
```

---

### 4. Peer Benchmarking

#### Get Peer Companies
```http
GET /api/benchmarking/peers/{slug}
```

**Example:**
```bash
curl https://api.aeon-nimbus.com/api/benchmarking/peers/aapl
```

**Response:**
```json
{
  "company": "Apple Inc.",
  "sector": "Technology",
  "peers": [
    {
      "name": "Microsoft Corporation",
      "ticker": "MSFT",
      "profit_margin": 0.3012,
      "roe": 0.4285,
      "revenue_growth": 0.1234
    },
    {
      "name": "Alphabet Inc.",
      "ticker": "GOOGL",
      "profit_margin": 0.2145,
      "roe": 0.2876,
      "revenue_growth": 0.0987
    }
  ],
  "sector_averages": {
    "profit_margin": 0.2456,
    "roe": 0.3245,
    "revenue_growth": 0.1123
  }
}
```

#### Custom Metric Comparison
```http
POST /api/benchmarking/custom
Content-Type: application/json

{
  "tickers": ["AAPL", "MSFT", "GOOGL"],
  "metrics": ["profit_margin", "roe", "revenue"]
}
```

---

### 5. News & Events

#### Company News
```http
GET /api/news/{ticker}?limit={LIMIT}
```

**Example:**
```bash
curl https://api.aeon-nimbus.com/api/news/AAPL?limit=10
```

**Response:**
```json
{
  "ticker": "AAPL",
  "news": [
    {
      "title": "Apple Announces Record Q4 Earnings",
      "source": "Reuters",
      "published": "2026-09-25T14:30:00Z",
      "url": "https://reuters.com/...",
      "sentiment": "positive"
    }
  ]
}
```

#### Earnings Calendar
```http
GET /api/earnings/upcoming?days={DAYS}
```

**Parameters:**
- `days`: Number of days ahead (default: 30)

**Response:**
```json
{
  "earnings": [
    {
      "ticker": "AAPL",
      "company": "Apple Inc.",
      "date": "2026-10-28",
      "time": "after_market",
      "eps_estimate": 1.52
    }
  ]
}
```

---

### 6. Watchlist & Alerts

#### Get Watchlist
```http
GET /api/watchlist
```

#### Add to Watchlist
```http
POST /api/watchlist/add
Content-Type: application/json

{
  "slug": "aapl"
}
```

#### Enable Price Alert
```http
POST /api/watchlist/alerts/enable
Content-Type: application/json

{
  "ticker": "AAPL",
  "threshold": 5.0,
  "direction": "both"
}
```

**Parameters:**
- `threshold`: Percentage change (e.g., 5.0 for 5%)
- `direction`: `up`, `down`, or `both`

---

### 7. Export

#### Export to PDF
```http
GET /api/export/{slug}/pdf
```

Returns PDF binary file.

#### Export to CSV
```http
GET /api/export/{slug}/csv
```

Returns CSV file with company financials.

---

## Code Examples

### Python
```python
import requests

API_KEY = "sk_live_abc123..."
BASE_URL = "https://api.aeon-nimbus.com/api"

headers = {
    "Authorization": f"Bearer {API_KEY}"
}

# Get company data
response = requests.get(
    f"{BASE_URL}/export/aapl/json",
    headers=headers
)
company = response.json()

print(f"{company['name']} - ${company['market_cap']:,}")

# Get real-time price
response = requests.get(
    f"{BASE_URL}/prices/realtime?ticker=AAPL",
    headers=headers
)
price_data = response.json()

print(f"Current price: ${price_data['price']}")
print(f"Change: {price_data['change_percent']}%")
```

### JavaScript (Node.js)
```javascript
const axios = require('axios');

const API_KEY = 'sk_live_abc123...';
const BASE_URL = 'https://api.aeon-nimbus.com/api';

const headers = {
  'Authorization': `Bearer ${API_KEY}`
};

// Get company data
async function getCompany(slug) {
  const response = await axios.get(
    `${BASE_URL}/export/${slug}/json`,
    { headers }
  );
  return response.data;
}

// Get real-time price
async function getPrice(ticker) {
  const response = await axios.get(
    `${BASE_URL}/prices/realtime`,
    { 
      params: { ticker },
      headers 
    }
  );
  return response.data;
}

// Usage
(async () => {
  const apple = await getCompany('aapl');
  console.log(`${apple.name} - $${apple.market_cap.toLocaleString()}`);
  
  const price = await getPrice('AAPL');
  console.log(`Current price: $${price.price}`);
})();
```

### cURL
```bash
# Get company data
curl -H "Authorization: Bearer sk_live_abc123..." \
  https://api.aeon-nimbus.com/api/export/aapl/json

# Get real-time price
curl -H "Authorization: Bearer sk_live_abc123..." \
  "https://api.aeon-nimbus.com/api/prices/realtime?ticker=AAPL"

# Batch pricing
curl -X POST \
  -H "Authorization: Bearer sk_live_abc123..." \
  -H "Content-Type: application/json" \
  -d '{"tickers":["AAPL","MSFT","GOOGL"]}' \
  https://api.aeon-nimbus.com/api/prices/batch

# Export to PDF
curl -H "Authorization: Bearer sk_live_abc123..." \
  https://api.aeon-nimbus.com/api/export/aapl/pdf \
  --output apple_report.pdf
```

---

## Error Handling

### Error Response Format
```json
{
  "error": "not_found",
  "message": "Company with slug 'xyz' not found",
  "status": 404
}
```

### Common Error Codes

| Code | Meaning |
|------|---------|
| 400 | Bad Request - Invalid parameters |
| 401 | Unauthorized - Missing or invalid API key |
| 403 | Forbidden - Insufficient permissions |
| 404 | Not Found - Resource doesn't exist |
| 429 | Too Many Requests - Rate limit exceeded |
| 500 | Internal Server Error |

### Rate Limit Exceeded
```json
{
  "error": "rate_limit_exceeded",
  "message": "You have exceeded your hourly rate limit of 100 requests",
  "retry_after": 3600,
  "status": 429
}
```

---

## Webhooks (Enterprise Only)

### Configure Webhook
```http
POST /api/webhooks/configure
Content-Type: application/json

{
  "url": "https://your-domain.com/webhook",
  "events": ["price_alert", "earnings_date", "news_published"]
}
```

### Webhook Payload Example
```json
{
  "event": "price_alert",
  "timestamp": "2026-09-26T15:45:00Z",
  "data": {
    "ticker": "AAPL",
    "price": 178.25,
    "change_percent": 5.2,
    "direction": "up"
  }
}
```

---

## Best Practices

### 1. Cache Responses
```python
import requests
from functools import lru_cache
import time

@lru_cache(maxsize=100)
def get_company_cached(slug, cache_time):
    # cache_time ensures cache expires every N seconds
    response = requests.get(f"{BASE_URL}/export/{slug}/json", headers=headers)
    return response.json()

# Use current time rounded to nearest 5 minutes
cache_key = int(time.time() / 300)
company = get_company_cached('aapl', cache_key)
```

### 2. Handle Rate Limits
```python
import time

def api_call_with_retry(url, max_retries=3):
    for attempt in range(max_retries):
        response = requests.get(url, headers=headers)
        
        if response.status_code == 429:
            retry_after = int(response.headers.get('X-RateLimit-Reset', 60))
            time.sleep(retry_after)
            continue
            
        return response.json()
    
    raise Exception("Max retries exceeded")
```

### 3. Batch Requests
```python
# Bad: Multiple single requests
for ticker in ['AAPL', 'MSFT', 'GOOGL']:
    price = get_price(ticker)  # 3 API calls

# Good: Single batch request
prices = get_batch_prices(['AAPL', 'MSFT', 'GOOGL'])  # 1 API call
```

---

## Support

**Documentation:** https://docs.aeon-nimbus.com  
**API Status:** https://status.aeon-nimbus.com  
**Email:** api@aeon-nimbus.com  
**Discord:** discord.gg/aeon-nimbus

---

**API Version:** 1.0  
**Last Updated:** September 26, 2026
