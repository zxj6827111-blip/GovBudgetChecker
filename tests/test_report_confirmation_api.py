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


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(
        jobs_routes, "require_job_access",
        lambda request, job_id: (None, None, {"username": "tester"}, _clean_payload()),
    )
    monkeypatch.delenv("GOVBUDGET_CONFIRM_REPORT_ENABLED", raising=False)
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path, raising=False)
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
