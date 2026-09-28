import pytest

from aeon_nimbus import forecast as fc

HIST = [
    {"fy": "FY2021", "revenue": 1000.0, "ebitda": 300.0, "ebit": 250.0, "net_income": 150.0},
    {"fy": "FY2022", "revenue": 1100.0, "ebitda": 330.0, "ebit": 275.0, "net_income": 170.0},
    {"fy": "FY2023", "revenue": 1210.0, "ebitda": 363.0, "ebit": 300.0, "net_income": 190.0},
]


def test_revenue_cagr():
    assert fc.revenue_cagr(HIST) == pytest.approx((1210 / 1000) ** (1 / 2) - 1)  # 10%


def test_project_shape_and_growth():
    rows = fc.project(HIST, years=5, shares=100.0)
    assert len(rows) == 5
    assert rows[0]["fy"] == "FY2024E" and rows[-1]["fy"] == "FY2028E"
    # revenue compounds at the (clamped) CAGR of 10%
    assert rows[0]["revenue"] == pytest.approx(1210 * 1.10, rel=1e-6)
    assert rows[1]["revenue"] > rows[0]["revenue"]
    assert "eps" in rows[0]


def test_project_uses_latest_margin():
    rows = fc.project(HIST, years=1)
    # EBITDA margin defaults to the latest year (363/1210 = 30%)
    assert rows[0]["ebitda_margin"] == pytest.approx(0.30, rel=1e-6)
    assert rows[0]["ebitda"] == pytest.approx(rows[0]["revenue"] * 0.30, rel=1e-6)


def test_project_empty():
    assert fc.project([]) == []
