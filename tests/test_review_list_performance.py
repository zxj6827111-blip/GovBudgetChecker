"""性能回归：减少详情读取时仍保持授权、决定指纹与 CAS 一致。"""
import hashlib
import json

import pytest

from api import runtime
from api.routes import reviews
from src.services import issue_workflow_store as workflow


@pytest.fixture
def snapshot_state(monkeypatch):
    state = {"decision_events": []}
    monkeypatch.setattr(workflow, "_read_state", lambda: state)
    monkeypatch.setattr(workflow, "_migrate_legacy_ignored_locked", lambda *_: False)
    # 使用临时锁由 fixture 替换，避免测试碰真实上传目录。
    from contextlib import nullcontext
    monkeypatch.setattr(workflow, "_state_lock", nullcontext)
    return state


def test_no_decisions_snapshot_never_loads_pdf_or_detail(snapshot_state, monkeypatch):
    monkeypatch.setattr(runtime, "get_job_status_payload", lambda *_a, **_k: pytest.fail("unexpected detail read"))
    snapshot = workflow.get_job_problem_snapshot("job")
    expected = {"algorithm": workflow.PROBLEM_SNAPSHOT_ALGORITHM, "decisions": [], "ignored_issue_ids": []}
    token = hashlib.sha256(json.dumps(expected, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    assert snapshot == {"decisions": {}, "ignored_issue_ids": [], "token": token}


def test_snapshot_projects_detail_once_and_preserves_version_bound_hidden_decisions(snapshot_state, monkeypatch):
    issue = {"id": "issue", "title": "需复核", "severity": "high"}
    payload = {"job_id": "job", "analysis_revision": "2", "result": {"ai_findings": [issue], "rule_findings": []}}
    snapshot_state["decision_events"] = [{"job_id": "job", "issue_id": "issue", "status": "no_issue",
        "analysis_revision": "2", "identity_fingerprint": workflow.issue_identity_fingerprint(issue),
        "hide_from_results": True}]
    calls = []
    monkeypatch.setattr(runtime, "get_job_status_payload", lambda *_a, **_k: calls.append(1) or payload)
    first = workflow.get_job_problem_snapshot("job")
    assert len(calls) == 1
    assert first["decisions"] == {"issue": "no_issue"}
    assert first["ignored_issue_ids"] == ["issue"]
    assert first["token"] == workflow._problem_snapshot_token(snapshot_state, "job")
    # 同一 ID 换了分析代际不能继续继承“无问题”，也不能复用旧 CAS token。
    payload["analysis_revision"] = "3"
    changed = workflow.get_job_problem_snapshot("job")
    assert changed["decisions"] == {}
    assert changed["ignored_issue_ids"] == []
    assert changed["token"] != first["token"]


def test_review_authorization_uses_raw_owner_and_canonical_org_without_detail(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    job = tmp_path / "job"
    job.mkdir()
    status = job / "status.json"
    status.write_text(json.dumps({"created_by": "owner", "organization_id": "old"}))
    monkeypatch.setattr(runtime, "_enrich_job_organization_context", lambda _id, payload: {**payload, "organization_id": "canonical"})
    monkeypatch.setattr(runtime, "get_job_status_payload", lambda *_a, **_k: pytest.fail("authorization loaded detail"))
    assert runtime.get_job_access_payload("job")["organization_id"] == "canonical"
    assert reviews._job_access_ok({"username": "owner"}, "job")
    assert not reviews._job_access_ok({"username": "other"}, "job")
    status.write_text("invalid json")
    assert not reviews._job_access_ok({"username": "admin", "is_admin": True}, "job")
    assert not reviews._job_access_ok({"username": "admin", "is_admin": True}, "missing")


@pytest.mark.asyncio
async def test_job_list_file_work_does_not_block_other_requests(tmp_path, monkeypatch):
    import asyncio
    import threading
    from api.routes import jobs

    released = threading.Event()
    monkeypatch.setattr(jobs, "require_login", lambda _: (None, "", {"is_admin": True}))
    monkeypatch.setattr(runtime, "iter_job_dirs", lambda: [tmp_path])
    monkeypatch.setattr(runtime, "collect_job_summary", lambda _: {"released_while_reading": released.wait(timeout=1)})
    task = asyncio.create_task(jobs.list_jobs(None, limit=None, offset=0))
    await asyncio.sleep(0.02)
    released.set()
    result = await task
    assert result[0]["released_while_reading"], "同步文件处理堵住了事件循环"
