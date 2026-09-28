from aeon_nimbus import controls as ctrl

GOOD = {
    "name": "Testco", "currency": "KES", "unit": "millions",
    "financials": [
        {"fy": "FY2023", "revenue": 100, "ebitda": 30, "ebit": 25, "net_income": 15,
         "total_assets": 200, "total_equity": 90, "total_debt": 60, "cash": 20, "net_debt": 40,
         "free_cash_flow": 12, "capex": 8, "source": "AR2025 p.10", "confidence": 0.95},
        {"fy": "FY2024", "revenue": 110, "ebitda": 33, "ebit": 27, "net_income": 17,
         "total_assets": 210, "total_equity": 95, "total_debt": 62, "cash": 22, "net_debt": 40,
         "free_cash_flow": 13, "capex": 9, "source": "AR2025 p.10", "confidence": 0.95},
        {"fy": "FY2025", "revenue": 121, "ebitda": 36, "ebit": 30, "net_income": 19,
         "total_assets": 220, "total_equity": 100, "total_debt": 64, "cash": 24, "net_debt": 40,
         "free_cash_flow": 15, "capex": 10, "source": "AR2025 p.10", "confidence": 0.95},
    ],
    "notes": {"capex": 10, "depreciation_amortisation": 6, "interest_expense": 5,
              "lease_liabilities": 3, "segments": [{"name": "Core", "value": 121}]},
    "sector_kpis": [{"name": "Volume", "value": 3.2}],
    "market": {"share_price": 50, "price_date": "2026-07-02", "shares_outstanding_m": 100},
    "data_quality": {"years_found": 3, "confidence": 0.95, "caveats": ""},
    "peers": [{"name": "PeerCo", "ticker": "PC"}],
}


def test_data_quality_score_in_range_and_banded():
    dq = ctrl.data_quality_score(GOOD)
    assert 0 <= dq["score"] <= 100
    assert dq["band"] in {"A", "B", "C", "D"}
    assert dq["export_allowed"] is True  # no critical failures


def test_net_debt_reconciliation_control():
    controls = ctrl.run_controls(GOOD)
    c04 = next(c for c in controls if c["control_id"] == "C04")
    assert c04["status"] == "pass"  # net_debt 40 == debt 64 - cash 24


def test_missing_source_fails_critical_and_blocks_export():
    bad = {**GOOD, "financials": [{**GOOD["financials"][0], "source": ""}] + GOOD["financials"][1:]}
    controls = ctrl.run_controls(bad)
    c02 = next(c for c in controls if c["control_id"] == "C02")
    assert c02["status"] == "fail" and c02["severity"] == ctrl.CRITICAL
    assert ctrl.data_quality_score(bad)["export_allowed"] is False


def test_missing_market_data_blocks_export():
    bad = {**GOOD, "market": {}}
    assert ctrl.data_quality_score(bad)["export_allowed"] is False
