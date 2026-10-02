"""ZC-05 evidence, append-only correction and feedback contracts."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api import runtime
from api.main import app
from src.services.correction_overlay_store import (
    affected_check_ids,
    append_record_with_basis_cas,
    current_analysis_basis_token,
    read_jsonl_records,
)

API_KEY = os.getenv("GOVBUDGET_API_KEY", "change_me_to_a_strong_secret")
JOB_ID = "zc05-job"
REVISION_ID = "rev-20261002T120000-abc12345"
EVIDENCE_ID = "evidence-zc05-1"


@pytest.fixture
def zc05_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", upload_root)
    job_dir = upload_root / JOB_ID
    job_dir.mkdir()
    fitz = pytest.importorskip("fitz")
    with fitz.open() as document:
        page = document.new_page(width=300, height=400)
        # AC-09：读取端按原件文本层核验引文——夹具必须带真实中文文本层
        page.insert_text((30, 40), "三公经费合计 35.20 万元", fontsize=11, fontname="china-s")
        document.save(job_dir / "source.pdf")
    # AC-09：读取端按当前原件重算哈希与引文真实性——夹具必须携带真实哈希
    # （错哈希证据现在会被判 failed，正是验收要求的反例行为）。
    import hashlib

    source_hash = hashlib.sha256((job_dir / "source.pdf").read_bytes()).hexdigest()

    finding = {
        "id": "issue-zc05",
        "rule_id": "C-001",
        "severity": "high",
        "title": "三公经费核对",
        "evidence": [
            {
                "evidence_id": EVIDENCE_ID,
                "file_hash": source_hash,
                "physical_page": 1,
                "printed_page": None,
                "quote": "三公经费合计 35.20 万元",
                "bbox": [25, 20, 190, 50],
                "source_method": "native_text",
                "verification_status": "verified",
                "location_level": "exact_span",
            }
        ],
    }
    status_payload: Dict[str, Any] = {
        "job_id": JOB_ID,
        "status": "review_required",
        "created_by": "test-admin",
        "analysis_revision": 1,
        "result": {
            "issues": {"all": [finding]},
            "meta": {
                "check_receipts": [
                    {
                        "rule_id": "C-001",
                        "sub_check_id": "C-001:total",
                        "status": "fail",
                        "operand_fact_uids": ["fact-zc05-1"],
                    }
                ]
            },
        },
    }
    runtime.write_json_file(job_dir / "status.json", status_payload)
    (job_dir / "analysis_versions").mkdir()
    runtime.write_json_file(
        job_dir / "analysis_versions" / "current.json",
        {"revision_id": REVISION_ID, "status": "review_required"},
    )
    with TestClient(app) as client:
        yield client, job_dir


def _headers() -> Dict[str, str]:
    return {"X-API-Key": API_KEY}


def _basis() -> str:
    return f"{JOB_ID}:{REVISION_ID}"


def test_zc05_evidence_read_returns_source_and_preview(zc05_client) -> None:
    client, _ = zc05_client
    response = client.get(
        f"/api/jobs/{JOB_ID}/evidence/{EVIDENCE_ID}", headers=_headers()
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["quote"] == "三公经费合计 35.20 万元"
    assert payload["physical_page"] == 1
    assert payload["location_level"] == "exact_span"
    assert payload["verification_status"] == "verified"
    assert "bbox=" in payload["preview_url"]


def test_zc05_correction_is_append_only_and_reports_no_fake_rerun(zc05_client) -> None:
    client, job_dir = zc05_client
    response = client.post(
        f"/api/jobs/{JOB_ID}/corrections",
        headers=_headers(),
        json={
            "analysis_basis_token": _basis(),
            "reason": "人工核对原始表格后修正原值",
            "changes": [
                {
                    "target_type": "fact",
                    "target_id": "fact-zc05-1",
                    "field": "raw_value",
                    "value": "35.21",
                    "evidence_ids": [EVIDENCE_ID],
                }
            ],
        },
    )
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["affected_checks"] == {
        "status": "mapped",
        "check_ids": ["C-001:total"],
        "reason": None,
    }
    assert result["new_analysis_revision"] is None
    # AC-10：校正触发重算（排队执行），不再宣称"留痕即完成"
    assert result["reanalysis_status"] == "queued"
    assert result["review_decisions_status"] == "pending_reanalysis"
    records = read_jsonl_records(job_dir / "corrections.jsonl")
    assert len(records) == 1
    assert records[0]["actor"] == "test-admin"
    assert records[0]["changes"][0]["value"] == "35.21"
    assert records[0]["analysis_basis_token"] == _basis()
    # AC-10：校正已排队重算，status.json 可能被重跑管道改写（新版本发布），
    # 不再断言其保持不变；台账只追加、原记录不可变仍然成立。


def test_zc05_stale_analysis_token_is_rejected_without_append(zc05_client) -> None:
    client, job_dir = zc05_client
    response = client.post(
        f"/api/jobs/{JOB_ID}/corrections",
        headers=_headers(),
        json={
            "analysis_basis_token": f"{JOB_ID}:old-revision",
            "reason": "基于旧版结果提交",
            "changes": [
                {
                    "target_type": "fact",
                    "target_id": "fact-zc05-1",
                    "field": "unit",
                    "value": "万元",
                    "evidence_ids": [EVIDENCE_ID],
                }
            ],
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "analysis_basis_changed"
    assert not (job_dir / "corrections.jsonl").exists()


def test_zc05_disallows_direct_normalized_amount_override(zc05_client) -> None:
    client, _ = zc05_client
    response = client.post(
        f"/api/jobs/{JOB_ID}/corrections",
        headers=_headers(),
        json={
            "analysis_basis_token": _basis(),
            "reason": "不允许直接写归一金额",
            "changes": [
                {
                    "target_type": "fact",
                    "target_id": "fact-zc05-1",
                    "field": "normalized_value",
                    "value": "0.003521",
                    "evidence_ids": [EVIDENCE_ID],
                }
            ],
        },
    )
    assert response.status_code == 422


def test_zc05_manual_location_keeps_manual_unverified_provenance(zc05_client) -> None:
    client, job_dir = zc05_client
    response = client.post(
        f"/api/jobs/{JOB_ID}/corrections",
        headers=_headers(),
        json={
            "analysis_basis_token": _basis(),
            "reason": "人工重新框选原文区域",
            "changes": [
                {
                    "target_type": "fact",
                    "target_id": "fact-zc05-1",
                    "field": "manual_location",
                    "value": {
                        "physical_page": 1,
                        "bbox": [20, 15, 200, 55],
                        "quote": "三公经费合计 35.20 万元",
                    },
                }
            ],
        },
    )
    assert response.status_code == 202, response.text
    record = read_jsonl_records(job_dir / "corrections.jsonl")[-1]
    evidence = record["manual_evidence"][0]
    assert evidence["source_method"] == "manual"
    assert evidence["verification_status"] == "unverified"
    assert evidence["location_level"] == "region"
    evidence_response = client.get(
        f"/api/jobs/{JOB_ID}/evidence/{evidence['evidence_id']}", headers=_headers()
    )
    assert evidence_response.status_code == 200, evidence_response.text
    assert evidence_response.json()["source_method"] == "manual"
    assert "bbox=" not in evidence_response.json()["preview_url"]


@pytest.mark.parametrize(
    ("target_type", "target_id", "field", "value"),
    [
        ("fact", "fact-zc05-unit", "unit", "万元"),
        ("table_instance", "table-zc05", "column_mapping", {"决算数": 4}),
    ],
)
def test_zc05_unit_and_column_mapping_corrections_are_audited_only(
    zc05_client, target_type, target_id, field, value
) -> None:
    client, job_dir = zc05_client
    response = client.post(
        f"/api/jobs/{JOB_ID}/corrections",
        headers=_headers(),
        json={
            "analysis_basis_token": _basis(),
            "reason": "人工核对单位或表头列映射",
            "changes": [
                {
                    "target_type": target_type,
                    "target_id": target_id,
                    "field": field,
                    "value": value,
                    "evidence_ids": [EVIDENCE_ID],
                }
            ],
        },
    )
    assert response.status_code == 202, response.text
    record = read_jsonl_records(job_dir / "corrections.jsonl")[0]
    assert record["changes"][0]["field"] == field
    # 新分析版本在重算完成后才发布；提交时刻不得宣称已生效
    assert response.json()["new_analysis_revision"] is None
    assert response.json()["reanalysis_status"] == "queued"


def test_zc05_feedback_fixed_reason_set_and_business_confirmation(zc05_client) -> None:
    client, job_dir = zc05_client
    invalid = client.post(
        f"/api/jobs/{JOB_ID}/feedback",
        headers=_headers(),
        json={
            "analysis_basis_token": _basis(),
            "feedback_type": "false_positive",
            "reason_code": "把规则关掉",
            "issue_id": "issue-zc05",
            "context": "这条结果是误报",
        },
    )
    assert invalid.status_code == 422

    accepted = client.post(
        f"/api/jobs/{JOB_ID}/feedback",
        headers=_headers(),
        json={
            "analysis_basis_token": _basis(),
            "feedback_type": "false_positive",
            "reason_code": "evidence_location_error",
            "issue_id": "issue-zc05",
            "evidence_ids": [EVIDENCE_ID],
            "context": "原文页码和引用需要业务人员再次核实",
        },
    )
    assert accepted.status_code == 202, accepted.text
    record = read_jsonl_records(job_dir / "feedback.jsonl")[0]
    assert record["feedback_status"] == "awaiting_business_confirmation"
    assert record["reason_code"] == "evidence_location_error"


def test_zc05_append_cas_serializes_concurrent_writers_and_keeps_both_records(tmp_path: Path) -> None:
    job_dir = tmp_path / JOB_ID
    (job_dir / "analysis_versions").mkdir(parents=True)
    (job_dir / "analysis_versions" / "current.json").write_text(
        json.dumps({"revision_id": REVISION_ID}), encoding="utf-8"
    )

    def append(index: int) -> Dict[str, Any]:
        return append_record_with_basis_cas(
            job_dir,
            filename="corrections.jsonl",
            expected_basis_token=_basis(),
            record={"record_id": str(index), "index": index},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(append, (1, 2)))
    assert all(item["accepted"] for item in results)
    assert {item["index"] for item in read_jsonl_records(job_dir / "corrections.jsonl")} == {1, 2}
    assert current_analysis_basis_token(JOB_ID, job_dir) == _basis()


def test_zc05_unlinked_target_has_unknown_affected_checks() -> None:
    result = affected_check_ids(
        [{"sub_check_id": "C-001:total", "operand_fact_uids": ["fact-a"]}],
        ["fact-b"],
    )
    assert result["status"] == "unknown"
    assert result["check_ids"] == []
