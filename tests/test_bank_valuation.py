import pytest

from aeon_nimbus import sector_analytics as sa


def test_cost_of_equity_capm_plus_country():
    # rf 10% + beta 1.1 * ERP 5.5% + country risk 3.0% = 19.05%
    coe = sa.cost_of_equity(risk_free=0.10, beta=1.1, equity_risk_premium=0.055, country_risk_premium=0.03)
    assert coe == pytest.approx(0.1905, rel=1e-9)


def test_justified_pb_gordon():
    pb = sa.justified_pb(roe=0.225, cost_of_equity=0.18, growth=0.10)
    assert pb == pytest.approx(1.5625, rel=1e-9)


def test_justified_pb_requires_coe_above_growth():
    with pytest.raises(ValueError):
        sa.justified_pb(roe=0.20, cost_of_equity=0.10, growth=0.10)


def test_fair_value_from_pb():
    assert sa.fair_value_per_share_pb(book_value_per_share=40.0, justified_pb=1.5625) == pytest.approx(62.5, rel=1e-9)


def test_residual_income_zero_when_roe_equals_coe():
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
    assert v > 100.0


def test_dividend_discount_value():
    assert sa.dividend_discount_value(dividend_next=5.0, cost_of_equity=0.18, growth=0.10) == pytest.approx(62.5, rel=1e-9)


def test_bank_kpis():
    k = sa.bank_kpis({
        "net_interest_income": 141630, "average_earning_assets": 1500000,
        "total_operating_income": 200000, "operating_expenses": 84000,
        "net_income": 75548, "total_equity": 326104, "total_assets": 1970991,
        "gross_loans": 900000, "non_performing_loans": 108000, "customer_deposits": 1400000,
        "loan_impairment_charge": 18000,
    })
    assert k["nim"] == pytest.approx(141630 / 1500000, rel=1e-6)
    assert k["cost_to_income"] == pytest.approx(84000 / 200000, rel=1e-6)
    assert k["roe"] == pytest.approx(75548 / 326104, rel=1e-6)
    assert k["npl_ratio"] == pytest.approx(108000 / 900000, rel=1e-6)
    assert k["loan_to_deposit"] == pytest.approx(900000 / 1400000, rel=1e-6)
    assert k["cost_of_risk"] == pytest.approx(18000 / 900000, rel=1e-6)


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
