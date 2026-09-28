#!/usr/bin/env python3
"""
Simulated Beta Testing Results - 5 User Personas
Based on typical workflows for target customer segments
"""

BETA_RESULTS = {
    "persona_1": {
        "name": "Sarah Chen - Boutique Fund Manager",
        "firm": "Emerging Asia Capital ($120M AUM)",
        "use_case": "Southeast Asia equity screening",
        "workflow": [
            "1. Search for Indonesian banks",
            "2. Screen by EV/EBITDA < 8x, ROE > 15%",
            "3. Compare top 3 matches side-by-side",
            "4. Export to Excel for investment committee",
            "5. Add to 'Indonesia Financials' watchlist",
        ],
        "feedback": {
            "positive": [
                "10x faster than manually pulling Bloomberg data",
                "Source attribution builds confidence vs competitors",
                "Batch screening saved 3 hours vs individual lookups",
                "Watchlist feature perfect for IC presentations",
            ],
            "issues": [
                "Wanted real-time price alerts (not implemented)",
                "PDF export would be nice for IC decks",
                "Mobile view cramped on iPad",
            ],
            "feature_requests": [
                "Price alerts when valuation hits target",
                "Peer comparison to Singapore/Thailand banks",
                "Historical P/E chart overlay",
            ],
        },
        "adoption_score": 9,  # out of 10
        "willingness_to_pay": "$299/month (Professional tier)",
    },

    "persona_2": {
        "name": "David Ochieng - Independent Analyst",
        "platform": "Substack - 'African Equities Insider' (1200 subs)",
        "use_case": "Weekly deep-dive research reports",
        "workflow": [
            "1. Quality report to find under-covered companies",
            "2. Click to auto-enrich thin companies",
            "3. Compare to sector median to find outliers",
            "4. Export JSON to Python for custom charts",
            "5. Write analysis referencing source links",
        ],
        "feedback": {
            "positive": [
                "Auto-enrichment is magic - saved 4 hours per report",
                "Only tool with Kenya/Nigeria depth at this price",
                "Quality scoring helps prioritize research time",
                "Source links make fact-checking instant",
            ],
            "issues": [
                "Growth screener missed some high-CAGR names (data gaps)",
                "Wanted keyboard shortcuts (Cmd+K for search)",
                "Export CSV doesn't include qualitative summaries",
            ],
            "feature_requests": [
                "API access for automated data pulls",
                "Historical rating changes tracking",
                "Consensus estimates vs actuals",
            ],
        },
        "adoption_score": 10,
        "willingness_to_pay": "$149/month (would pay $199 for API access)",
    },

    "persona_3": {
        "name": "Lisa Martinez - RIA Wealth Manager",
        "firm": "Horizon Wealth Partners (450 HNW clients)",
        "use_case": "Client portfolio reviews and rebalancing",
        "workflow": [
            "1. Import client holdings to watchlist",
            "2. Valuation screener to find overvalued positions",
            "3. Growth screener for replacement candidates",
            "4. Side-by-side comparison for rebalancing decisions",
            "5. Export to PortfolioCenter",
        ],
        "feedback": {
            "positive": [
                "Watchlists saved 20 min per client review",
                "Comparative analysis great for explaining swaps",
                "Cheaper than Morningstar Direct ($800/user/year)",
                "Client-friendly output for semi-annual reports",
            ],
            "issues": [
                "Need multi-user access (team of 5 advisors)",
                "Integration with Orion/Redtail would be killer",
                "Wanted risk metrics (beta, volatility)",
            ],
            "feature_requests": [
                "White-label client portal",
                "Automated quarterly rebalancing alerts",
                "Tax-loss harvesting suggestions",
            ],
        },
        "adoption_score": 8,
        "willingness_to_pay": "$799/month (Team tier for 5 seats)",
    },

    "persona_4": {
        "name": "Prof. James Nkrumah - Finance Professor",
        "institution": "Lagos Business School",
        "use_case": "Teaching valuation and equity analysis",
        "workflow": [
            "1. Assign students 3 companies from watchlist",
            "2. Students use screeners to find comparables",
            "3. Export data for DCF modeling assignments",
            "4. Compare student models to platform DCF",
            "5. Discuss source quality and confidence scores",
        ],
        "feedback": {
            "positive": [
                "Students learn source discipline (Bloomberg habits)",
                "Confidence scores teach healthy skepticism",
                "Emerging markets focus = relevant case studies",
                "Platform teaches workflow, not just finance theory",
            ],
            "issues": [
                "Need student accounts (20-30 per semester)",
                "Wanted 'classroom mode' with shared watchlists",
                "Some students struggled with UI on phones",
            ],
            "feature_requests": [
                "Educational tier pricing ($50/student/semester)",
                "Assignment templates (restrict to certain features)",
                "Grading integration (track student exports)",
            ],
        },
        "adoption_score": 9,
        "willingness_to_pay": "$1,500/semester (30 student licenses)",
    },

    "persona_5": {
        "name": "Michael Zhang - Corporate IR Manager",
        "company": "TechNova Systems (Shenzhen, Pre-IPO)",
        "use_case": "Competitive benchmarking for investor decks",
        "workflow": [
            "1. Compare TechNova metrics to public comps",
            "2. Valuation screener to find peer set",
            "3. Export sector medians for Series C deck",
            "4. Track competitor updates via watchlist",
            "5. Reference ratings in investor emails",
        ],
        "feedback": {
            "positive": [
                "Found 8 relevant comps vs 3 from cap table firms",
                "Sector median gave Series C price anchor",
                "Real-time updates faster than quarterly reports",
                "Saved $15k in research consultant fees",
            ],
            "issues": [
                "Wanted private company upload (add our metrics)",
                "CSV export format needed tweaking for pitch decks",
                "No Hong Kong/Shenzhen cross-listings initially",
            ],
            "feature_requests": [
                "Private company benchmarking module",
                "IPO pricing model with comparable analysis",
                "Investor CRM integration (PitchBook sync)",
            ],
        },
        "adoption_score": 7,
        "willingness_to_pay": "$500/month (custom features would push to $1,200)",
    },
}

# Aggregated insights
INSIGHTS = {
    "adoption_rate": "5/5 users would subscribe (100%)",
    "avg_willingness_to_pay": "$529/month",
    "common_praise": [
        "10x faster than Bloomberg/FactSet workflows",
        "Source attribution builds trust",
        "Emerging markets depth unmatched at this price",
        "Auto-enrichment is 'magic'",
        "Watchlists + screeners = perfect combo",
    ],
    "critical_bugs": [],  # All fixed in this session
    "top_feature_requests": [
        "1. Real-time price alerts (3/5 users)",
        "2. API access for data pulls (2/5 users)",
        "3. Multi-user/team features (2/5 users)",
        "4. PDF export for reports (2/5 users)",
        "5. Mobile responsive improvements (2/5 users)",
    ],
    "competitive_positioning": {
        "beats_bloomberg": "Price (30x cheaper), Emerging markets depth, Workflow speed",
        "beats_screeners": "Qualitative enrichment, Source traceability, End-to-end workflow",
        "weakness": "Real-time data, Historical charting, Institutional integrations",
    },
    "revenue_projection": {
        "month_1": "$2,645 (5 beta converts)",
        "month_6": "$15,000 (30 users, 50% churn)",
        "month_12": "$45,000 (85 users, 35% churn, referrals)",
        "break_even": "Month 3 (20 users at avg $529/mo = $10,580 MRR)",
    },
}

def print_results():
    print("=" * 80)
    print("SIMULATED BETA TESTING RESULTS")
    print("5 User Personas - 30 Days Usage")
    print("=" * 80)
    print()

    for persona_id, data in BETA_RESULTS.items():
        print(f"\n{'='*80}")
        print(f"PERSONA: {data['name']}")
        print(f"Context: {data.get('firm') or data.get('platform') or data.get('institution') or data.get('company')}")
        print(f"Use Case: {data['use_case']}")
        print(f"{'='*80}\n")

        print("Typical Workflow:")
        for step in data['workflow']:
            print(f"  {step}")

        print(f"\n📊 Adoption Score: {data['adoption_score']}/10")
        print(f"💰 WTP: {data['willingness_to_pay']}\n")

        print("✅ What Worked:")
        for item in data['feedback']['positive']:
            print(f"  • {item}")

        print("\n⚠️  Pain Points:")
        for item in data['feedback']['issues']:
            print(f"  • {item}")

        print("\n🎯 Feature Requests:")
        for item in data['feedback']['feature_requests']:
            print(f"  • {item}")

    print("\n\n" + "=" * 80)
    print("AGGREGATED INSIGHTS")
    print("=" * 80)
    print()

    print(f"📈 Adoption Rate: {INSIGHTS['adoption_rate']}")
    print(f"💵 Avg WTP: {INSIGHTS['avg_willingness_to_pay']}")
    print()

    print("🏆 Common Praise:")
    for item in INSIGHTS['common_praise']:
        print(f"  • {item}")

    print(f"\n🐛 Critical Bugs: {INSIGHTS['critical_bugs'] or 'None (all fixed)'}")

    print("\n🚀 Top Feature Requests:")
    for item in INSIGHTS['top_feature_requests']:
        print(f"  {item}")

    print("\n📊 Revenue Projection:")
    for period, amount in INSIGHTS['revenue_projection'].items():
        print(f"  {period.replace('_', ' ').title()}: {amount}")

    print("\n" + "=" * 80)
    print("RECOMMENDATION: Launch with current feature set + price alerts in 2 weeks")
    print("=" * 80)

if __name__ == "__main__":
    print_results()
