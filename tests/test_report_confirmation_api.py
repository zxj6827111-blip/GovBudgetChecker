"""D4/IR-07：整份确认写侧契约（版本约束/稳定ID/排除决定/幂等/持久化读回）。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api.main import app  # noqa: E402
from api import runtime  # noqa: E402
from api.routes import jobs as jobs_routes  # noqa: E402


JOB = "11111111-1111-1111-1111-111111111111"


def _payload():
    return {
        "analysis_revision": "rev-current",
        "analysis_conclusion": "incomplete",
        "human_decisions": [],
        "result": {
            "rule_findings": [
                {"id": "issue-1", "rule_id": "V33-243", "severity": "high", "message": "m",
                 "location": {"page": 22}, "evidence_verification": {"status": "verified"}},
                {"id": "issue-1b", "rule_id": "V33-243", "severity": "high", "message": "m2",
                 "location": {"page": 22}, "evidence_verification": {"status": "verified"}},
                {"id": "issue-2", "rule_id": "CMM-007", "severity": "manual_review",
                 "message": "amb", "location": {"page": 36},
                 "evidence_verification": {"status": "verified"}},
                {"id": "issue-3", "rule_id": "V33-104", "severity": "warn", "message": "x",
                 "location": {"page": 8}, "evidence_verification": {"status": "failed"}},
            ],
            "ai_findings": [],
            "meta": {"obligation_coverage": {"instances": [
                {"obligation_id": "OBL-AI", "status": "ai_not_run"},
            ]}},
        },
        "review_reasons": [],
    }


def _clean_payload():
    payload = _payload()
    payload["result"]["rule_findings"] = [
        {"id": "issue-1", "rule_id": "V33-243", "severity": "high", "message": "m",
         "location": {"page": 22}, "evidence_verification": {"status": "verified"}},
        {"id": "issue-1b", "rule_id": "V33-243", "severity": "high", "message": "m2",
         "location": {"page": 22}, "evidence_verification": {"status": "verified"}},
    ]
    payload["result"]["meta"]["obligation_coverage"]["instances"] = []
    return payload


def _write_status(tmp_path: Path, payload: dict) -> None:
    """写权威 status.json：R2-03 后写侧在锁内重新读取它，而不是请求入口快照。"""
    job_dir = tmp_path / JOB
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "status.json").write_text(
        json.dumps({"job_id": JOB, **payload}, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(
        jobs_routes, "require_job_access",
        lambda request, job_id: (None, None, {"username": "tester"}, _clean_payload()),
    )
    monkeypatch.delenv("GOVBUDGET_CONFIRM_REPORT_ENABLED", raising=False)
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path, raising=False)
    _write_status(tmp_path, _clean_payload())
    with TestClient(app) as test_client:
        yield test_client


def test_write_disabled_by_default(client):
    response = client.post(
        f"/api/jobs/{JOB}/confirm-report",
        json={"expected_analysis_revision": "rev-current",
              "idempotency_key": "key-12345678"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "CONFIRM_REPORT_DISABLED"


def test_missing_version_or_key_is_422(client):
    response = client.post(f"/api/jobs/{JOB}/confirm-report", json={})
    assert response.status_code == 422


def test_stale_page_revision_is_rejected(client, monkeypatch):
    monkeypatch.setenv("GOVBUDGET_CONFIRM_REPORT_ENABLED", "1")
    response = client.post(
        f"/api/jobs/{JOB}/confirm-report",
        json={"expected_analysis_revision": "rev-old",
              "idempotency_key": "key-12345678"},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["error"] == "REVISION_CHANGED"
    assert detail["current"] == "rev-current"


def test_confirm_persists_stable_ids_and_readback(client, monkeypatch):
    monkeypatch.setenv("GOVBUDGET_CONFIRM_REPORT_ENABLED", "1")
    body = {"expected_analysis_revision": "rev-current", "idempotency_key": "key-12345678"}
    first = client.post(f"/api/jobs/{JOB}/confirm-report", json=body)
    assert first.status_code == 200
    record = first.json()["record"]
    assert first.json()["idempotent"] is False
    # 稳定问题 ID：同规则同页两条不同关系不碰撞（IR-07）
    assert record["confirmed_issue_ids"] == ["issue-1", "issue-1b"]
    assert record["analysis_revision"] == "rev-current"
    # 幂等重放：同 key 同版本返回同一条记录，不重复审计
    repeat = client.post(f"/api/jobs/{JOB}/confirm-report", json=body)
    assert repeat.status_code == 200 and repeat.json()["idempotent"] is True
    assert repeat.json()["record"] == record
    # 读取接口读回最近记录（刷新/重开可对账）
    gate = client.get(f"/api/jobs/{JOB}/report-confirmation")
    gate.raise_for_status()
    body_json = gate.json()
    assert body_json["latest_confirm_record"] == record
    assert body_json["counts"]["confirmed_material_issues"] == 2
    assert (runtime.UPLOAD_ROOT / JOB / "confirm_reports.jsonl").exists()


def test_manual_review_and_failed_evidence_excluded_from_scope(client, monkeypatch):
    monkeypatch.setenv("GOVBUDGET_CONFIRM_REPORT_ENABLED", "1")
    # 换成含歧义/未完成的载荷 → 门禁 409，且 never 签认 manual_review
    import api.routes.jobs as jobs_mod

    original = jobs_mod.require_job_access

    def _with_dirty(request, job_id):
        return (None, None, {"username": "tester"}, _payload())

    monkeypatch.setattr(jobs_mod, "require_job_access", _with_dirty)
    _write_status(runtime.UPLOAD_ROOT, _payload())
    response = client.post(
        f"/api/jobs/{JOB}/confirm-report",
        json={"expected_analysis_revision": "rev-current",
              "idempotency_key": "key-87654321"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "SYSTEM_INCOMPLETE"
    gate = client.get(f"/api/jobs/{JOB}/report-confirmation")
    counts = gate.json()["counts"]
    assert counts["genuine_ambiguities"] >= 1  # manual_review 归歧义
    assert counts["system_incomplete"] >= 1    # failed 证据 + ai_not_run


# ---------------------------------------------------------------------------
# R2-03：签认与权威版本发布共享同步边界
# ---------------------------------------------------------------------------

def test_confirm_waits_for_job_state_lock_and_records_after_release(monkeypatch, tmp_path):
    """持锁（模拟分析发布临界区）期间不得落盘；释放后按当前版本落盘。"""
    import threading
    import time

    from src.services.report_confirmation_gate import (
        confirm_report_atomic,
        read_confirm_records,
    )

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path, raising=False)
    _write_status(tmp_path, _clean_payload())
    job_dir = tmp_path / JOB
    entered, release = threading.Event(), threading.Event()

    def holder():
        with runtime.job_state_lock(job_dir):
            entered.set()
            release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert entered.wait(2)
    results = {}

    def confirmer():
        results["value"] = confirm_report_atomic(
            JOB, expected_analysis_revision="rev-current",
            idempotency_key="key-atomic-1", confirm_report_enabled=True,
            actor="tester")

    worker = threading.Thread(target=confirmer)
    worker.start()
    time.sleep(0.2)
    assert read_confirm_records(JOB) == [], "持锁期间不得写入确认记录"
    release.set()
    worker.join(5)
    thread.join(5)
    assert results["value"]["http"] == 200
    assert len(read_confirm_records(JOB)) == 1


def test_confirm_then_publish_leaves_current_record_historical(monkeypatch, tmp_path):
    """先签认 R1、后发布 R2：R1 记录只能作为历史，不得当 R2 有效签认。"""
    from src.services.report_confirmation_gate import (
        build_report_confirmation,
        confirm_readback,
        confirm_report_atomic,
        read_confirm_records,
    )

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path, raising=False)
    _write_status(tmp_path, _clean_payload())
    result = confirm_report_atomic(
        JOB, expected_analysis_revision="rev-current",
        idempotency_key="key-history-1", confirm_report_enabled=True, actor="tester")
    assert result["http"] == 200
    runtime.merge_job_status(tmp_path / JOB, {"analysis_revision": "rev-next"})
    payload = runtime.get_job_status_payload(JOB, apply_issue_filters=False)
    readback = confirm_readback(
        build_report_confirmation(payload, confirm_report_enabled=True),
        read_confirm_records(JOB), payload)
    assert readback["latest_confirm_record"]["analysis_revision"] == "rev-current"
    assert readback["latest_confirm_record_is_current"] is False
    assert readback["current_confirm_record"] is None
    assert len(readback["historical_confirm_records"]) == 1
    stale = confirm_report_atomic(
        JOB, expected_analysis_revision="rev-current",
        idempotency_key="key-history-2", confirm_report_enabled=True, actor="tester")
    assert stale["http"] == 409 and stale["detail"]["current"] == "rev-next"


def test_decision_change_marks_current_confirm_stale_and_new_key_excludes_ignored(
        monkeypatch, tmp_path):
    from src.services.report_confirmation_gate import (
        build_report_confirmation,
        confirm_readback,
        confirm_report_atomic,
        read_confirm_records,
    )

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path, raising=False)
    _write_status(tmp_path, _clean_payload())
    assert confirm_report_atomic(
        JOB, expected_analysis_revision="rev-current",
        idempotency_key="key-decision-1", confirm_report_enabled=True,
        actor="tester")["http"] == 200
    changed = _clean_payload()
    changed["human_decisions"] = [{"issue_id": "issue-1", "status": "ignored"}]
    _write_status(tmp_path, changed)
    payload = runtime.get_job_status_payload(JOB, apply_issue_filters=False)
    readback = confirm_readback(
        build_report_confirmation(payload, confirm_report_enabled=True),
        read_confirm_records(JOB), payload)
    assert readback["current_confirm_decision_changed"] is True
    second = confirm_report_atomic(
        JOB, expected_analysis_revision="rev-current",
        idempotency_key="key-decision-2", confirm_report_enabled=True,
        actor="tester")
    assert second["http"] == 200
    assert second["body"]["record"]["confirmed_issue_ids"] == ["issue-1b"]


def test_concurrent_fresh_keys_both_write_distinct_records(monkeypatch, tmp_path):
    """全新幂等键并发首写：两个都成功，各留一条记录（不是重复点击）。"""
    import concurrent.futures

    from src.services.report_confirmation_gate import (
        confirm_report_atomic,
        read_confirm_records,
    )

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path, raising=False)
    _write_status(tmp_path, _clean_payload())

    def submit(index):
        return confirm_report_atomic(
            JOB, expected_analysis_revision="rev-current",
            idempotency_key=f"key-fresh-{index}",
            confirm_report_enabled=True, actor="tester")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert all(item["http"] == 200 for item in results)
    assert all(item["body"]["idempotent"] is False for item in results)
    assert len(read_confirm_records(JOB)) == 2


def test_publication_during_append_rolls_back_and_rejects(monkeypatch, tmp_path):
    """受控交错：核对 R1 → 发布 R2 → 落盘前/后复核 → 必须撤销并 409。"""
    from src.services import report_confirmation_gate as gate

    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path, raising=False)
    _write_status(tmp_path, _clean_payload())
    original_append = gate.append_confirm_record

    def append_after_publication(job_id, record):
        runtime.write_json_file(
            tmp_path / JOB / "status.json",
            {"job_id": JOB, **{**_clean_payload(), "analysis_revision": "rev-next"}})
        return original_append(job_id, record)

    monkeypatch.setattr(gate, "append_confirm_record", append_after_publication)
    result = gate.confirm_report_atomic(
        JOB, expected_analysis_revision="rev-current",
        idempotency_key="key-race-1", confirm_report_enabled=True, actor="tester")
    assert result["http"] == 409
    assert result["detail"]["current"] == "rev-next"
    assert gate.read_confirm_records(JOB) == [], "旧版本记录必须撤销"
