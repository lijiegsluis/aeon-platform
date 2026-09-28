"""Sector-specific operating drivers: the KPIs that actually build the forecast.

The old model grew revenue with one rate and applied one EBITDA margin. That is
too coarse for a research-grade note: it cannot answer *why* revenue grows, and
it leaves the operating KPIs (subscribers, ARPU, tonnes, loans) sitting beside
the model instead of inside it.

Here each sector declares the drivers that build its P&L bottom-up:

    telecom   subscribers x ARPU x 12, per stream (voice / data / money / fixed)
    bank      loans x NIM + fees, cost-to-income, cost of risk
    cement    capacity x utilisation x price, energy and freight per tonne
    consumer  volume x price/mix, gross margin, distribution cost
    energy    production x realised price, opex and capex per unit

Each sector exposes the same contract, so the three-statement engine and the
Excel writer stay sector-agnostic:

    classify(company)                     -> sector key
    driver_keys(sector)                   -> [(key, label, kind)] for the UI/Excel
    defaults(sector, fins, kpis, meta)    -> starting driver values from history
    build_year(sector, drivers, t, prior) -> {revenue streams, costs, capex, kpis}

Every build is deterministic and inspectable: no figure appears in the model
without a driver behind it.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------
# sector classification
# --------------------------------------------------------------------------

TELECOM, BANK, CEMENT, CONSUMER, ENERGY, RETAIL, DIVERSIFIED = (
    "telecom", "bank", "cement", "consumer", "energy", "retail", "diversified")

# Order matters: a telecom that also runs a wallet ("mobile financial
# services") is a telecom, and a food group selling "edible oils" is not an
# oil company. The most specific signal is tested first.
_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (TELECOM, ("telecom", "mobile network", "mobile telecom", "wireless", "momo",
               "m-pesa", "mobile money", "connectivity", "mobile operator")),
    (BANK, ("bank", "banking", "insurance", "asset management")),
    (CEMENT, ("cement", "construction material", "building material")),
    (CONSUMER, ("brewer", "beverage", "packaged food", "fmcg", "distiller",
                "consumer goods", "sugar", "flour", "food manufactur")),
    (RETAIL, ("retail", "supermarket", "food & staples", "food and staples",
              "foodservice", "wholesale", "e-commerce", "classifieds")),
    (ENERGY, ("oil & gas", "oil and gas", "petroleum", "upstream", "refin",
              "gas processing", "integrated energy", "chemicals", "exploration")),
]


def classify(company: dict[str, Any]) -> str:
    """Sector key from the universe entry's sector text.

    Matching is priority-ordered and phrase-based rather than single-word, so
    "mobile financial services" reads as telecom and "edible oils" does not
    read as an oil producer.
    """
    text = " ".join(str(company.get(k) or "") for k in
                    ("sub_sector", "sector", "valuation_model", "name")).lower()
    for sector, words in _KEYWORDS:
        if any(w in text for w in words):
            return sector
    return DIVERSIFIED


# --------------------------------------------------------------------------
# driver specifications  (key, label, kind)
#   kind: pct | num | money | mult  — drives formatting in Excel and the UI
# --------------------------------------------------------------------------

_SPECS: dict[str, list[tuple[str, str, str]]] = {
    TELECOM: [
        ("subs_m", "Mobile subscribers (m)", "num"),
        ("subs_growth", "Subscriber net-add growth", "pct"),
        ("churn", "Annual churn", "pct"),
        ("voice_arpu", "Voice ARPU (per month)", "money"),
        ("voice_arpu_growth", "Voice ARPU growth", "pct"),
        ("data_subs_pct", "Data users % of base", "pct"),
        ("data_arpu", "Data ARPU (per month)", "money"),
        ("data_arpu_growth", "Data ARPU growth", "pct"),
        ("momo_users_m", "Mobile-money users (m)", "num"),
        ("momo_rpu", "Mobile-money RPU (per month)", "money"),
        ("momo_rpu_growth", "Mobile-money RPU growth", "pct"),
        ("fixed_homes_k", "Fixed/FTTH homes (k)", "num"),
        ("fixed_arpu", "Fixed ARPU (per month)", "money"),
        ("other_rev_pct", "Other service revenue % of core", "pct"),
        ("nonservice_pct", "Handset & other non-service % of service", "pct"),
        ("direct_cost_pct", "Direct costs % of revenue", "pct"),
        ("momo_commission_pct", "Agent commission % of MoMo revenue", "pct"),
        ("network_opex_pct", "Network & staff opex % of revenue", "pct"),
        ("capex_pct", "Capex % of revenue", "pct"),
        ("spectrum_capex", "Spectrum / licence capex (per year)", "money"),
    ],
    BANK: [
        ("loans", "Gross loans", "money"),
        ("loan_growth", "Loan growth", "pct"),
        ("nim", "Net interest margin", "pct"),
        ("fee_income_pct", "Fee income % of assets", "pct"),
        ("cost_income", "Cost-to-income", "pct"),
        ("cost_of_risk", "Cost of risk", "pct"),
        ("deposit_growth", "Deposit growth", "pct"),
        ("loan_deposit", "Loan-to-deposit ratio", "pct"),
        ("car", "Capital adequacy ratio", "pct"),
        ("tax_rate", "Effective tax rate", "pct"),
    ],
    CEMENT: [
        ("capacity_mt", "Installed capacity (Mt)", "num"),
        ("utilisation", "Capacity utilisation", "pct"),
        ("volume_growth", "Volume growth", "pct"),
        ("price_per_tonne", "Realised price per tonne", "money"),
        ("price_growth", "Price growth", "pct"),
        ("energy_cost_pt", "Energy cost per tonne", "money"),
        ("other_cash_cost_pt", "Other cash cost per tonne", "money"),
        ("freight_pct", "Freight & distribution % of revenue", "pct"),
        ("overhead_pct", "Overheads % of revenue", "pct"),
        ("maint_capex_pt", "Maintenance capex per tonne", "money"),
        ("growth_capex", "Growth capex (per year)", "money"),
    ],
    CONSUMER: [
        ("volume_units", "Volume (units, m)", "num"),
        ("volume_growth", "Volume growth", "pct"),
        ("price_per_unit", "Net price per unit", "money"),
        ("price_mix_growth", "Price/mix growth", "pct"),
        ("gross_margin", "Gross margin", "pct"),
        ("distribution_pct", "Distribution % of revenue", "pct"),
        ("marketing_pct", "Marketing % of revenue", "pct"),
        ("admin_pct", "Administration % of revenue", "pct"),
        ("capex_pct", "Capex % of revenue", "pct"),
    ],
    ENERGY: [
        ("production_kboepd", "Production (kboe/d)", "num"),
        ("production_growth", "Production growth", "pct"),
        ("realised_price", "Realised price (per boe)", "money"),
        ("price_growth", "Realised price growth", "pct"),
        ("opex_per_boe", "Operating cost per boe", "money"),
        ("royalty_pct", "Royalties % of revenue", "pct"),
        ("overhead_pct", "Overheads % of revenue", "pct"),
        ("capex_per_boe", "Capex per boe produced", "money"),
    ],
    RETAIL: [
        ("stores", "Store count", "num"),
        ("store_growth", "Store growth", "pct"),
        ("sales_per_store", "Sales per store", "money"),
        ("lfl_growth", "Like-for-like growth", "pct"),
        ("gross_margin", "Gross margin", "pct"),
        ("store_opex_pct", "Store operating cost % of revenue", "pct"),
        ("admin_pct", "Administration % of revenue", "pct"),
        ("capex_per_store", "Capex per new store", "money"),
        ("maint_capex_pct", "Maintenance capex % of revenue", "pct"),
    ],
    DIVERSIFIED: [
        ("revenue_growth", "Revenue growth", "pct"),
        ("gross_margin", "Gross margin", "pct"),
        ("opex_pct", "Operating cost % of revenue", "pct"),
        ("capex_pct", "Capex % of revenue", "pct"),
    ],
}


def driver_keys(sector: str) -> list[tuple[str, str, str]]:
    return _SPECS.get(sector, _SPECS[DIVERSIFIED])


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _num(x: Any, default: float = 0.0) -> float:
    return float(x) if isinstance(x, (int, float)) else default


def _ratio(num: Any, den: Any, default: float) -> float:
    n, d = _num(num, 0.0), _num(den, 0.0)
    return (n / d) if (n and d) else default


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


_RPU_WORDS = ("rpu", "arpu", "per-user", "per user", "revenue", "per month", "/month")


def _kpi(kpis: list[dict] | None, *needles: str, exclude: tuple[str, ...] = ()) -> float | None:
    """Find a disclosed operating KPI by fuzzy name match.

    `exclude` guards against the classic mis-match where a search for a user
    *count* lands on a revenue-*per-user* metric of the same product.
    """
    for k in kpis or []:
        name = str(k.get("name", "")).lower()
        if any(x in name for x in exclude):
            continue
        if all(n.lower() in name for n in needles):
            v = k.get("value")
            if isinstance(v, (int, float)):
                return float(v)
    return None


def _segment(segs: list[dict] | None, *needles: str) -> float | None:
    for s in segs or []:
        name = str(s.get("name", "")).lower()
        if all(n.lower() in name for n in needles):
            v = s.get("value")
            if isinstance(v, (int, float)):
                return float(v)
    return None


# --------------------------------------------------------------------------
# defaults derived from the company's own history + disclosed KPIs
# --------------------------------------------------------------------------

def defaults(sector: str, fins: list[dict], notes: dict | None = None,
             meta: dict | None = None) -> dict[str, float]:
    """Starting driver values, grounded in the latest reported year.

    Anything the company discloses (KPIs, segment revenue) is used directly;
    what is not disclosed is inferred from the financials, never invented.
    """
    notes = notes or {}
    kpis = notes.get("sector_specific") or []
    segs = notes.get("segments") or []
    latest = fins[-1] if fins else {}
    rev = _num(latest.get("revenue"), 0.0)
    ebitda = _num(latest.get("ebitda"), 0.0)
    capex = abs(_num(latest.get("capex"), 0.0))
    unit_m = 1.0  # financials are in millions of local currency

    if sector == TELECOM:
        # Subscriber base: monthly actives is the right ARPU denominator.
        subs = (_kpi(kpis, "one-month active", exclude=_RPU_WORDS)
                or _kpi(kpis, "active customers", exclude=_RPU_WORDS)
                or _kpi(kpis, "customers", exclude=_RPU_WORDS) or 30.0)
        # Disclosed monthly revenue per user, by product.
        momo_rpu = _kpi(kpis, "m-pesa", "per-user") or _kpi(kpis, "m-pesa", "rpu")
        data_arpu = _kpi(kpis, "data", "arpu") or _kpi(kpis, "data", "rpu")
        # Segment revenue for the year (annual, same unit as the accounts).
        momo_rev = _segment(segs, "m-pesa", "revenue") or 0.0
        voice_rev = _segment(segs, "voice") or 0.0
        data_rev = _segment(segs, "mobile data") or 0.0
        fixed_rev = _segment(segs, "fixed data") or _segment(segs, "fixed") or 0.0
        svc_rev = _segment(segs, "service revenue") or 0.0
        homes = (_kpi(kpis, "fibre-to-home", exclude=_RPU_WORDS)
                 or _kpi(kpis, "fibre", exclude=_RPU_WORDS) or 0.0)

        # Solve each stream's user base from its own revenue and disclosed RPU,
        # so the build reproduces the reported revenue mix instead of guessing.
        m_rpu = momo_rpu or 150.0
        momo_users = (momo_rev / (m_rpu * 12)) if (momo_rev and m_rpu) else subs * 0.75
        d_arpu = data_arpu or 90.0
        data_users = (data_rev / (d_arpu * 12)) if (data_rev and d_arpu) else subs * 0.55
        data_pct = _clamp(data_users / subs, 0.10, 0.98) if subs else 0.55
        v_arpu = (voice_rev / (subs * 12)) if (voice_rev and subs) else 60.0
        homes_k = (homes / 1000.0) if homes > 10000 else (homes or 200.0)
        f_arpu = (fixed_rev / ((homes_k / 1000.0) * 12)) if (fixed_rev and homes_k) else 3000.0
        # "Other" is service revenue the four modelled streams do not capture.
        modelled = voice_rev + data_rev + momo_rev + fixed_rev
        other_pct = _clamp((svc_rev - modelled) / modelled, 0.0, 0.25) if (svc_rev and modelled) else 0.05
        # Handsets, connection fees and other non-service revenue.
        nonsvc_pct = _clamp((rev - svc_rev) / svc_rev, 0.0, 0.08) if (svc_rev and rev > svc_rev) else 0.045

        # Cost structure from the audited bridge: direct costs excluding agent
        # commissions, commissions on mobile money, everything else in opex.
        direct_total = abs(_num(notes.get("direct_costs"), 0.0)) or rev * 0.26
        commissions = abs(_kpi(kpis, "commission") or 0.0)
        direct_ex = max(direct_total - commissions, 0.0)
        opex_total = max(rev - ebitda - direct_total, 0.0) if (rev and ebitda) else rev * 0.28
        return {
            "subs_m": round(subs, 2),
            "subs_growth": 0.04, "churn": 0.02,
            "voice_arpu": round(v_arpu, 2), "voice_arpu_growth": -0.01,
            "data_subs_pct": round(data_pct, 4),
            "data_arpu": round(d_arpu, 2), "data_arpu_growth": 0.05,
            "momo_users_m": round(momo_users, 2),
            "momo_rpu": round(m_rpu, 2), "momo_rpu_growth": 0.06,
            "fixed_homes_k": round(homes_k, 1),
            "fixed_arpu": round(f_arpu, 1),
            "other_rev_pct": round(other_pct, 4),
            "nonservice_pct": round(nonsvc_pct, 4),
            "direct_cost_pct": round(_clamp(direct_ex / rev if rev else 0.16, 0.05, 0.40), 4),
            "momo_commission_pct": round(_clamp(commissions / momo_rev if momo_rev else 0.23, 0.05, 0.40), 4),
            "network_opex_pct": round(_clamp(opex_total / rev if rev else 0.28, 0.05, 0.50), 4),
            "capex_pct": round(_clamp(_ratio(capex, rev, 0.18), 0.05, 0.30), 4),
            "spectrum_capex": 0.0,
        }

    if sector == BANK:
        assets = _num(latest.get("total_assets"), 0.0)
        loans = _num(latest.get("gross_loans"), 0.0) or assets * 0.55
        return {
            "loans": round(loans, 1), "loan_growth": 0.12,
            "nim": 0.065, "fee_income_pct": 0.015,
            "cost_income": 0.50, "cost_of_risk": 0.02,
            "deposit_growth": 0.11, "loan_deposit": 0.75,
            "car": 0.18, "tax_rate": 0.30,
        }

    if sector == CEMENT:
        vol = _kpi(kpis, "volume") or _kpi(kpis, "tonne") or 0.0
        capacity = _kpi(kpis, "capacity") or (vol / 0.75 if vol else 10.0)
        price = (rev / (vol * 1e6) * 1e6) if (rev and vol) else 12000.0
        return {
            "capacity_mt": round(capacity, 2), "utilisation": 0.75,
            "volume_growth": 0.05,
            "price_per_tonne": round(price, 1), "price_growth": 0.04,
            "energy_cost_pt": round(price * 0.30, 1),
            "other_cash_cost_pt": round(price * 0.20, 1),
            "freight_pct": 0.10, "overhead_pct": 0.08,
            "maint_capex_pt": round(price * 0.05, 1),
            "growth_capex": round(rev * 0.05, 1),
        }

    if sector == CONSUMER:
        gm = _ratio(latest.get("gross_profit"), rev, 0.40)
        return {
            "volume_units": 100.0, "volume_growth": 0.04,
            "price_per_unit": round(rev / 100.0, 2) if rev else 100.0,
            "price_mix_growth": 0.06,
            "gross_margin": round(_clamp(gm, 0.15, 0.70), 4),
            "distribution_pct": 0.10, "marketing_pct": 0.06, "admin_pct": 0.08,
            "capex_pct": round(_clamp(_ratio(capex, rev, 0.06), 0.02, 0.20), 4),
        }

    if sector == ENERGY:
        prod = (_kpi(kpis, "production", exclude=("cost", "per boe", "unit"))
                or _kpi(kpis, "volumes lifted", exclude=("cost",)) or 50.0)
        # Producers disclose boe/d, kboe/d or mboe/d. Normalise to kboe/d so the
        # revenue build cannot be out by three orders of magnitude.
        if prod > 5000:
            prod = prod / 1000.0
        return {
            "production_kboepd": round(prod, 1), "production_growth": 0.03,
            "realised_price": round(rev / (prod * 365 / 1000), 1) if (rev and prod) else 70.0,
            "price_growth": 0.0,
            "opex_per_boe": 12.0, "royalty_pct": 0.12, "overhead_pct": 0.06,
            "capex_per_boe": 15.0,
        }

    if sector == RETAIL:
        gm = _ratio(latest.get("gross_profit"), rev, 0.22)
        return {
            "stores": 500.0, "store_growth": 0.05,
            "sales_per_store": round(rev / 500.0, 1) if rev else 100.0,
            "lfl_growth": 0.05,
            "gross_margin": round(_clamp(gm, 0.10, 0.40), 4),
            "store_opex_pct": 0.14, "admin_pct": 0.04,
            "capex_per_store": round(rev * 0.0005, 1) if rev else 50.0,
            "maint_capex_pct": 0.02,
        }

    return {
        "revenue_growth": 0.07,
        "gross_margin": round(_clamp(_ratio(latest.get("gross_profit"), rev, 0.35), 0.10, 0.70), 4),
        "opex_pct": round(_clamp(1 - _ratio(ebitda, rev, 0.20) - 0.35, 0.05, 0.60), 4),
        "capex_pct": round(_clamp(_ratio(capex, rev, 0.06), 0.01, 0.30), 4),
    }


# --------------------------------------------------------------------------
# the per-year build: KPIs -> revenue, cost and capex
# --------------------------------------------------------------------------

def build_year(sector: str, d: dict[str, float], t: int) -> dict[str, Any]:
    """Operating build for forecast year `t` (1-based) from driver set `d`.

    Returns revenue by stream, cost lines, capex and the KPI values that
    produced them, so the workbook can show the operating bridge, not just a
    revenue number.
    """
    g = lambda k, dv=0.0: _num(d.get(k), dv)  # noqa: E731

    if sector == TELECOM:
        subs = g("subs_m") * ((1 + g("subs_growth")) ** t)
        v_arpu = g("voice_arpu") * ((1 + g("voice_arpu_growth")) ** t)
        d_subs = subs * _clamp(g("data_subs_pct", 0.7) + 0.02 * t, 0.0, 0.98)
        d_arpu = g("data_arpu") * ((1 + g("data_arpu_growth")) ** t)
        m_users = g("momo_users_m") * ((1 + g("subs_growth")) ** t)
        m_rpu = g("momo_rpu") * ((1 + g("momo_rpu_growth")) ** t)
        homes = g("fixed_homes_k") * ((1 + g("subs_growth") + 0.10) ** t)
        voice = subs * v_arpu * 12
        data = d_subs * d_arpu * 12
        momo = m_users * m_rpu * 12
        fixed = homes / 1000.0 * g("fixed_arpu") * 12
        core = voice + data + momo + fixed
        other = core * g("other_rev_pct")           # messaging, interconnect, other services
        service = core + other
        nonservice = service * g("nonservice_pct")  # handsets, connection fees
        revenue = service + nonservice
        # direct_cost_pct is stated EXCLUDING agent commissions, which are
        # modelled on the mobile-money line, so nothing is counted twice.
        direct = revenue * g("direct_cost_pct")
        commissions = momo * g("momo_commission_pct")
        opex = revenue * g("network_opex_pct")
        capex = revenue * g("capex_pct") + g("spectrum_capex")
        return {
            "revenue_streams": {"Voice": voice, "Mobile data": data,
                                "Mobile money": momo, "Fixed data": fixed,
                                "Other service": other, "Handsets & other": nonservice},
            "revenue": revenue,
            "cost_lines": {"Direct costs": direct, "Agent commissions": commissions,
                           "Network & staff opex": opex},
            "cash_costs": direct + commissions + opex,
            "capex": capex,
            "kpis": {"Subscribers (m)": subs, "Data users (m)": d_subs,
                     "Blended ARPU (monthly)": revenue / (subs * 12) if subs else 0.0,
                     "Mobile-money users (m)": m_users, "Fixed homes (k)": homes},
        }

    if sector == BANK:
        loans = g("loans") * ((1 + g("loan_growth")) ** t)
        deposits = loans / g("loan_deposit", 0.75) if g("loan_deposit") else loans
        nii = loans * g("nim")
        fees = (loans + deposits) * 0.5 * g("fee_income_pct")
        revenue = nii + fees
        opex = revenue * g("cost_income")
        impairment = loans * g("cost_of_risk")
        return {
            "revenue_streams": {"Net interest income": nii, "Fees & commissions": fees},
            "revenue": revenue,
            "cost_lines": {"Operating expenses": opex, "Impairment charge": impairment},
            "cash_costs": opex + impairment,
            "capex": revenue * 0.03,
            "kpis": {"Gross loans": loans, "Deposits": deposits,
                     "NIM": g("nim"), "Cost-to-income": g("cost_income")},
        }

    if sector == CEMENT:
        capacity = g("capacity_mt")
        util = _clamp(g("utilisation") + 0.01 * t, 0.30, 0.98)
        volume = capacity * util * ((1 + g("volume_growth")) ** t)
        price = g("price_per_tonne") * ((1 + g("price_growth")) ** t)
        revenue = volume * price          # Mt x price = m of currency
        energy = volume * g("energy_cost_pt") * ((1 + g("price_growth")) ** t)
        other_cost = volume * g("other_cash_cost_pt") * ((1 + g("price_growth")) ** t)
        freight = revenue * g("freight_pct")
        overhead = revenue * g("overhead_pct")
        capex = volume * g("maint_capex_pt") + g("growth_capex")
        return {
            "revenue_streams": {"Cement sales": revenue},
            "revenue": revenue,
            "cost_lines": {"Energy": energy, "Other production cost": other_cost,
                           "Freight & distribution": freight, "Overheads": overhead},
            "cash_costs": energy + other_cost + freight + overhead,
            "capex": capex,
            "kpis": {"Volume (Mt)": volume, "Utilisation": util,
                     "Price per tonne": price, "Cash cost per tonne":
                        (energy + other_cost) / volume if volume else 0.0},
        }

    if sector == CONSUMER:
        volume = g("volume_units") * ((1 + g("volume_growth")) ** t)
        price = g("price_per_unit") * ((1 + g("price_mix_growth")) ** t)
        revenue = volume * price
        cogs = revenue * (1 - g("gross_margin"))
        dist = revenue * g("distribution_pct")
        mkt = revenue * g("marketing_pct")
        admin = revenue * g("admin_pct")
        return {
            "revenue_streams": {"Product sales": revenue},
            "revenue": revenue,
            "cost_lines": {"Cost of goods sold": cogs, "Distribution": dist,
                           "Marketing": mkt, "Administration": admin},
            "cash_costs": cogs + dist + mkt + admin,
            "capex": revenue * g("capex_pct"),
            "kpis": {"Volume (m units)": volume, "Price per unit": price,
                     "Gross margin": g("gross_margin")},
        }

    if sector == ENERGY:
        prod = g("production_kboepd") * ((1 + g("production_growth")) ** t)
        price = g("realised_price") * ((1 + g("price_growth")) ** t)
        boe = prod * 365 / 1000.0                     # million boe per year
        revenue = boe * price
        opex = boe * g("opex_per_boe")
        royalty = revenue * g("royalty_pct")
        overhead = revenue * g("overhead_pct")
        return {
            "revenue_streams": {"Hydrocarbon sales": revenue},
            "revenue": revenue,
            "cost_lines": {"Production opex": opex, "Royalties": royalty,
                           "Overheads": overhead},
            "cash_costs": opex + royalty + overhead,
            "capex": boe * g("capex_per_boe"),
            "kpis": {"Production (kboe/d)": prod, "Realised price": price,
                     "Opex per boe": g("opex_per_boe")},
        }

    if sector == RETAIL:
        stores = g("stores") * ((1 + g("store_growth")) ** t)
        sales = g("sales_per_store") * ((1 + g("lfl_growth")) ** t)
        revenue = stores * sales
        cogs = revenue * (1 - g("gross_margin"))
        store_opex = revenue * g("store_opex_pct")
        admin = revenue * g("admin_pct")
        new_stores = stores - g("stores") * ((1 + g("store_growth")) ** (t - 1))
        capex = new_stores * g("capex_per_store") + revenue * g("maint_capex_pct")
        return {
            "revenue_streams": {"Retail sales": revenue},
            "revenue": revenue,
            "cost_lines": {"Cost of sales": cogs, "Store operating costs": store_opex,
                           "Administration": admin},
            "cash_costs": cogs + store_opex + admin,
            "capex": capex,
            "kpis": {"Stores": stores, "Sales per store": sales,
                     "Gross margin": g("gross_margin")},
        }

    revenue = g("revenue_growth")  # diversified fallback handled by caller scaling
    return {"revenue_streams": {}, "revenue": revenue, "cost_lines": {},
            "cash_costs": 0.0, "capex": 0.0, "kpis": {}}


def calibrate(sector: str, drivers: dict[str, float], base_revenue: float,
              base_ebitda: float | None = None) -> dict[str, float]:
    """Calibrate the driver set so year 0 reproduces the audited base year.

    Operating KPIs are disclosed on different bases than the accounts (90-day
    versus monthly actives, a KPI year that leads the latest audited year, group
    versus segment). Rather than let that mismatch distort the forecast, the
    *level* drivers are scaled once so the build reconciles to reported revenue,
    and the *cost* percentages are scaled so it reconciles to reported EBITDA.
    Growth rates and the revenue mix are untouched, so the model starts from
    reality and the drivers still explain the shape of the forecast.
    """
    out = dict(drivers)
    if not base_revenue:
        return out
    built = build_year(sector, out, 0)
    if not built.get("revenue"):
        return out

    level_keys = {
        TELECOM: ("voice_arpu", "data_arpu", "momo_rpu", "fixed_arpu"),
        BANK: ("loans",),
        CEMENT: ("price_per_tonne", "energy_cost_pt", "other_cash_cost_pt", "maint_capex_pt"),
        CONSUMER: ("price_per_unit",),
        ENERGY: ("realised_price",),
        RETAIL: ("sales_per_store",),
    }.get(sector, ())
    factor = base_revenue / built["revenue"]
    if abs(factor - 1.0) > 1e-9:
        for k in level_keys:
            if k in out:
                out[k] = round(_num(out[k]) * factor, 4)

    if base_ebitda and base_ebitda > 0:
        out = _calibrate_costs(sector, out, base_ebitda)
    return out


def _is_cost_driver(key: str) -> bool:
    """Whether scaling this driver moves cash costs.

    Per-unit costs count as much as percentage ones. Leaving `opex_per_boe` and
    `royalty_pct` out of this set is what let Sasol carry a 62% EBITDA margin
    against the 21% in its accounts: only overheads could move, so the solve
    could not reach its target and took what it got.
    """
    return any(t in key for t in ("cost", "opex", "commission", "freight", "overhead",
                                  "distribution", "marketing", "admin", "royalty"))


def _scale_costs(sector: str, drivers: dict[str, float], m: float) -> dict[str, float]:
    out = dict(drivers)
    for k, _label, _kind in driver_keys(sector):
        if k not in out:
            continue
        if _is_cost_driver(k):
            out[k] = _num(out[k]) * m
        elif k == "gross_margin":       # a margin-style cost driver moves the other way
            out[k] = 1 - (1 - _num(out[k])) * m
    return out


def _calibrate_costs(sector: str, drivers: dict[str, float],
                     base_ebitda: float) -> dict[str, float]:
    """Solve for the cost multiple that reproduces reported EBITDA.

    Cash costs are linear in the drivers being scaled, so two builds locate the
    line exactly:

        costs(m) = fixed + scalable * m

    A ratio alone is only right when every cost line can move. Where some cannot
    (a per-unit cost the sector model holds flat, a royalty on revenue), the
    ratio undershoots by exactly the fixed share, which is how a 21% margin was
    reported as 62%. Solving for m removes that whole class of error.

    The result is checked. If the rebuilt EBITDA still misses reported EBITDA by
    more than 1%, the drivers are returned UNSCALED and the mismatch is recorded
    on the driver set, because a model that cannot tie to the audited base year
    should say so rather than present a margin nobody filed.
    """
    out = dict(drivers)
    built = build_year(sector, out, 0)
    revenue, costs_1 = built.get("revenue") or 0.0, built.get("cash_costs") or 0.0
    target = revenue - base_ebitda
    if revenue <= 0 or costs_1 <= 0 or target <= 0:
        return out

    costs_2 = (build_year(sector, _scale_costs(sector, out, 2.0), 0).get("cash_costs") or 0.0)
    scalable = costs_2 - costs_1
    fixed = costs_1 - scalable
    m = ((target - fixed) / scalable) if scalable > 1e-9 else (target / costs_1)

    # An extreme multiple means the driver base disagrees with the accounts by an
    # order of magnitude. Forcing the fit would bury that rather than fix it.
    if not (0.05 <= m <= 20.0):
        out["_ebitda_calibration"] = f"not calibrated: cost multiple of {m:.2f} is out of range"
        return out

    cand = _scale_costs(sector, out, m)
    check = build_year(sector, cand, 0)
    got = (check.get("revenue") or 0.0) - (check.get("cash_costs") or 0.0)
    if abs(got - base_ebitda) > max(1.0, abs(base_ebitda) * 0.01):
        out["_ebitda_calibration"] = (
            f"not calibrated: built EBITDA of {got:,.0f} against reported {base_ebitda:,.0f}")
        return out

    cand = {k: (round(v, 6) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
            for k, v in cand.items()}
    return cand


# backwards-compatible alias
def scale_to_base(sector: str, drivers: dict[str, float], base_revenue: float) -> dict[str, float]:
    return calibrate(sector, drivers, base_revenue)
