"""Sector detection and per-sector modelling configuration.

Maps the free-text sector strings in universe.json to H500 guide codes, then
provides per-sector:
  - valuation_kind  : 'dcf' | 'bank' | 'sum_of_parts'
  - scenario_keys   : which assumption sliders the sector exposes in the studio
  - required_kpis   : KPI names that controls.py validates as present
  - forecast_drivers: the operating drivers the forecast engine should use
  - valuation_notes : what a reviewer should see in the valuation section

This file is the single place where new sector logic is registered. Adding a
sector here automatically routes scenarios, controls, and the studio UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ValKind = Literal["dcf", "bank", "sum_of_parts", "nav", "ddm"]


@dataclass(frozen=True)
class SectorConfig:
    code: str                          # H500 guide code (BK01, TC01, …)
    label: str                         # display name
    valuation_kind: ValKind            # primary valuation method
    scenario_keys: list[str]           # keys exposed in the Assumptions studio
    required_kpis: list[str]           # controls C12 checks these are non-null
    forecast_drivers: list[str]        # for documentation / future forecast engine
    valuation_notes: str               # shown in the platform valuation panel
    alt_valuation_kinds: list[ValKind] = field(default_factory=list)  # secondary methods


# ---------------------------------------------------------------------------
# Sector registry — one entry per H500 guide code
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, SectorConfig] = {}


def _reg(cfg: SectorConfig) -> SectorConfig:
    _REGISTRY[cfg.code] = cfg
    return cfg


BK01 = _reg(SectorConfig(
    code="BK01", label="Banks",
    valuation_kind="bank",
    scenario_keys=["loan_growth", "nim", "cost_income", "cost_of_risk", "roe", "coe", "growth", "car"],
    required_kpis=["net_interest_income", "loan_book", "npl_ratio", "cost_income_ratio",
                   "tier1_capital_ratio", "roe"],
    forecast_drivers=["loan_growth", "nim", "cost_to_income", "cost_of_risk",
                      "capital_adequacy", "payout_ratio"],
    valuation_notes=(
        "Banks are valued on justified P/B (Gordon Growth) and residual income. "
        "DCF is NOT applied — unlevered FCFF is not meaningful for a financial institution "
        "where funding is a product, not a capital structure choice. "
        "Key drivers: NIM, loan growth, cost-to-income, NPL/cost-of-risk, ROE vs COE spread, "
        "capital adequacy. A P/B > justified P/B is a sell signal regardless of earnings growth."
    ),
    alt_valuation_kinds=["ddm"],
))

TC01 = _reg(SectorConfig(
    code="TC01", label="Telecoms",
    valuation_kind="dcf",
    scenario_keys=["revenue_growth", "ebitda_margin", "d_and_a_pct_sales", "capex_pct_sales",
                   "working_capital_pct_sales", "tax_rate", "wacc", "terminal_growth", "exit_multiple"],
    required_kpis=["arpu", "subscribers_m", "data_revenue_pct", "mobile_money_revenue",
                   "ebitda_margin", "capex_revenue_pct"],
    forecast_drivers=["subscriber_growth", "arpu_trend", "data_monetisation",
                      "mobile_money_penetration", "capex_cycle"],
    valuation_notes=(
        "Telecoms are valued on DCF (unlevered FCFF) with EV/EBITDA and EV/Sales peer checks. "
        "Key drivers: subscriber net adds, blended ARPU (voice/data/mobile money), "
        "capex intensity (network investment vs maintenance cycle), EBITDA margin trajectory. "
        "Mobile money contributes a higher-margin, lower-capex revenue stream — model separately. "
        "Sum-of-parts (by geography or service line) where material cross-border operations exist."
    ),
    alt_valuation_kinds=["sum_of_parts"],
))

CS04 = _reg(SectorConfig(
    code="CS04", label="Beverages",
    valuation_kind="dcf",
    scenario_keys=["revenue_growth", "ebitda_margin", "d_and_a_pct_sales", "capex_pct_sales",
                   "working_capital_pct_sales", "tax_rate", "wacc", "terminal_growth", "exit_multiple"],
    required_kpis=["volume_m_hl", "price_per_hl", "ebitda_margin", "capex_revenue_pct"],
    forecast_drivers=["volume_growth", "price_mix", "raw_material_cost", "distribution_opex",
                      "capex_capacity_expansion"],
    valuation_notes=(
        "Beverages are valued on DCF with EV/EBITDA and EV/Sales peer checks. "
        "Key P&L drivers: volume growth, pricing power (price/mix), raw material costs "
        "(malt, hops, aluminium, sugar), distribution leverage. "
        "Capex distinguishes maintenance (steady-state) from capacity expansion (growth call). "
        "Premium brand premium justifies a higher terminal multiple vs commodity peers."
    ),
))

IN02 = _reg(SectorConfig(
    code="IN02", label="Cement & Construction Materials",
    valuation_kind="dcf",
    scenario_keys=["revenue_growth", "ebitda_margin", "d_and_a_pct_sales", "capex_pct_sales",
                   "working_capital_pct_sales", "tax_rate", "wacc", "terminal_growth", "exit_multiple"],
    required_kpis=["capacity_mt", "utilisation_pct", "price_per_tonne", "energy_cost_pct"],
    forecast_drivers=["capacity_utilisation", "cement_price", "energy_cost", "clinker_ratio",
                      "logistics_cost"],
    valuation_notes=(
        "Cement is valued on DCF with EV/EBITDA and EV/tonne peer checks. "
        "Key drivers: capacity utilisation, cement/clinker price, energy costs (fuel and power "
        "together typically 35–45% of COGS). Expansion capex is lumpy — model separately from "
        "maintenance capex. Regional pricing power and import-parity pricing set the ceiling."
    ),
))

EN01 = _reg(SectorConfig(
    code="EN01", label="Oil & Gas",
    valuation_kind="dcf",
    scenario_keys=["revenue_growth", "ebitda_margin", "d_and_a_pct_sales", "capex_pct_sales",
                   "working_capital_pct_sales", "tax_rate", "wacc", "terminal_growth", "exit_multiple"],
    required_kpis=["production_boepd", "reserve_life_years", "lifting_cost_boe",
                   "realised_price_boe", "ebitda_margin"],
    forecast_drivers=["production_profile", "oil_price_deck", "lifting_cost", "reserve_replacement",
                      "government_take"],
    valuation_notes=(
        "E&P companies are valued on DCF (NPV of production) and EV/2P reserves. "
        "Key drivers: production profile (boepd), lifting costs ($/boe), realised price vs "
        "benchmark, reserve life. Government take (royalties, profit oil splits) must be "
        "modelled explicitly — PSC structures vary materially by country."
    ),
    alt_valuation_kinds=["nav"],
))

RE01 = _reg(SectorConfig(
    code="RE01", label="Real Estate",
    valuation_kind="nav",
    scenario_keys=["revenue_growth", "ebitda_margin", "capex_pct_sales", "tax_rate",
                   "wacc", "terminal_growth"],
    required_kpis=["nla_sqm", "occupancy_pct", "rental_yield_pct", "nav_per_share"],
    forecast_drivers=["rental_growth", "occupancy", "yield_compression", "development_pipeline"],
    valuation_notes=(
        "REITs and property companies are valued on NAV (appraised portfolio value − net debt ÷ shares) "
        "and implied yield (income / market cap). DCF is secondary. "
        "Key drivers: occupancy, rental reversions, yield compression/expansion, "
        "development pipeline delivery. Debt structure (LTV, interest cover) is critical for REITs."
    ),
    alt_valuation_kinds=["dcf"],
))

MA01 = _reg(SectorConfig(
    code="MA01", label="Mining",
    valuation_kind="dcf",
    scenario_keys=["revenue_growth", "ebitda_margin", "d_and_a_pct_sales", "capex_pct_sales",
                   "tax_rate", "wacc", "terminal_growth", "exit_multiple"],
    required_kpis=["production_kt", "all_in_cost_per_tonne", "reserve_life_years",
                   "realised_price_per_tonne", "ebitda_margin"],
    forecast_drivers=["production_profile", "commodity_price_deck", "aisc", "capex_sustaining",
                      "royalties"],
    valuation_notes=(
        "Mining companies are valued on DCF (mine-life NPV) and EV/EBITDA. "
        "Key drivers: production schedule (kt/year), all-in sustaining cost (AISC, $/t), "
        "commodity price deck, reserve life, royalty structure. "
        "Sustaining vs growth capex must be disaggregated."
    ),
    alt_valuation_kinds=["nav"],
))

# Generic DCF sectors — no special KPI requirements beyond the standard set
for _code, _label in [("CS01", "Food"), ("CS02", "Personal Care"),
                       ("CS03", "Retail"), ("HC01", "Healthcare"),
                       ("HC02", "Pharma"), ("IN01", "Industrial"),
                       ("IN03", "Packaging"), ("TC02", "Media"),
                       ("TC03", "Technology"), ("UT01", "Utilities"),
                       ("AG01", "Agribusiness"), ("TR01", "Transport"),
                       ("FN02", "Insurance"), ("FN03", "Asset Management"),
                       ("DI01", "Diversified")]:
    _reg(SectorConfig(
        code=_code, label=_label,
        valuation_kind="dcf",
        scenario_keys=["revenue_growth", "ebitda_margin", "d_and_a_pct_sales", "capex_pct_sales",
                       "working_capital_pct_sales", "tax_rate", "wacc", "terminal_growth", "exit_multiple"],
        required_kpis=["ebitda_margin"],
        forecast_drivers=["revenue_growth", "ebitda_margin", "capex"],
        valuation_notes=f"{_label} valued on DCF with EV/EBITDA and peer comparable checks.",
    ))

_DEFAULT = SectorConfig(
    code="XX00", label="Other",
    valuation_kind="dcf",
    scenario_keys=["revenue_growth", "ebitda_margin", "d_and_a_pct_sales", "capex_pct_sales",
                   "working_capital_pct_sales", "tax_rate", "wacc", "terminal_growth", "exit_multiple"],
    required_kpis=["ebitda_margin"],
    forecast_drivers=["revenue_growth", "ebitda_margin"],
    valuation_notes="Valued on DCF with EV/EBITDA peer checks.",
)


# ---------------------------------------------------------------------------
# Sector keyword → guide code mapping
# ---------------------------------------------------------------------------

# Lower-case keyword fragments → guide code.  Checked longest-first at runtime.
_KEYWORD_MAP: list[tuple[str, str]] = [
    # Banks / financials
    ("bank", "BK01"), ("banking", "BK01"), ("financial services", "BK01"),
    ("microfinance", "BK01"), ("saving", "BK01"),
    # Telecoms
    ("telecom", "TC01"), ("telco", "TC01"), ("mobile", "TC01"),
    ("wireless", "TC01"), ("fibre", "TC01"), ("broadband", "TC01"),
    # Beverages
    ("beverage", "CS04"), ("brewery", "CS04"), ("beer", "CS04"),
    ("spirits", "CS04"), ("soft drink", "CS04"),
    # Cement / construction materials
    ("cement", "IN02"), ("concrete", "IN02"), ("construction material", "IN02"),
    # Oil & gas
    ("oil", "EN01"), ("gas", "EN01"), ("petroleum", "EN01"), ("e&p", "EN01"),
    ("exploration", "EN01"),
    # Real estate
    ("real estate", "RE01"), ("reit", "RE01"), ("property", "RE01"),
    ("landlord", "RE01"),
    # Mining
    ("mining", "MA01"), ("mineral", "MA01"), ("gold", "MA01"), ("copper", "MA01"),
    ("cobalt", "MA01"),
    # Food / agri
    ("food", "CS01"), ("agriculture", "AG01"), ("agri", "AG01"), ("farm", "AG01"),
    # Healthcare
    ("healthcare", "HC01"), ("hospital", "HC01"), ("clinic", "HC01"),
    ("pharmaceutical", "HC02"), ("pharma", "HC02"),
    # Retail
    ("retail", "CS03"), ("supermarket", "CS03"), ("grocer", "CS03"),
    # Technology / media
    ("technology", "TC03"), ("software", "TC03"), ("media", "TC02"),
    # Utilities
    ("utility", "UT01"), ("utilities", "UT01"), ("power", "UT01"),
    ("electricity", "UT01"), ("water", "UT01"),
    # Transport
    ("transport", "TR01"), ("logistics", "TR01"), ("airline", "TR01"),
    ("port", "TR01"), ("shipping", "TR01"),
    # Insurance / asset management
    ("insurance", "FN02"), ("asset management", "FN03"),
    ("fund", "FN03"),
    # Industrial
    ("industrial", "IN01"), ("manufacturing", "IN01"), ("packaging", "IN03"),
    # Diversified
    ("diversified", "DI01"), ("conglomerate", "DI01"),
    # Personal care
    ("personal care", "CS02"), ("consumer goods", "CS02"),
]
# Sort longest-first so more specific phrases match before shorter substrings
_KEYWORD_MAP.sort(key=lambda t: -len(t[0]))


def sector_code(sector: str, sub_sector: str | None = None,
                is_bank: bool = False) -> str:
    """Return the H500 guide code for a sector/sub-sector string.

    Priority: explicit `is_bank` flag > sub_sector keyword > sector keyword > 'XX00'.
    """
    if is_bank:
        return "BK01"
    combined = f"{sector or ''} {sub_sector or ''}".lower()
    for kw, code in _KEYWORD_MAP:
        if kw in combined:
            return code
    return "XX00"


def config_for(sector: str, sub_sector: str | None = None,
               is_bank: bool = False) -> SectorConfig:
    """Return the SectorConfig for a sector string."""
    code = sector_code(sector, sub_sector, is_bank)
    return _REGISTRY.get(code, _DEFAULT)


def valuation_kind_for(sector: str, sub_sector: str | None = None,
                        is_bank: bool = False, valuation_model: str | None = None) -> ValKind:
    """Canonical valuation kind: explicit `valuation_model` in universe.json wins,
    then sector routing, then DCF default."""
    if valuation_model and valuation_model in ("bank", "dcf", "sum_of_parts", "nav", "ddm"):
        return valuation_model  # type: ignore[return-value]
    return config_for(sector, sub_sector, is_bank).valuation_kind
