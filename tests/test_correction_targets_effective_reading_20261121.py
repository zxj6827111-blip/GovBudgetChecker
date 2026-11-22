"""R6 自检加固（2026-11-21）：当版有效读数以**该版自己的** applied 回执为准。

构造 A→B→C 链条，其中校正 X 在 B 版生效、却在 C 版重分析中未再命中
（C 的 applied 回执不含 X）：C 视图必须显示原文读数为当版采用值，
且 X 仍在历史里如实标注"在 B 首次生效"——不得把旧人工值标成当版采用。
"""
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
JOB_ID = "r6-effective-reading-job"
REV_A = "rev-20261121T100000-aaaa0001"
REV_B = "rev-20261121T110000-bbbb0002"
REV_C = "rev-20261121T120000-cccc0003"
QUOTE = "支出决算为 5,465.41"
BBOX = [100.0, 100.0, 260.0, 120.0]


@pytest.fixture
def r6_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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

    def _payload(revision: str, applied=None) -> dict:
        return {
            "job_id": JOB_ID,
            "status": "review_required",
            "created_by": "test-admin",
            "analysis_revision": revision,
            "mode": "legacy",
            "use_local_rules": True,
            "use_ai_assist": False,
            "result": {"issues": {"all": [issue]}, "meta": {"check_receipts": []}},
            **({"applied_text_correction_ids": applied} if applied else {}),
        }

    versions = job_dir / "analysis_versions"
    versions.mkdir()
    # A：无校正；B：校正 X 生效；C：X 未再命中（applied 回执为空）。
    for revision, applied in ((REV_A, None), (REV_B, ["corr-x"]), (REV_C, None)):
        (versions / revision).mkdir()
        runtime.write_json_file(versions / revision / "status.json",
                                _payload(revision, applied))
    runtime.write_json_file(job_dir / "status.json", _payload(REV_C))

    async def _noop_reanalyze(*args, **kwargs):
        return None

    monkeypatch.setattr(runtime, "reanalyze_job", _noop_reanalyze)
    with TestClient(app) as client:
        # 先取当前视图里真实声明的 target_id，再落校正台账——
        # 台账按 target_id 与声明分组，猜哈希格式反而脆。
        response = client.get(
            f"/api/jobs/{JOB_ID}/correction-targets", headers={"X-API-Key": API_KEY},
        )
        assert response.status_code == 200, response.text
        statements = response.json()["text_statements"]
        assert statements, response.json()
        target_id = statements[0]["target_id"]
        (job_dir / "corrections.jsonl").write_text(
            json.dumps({
                "record_id": "corr-x",
                "actor": "admin",
                "reason": "人工校正读数",
                "created_at": "2026-11-21T10:30:00+00:00",
                "analysis_basis_token": f"{JOB_ID}:{REV_A}",
                "changes": [{
                    "target_type": "text_statement",
                    "target_id": target_id,
                    "field": "raw_value",
                    "value": "5,455.41",
                    "span": QUOTE,
                    "page": 26,
                    "pos": 534,
                }],
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        yield client, job_dir


def _targets(client, revision: str | None = None) -> dict:
    path = f"/api/jobs/{JOB_ID}/correction-targets"
    if revision:
        path += f"?analysis_revision={revision}"
    response = client.get(path, headers={"X-API-Key": API_KEY})
    assert response.status_code == 200, response.text
    return response.json()


def _statement(payload: dict) -> dict:
    statements = payload["text_statements"]
    assert statements, payload
    return statements[0]


def test_c_view_adopts_original_when_correction_unapplied(r6_client):
    """C 版 applied 回执不含 X：当版采用读数必须回到原文，不得沿用旧人工值。"""
    client, _ = r6_client
    target = _statement(_targets(client, REV_C))
    assert target["effective_value"] == "5,465.41", target
    assert target["effective_source"] == "original_disclosure", target


def test_b_view_adopts_manual_correction(r6_client):
    """B 版 applied 回执含 X：当版采用读数是人工校正值。"""
    client, _ = r6_client
    target = _statement(_targets(client, REV_B))
    assert target["effective_value"] == "5,455.41", target
    assert target["effective_source"] == "manual_correction", target
    assert target["effective_correction_id"] == "corr-x", target


def test_c_view_history_keeps_first_effective(r6_client):
    """X 在 C 视图历史里仍如实标注首次生效版本 B（历史事实不受当版回执影响）。"""
    client, _ = r6_client
    history = _targets(client, REV_C)["history"]
    record = next(item for item in history if item["record_id"] == "corr-x")
    assert record["effective_in_revision"] == REV_B, record
    assert record["superseded"] is False, record


def test_records_without_effect_version_excludes_applied_record(r6_client):
    """X 有 B 版生效回执：不得列入"缺生效版本明细"。"""
    client, _ = r6_client
    payload = _targets(client, REV_C)
    assert "corr-x" not in (payload.get("records_without_effect_version") or []), payload
    assert payload.get("history_note") is None, payload
