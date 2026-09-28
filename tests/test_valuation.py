import pytest

from aeon_nimbus import valuation as v

FIN = {"revenue": 1000.0, "net_debt": 200.0, "shares_outstanding": 100.0}
ASSUMP = {"years": 5, "revenue_growth": 0.08, "ebitda_margin": 0.30, "d_and_a_pct_sales": 0.05,
          "tax_rate": 0.30, "capex_pct_sales": 0.06, "working_capital_pct_sales": 0.02,
          "wacc": 0.15, "terminal_growth": 0.03, "terminal_multiple": 8.0}


def test_blended_dcf_is_average_of_perpetuity_and_exit():
    out = v.blended_dcf(FIN, ASSUMP)
    assert out["value_per_share"] == pytest.approx(
        0.5 * out["perpetuity_value_per_share"] + 0.5 * out["exit_multiple_value_per_share"], rel=1e-9)
    assert out["value_per_share"] > 0


def test_comparable_values():
    out = v.comparable_values({"ebitda": 100.0, "net_income": 40.0},
                              {"ev_ebitda": 6.0, "pe": 12.0}, shares=10.0, net_debt=50.0)
    assert out["ev_ebitda"] == pytest.approx((6 * 100 - 50) / 10)  # 55.0
    assert out["pe"] == pytest.approx(12 * 40 / 10)                 # 48.0


def test_peer_medians():
    m = v.peer_medians([{"ev_ebitda": 5, "pe": 10}, {"ev_ebitda": 6, "pe": 12}, {"ev_ebitda": 7, "pe": 14}])
    assert m["ev_ebitda"] == 6
    assert m["pe"] == 12


def test_wacc_build():
    w = v.wacc_build(risk_free=0.12, equity_risk_premium=0.10, beta=0.9,
                     cost_of_debt=0.09, tax_rate=0.25, debt_weight=0.2)
    assert w["cost_of_equity"] == pytest.approx(0.12 + 0.9 * 0.10, rel=1e-9)  # 0.21
    assert w["wacc"] == pytest.approx(0.21 * 0.8 + 0.09 * 0.75 * 0.2, rel=1e-6)  # 0.1815


def test_football_field_target_and_upside():
    ff = v.football_field([{"method": "DCF", "low": 50.0, "high": 70.0},
                           {"method": "EV/EBITDA comps", "low": 45.0, "high": 60.0}],
                          current_price=48.0)
    assert ff["target_price"] == pytest.approx(60.0)     # DCF midpoint
    assert ff["upside"] == pytest.approx(60 / 48 - 1)     # 0.25
    assert len(ff["methods"]) == 2


def test_rating_from_upside():
    assert v.rating_from_upside(0.25) == "Buy"
    assert v.rating_from_upside(0.10) == "Accumulate"
    assert v.rating_from_upside(-0.05) == "Hold"
    assert v.rating_from_upside(-0.30) == "Reduce"
    assert v.rating_from_upside(None) == "Under review"


def test_sensitivity_grid_shape():
    grid = v.sensitivity(FIN, ASSUMP, wacc_deltas=[-0.02, 0.0, 0.02], exit_multiples=[6.0, 8.0, 10.0])
    assert len(grid) == 3 and all(len(r) == 3 for r in grid)
    # higher exit multiple -> higher value, along the base-WACC row
    assert grid[1][2] > grid[1][0]
