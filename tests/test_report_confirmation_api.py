"""D4：整份确认门禁 API 契约（读取接口 + 默认关闭写侧）。"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api.main import app  # noqa: E402
from api.routes import jobs as jobs_routes  # noqa: E402


JOB = "11111111-1111-1111-1111-111111111111"


def _payload():
    return {
        "analysis_revision": "rev-test-1",
        "analysis_conclusion": "incomplete",
        "result": {
            "rule_findings": [
                {"rule_id": "V33-243", "severity": "high", "message": "m",
                 "location": {"page": 22}, "evidence_verification": {"status": "verified"}},
            ],
            "ai_findings": [],
            "meta": {"obligation_coverage": {"instances": [
                {"obligation_id": "OBL-AI-SEMANTIC-REVIEW", "status": "ai_not_run"},
            ]}},
        },
        "review_reasons": [],
    }


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(
        jobs_routes, "require_job_access",
        lambda request, job_id: (None, None, {"username": "tester"}, _payload()),
    )
    monkeypatch.delenv("GOVBUDGET_CONFIRM_REPORT_ENABLED", raising=False)
    with TestClient(app) as test_client:
        yield test_client


def test_read_gate_always_available(client):
    response = client.get(f"/api/jobs/{JOB}/report-confirmation")
    assert response.status_code == 200
    body = response.json()
    assert body["gate"]["can_confirm_whole_report"] is False
    assert body["gate"]["confirm_scope"] == "deterministic_findings_only"
    assert body["counts"]["confirmed_material_issues"] == 1
    assert body["counts"]["system_incomplete"] >= 1
    assert body["confirm_report_enabled"] is False


def test_write_disabled_by_default(client):
    response = client.post(f"/api/jobs/{JOB}/confirm-report", json={})
    assert response.status_code == 403
    assert response.json()["detail"] == "CONFIRM_REPORT_DISABLED"


def test_write_refuses_when_system_incomplete(client, monkeypatch):
    monkeypatch.setenv("GOVBUDGET_CONFIRM_REPORT_ENABLED", "1")
    response = client.post(f"/api/jobs/{JOB}/confirm-report", json={})
    assert response.status_code == 409
    assert response.json()["detail"] == "SYSTEM_INCOMPLETE"


def test_write_confirms_deterministic_scope_only(client, monkeypatch):
    monkeypatch.setenv("GOVBUDGET_CONFIRM_REPORT_ENABLED", "1")
    clean = _payload()
    clean["result"]["meta"]["obligation_coverage"]["instances"] = []
    clean["review_reasons"] = []
    monkeypatch.setattr(
        jobs_routes, "require_job_access",
        lambda request, job_id: (None, None, {"username": "tester"}, clean),
    )
    response = client.post(f"/api/jobs/{JOB}/confirm-report", json={})
    assert response.status_code == 200
    record = response.json()["record"]
    assert record["scope"] == "deterministic_findings_only"
    assert record["confirmed_count"] == 1
    assert record["analysis_revision"] == "rev-test-1"
