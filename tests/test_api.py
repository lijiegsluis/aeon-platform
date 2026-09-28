import os
import tempfile
import time

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mktemp(suffix='.db')}"  # isolated test DB
os.environ.setdefault("ADMIN_EMAIL",    "tests@aeon.test")
os.environ.setdefault("ADMIN_USERNAME", "testadmin")
os.environ.setdefault("ADMIN_PASSWORD", "tests-bootstrap-9271")

from fastapi.testclient import TestClient  # noqa: E402

from aeon_nimbus.api import app  # noqa: E402


def _sign_in(c):
    r = c.post("/api/auth/login", json={
        "identifier": os.environ["ADMIN_EMAIL"],
        "password":   os.environ["ADMIN_PASSWORD"],
    })
    assert r.status_code == 200, f"sign in failed: {r.text}"
    return c


def _wait(c, jid, timeout=30):
    for _ in range(int(timeout / 0.25)):
        st = c.get(f"/api/jobs/{jid}").json()["status"]
        if st in ("complete", "failed"):
            return st
        time.sleep(0.25)
    return "timeout"


def test_full_click_through_workflow():
    with TestClient(app) as c:  # lifespan seeds the DB from files
        _sign_in(c)
        assert c.get("/api/health").json()["status"] == "ok"
        res = c.get("/api/companies", params={"q": "bamburi"}).json()["results"]
        assert res and res[0]["ticker"] == "BAMB"
        cid = res[0]["id"]
        det = c.get(f"/api/companies/{cid}").json()
        assert det["accounting_standard"] == "IFRS" and len(det["years"]) == 5

        pid = c.post("/api/projects", json={"company_id": cid}).json()["project_id"]
        ejid = c.post(f"/api/projects/{pid}/extract").json()["job_id"]
        assert _wait(c, ejid) == "complete"
        facts = c.get(f"/api/projects/{pid}/facts").json()["facts"]
        assert len(facts) > 10

        mjid = c.post(f"/api/projects/{pid}/generate-model").json()["job_id"]
        assert _wait(c, mjid) == "complete"
        ctl = c.get(f"/api/projects/{pid}/controls").json()
        assert ctl["data_quality"]["score"] > 0
        c.post(f"/api/projects/{pid}/approve-export")  # clear critical-control gate
        dl = c.get(f"/api/projects/{pid}/model/download")
        assert dl.status_code == 200
        assert dl.headers["content-type"].startswith("application/vnd.openxmlformats")
        assert len(dl.content) > 5000  # a real workbook
        audit = c.get(f"/api/projects/{pid}/audit").json()["audit"]
        assert any(a["action"] == "model_generated" for a in audit)


def test_override_request_and_decision():
    with TestClient(app) as c:
        _sign_in(c)
        cid = c.get("/api/companies", params={"q": "equity"}).json()["results"][0]["id"]
        pid = c.post("/api/projects", json={"company_id": cid}).json()["project_id"]
        oid = c.post(f"/api/projects/{pid}/overrides",
                     json={"control_id": "C04", "rationale": "reconciled manually", "evidence": "AR p.141"}).json()["override_id"]
        out = c.post(f"/api/overrides/{oid}/decide", json={"decision": "approved"}).json()
        assert out["status"] == "approved" and out["approved_by"] == "senior_analyst"


def test_download_blocked_before_generation():
    with TestClient(app) as c:
        _sign_in(c)
        cid = c.get("/api/companies", params={"q": "safaricom"}).json()["results"][0]["id"]
        pid = c.post("/api/projects", json={"company_id": cid}).json()["project_id"]
        assert c.get(f"/api/projects/{pid}/model/download").status_code == 409  # no model yet
