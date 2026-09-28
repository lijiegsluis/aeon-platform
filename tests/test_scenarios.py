"""Scenario engine + assumption-driven valuation (shared by studio + Excel)."""

import json

from aeon_nimbus import platform_data as pdata
from aeon_nimbus import scenarios as scn


def _scom():
    ext = json.load(open("data/extracted/safaricom_plc.json"))
    return sorted(ext["financials"], key=lambda f: f["fy"]), ext.get("market", {})


def test_default_scenarios_structure():
    fins, _ = _scom()
    sc = scn.default_scenarios(fins, country="Kenya", is_bank=False)
    assert sc["kind"] == "dcf" and sc["selected"] == "Base"
    assert set(sc["sets"]) == {"Bull", "Base", "Bear"}
    s = sc["sets"]
    assert s["Bull"]["revenue_growth"] > s["Base"]["revenue_growth"] > s["Bear"]["revenue_growth"]
    assert s["Bull"]["wacc"] < s["Base"]["wacc"] < s["Bear"]["wacc"]


def test_bank_scenarios():
    ext = json.load(open("data/extracted/absa_group.json"))
    fins = sorted(ext["financials"], key=lambda f: f["fy"])
    sc = scn.default_scenarios(fins, country="South Africa", is_bank=True, bank=ext.get("bank"))
    assert sc["kind"] == "bank"
    assert {"roe", "coe", "nim", "cost_of_risk", "car"} <= set(sc["sets"]["Base"])


def test_normalise_fills_and_clamps():
    fins, _ = _scom()
    partial = {"selected": "Nope", "sets": {"Base": {"revenue_growth": 0.5}}}
    n = scn.normalise(partial, fins, country="Kenya")
    assert n["selected"] == "Base"                 # unknown -> Base
    assert n["sets"]["Base"]["revenue_growth"] == 0.5   # user value kept
    assert "wacc" in n["sets"]["Base"]             # gaps filled from defaults
    assert "Bull" in n["sets"] and "Bear" in n["sets"]


def test_value_per_share_monotonic():
    fins, mkt = _scom()
    sc = scn.default_scenarios(fins, country="Kenya")
    per = pdata.scenario_values(fins, mkt, sc)["per_scenario"]
    assert per["Bull"]["value_per_share"] > per["Base"]["value_per_share"] > per["Bear"]["value_per_share"]


def test_dashboard_dcf_uses_scenario_engine():
    # deep_from_extracted's base value must equal the scenario engine's base VPS,
    # so the dashboard, studio and Excel agree.
    ext = json.load(open("data/extracted/safaricom_plc.json"))
    uni = {u["slug"]: u for u in json.load(open("data/universe.json"))}
    deep = pdata.deep_from_extracted(ext, uni["safaricom_plc"])
    fins, mkt = _scom()
    base = pdata.value_per_share(fins, mkt, scn.default_scenarios(fins, country="Kenya")["sets"]["Base"])
    assert abs(deep["dcf"]["base"]["value"] - base) < 0.01
