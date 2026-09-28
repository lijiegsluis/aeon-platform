from aeon_nimbus import analytics


def test_dcf_reused_runs():
    out = analytics.run_dcf(
        {"revenue": 1000.0, "net_debt": 200.0, "shares_outstanding": 100.0},
        {"years": 5, "revenue_growth": 0.08, "ebitda_margin": 0.30, "wacc": 0.17, "terminal_growth": 0.04},
    )
    assert out["enterprise_value"] > 0
    assert out["value_per_share"] is not None
