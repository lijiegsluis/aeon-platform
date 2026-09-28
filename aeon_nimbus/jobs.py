"""Background job runner with the spec's status lifecycle (§16).

In-process ThreadPoolExecutor for dev (no Redis needed). The same task functions
run unchanged under Celery in production — register them as Celery tasks and
swap `_EXEC.submit` for `.delay()`. Job + project status transitions are
persisted so the API can poll progress.
"""

from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aeon_nimbus import controls as ctrl
from aeon_nimbus import db as D
from aeon_nimbus import ingest, studio_core
from aeon_nimbus.excel_model import compile_model

_EXEC = ThreadPoolExecutor(max_workers=4)

PROJECT_STATUSES = [
    "draft", "sources_loaded", "extraction_running", "extraction_complete",
    "review_required", "historical_approved", "model_generation_running",
    "model_validation_failed", "model_ready", "approved_for_export", "archived",
]


def _set_project(db, project, status: str) -> None:
    project.status = status
    db.add(project)
    db.commit()
    D.audit(db, "project_status", "project", project.id, status=status)


def _job_update(db, job, **kw) -> None:
    for k, v in kw.items():
        setattr(job, k, v)
    db.add(job)
    db.commit()


# --- extraction -----------------------------------------------------------

def start_extraction(project_id: int) -> int:
    with D.SessionLocal() as db:
        job = D.Job(project_id=project_id, kind="extraction", status="queued")
        db.add(job)
        db.commit()
        jid = job.id
    _EXEC.submit(_run_extraction, project_id, jid)
    return jid


def _run_extraction(project_id: int, jid: int) -> None:
    with D.SessionLocal() as db:
        job, project = db.get(D.Job, jid), db.get(D.Project, project_id)
        co = project.company
        try:
            _job_update(db, job, status="running", progress=10)
            _set_project(db, project, "sources_loaded")
            _set_project(db, project, "extraction_running")
            facts = db.query(D.FinancialFact).filter_by(company_id=co.id).all()
            _job_update(db, job, progress=60)
            _set_project(db, project, "extraction_complete")
            dq = ctrl.data_quality_score(co.extracted or {})
            low = [f for f in facts if (f.confidence or 1.0) < 0.85]
            _set_project(db, project, "review_required" if (low or dq["score"] < 70) else "historical_approved")
            _job_update(db, job, status="complete", progress=100,
                        result={"facts": len(facts), "dq_score": dq["score"], "needs_review": len(low)})
        except Exception as e:  # noqa: BLE001
            _job_update(db, job, status="failed", error=str(e))
            traceback.print_exc()


# --- web collection (Data Studio) -----------------------------------------

def start_collection(company_id: int, threshold: float) -> int:
    with D.SessionLocal() as db:
        co = db.get(D.Company, company_id)
        project = studio_core.project_for(db, co)
        job = D.Job(project_id=project.id, kind="web_collection", status="queued")
        db.add(job)
        db.commit()
        jid = job.id
    _EXEC.submit(_run_collection, company_id, threshold, jid)
    return jid


def _run_collection(company_id: int, threshold: float, jid: int) -> None:
    with D.SessionLocal() as db:
        job, co = db.get(D.Job, jid), db.get(D.Company, company_id)
        try:
            _job_update(db, job, status="running", progress=15)
            proposals = ingest.collect_web(studio_core.company_view(co))
            _job_update(db, job, progress=70)
            summary = studio_core.stage_proposals(db, co, proposals, "web", threshold)
            _job_update(db, job, status="complete", progress=100, result=summary)
            D.audit(db, "web_collection", "company", company_id,
                    proposed=summary["proposed"], auto=summary["auto_accepted"])
        except Exception as e:  # noqa: BLE001
            _job_update(db, job, status="failed", error=str(e))
            traceback.print_exc()


# --- model generation -----------------------------------------------------

def start_model_generation(project_id: int) -> int:
    with D.SessionLocal() as db:
        job = D.Job(project_id=project_id, kind="model_generation", status="queued")
        db.add(job)
        db.commit()
        jid = job.id
    _EXEC.submit(_run_model, project_id, jid)
    return jid


def _run_model(project_id: int, jid: int) -> None:
    with D.SessionLocal() as db:
        job, project = db.get(D.Job, jid), db.get(D.Project, project_id)
        co = project.company
        try:
            _job_update(db, job, status="running", progress=10)
            _set_project(db, project, "model_generation_running")
            out_path = D.ROOT / "output" / "models" / f"{co.slug}_model.xlsx"
            res = compile_model(co.extracted or {}, co.universe or {}, out_path)
            db.query(D.Control).filter_by(project_id=project_id).delete()
            for c in res["controls"]:
                db.add(D.Control(project_id=project_id, control_id=c["control_id"], category=c["category"],
                                 severity=c["severity"], status=c["status"], description=c["description"],
                                 expected=c["expected"], actual=c["actual"]))
            db.add(D.GeneratedModel(project_id=project_id, model_id=res["model_id"], version=res["version"],
                                    path=res["path"], dq_score=res["data_quality"]["score"],
                                    export_allowed=res["export_allowed"]))
            db.commit()
            approved = {o.control_id for o in db.query(D.Override).filter_by(project_id=project_id, status="approved")}
            crit_fail = [c for c in res["controls"]
                         if c["severity"] == "critical" and c["status"] == "fail" and c["control_id"] not in approved]
            _set_project(db, project, "model_validation_failed" if crit_fail else "model_ready")
            _job_update(db, job, status="complete", progress=100,
                        result={"model_id": res["model_id"], "dq_score": res["data_quality"]["score"],
                                "export_allowed": not crit_fail, "path": res["path"]})
            D.audit(db, "model_generated", "project", project_id,
                    model_id=res["model_id"], dq=res["data_quality"]["score"], export_allowed=not crit_fail)
        except Exception as e:  # noqa: BLE001
            _job_update(db, job, status="failed", error=str(e))
            _set_project(db, project, "model_validation_failed")
            traceback.print_exc()
