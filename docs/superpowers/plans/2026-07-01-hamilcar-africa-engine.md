# Hamilcar Africa Engine (Plan 1: analytics foundation + bank valuation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `aeon_nimbus` engine by reusing the proven DCF/analytics from `Sample_ER_algorithm` and building the credibility-critical new piece — a sector-aware valuation router with a real bank model (banks valued on justified P/B, residual income and DDM, never DCF).

**Architecture:** Reuse `analytics.py` (DCF, WACC, liquidity) and `schemas.py`/`standardisation.py` as-is inside `aeon_nimbus/`. Add `sector_analytics.py` with (1) cost of equity, (2) justified P/B, (3) residual-income valuation, (4) Gordon DDM, (5) bank KPIs, and (6) a `value_company` router that dispatches on `valuation_model` (`dcf` → existing `run_dcf`; `bank_pb_ddm` → bank model). Pure functions, fully unit-tested.

**Tech Stack:** Python 3.9+, pytest. No new third-party deps (pandas/numpy/plotly already used by the engine; the valuation math is stdlib).

**Roadmap (later plans):** Plan 2 `report_initiation.py` (full initiation report generator). Plan 3 `universe.py` + templates (multi-company dashboard + screener). Plan 4 `seeds/` + full 5-year statement extraction across the 30-company universe.

---

### Task 1: Scaffold the engine package (reuse proven modules)

**Files:**
- Create: `aeon_nimbus/analytics.py` (copied from `../Sample_ER_algorithm/hamilcar_platform/analytics.py`)
- Create: `aeon_nimbus/schemas.py` (copied)
- Create: `aeon_nimbus/standardisation.py` (copied)
- Create: `requirements.txt`
- Test: `tests/test_smoke.py`

- [ ] **Step 1: Copy the three reusable modules**

```bash
cd "/Users/nikolasdionsavio/Documents/Personal Projects/Hamilcar_project/ER_Safaricom_algo"
SRC=../Sample_ER_algorithm/hamilcar_platform
cp "$SRC/analytics.py" "$SRC/schemas.py" "$SRC/standardisation.py" aeon_nimbus/
```

- [ ] **Step 2: Write the failing smoke test**

```python
# tests/test_smoke.py
from aeon_nimbus import analytics


def test_dcf_reused_runs():
    out = analytics.run_dcf(
        {"revenue": 1000.0, "net_debt": 200.0, "shares_outstanding": 100.0},
        {"years": 5, "revenue_growth": 0.08, "ebitda_margin": 0.30, "wacc": 0.17, "terminal_growth": 0.04},
    )
    assert out["enterprise_value"] > 0
    assert out["value_per_share"] is not None
```

- [ ] **Step 3: Run it (expect PASS — the module is a straight copy)**

Run: `python3 -m pytest tests/test_smoke.py -v`
Expected: PASS

- [ ] **Step 4: Write requirements.txt**

```
pandas
numpy
plotly
jinja2
pymupdf
requests
pytest
```

- [ ] **Step 5: Commit**

```bash
git init -q 2>/dev/null; git add -A && git commit -q -m "feat(engine): scaffold aeon_nimbus, reuse DCF/analytics/schemas"
```

---

### Task 2: Cost of equity (CAPM + country risk premium)

**Files:**
- Create: `aeon_nimbus/sector_analytics.py`
- Test: `tests/test_bank_valuation.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bank_valuation.py
import pytest
from aeon_nimbus import sector_analytics as sa


def test_cost_of_equity_capm_plus_country():
    # rf 10% + beta 1.1 * ERP 5.5% + country risk 3.0% = 19.05%
    coe = sa.cost_of_equity(risk_free=0.10, beta=1.1, equity_risk_premium=0.055, country_risk_premium=0.03)
    assert coe == pytest.approx(0.1905, rel=1e-9)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_bank_valuation.py::test_cost_of_equity_capm_plus_country -v`
Expected: FAIL (module/function not defined)

- [ ] **Step 3: Implement**

```python
# aeon_nimbus/sector_analytics.py
"""Sector-aware valuation: banks are valued as banks (justified P/B, residual
income, DDM), never DCF. Non-financials route to the existing run_dcf."""

from __future__ import annotations

from typing import Any

from aeon_nimbus import analytics


def cost_of_equity(risk_free: float, beta: float, equity_risk_premium: float,
                   country_risk_premium: float = 0.0) -> float:
    """CAPM cost of equity plus an explicit country-risk premium (frontier markets)."""
    return risk_free + beta * equity_risk_premium + country_risk_premium
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_bank_valuation.py::test_cost_of_equity_capm_plus_country -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aeon_nimbus/sector_analytics.py tests/test_bank_valuation.py && git commit -q -m "feat(bank): cost of equity (CAPM + country risk)"
```

---

### Task 3: Justified P/B and fair value per share

**Files:**
- Modify: `aeon_nimbus/sector_analytics.py`
- Test: `tests/test_bank_valuation.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_justified_pb_gordon():
    # (ROE - g) / (COE - g) = (0.225 - 0.10) / (0.18 - 0.10) = 1.5625
    pb = sa.justified_pb(roe=0.225, cost_of_equity=0.18, growth=0.10)
    assert pb == pytest.approx(1.5625, rel=1e-9)


def test_justified_pb_requires_coe_above_growth():
    with pytest.raises(ValueError):
        sa.justified_pb(roe=0.20, cost_of_equity=0.10, growth=0.10)


def test_fair_value_from_pb():
    # 1.5625x book on BVPS 40 = 62.5
    assert sa.fair_value_per_share_pb(book_value_per_share=40.0, justified_pb=1.5625) == pytest.approx(62.5, rel=1e-9)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_bank_valuation.py -k "pb" -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
def justified_pb(roe: float, cost_of_equity: float, growth: float) -> float:
    """Warranted price/book for a bank: (ROE - g) / (COE - g)."""
    if cost_of_equity <= growth:
        raise ValueError("cost_of_equity must exceed growth for a finite justified P/B")
    return (roe - growth) / (cost_of_equity - growth)


def fair_value_per_share_pb(book_value_per_share: float, justified_pb: float) -> float:
    """Fair value per share = justified P/B x book value per share."""
    return justified_pb * book_value_per_share
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/test_bank_valuation.py -k "pb" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add aeon_nimbus/sector_analytics.py tests/test_bank_valuation.py && git commit -q -m "feat(bank): justified P/B + fair value per share"
```

---

### Task 4: Residual-income (excess-return) valuation

**Files:**
- Modify: `aeon_nimbus/sector_analytics.py`
- Test: `tests/test_bank_valuation.py`

- [ ] **Step 1: Write the failing test**

```python
def test_residual_income_zero_when_roe_equals_coe():
    # If ROE == COE every year, excess return is zero, so value == starting book.
    v = sa.residual_income_value(
        book_value_per_share=100.0, roe_path=[0.15, 0.15, 0.15], cost_of_equity=0.15,
        payout_ratio=0.4, terminal_growth=0.0,
    )
    assert v == pytest.approx(100.0, abs=1e-6)


def test_residual_income_adds_value_when_roe_above_coe():
    v = sa.residual_income_value(
        book_value_per_share=100.0, roe_path=[0.20, 0.20, 0.20], cost_of_equity=0.15,
        payout_ratio=0.4, terminal_growth=0.03,
    )
    assert v > 100.0  # positive excess return lifts value above book
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_bank_valuation.py -k "residual" -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
def residual_income_value(book_value_per_share: float, roe_path: list[float],
                          cost_of_equity: float, payout_ratio: float,
                          terminal_growth: float = 0.0) -> float:
    """Value per share = starting book + PV of forecast residual income
    (excess return over the cost of equity) + PV of a terminal residual income.

    BVPS grows by retained earnings: BVPS_t = BVPS_{t-1} x (1 + ROE_t x (1 - payout)).
    Residual income_t = (ROE_t - COE) x BVPS_{t-1}.
    """
    if cost_of_equity <= terminal_growth:
        raise ValueError("cost_of_equity must exceed terminal_growth")
    bvps = book_value_per_share
    pv_ri = 0.0
    last_ri = 0.0
    for t, roe in enumerate(roe_path, start=1):
        ri = (roe - cost_of_equity) * bvps
        pv_ri += ri / (1 + cost_of_equity) ** t
        last_ri = ri
        bvps = bvps * (1 + roe * (1 - payout_ratio))
    terminal_ri = last_ri * (1 + terminal_growth)
    pv_terminal = (terminal_ri / (cost_of_equity - terminal_growth)) / (1 + cost_of_equity) ** len(roe_path)
    return book_value_per_share + pv_ri + pv_terminal
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_bank_valuation.py -k "residual" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add aeon_nimbus/sector_analytics.py tests/test_bank_valuation.py && git commit -q -m "feat(bank): residual-income (excess-return) valuation"
```

---

### Task 5: Bank KPIs

**Files:**
- Modify: `aeon_nimbus/sector_analytics.py`
- Test: `tests/test_bank_valuation.py`

- [ ] **Step 1: Write the failing test**

```python
def test_bank_kpis():
    k = sa.bank_kpis({
        "net_interest_income": 141630, "average_earning_assets": 1500000,
        "total_operating_income": 200000, "operating_expenses": 84000,
        "net_income": 75548, "total_equity": 326104, "total_assets": 1970991,
        "gross_loans": 900000, "non_performing_loans": 108000, "customer_deposits": 1400000,
        "loan_impairment_charge": 18000,
    })
    assert k["nim"] == pytest.approx(141630 / 1500000, rel=1e-6)          # 9.44%
    assert k["cost_to_income"] == pytest.approx(84000 / 200000, rel=1e-6)  # 42.0%
    assert k["roe"] == pytest.approx(75548 / 326104, rel=1e-6)             # 23.2%
    assert k["npl_ratio"] == pytest.approx(108000 / 900000, rel=1e-6)      # 12.0%
    assert k["loan_to_deposit"] == pytest.approx(900000 / 1400000, rel=1e-6)
    assert k["cost_of_risk"] == pytest.approx(18000 / 900000, rel=1e-6)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_bank_valuation.py::test_bank_kpis -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return a / b


def bank_kpis(f: dict[str, float]) -> dict[str, float | None]:
    """Standard bank analysis ratios from a financial-line dict."""
    return {
        "nim": _safe_div(f.get("net_interest_income"), f.get("average_earning_assets")),
        "cost_to_income": _safe_div(f.get("operating_expenses"), f.get("total_operating_income")),
        "roe": _safe_div(f.get("net_income"), f.get("total_equity")),
        "roa": _safe_div(f.get("net_income"), f.get("total_assets")),
        "npl_ratio": _safe_div(f.get("non_performing_loans"), f.get("gross_loans")),
        "loan_to_deposit": _safe_div(f.get("gross_loans"), f.get("customer_deposits")),
        "cost_of_risk": _safe_div(f.get("loan_impairment_charge"), f.get("gross_loans")),
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_bank_valuation.py::test_bank_kpis -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add aeon_nimbus/sector_analytics.py tests/test_bank_valuation.py && git commit -q -m "feat(bank): bank KPI set (NIM, cost/income, ROE, NPL, cost of risk)"
```

---

### Task 6: Sector valuation router

**Files:**
- Modify: `aeon_nimbus/sector_analytics.py`
- Test: `tests/test_bank_valuation.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_router_dispatches_bank_to_pb_ddm():
    out = sa.value_company(
        valuation_model="bank_pb_ddm",
        inputs={"book_value_per_share": 40.0, "roe": 0.225, "cost_of_equity": 0.18,
                "growth": 0.10, "roe_path": [0.22, 0.21, 0.20], "payout_ratio": 0.4,
                "terminal_growth": 0.05},
    )
    assert out["method"] == "bank_pb_ddm"
    assert out["justified_pb"] == pytest.approx(1.5625, rel=1e-9)
    assert out["fair_value_pb"] == pytest.approx(62.5, rel=1e-9)
    assert out["residual_income_value"] > 0


def test_router_dispatches_nonfinancial_to_dcf():
    out = sa.value_company(
        valuation_model="dcf",
        inputs={"financials": {"revenue": 1000.0, "net_debt": 200.0, "shares_outstanding": 100.0},
                "assumptions": {"years": 5, "revenue_growth": 0.08, "ebitda_margin": 0.30, "wacc": 0.17, "terminal_growth": 0.04}},
    )
    assert out["method"] == "dcf"
    assert out["value_per_share"] is not None
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_bank_valuation.py -k "router" -v`
Expected: FAIL

- [ ] **Step 3: Implement**

```python
def value_company(valuation_model: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Route to the correct valuation for the company's sector."""
    if valuation_model == "bank_pb_ddm":
        pb = justified_pb(inputs["roe"], inputs["cost_of_equity"], inputs["growth"])
        return {
            "method": "bank_pb_ddm",
            "justified_pb": pb,
            "fair_value_pb": fair_value_per_share_pb(inputs["book_value_per_share"], pb),
            "residual_income_value": residual_income_value(
                inputs["book_value_per_share"], inputs["roe_path"], inputs["cost_of_equity"],
                inputs["payout_ratio"], inputs.get("terminal_growth", 0.0)),
        }
    if valuation_model == "dcf":
        dcf = analytics.run_dcf(inputs["financials"], inputs["assumptions"])
        return {"method": "dcf", **dcf}
    raise ValueError(f"unknown valuation_model {valuation_model!r}")
```

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add aeon_nimbus/sector_analytics.py tests/test_bank_valuation.py && git commit -q -m "feat(engine): sector valuation router (bank vs DCF)"
```

---

## Self-review notes

- **Spec coverage:** implements §5 (sector-aware analytics + bank model) and the reuse half of §4. Report generator (§6), universe screener (§8), seeds + extraction (§9-10) are Plans 2-4.
- **No placeholders:** every step has runnable code/commands and expected output.
- **Type consistency:** `cost_of_equity`, `justified_pb`, `residual_income_value`, `fair_value_per_share_pb`, `bank_kpis`, `value_company` names are consistent across tasks and the router calls them with the same signatures defined earlier.
