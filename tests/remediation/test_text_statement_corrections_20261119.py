"""正文金额 text_statement 校正：可定位、留原文/依据、重复提交明确状态。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api import runtime
from api.main import app

API_KEY = os.getenv("GOVBUDGET_API_KEY", "change_me_to_a_strong_secret")
JOB_ID = "text-corr-job"
REVISION_ID = "rev-20261119T120000-abc12345"
QUOTE = "支出决算为 5,465.41"
BBOX = [100.0, 100.0, 260.0, 120.0]


@pytest.fixture
def text_corr_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", upload_root)
    job_dir = upload_root / JOB_ID
    job_dir.mkdir()
    fitz = pytest.importorskip("fitz")
    with fitz.open() as document:
        for _ in range(25):
            document.new_page(width=400, height=400)
        page = document.new_page(width=400, height=400)
        page.insert_text((100, 112), QUOTE, fontsize=10, fontname="china-s")
        document.save(job_dir / "source.pdf")
    source_hash = hashlib.sha256((job_dir / "source.pdf").read_bytes()).hexdigest()

    issue = {
        "id": "CMM-011-24",
        "source": "rule",
        "rule": "CMM-011",
        "rule_id": "CMM-011",
        "severity": "warn",
        "title": "金额缺少单位“万元”",
        "message": f"十八、金额缺少单位“万元”：“{QUOTE}”，同段其余金额均以万元列示。",
        "location": {
            "page": 26,
            "pos": 534,
            "verified_by": {
                "rule_id": "V33-251",
                "sub_check_id": "V33-251:final:item_sum",
                "table_page": 16,
                "table_code": "FIN_05_general_public_expenditure",
                "subject_code": "2160250",
                "subject_name": "事业运行",
                "table_value": "5,465.41",
                "table_unit": "万元",
                "included_count": 20,
                "subtotal_wan": "168959.54",
                "counts_as_pass": True,
            },
        },
        "evidence": [{
            "physical_page": 26,
            "bbox": BBOX,
            "quote": QUOTE,
            "file_hash": source_hash,
            "verification_status": "verified",
        }],
    }
    status_payload = {
        "job_id": JOB_ID,
        "status": "review_required",
        "created_by": "test-admin",
        "analysis_revision": REVISION_ID,
        "mode": "legacy",
        "use_local_rules": True,
        "use_ai_assist": False,
        "result": {"issues": {"all": [issue]}, "meta": {"check_receipts": []}},
    }
    runtime.write_json_file(job_dir / "status.json", status_payload)
    (job_dir / "analysis_versions").mkdir()
    runtime.write_json_file(
        job_dir / "analysis_versions" / "current.json",
        {"revision_id": REVISION_ID, "status": "review_required"},
    )

    async def _noop_reanalyze(*args, **kwargs):
        return None

    monkeypatch.setattr(runtime, "reanalyze_job", _noop_reanalyze)
    with TestClient(app) as client:
        yield client, job_dir


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def _basis() -> str:
    return f"{JOB_ID}:{REVISION_ID}"


def _target(client) -> dict:
    response = client.get(f"/api/jobs/{JOB_ID}/correction-targets", headers=_headers())
    assert response.status_code == 200, response.text
    targets = response.json()["text_statements"]
    assert len(targets) == 1, targets
    return targets[0]


def test_targets_expose_locatable_statement_and_table_basis(text_corr_client):
    client, _ = text_corr_client
    target = _target(client)
    assert target["target_type"] == "text_statement"
    assert target["page"] == 26
    assert target["amount_raw"] == "5,465.41"
    assert target["suggested_unit"] == "万元"
    assert target["subject"] == {"code": "2160250", "name": "事业运行"}
    assert target["evidence"][0]["bbox"] == BBOX
    assert {action["field"] for action in target["actions"]} >= {"unit", "raw_value"}


def test_text_statement_unit_correction_is_recorded_with_original_text_and_basis(text_corr_client):
    client, job_dir = text_corr_client
    target = _target(client)
    body = {
        "analysis_basis_token": _basis(),
        "reason": "按第16页原表确认单位为万元",
        "changes": [{
            "target_type": "text_statement",
            "target_id": target["target_id"],
            "field": "unit",
            "value": "万元",
            "location": {"physical_page": 26, "bbox": BBOX, "quote": QUOTE},
        }],
    }
    response = client.post(f"/api/jobs/{JOB_ID}/corrections", headers=_headers(), json=body)
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["record_status"] == "append_only_overlay"
    # R4：同一笔正文金额还会进入单项表文核对（V33-222），受影响检查由
    # 当前版本的实际依赖产生，不再是固定两条。
    assert payload["affected_checks"]["check_ids"] == ["CMM-011", "V33-222", "V33-251"]
    assert payload["reanalysis_status"] == "queued"
    assert payload["apply_status"] == "pending_reanalysis"

    records = [
        json.loads(line)
        for line in (job_dir / "corrections.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 1
    change = records[0]["changes"][0]
    assert change["original_text"] == QUOTE
    assert change["target_snapshot"]["subject"]["code"] == "2160250"
    assert change["target_snapshot"]["table_basis"]["table_page"] == 16
    assert records[0]["actor"] == "test-admin"
    assert records[0]["analysis_basis_token"] == _basis()


def test_duplicate_text_statement_submission_is_not_appended_twice(text_corr_client):
    client, job_dir = text_corr_client
    target = _target(client)
    body = {
        "analysis_basis_token": _basis(),
        "reason": "确认单位",
        "changes": [{
            "target_type": "text_statement",
            "target_id": target["target_id"],
            "field": "unit",
            "value": "万元",
            "location": {"physical_page": 26, "bbox": BBOX, "quote": QUOTE},
        }],
    }
    first = client.post(f"/api/jobs/{JOB_ID}/corrections", headers=_headers(), json=body)
    assert first.status_code == 202
    second = client.post(f"/api/jobs/{JOB_ID}/corrections", headers=_headers(), json=body)
    assert second.status_code == 202, second.text
    duplicate = second.json()
    assert duplicate["duplicate"] is True
    assert duplicate["record_status"] == "duplicate_ignored"
    assert duplicate["reanalysis_status"] == "not_executed_duplicate"
    records = [
        json.loads(line)
        for line in (job_dir / "corrections.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 1


def test_stale_text_statement_submission_is_rejected_without_append(text_corr_client):
    client, job_dir = text_corr_client
    target = _target(client)
    body = {
        "analysis_basis_token": f"{JOB_ID}:rev-old",
        "reason": "旧版本提交",
        "changes": [{
            "target_type": "text_statement",
            "target_id": target["target_id"],
            "field": "unit",
            "value": "万元",
            "location": {"physical_page": 26, "bbox": BBOX, "quote": QUOTE},
        }],
    }
    response = client.post(f"/api/jobs/{JOB_ID}/corrections", headers=_headers(), json=body)
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "analysis_basis_changed"
    assert not (job_dir / "corrections.jsonl").exists()


def test_dispatch_failure_keeps_record_and_reports_not_applied(
    text_corr_client, monkeypatch: pytest.MonkeyPatch
):
    client, job_dir = text_corr_client
    target = _target(client)

    async def _reject(*args, **kwargs):
        from fastapi import HTTPException

        raise HTTPException(status_code=409, detail="模型不可用")

    monkeypatch.setattr(runtime, "reanalyze_job", _reject)
    body = {
        "analysis_basis_token": _basis(),
        "reason": "确认单位",
        "changes": [{
            "target_type": "text_statement",
            "target_id": target["target_id"],
            "field": "unit",
            "value": "万元",
            "location": {"physical_page": 26, "bbox": BBOX, "quote": QUOTE},
        }],
    }
    response = client.post(f"/api/jobs/{JOB_ID}/corrections", headers=_headers(), json=body)
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["reanalysis_status"] == "not_executed_dispatch_rejected"
    assert payload["apply_status"] == "not_applied"
    assert payload["review_decisions_status"] == "unchanged_until_new_analysis_is_published"
    records = [
        json.loads(line)
        for line in (job_dir / "corrections.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 1


def test_unknown_text_target_is_rejected(text_corr_client):
    client, _ = text_corr_client
    body = {
        "analysis_basis_token": _basis(),
        "reason": "不存在的目标",
        "changes": [{
            "target_type": "text_statement",
            "target_id": "text-stmt:not-in-this-version",
            "field": "unit",
            "value": "万元",
            "location": {"physical_page": 26, "bbox": BBOX, "quote": QUOTE},
        }],
    }
    response = client.post(f"/api/jobs/{JOB_ID}/corrections", headers=_headers(), json=body)
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "text_correction_target_not_found"


def test_text_correction_without_bbox_accepts_unique_page_span(text_corr_client):
    """R4：有页码且原句在该页唯一时，不强制 bbox；依据标为页级定位。"""
    client, job_dir = text_corr_client
    target = _target(client)
    body = {
        "analysis_basis_token": _basis(),
        "reason": "按第26页唯一原句确认读数",
        "changes": [{
            "target_type": "text_statement",
            "target_id": target["target_id"],
            "field": "raw_value",
            "value": "5,455.41",
            "location": {"physical_page": 26, "quote": QUOTE},
        }],
    }
    response = client.post(f"/api/jobs/{JOB_ID}/corrections", headers=_headers(), json=body)
    assert response.status_code == 202, response.text
    records = [
        json.loads(line)
        for line in (job_dir / "corrections.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    evidence = records[0]["manual_evidence"][0]
    assert evidence["location_level"] == "page_span"
    assert evidence["bbox"] is None
    assert evidence["quote"] == QUOTE


def test_text_correction_without_bbox_rejects_non_unique_span(tmp_path: Path):
    """R4：原句在本页不唯一时拒绝页级定位，要求框选具体位置。"""
    from fastapi import HTTPException
    from api.routes.corrections import _validate_manual_location

    fitz = pytest.importorskip("fitz")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    with fitz.open() as document:
        page = document.new_page(width=400, height=400)
        page.insert_text((50, 100), "支出决算为 5,465.41", fontsize=10, fontname="china-s")
        page.insert_text((50, 130), "支出决算为 5,465.41", fontsize=10, fontname="china-s")
        document.save(job_dir / "source.pdf")
    with pytest.raises(HTTPException) as excinfo:
        _validate_manual_location(
            "job", job_dir,
            {"physical_page": 1, "quote": "支出决算为 5,465.41"},
            require_bbox=False,
        )
    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["error"] == "manual_location_needs_selection"
