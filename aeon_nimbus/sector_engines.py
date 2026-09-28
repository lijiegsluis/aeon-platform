"""Sector-specific bottom-up forecast engines.

Each engine takes base-year data and a set of operating drivers, then projects
revenue / EBITDA / net income from those drivers rather than from a generic
growth-rate assumption.

Supported engines
-----------------
BankForecastEngine        — net interest income, provisions, PAT
TelecomForecastEngine     — subscriber × ARPU revenue build
VolumePriceForecastEngine — volume × price model (cement / mining / oil)
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _f(x: Any, default: float) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 1. Bank
# ---------------------------------------------------------------------------

class BankForecastEngine:
    """Bottom-up bank forecast from NIM and loan-book drivers."""

    def __init__(self, base_year_data: dict, drivers: dict, years: int = 5):
        self.base = base_year_data
        self.d = drivers
        self.years = years

    # --- drivers ----------------------------------------------------------------
    @property
    def _loan_growth(self) -> float:
        return _f(self.d.get("loan_growth_pct"), 0.12)

    @property
    def _nim(self) -> float:
        return _f(self.d.get("nim_pct"), 0.08)

    @property
    def _cir(self) -> float:
        return _f(self.d.get("cost_income_ratio"), 0.50)

    @property
    def _llp(self) -> float:
        return _f(self.d.get("loan_loss_provision_pct"), 0.02)

    @property
    def _tax(self) -> float:
        return _f(self.d.get("tax_rate"), 0.30)

    @property
    def _base_loan_book(self) -> float:
        return _f(self.base.get("loan_book"), 1000.0)

    @property
    def _base_nii(self) -> float:
        return _f(self.base.get("non_interest_income"), 200.0)

    # --- forecast ---------------------------------------------------------------
    def forecast(self) -> list[dict]:
        rows = []
        for n in range(1, self.years + 1):
            loan_book = self._base_loan_book * (1 + self._loan_growth) ** n
            nii = loan_book * self._nim
            noni = self._base_nii * (1 + 0.05) ** n        # 5% small growth for non-interest income
            total_income = nii + noni
            costs = total_income * self._cir
            ppop = total_income - costs
            provisions = loan_book * self._llp
            pbt = ppop - provisions
            pat = pbt * (1 - self._tax)
            rows.append({
                "year": n,
                "loan_book": round(loan_book, 2),
                "nii": round(nii, 2),
                "non_interest_income": round(noni, 2),
                "total_income": round(total_income, 2),
                "costs": round(costs, 2),
                "ppop": round(ppop, 2),
                "provisions": round(provisions, 2),
                "pbt": round(pbt, 2),
                "pat": round(pat, 2),
            })
        return rows

    def to_financials(self) -> list[dict]:
        """Return forecast in the platform's standard financials schema."""
        import datetime as _dt
        base_fy = _f(self.base.get("fy"), _dt.date.today().year - 1)
        out = []
        for row in self.forecast():
            out.append({
                "fy": int(base_fy) + row["year"],
                "revenue": row["total_income"],
                "ebitda": row["ppop"],
                "net_income": row["pat"],
                "loan_book": row["loan_book"],
            })
        return out


# ---------------------------------------------------------------------------
# 2. Telecom
# ---------------------------------------------------------------------------

class TelecomForecastEngine:
    """Subscriber × ARPU revenue build for telecom companies."""

    def __init__(self, base_year_data: dict, drivers: dict, years: int = 5):
        self.base = base_year_data
        self.d = drivers
        self.years = years

    @property
    def _sub_growth(self) -> float:
        return _f(self.d.get("sub_growth_pct"), 0.05)

    @property
    def _arpu_growth(self) -> float:
        return _f(self.d.get("arpu_growth_pct"), 0.03)

    @property
    def _ebitda_margin(self) -> float:
        return _f(self.d.get("ebitda_margin_pct"), 0.40)

    @property
    def _da_pct(self) -> float:
        return _f(self.d.get("da_pct_revenue"), 0.12)

    @property
    def _tax(self) -> float:
        return _f(self.d.get("tax_rate"), 0.30)

    @property
    def _capex_pct(self) -> float:
        return _f(self.d.get("capex_pct_revenue"), 0.18)

    @property
    def _base_subs(self) -> float:
        return _f(self.base.get("subscribers_m"), 10.0)

    @property
    def _base_arpu(self) -> float:
        return _f(self.base.get("arpu"), 5.0)

    def forecast(self) -> list[dict]:
        rows = []
        for n in range(1, self.years + 1):
            subs = self._base_subs * (1 + self._sub_growth) ** n
            arpu = self._base_arpu * (1 + self._arpu_growth) ** n
            revenue = subs * arpu * 12          # annualised
            ebitda = revenue * self._ebitda_margin
            da = revenue * self._da_pct
            ebit = ebitda - da
            net_income = ebit * (1 - self._tax)
            capex = revenue * self._capex_pct
            fcf = ebitda - capex
            rows.append({
                "year": n,
                "subscribers_m": round(subs, 3),
                "arpu": round(arpu, 4),
                "revenue": round(revenue, 2),
                "ebitda": round(ebitda, 2),
                "da": round(da, 2),
                "ebit": round(ebit, 2),
                "net_income": round(net_income, 2),
                "capex": round(capex, 2),
                "fcf": round(fcf, 2),
            })
        return rows

    def to_financials(self) -> list[dict]:
        import datetime as _dt
        base_fy = _f(self.base.get("fy"), _dt.date.today().year - 1)
        return [
            {
                "fy": int(base_fy) + row["year"],
                "revenue": row["revenue"],
                "ebitda": row["ebitda"],
                "net_income": row["net_income"],
                "capex": row["capex"],
                "fcf": row["fcf"],
            }
            for row in self.forecast()
        ]


# ---------------------------------------------------------------------------
# 3. Volume × Price (Cement / Mining / Oil)
# ---------------------------------------------------------------------------

class VolumePriceForecastEngine:
    """Volume × realised-price model for cement, mining, and oil companies."""

    def __init__(self, base_year_data: dict, drivers: dict, years: int = 5):
        self.base = base_year_data
        self.d = drivers
        self.years = years

    @property
    def _vol_growth(self) -> float:
        return _f(self.d.get("volume_growth_pct"), 0.05)

    @property
    def _price_growth(self) -> float:
        return _f(self.d.get("price_growth_pct"), 0.03)

    @property
    def _da_pct(self) -> float:
        return _f(self.d.get("da_pct_revenue"), 0.08)

    @property
    def _tax(self) -> float:
        return _f(self.d.get("tax_rate"), 0.30)

    @property
    def _base_volume(self) -> float:
        return _f(self.base.get("volume_mt"), 5.0)

    @property
    def _base_price(self) -> float:
        return _f(self.base.get("price_per_tonne"), 100.0)

    @property
    def _cash_cost(self) -> float:
        return _f(self.d.get("cash_cost_per_tonne") or self.base.get("cash_cost_per_tonne"), 65.0)

    @property
    def _fixed_costs(self) -> float:
        return _f(self.d.get("fixed_costs_m") or self.base.get("fixed_costs_m"), 50.0)

    @property
    def _capex_m(self) -> float:
        return _f(self.d.get("capex_m") or self.base.get("capex_m"), 30.0)

    def forecast(self) -> list[dict]:
        rows = []
        for n in range(1, self.years + 1):
            volume = self._base_volume * (1 + self._vol_growth) ** n
            price = self._base_price * (1 + self._price_growth) ** n
            revenue = volume * price
            variable_costs = volume * self._cash_cost
            gross_profit = revenue - variable_costs - self._fixed_costs
            ebitda = gross_profit
            da = revenue * self._da_pct
            ebit = ebitda - da
            net_income = ebit * (1 - self._tax)
            fcf = ebitda - self._capex_m
            rows.append({
                "year": n,
                "volume_mt": round(volume, 3),
                "price_per_tonne": round(price, 2),
                "revenue": round(revenue, 2),
                "variable_costs": round(variable_costs, 2),
                "fixed_costs": round(self._fixed_costs, 2),
                "gross_profit": round(gross_profit, 2),
                "ebitda": round(ebitda, 2),
                "da": round(da, 2),
                "ebit": round(ebit, 2),
                "net_income": round(net_income, 2),
                "capex_m": round(self._capex_m, 2),
                "fcf": round(fcf, 2),
            })
        return rows

    def to_financials(self) -> list[dict]:
        import datetime as _dt
        base_fy = _f(self.base.get("fy"), _dt.date.today().year - 1)
        return [
            {
                "fy": int(base_fy) + row["year"],
                "revenue": row["revenue"],
                "ebitda": row["ebitda"],
                "net_income": row["net_income"],
                "capex": row["capex_m"],
                "fcf": row["fcf"],
            }
            for row in self.forecast()
        ]


# ---------------------------------------------------------------------------
# 4. Generic Revenue-Growth (Technology / Consumer / Healthcare / Industrials)
# ---------------------------------------------------------------------------

class RevenueGrowthForecastEngine:
    """Generic top-down revenue-growth + margin forecast for sectors without
    a dedicated bottom-up driver model (tech, consumer, healthcare, industrials,
    utilities, etc.)."""

    def __init__(self, base_year_data: dict, drivers: dict, years: int = 5):
        self.base = base_year_data
        self.d = drivers
        self.years = years

    @property
    def _rev_growth(self) -> float:
        return _f(self.d.get("revenue_growth_pct"), 0.08)

    @property
    def _ebitda_margin(self) -> float:
        return _f(self.d.get("ebitda_margin_pct") or self.d.get("ebitda_margin"), 0.20)

    @property
    def _da_pct(self) -> float:
        return _f(self.d.get("da_pct_revenue"), 0.05)

    @property
    def _tax(self) -> float:
        return _f(self.d.get("tax_rate"), 0.25)

    @property
    def _capex_pct(self) -> float:
        return _f(self.d.get("capex_pct_revenue"), 0.04)

    def forecast(self) -> list[dict]:
        rows = []
        base_rev = _f(self.base.get("revenue"), 1000.0)
        for n in range(1, self.years + 1):
            revenue = base_rev * (1 + self._rev_growth) ** n
            ebitda = revenue * self._ebitda_margin
            da = revenue * self._da_pct
            ebit = ebitda - da
            net_income = ebit * (1 - self._tax)
            capex = revenue * self._capex_pct
            fcf = ebitda - capex
            rows.append({
                "year": n,
                "revenue": round(revenue, 2),
                "ebitda": round(ebitda, 2),
                "ebitda_margin": round(self._ebitda_margin * 100, 1),
                "da": round(da, 2),
                "ebit": round(ebit, 2),
                "net_income": round(net_income, 2),
                "capex": round(capex, 2),
                "fcf": round(fcf, 2),
            })
        return rows

    def to_financials(self) -> list[dict]:
        import datetime as _dt
        base_fy = _f(self.base.get("fy"), _dt.date.today().year - 1)
        return [
            {
                "fy": int(base_fy) + row["year"],
                "revenue": row["revenue"],
                "ebitda": row["ebitda"],
                "net_income": row["net_income"],
                "capex": row["capex"],
                "fcf": row["fcf"],
            }
            for row in self.forecast()
        ]


# ---------------------------------------------------------------------------
# Registry helper
# ---------------------------------------------------------------------------

_SECTOR_MAP = {
    # Banks / Financials
    "banks": BankForecastEngine,
    "bank": BankForecastEngine,
    "financials": BankForecastEngine,
    "diversified financials": BankForecastEngine,
    "insurance": BankForecastEngine,
    # Telecoms
    "telecoms": TelecomForecastEngine,
    "telecom": TelecomForecastEngine,
    "telecommunications": TelecomForecastEngine,
    "communication services": TelecomForecastEngine,
    "communication services / telecommunications": TelecomForecastEngine,
    # Commodities / Volume-price
    "cement": VolumePriceForecastEngine,
    "mining": VolumePriceForecastEngine,
    "oil": VolumePriceForecastEngine,
    "oil & gas": VolumePriceForecastEngine,
    "energy": VolumePriceForecastEngine,
    "materials": VolumePriceForecastEngine,
    # Generic / revenue-growth
    "technology": RevenueGrowthForecastEngine,
    "information technology": RevenueGrowthForecastEngine,
    "technology / internet": RevenueGrowthForecastEngine,
    "consumer discretionary": RevenueGrowthForecastEngine,
    "consumer discretionary / information technology": RevenueGrowthForecastEngine,
    "consumer staples": RevenueGrowthForecastEngine,
    "healthcare": RevenueGrowthForecastEngine,
    "health care": RevenueGrowthForecastEngine,
    "industrials": RevenueGrowthForecastEngine,
    "utilities": RevenueGrowthForecastEngine,
}


def engine_for_sector(sector: str) -> type | None:
    """Return the engine class for a sector string, or None if unsupported."""
    s = (sector or "").lower().strip()
    if s in _SECTOR_MAP:
        return _SECTOR_MAP[s]
    # Partial-match fallback for compound sector strings not in the map
    for key, cls in _SECTOR_MAP.items():
        if key in s or s in key:
            return cls
    return None
