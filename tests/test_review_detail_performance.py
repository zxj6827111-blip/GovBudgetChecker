"""单条复核读取：任务范围、每任务一次投影与原文预览鉴权。"""
import json

import pytest
from fastapi import HTTPException

from api import auth_utils, runtime
from api.routes import files
from src.services import issue_workflow_store as workflow


@pytest.fixture
def jobs_and_decisions(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    for job in ("a", "b", "empty"):
        path = tmp_path / job
        path.mkdir()
        runtime.write_json_file(path / "status.json", {
            "job_id": job, "status": "done", "created_by": f"owner-{job}", "analysis_revision": "2",
            "result": {"issues": {"all": [{"id": f"{job}-{i}", "title": "问题", "severity": "high"} for i in range(2)]}},
        })
    records = [{"job_id": job, "issue_id": f"{job}-{i}", "status": "confirmed", "analysis_revision": "2"}
               for job in ("a", "b") for i in range(2)]
    runtime.write_json_file(tmp_path / ".issue_workflow.json", {
        "issues": {f"{r['job_id']}::{r['issue_id']}": r for r in records},
        "decision_events": records, "packages": [],
    })
    return tmp_path


def test_workflow_full_list_reads_each_task_once_and_scoped_list_reads_only_requested_task(jobs_and_decisions, monkeypatch):
    original = runtime.get_job_status_payload
    reads = []
    monkeypatch.setattr(runtime, "get_job_status_payload", lambda job, **kwargs: reads.append(job) or original(job, **kwargs))
    admin = {"is_admin": True}
    full = workflow.get_visible_state(admin)
    assert sorted(reads) == ["a", "b"]
    reads.clear()
    scoped = workflow.get_visible_state(admin, job_id="a")
    assert reads == ["a"]
    assert scoped["issues"] == {key: row for key, row in full["issues"].items() if row["job_id"] == "a"}
    assert {r["status"] for r in scoped["issues"].values()} == {"confirmed"}
    reads.clear()
    assert workflow.get_visible_state(admin, job_id="empty")["issues"] == {}
    assert reads == [], "无决定的当前任务不能扫描别的任务"


def test_scoped_workflow_denies_unowned_or_missing_tasks_even_without_decisions(jobs_and_decisions):
    assert workflow.get_visible_state({"username": "owner-a"}, job_id="a")["issues"]
    for job, expected in (("b", 403), ("empty", 403), ("missing", 404), (" ", 400)):
        with pytest.raises(HTTPException) as error:
            workflow.get_visible_state({"username": "owner-a"}, job_id=job)
        assert error.value.status_code == expected


def test_scoped_workflow_does_not_inherit_no_issue_into_new_analysis(jobs_and_decisions):
    state_file = jobs_and_decisions / ".issue_workflow.json"
    state = json.loads(state_file.read_text())
    state["decision_events"][0].update(status="no_issue", analysis_revision="1", hide_from_results=True)
    state["issues"]["a::a-0"].update(status="no_issue", analysis_revision="1", hide_from_results=True)
    state_file.write_text(json.dumps(state))
    assert workflow.get_visible_state({"is_admin": True}, job_id="a")["issues"]["a::a-0"]["status"] == "pending"


@pytest.mark.asyncio
async def test_pdf_preview_auth_uses_metadata_and_still_denies_other_owner(jobs_and_decisions, monkeypatch):
    monkeypatch.setattr(auth_utils, "require_login", lambda _: (None, "", {"username": "owner-a"}))
    monkeypatch.setattr(runtime, "get_job_status_payload", lambda *_a, **_k: pytest.fail("preview loaded full detail"))
    monkeypatch.setattr(files, "_resolve_source_pdf", lambda _: jobs_and_decisions / "test.pdf")
    monkeypatch.setattr(files, "_render_preview_png", lambda *_a, **_k: b"preview")
    response = await files.get_source_preview(None, "a", page=1, bbox=None, padding=0, scale=1.6)
    assert response.body == b"preview"
    with pytest.raises(HTTPException) as error:
        await files.get_source_preview(None, "b", page=1, bbox=None, padding=0, scale=1.6)
    assert error.value.status_code == 403
