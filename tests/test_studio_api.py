"""Data Studio API: upload -> stage -> threshold -> review -> recompute -> export.

Runs against an isolated temp SQLite DB so it never mutates the working
data/platform.db that the live app serves.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

CSV = ("Item,FY2025,FY2024,FY2023\n"
       "Revenue,350000,335250,310900\n"
       "EBITDA,190000,180100,168400\n"
       "Profit for the year,72000,69800,62100\n"
       "Total assets,560000,545000,520000\n"
       "Total equity,220000,210000,198000\n"
       "Borrowings,118000,120000,131000\n"
       "Cash and cash equivalents,45000,42000,38000\n").encode()


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    from aeon_nimbus import db as D
    url = f"sqlite:///{tmp_path / 't.db'}"
    eng = create_engine(url, connect_args={"check_same_thread": False}, future=True)
    Sess = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)
    monkeypatch.setattr(D, "engine", eng)
    monkeypatch.setattr(D, "SessionLocal", Sess)
    yield


def client():
    from fastapi.testclient import TestClient
    from aeon_nimbus.api import app
    from tests.conftest import sign_in
    return sign_in(TestClient(app))


def _scom(c):
    return c.get("/api/companies?q=safaricom").json()["results"][0]["id"]


def test_upload_stage_review_recompute_export():
    with client() as c:
        cid = _scom(c)
        # high threshold -> everything pending, nothing auto-merged into the model
        r = c.post(f"/api/studio/companies/{cid}/upload",
                   files={"file": ("t.csv", CSV, "text/csv")}, data={"threshold": "0.99"})
        assert r.status_code == 200
        j = r.json()
        assert j["proposed"] > 0 and j["auto_accepted"] == 0 and j["pending"] == j["proposed"]
        # review table lists the pending proposals with their sources
        pr = c.get(f"/api/studio/companies/{cid}/proposed").json()["proposed"]
        assert any(x["status"] == "pending" for x in pr)
        assert all(x["source"] for x in pr)                       # never unsourced
        # approve one -> merged, fresh panes returned
        pid = next(x["id"] for x in j["items"] if x["status"] == "pending")
        rv = c.post(f"/api/studio/proposed/{pid}/review", json={"decision": "approved"})
        assert rv.status_code == 200 and rv.json()["status"] == "approved"
        assert rv.json()["deep"]["years"]
        # recompute returns deep + data quality
        rc = c.post(f"/api/studio/companies/{cid}/recompute").json()
        assert rc["deep"]["years"] and "data_quality" in rc
        # export a real workbook
        ex = c.get(f"/api/studio/companies/{cid}/export")
        assert ex.status_code == 200
        assert ex.headers["content-type"].startswith("application/vnd.openxml")
        assert len(ex.content) > 2000


def test_threshold_auto_accepts_below_bar():
    with client() as c:
        cid = _scom(c)
        r = c.post(f"/api/studio/companies/{cid}/upload",
                   files={"file": ("t.csv", CSV, "text/csv")}, data={"threshold": "0.5"})
        j = r.json()
        assert j["auto_accepted"] == j["proposed"] and j["pending"] == 0


def test_web_collect_cannot_downgrade_audited_but_upload_can():
    """A lower-confidence WEB figure must never auto-overwrite a higher-confidence
    (audited) one — it waits for review. A deliberate UPLOAD is not gated this way."""
    from aeon_nimbus import db as D, studio_core
    D.init_db()
    with D.SessionLocal() as s:
        co = D.Company(slug="_g", name="G", ticker="G",
                       extracted={"currency": "KES", "unit": "millions",
                                  "financials": [{"fy": "FY2026", "ebitda": 220262.0, "confidence": 0.95}]})
        s.add(co); s.commit()
        prop = [{"fy": "FY2026", "item": "ebitda", "value": 233695.0, "confidence": 0.80,
                 "source": "aggregator", "statement": "income_statement"}]
        # web collection, threshold below the incoming conf -> still held pending (downgrade guard)
        web = studio_core.stage_proposals(s, co, prop, origin="web", threshold=0.75)
        s.refresh(co)
        assert web["auto_accepted"] == 0 and web["pending"] == 1
        assert co.extracted["financials"][0]["ebitda"] == 220262.0   # audited untouched
        # same figure via upload -> user-initiated, auto-merges
        up = studio_core.stage_proposals(s, co, prop, origin="upload", threshold=0.75)
        s.refresh(co)
        assert up["auto_accepted"] == 1
        assert co.extracted["financials"][0]["ebitda"] == 233695.0


def test_upload_without_line_items_is_rejected():
    with client() as c:
        cid = _scom(c)
        r = c.post(f"/api/studio/companies/{cid}/upload",
                   files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")}, data={"threshold": "0.85"})
        assert r.status_code == 422


def test_create_company_is_modular_and_deduped():
    """The '+ Add a company' flow: create an empty, source-disciplined shell,
    dedupe slugs, reject a blank name, and surface it in the live universe."""
    with client() as c:
        before = len(c.get("/api/companies").json()["results"])
        r = c.post("/api/studio/companies", json={"name": "KenGen PLC", "ticker": "KEGN",
                                                  "country": "Kenya", "sector": "Utilities", "currency": "KES"})
        assert r.status_code == 200
        j = r.json()
        assert j["slug"] == "kengen_plc" and j["name"] == "KenGen PLC"
        # same name again -> new, non-colliding slug
        assert c.post("/api/studio/companies", json={"name": "KenGen PLC"}).json()["slug"] == "kengen_plc_2"
        # blank name rejected
        assert c.post("/api/studio/companies", json={"name": "   "}).status_code == 400
        # shell has no financials yet (nothing fabricated) but is a real, selectable company
        after = c.get("/api/companies").json()["results"]
        assert len(after) == before + 2
        assert any(x["slug"] == "kengen_plc" for x in after)


def test_reference_search_and_delete():
    """Type-ahead directory search finds companies (marking those already covered),
    and a company can be removed from coverage."""
    with client() as c:
        # directory search — a not-yet-covered blue chip and a covered one
        r = c.get("/api/reference/search?q=gold").json()["results"]
        assert any(x["ticker"] == "GFI" and not x["in_coverage"] for x in r)
        cov = c.get("/api/reference/search?q=safaric").json()["results"]
        assert any(x["in_coverage"] for x in cov)
        assert c.get("/api/reference/search?q=z").json()["results"] == []   # <2 chars → nothing
        # create then delete
        cid = c.post("/api/studio/companies", json={"name": "Test Co", "ticker": "TST"}).json()["id"]
        assert c.get("/api/companies?q=test co").json()["results"]
        d = c.delete(f"/api/studio/companies/{cid}")
        assert d.status_code == 200 and d.json()["deleted"] == cid
        assert not c.get("/api/companies?q=test co").json()["results"]
        assert c.delete(f"/api/studio/companies/{cid}").status_code == 404   # already gone


def test_collect_endpoint_returns_job(monkeypatch):
    import aeon_nimbus.jobs as J
    monkeypatch.setattr(J, "start_collection", lambda cid, thr: 4242)
    with client() as c:
        cid = _scom(c)
        r = c.post(f"/api/studio/companies/{cid}/collect", json={"threshold": 0.85})
        assert r.status_code == 200 and r.json()["job_id"] == 4242


def test_live_dashboard_served():
    with client() as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "paneStudio" in r.text and '"live": true' in r.text


def test_scenarios_get_edit_persist():
    with client() as c:
        cid = _scom(c)
        r = c.get(f"/api/studio/companies/{cid}/scenarios").json()
        assert r["scenarios"]["kind"] == "dcf"
        v = r["valuation"]["per_scenario"]
        assert v["Bull"]["value_per_share"] > v["Base"]["value_per_share"] > v["Bear"]["value_per_share"]
        # edit base growth + switch active scenario, save
        sc = r["scenarios"]
        sc["sets"]["Base"]["revenue_growth"] = 0.14
        sc["selected"] = "Bull"
        r2 = c.post(f"/api/studio/companies/{cid}/scenarios", json={"scenarios": sc}).json()
        assert r2["scenarios"]["selected"] == "Bull"
        assert r2["valuation"]["active"]["value_per_share"] == v["Bull"]["value_per_share"]
        # persisted
        r3 = c.get(f"/api/studio/companies/{cid}/scenarios").json()
        assert r3["scenarios"]["sets"]["Base"]["revenue_growth"] == 0.14
        assert r3["scenarios"]["selected"] == "Bull"
