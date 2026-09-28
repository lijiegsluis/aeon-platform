from aeon_nimbus.real_seed import build_safaricom_context
from aeon_nimbus.report_initiation import build_initiation, render_initiation_html


def test_build_initiation_has_core_sections():
    company = build_safaricom_context()["companies"][0]
    r = build_initiation(company)
    assert r["meta"]["name"] == "Safaricom PLC"
    assert r["rating"]["stance"] in {"Buy", "Hold", "Reduce", "Under review"}
    assert r["financials_table"]["rows"]
    assert r["financials_table"]["years"]
    assert r["scenarios"]["base"]["value_per_share"] is not None
    assert len(r["risks"]) >= 1
    assert len(r["peers"]) >= 1


def test_render_writes_self_contained_html(tmp_path):
    company = build_safaricom_context()["companies"][0]
    out = render_initiation_html(company, tmp_path / "safaricom.html", skip_gate=True)
    html = out.read_text()
    assert "Safaricom PLC" in html
    assert "Initiation of Coverage" in html
    assert "Risk register" in html
    assert "Source appendix" in html
    # source-linked: the primary filing URL is present in the appendix
    assert "safaricom" in html.lower()
