"""Formula-driven cost of capital for any country, not just the hand-curated ones.

`config.WACC_BY_COUNTRY` / `BANK_COE_BY_COUNTRY` stay authoritative where present —
they're calibrated against real filings for a handful of markets. Everywhere else,
`implied_wacc()` builds a CAPM estimate instead of guessing a single flat number:

    WACC ≈ risk_free + beta × equity_risk_premium + country_risk_premium + illiquidity_premium

Country risk premiums below are Damodaran-style sovereign default spreads (USD
terms, approximate — refresh periodically from Damodaran's country risk data).
Grouped by rough region so an unlisted country still gets a plausible number
via its region's median rather than a hard failure or an arbitrary constant.
"""

from __future__ import annotations

from aeon_nimbus.sector_analytics import cost_of_equity

# --- global CAPM inputs -----------------------------------------------------
RISK_FREE_RATE = 0.045          # proxy 10Y UST yield
EQUITY_RISK_PREMIUM = 0.055     # mature-market ERP (Damodaran-style US ERP)
FRONTIER_ILLIQUIDITY_PREMIUM = 0.02   # thin free float / weak market microstructure
DEFAULT_BETA = 1.0

# --- sovereign default spreads (country risk premium), USD terms -----------
# Not exhaustive — a representative set per region. Unlisted countries fall
# back to their region's median spread (see _REGION_OF / _region_median).
COUNTRY_RISK_PREMIUM = {
    # Africa
    "Kenya": 0.085, "Nigeria": 0.11, "South Africa": 0.045, "Ghana": 0.14,
    "Egypt": 0.13, "Morocco": 0.035, "Senegal": 0.055, "Tanzania": 0.09,
    "Uganda": 0.10, "Zambia": 0.13, "Côte d'Ivoire": 0.06, "Botswana": 0.03,
    "Mauritius": 0.025, "Rwanda": 0.075,
    # Middle East
    "United Arab Emirates": 0.01, "Saudi Arabia": 0.015, "Qatar": 0.015,
    "Israel": 0.015, "Turkey": 0.10, "Jordan": 0.06,
    # Asia
    "China": 0.015, "India": 0.02, "Indonesia": 0.03, "Vietnam": 0.045,
    "Philippines": 0.025, "Thailand": 0.015, "Malaysia": 0.015, "Pakistan": 0.12,
    "Bangladesh": 0.055, "South Korea": 0.008, "Japan": 0.005, "Singapore": 0.0,
    "Hong Kong": 0.005, "Taiwan": 0.008, "Sri Lanka": 0.10,
    # Latin America (matches figures already used in Aeon Nimbus deep-dive prompts)
    "Brazil": 0.045, "Mexico": 0.032, "Colombia": 0.051, "Argentina": 0.12,
    "Chile": 0.015, "Peru": 0.02, "Uruguay": 0.018,
    # Europe / CEE
    "United Kingdom": 0.0, "Germany": 0.0, "France": 0.005, "Spain": 0.01,
    "Italy": 0.015, "Poland": 0.012, "Czech Republic": 0.008, "Hungary": 0.02,
    "Romania": 0.025, "Greece": 0.02, "Portugal": 0.012, "Netherlands": 0.0,
    "Ireland": 0.005, "Switzerland": 0.0, "Sweden": 0.0, "Norway": 0.0,
    # North America / Oceania
    "United States": 0.0, "Canada": 0.0, "Australia": 0.0, "New Zealand": 0.0,
}

_REGION_OF = {
    "Africa": ["Kenya", "Nigeria", "South Africa", "Ghana", "Egypt", "Morocco", "Senegal",
               "Tanzania", "Uganda", "Zambia", "Côte d'Ivoire", "Botswana", "Mauritius", "Rwanda"],
    "Middle East": ["United Arab Emirates", "Saudi Arabia", "Qatar", "Israel", "Turkey", "Jordan"],
    "Asia": ["China", "India", "Indonesia", "Vietnam", "Philippines", "Thailand", "Malaysia",
             "Pakistan", "Bangladesh", "South Korea", "Japan", "Singapore", "Hong Kong",
             "Taiwan", "Sri Lanka"],
    "Latin America": ["Brazil", "Mexico", "Colombia", "Argentina", "Chile", "Peru", "Uruguay"],
    "Europe": ["United Kingdom", "Germany", "France", "Spain", "Italy", "Poland",
               "Czech Republic", "Hungary", "Romania", "Greece", "Portugal", "Netherlands",
               "Ireland", "Switzerland", "Sweden", "Norway"],
    "Developed": ["United States", "Canada", "Australia", "New Zealand"],
}
# Frontier/EM regions carry the extra illiquidity premium; developed markets don't.
_FRONTIER_REGIONS = {"Africa", "Middle East", "Asia", "Latin America"}


def _region_of(country: str | None) -> str | None:
    for region, members in _REGION_OF.items():
        if country in members:
            return region
    return None


def _region_median(region: str | None) -> float:
    if region is None:
        # Unknown region entirely: use the global median across all listed countries
        # rather than defaulting to either extreme.
        values = sorted(COUNTRY_RISK_PREMIUM.values())
        return values[len(values) // 2]
    values = sorted(COUNTRY_RISK_PREMIUM[c] for c in _REGION_OF[region])
    return values[len(values) // 2]


def country_risk_premium(country: str | None) -> float:
    """Sovereign default spread for a country — exact match, else its region's
    median, else the global median. Always returns a number, never raises."""
    if country in COUNTRY_RISK_PREMIUM:
        return COUNTRY_RISK_PREMIUM[country]
    return _region_median(_region_of(country))


def implied_wacc(country: str | None, *, is_bank: bool = False, beta: float = DEFAULT_BETA) -> float:
    """CAPM-derived WACC/cost-of-equity estimate for any country. Banks carry a
    higher beta by convention (frontier bank equity is riskier than the market
    proxy) to mirror the existing curated BANK_COE_BY_COUNTRY gap over WACC_BY_COUNTRY.
    """
    region = _region_of(country)
    illiquidity = FRONTIER_ILLIQUIDITY_PREMIUM if region in _FRONTIER_REGIONS else 0.0
    effective_beta = beta * 1.15 if is_bank else beta
    return cost_of_equity(
        RISK_FREE_RATE, effective_beta, EQUITY_RISK_PREMIUM,
        country_risk_premium=country_risk_premium(country) + illiquidity,
    )
