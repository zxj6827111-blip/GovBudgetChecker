"""RR-05：存量重分析工具的只读预览、冻结选集与断点续跑接口。

2026-11-17 第二次独立复核 RR-05A 补充：
- 最终 status=error 的任务必须记 failed，不得进断点跳过集合；
- 断点按批次指纹 + 结果 revision 匹配，旧批次/旧 revision 不静默跳过；
- 同批次恢复先识别本批已产生的新 revision，不先报 frozen_revision_changed。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/reanalyze_current_slots.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("reanalyze_current_slots_tool", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _job(tmp_path, *, use_ai=False, status="done"):
    from api import runtime

    job_dir = tmp_path / "job-1"
    job_dir.mkdir()
    (job_dir / "source.pdf").write_bytes(b"%PDF-1.4\n")
    (job_dir / "status.json").write_text(json.dumps({
        "status": status, "use_ai_assist": use_ai,
        "human_decisions": [{"issue_id": "x", "status": "confirmed"}],
    }), encoding="utf-8")
    runtime.UPLOAD_ROOT = tmp_path
    return {
        "slot_id": "slot-1", "job_uuid": "job-1", "current_document_version_id": 7,
        "file_hash": "a" * 64, "analysis_revision": "rev-1", "mode": "dual",
        "subject_org_name": "甲单位", "fiscal_year": 2026, "report_kind": "final",
    }


def test_preview_is_read_only_and_classifies_slots(tmp_path):
    module = _load_module()
    row = _job(tmp_path)
    before = (tmp_path / "job-1/status.json").read_bytes()
    preview = module._slot_preview(row, ai_policy="inherit", recover_stale_active=False)
    assert preview["disposition"] == "ready"
    assert preview["ai_mode"] == "local"
    assert preview["human_decision_count"] == 1
    assert preview["result_fingerprint"]
    assert (tmp_path / "job-1/status.json").read_bytes() == before
    frozen = module._frozen_slot(preview)
    assert frozen["file_hash"] == "a" * 64


def test_preview_marks_ai_skip_and_missing_inputs(tmp_path):
    module = _load_module()
    row = _job(tmp_path, use_ai=True)
    preview = module._slot_preview(row, ai_policy="skip", recover_stale_active=False)
    assert preview["disposition"] == "skip_ai_assist_enabled"
    inherit = module._slot_preview(row, ai_policy="inherit", recover_stale_active=False)
    assert inherit["disposition"] == "ready"
    (tmp_path / "job-1/source.pdf").unlink()
    missing = module._slot_preview(row, ai_policy="inherit", recover_stale_active=False)
    assert missing["disposition"] == "missing_pdf"


def test_checkpoint_resume_matches_batch_and_result_revision(tmp_path):
    """completed + 同批次 + 结果 revision 匹配才允许跳过。"""
    module = _load_module()
    path = tmp_path / "checkpoint.jsonl"
    path.write_text(
        json.dumps({
            "job_id": "job-1", "outcome": "completed",
            "batch_fingerprint": "batch-1", "result_revision": "rev-new",
            "file_hash": "a" * 64, "document_version_id": 7,
        }) + "\n"
        + json.dumps({
            "job_id": "job-2", "outcome": "failed",
            "batch_fingerprint": "batch-1", "result_revision": None,
        }) + "\n",
        encoding="utf-8",
    )
    records = module._load_checkpoint(path)
    assert [r["job_id"] for r in records] == ["job-1", "job-2"]
    ok = module._resumable_checkpoint_record(
        records, batch_fingerprint="batch-1", job_id="job-1",
        file_hash="a" * 64, document_version_id=7, result_revision="rev-new",
    )
    assert ok is True
    # 旧批次断点不静默复用。
    assert module._resumable_checkpoint_record(
        records, batch_fingerprint="batch-2", job_id="job-1",
        file_hash="a" * 64, document_version_id=7, result_revision="rev-new",
    ) is False
    # 当前 revision 已前进（他人重跑过）：不是断点描述的状态，不跳过。
    assert module._resumable_checkpoint_record(
        records, batch_fingerprint="batch-1", job_id="job-1",
        file_hash="a" * 64, document_version_id=7, result_revision="rev-other",
    ) is False
    # 失败回执永不跳过。
    assert module._resumable_checkpoint_record(
        records, batch_fingerprint="batch-1", job_id="job-2",
        file_hash="a" * 64, document_version_id=7, result_revision="rev-new",
    ) is False


def test_run_one_classifies_final_status_error_as_failed(tmp_path, monkeypatch):
    """流水线正常返回但最终 status=error：必须记 failed，不进断点跳过。"""
    import asyncio
    from api import main as api_main
    from api import runtime

    module = _load_module()
    row = _job(tmp_path)

    async def fake_reanalyze(job_id, body):
        return {"check_method": "local"}

    async def fake_pipeline(job_dir):
        return None

    monkeypatch.setattr(runtime, "reanalyze_job", fake_reanalyze)
    monkeypatch.setattr(api_main, "_run_pipeline", fake_pipeline)
    monkeypatch.setattr(runtime, "get_job_status_payload", lambda job_id: {
        "status": "error", "error": "pipeline failed", "analysis_revision": "rev-failed",
    })
    result = asyncio.run(module._run_one(
        "job-1", recover_stale_active=False, ai_policy="inherit",
        batch_fingerprint="batch-1", row=row,
    ))
    assert result["outcome"] == "failed"
    assert result["status"] == "error"
    assert result["error"] == "pipeline failed"
    assert result["result_revision"] == "rev-failed"
    assert result["batch_fingerprint"] == "batch-1"
    # 这条回执写进断点后也不构成跳过条件。
    assert module._resumable_checkpoint_record(
        [result], batch_fingerprint="batch-1", job_id="job-1",
        file_hash="a" * 64, document_version_id=7, result_revision="rev-failed",
    ) is False


def test_run_one_non_terminal_status_is_not_completed(tmp_path, monkeypatch):
    """queued/processing 等非终态不能记 completed。"""
    import asyncio
    from api import main as api_main
    from api import runtime

    module = _load_module()
    row = _job(tmp_path)

    async def fake_reanalyze(job_id, body):
        return {"check_method": "local"}

    async def fake_pipeline(job_dir):
        return None

    monkeypatch.setattr(runtime, "reanalyze_job", fake_reanalyze)
    monkeypatch.setattr(api_main, "_run_pipeline", fake_pipeline)
    monkeypatch.setattr(runtime, "get_job_status_payload", lambda job_id: {
        "status": "processing", "analysis_revision": "rev-running",
    })
    result = asyncio.run(module._run_one(
        "job-1", recover_stale_active=False, ai_policy="inherit", row=row,
    ))
    assert result["outcome"] == "failed"
    assert result["error"] == "final_status_not_terminal:processing"

    monkeypatch.setattr(runtime, "get_job_status_payload", lambda job_id: {
        "status": "review_required", "analysis_revision": "rev-review",
    })
    result = asyncio.run(module._run_one(
        "job-1", recover_stale_active=False, ai_policy="inherit", row=row,
    ))
    assert result["outcome"] == "completed"
    assert result["status"] == "review_required"
    assert result["coverage_status"] == ""


def test_run_records_input_and_result_revisions(tmp_path, monkeypatch):
    """断点回执携带输入/结果 revision 与批次指纹，供恢复身份校验。"""
    import asyncio
    from api import main as api_main
    from api import runtime

    module = _load_module()
    row = _job(tmp_path)

    async def fake_reanalyze(job_id, body):
        return {"check_method": "local"}

    async def fake_pipeline(job_dir):
        return None

    monkeypatch.setattr(runtime, "reanalyze_job", fake_reanalyze)
    monkeypatch.setattr(api_main, "_run_pipeline", fake_pipeline)
    monkeypatch.setattr(runtime, "get_job_status_payload", lambda job_id: {
        "status": "done", "analysis_revision": "rev-new-20261117",
    })
    result = asyncio.run(module._run_one(
        "job-1", recover_stale_active=False, ai_policy="inherit",
        batch_fingerprint="batch-9", row=row,
    ))
    assert result["outcome"] == "completed"
    assert result["input_revision"] == "rev-1"
    assert result["result_revision"] == "rev-new-20261117"
    assert result["file_hash"] == "a" * 64
    assert result["document_version_id"] == 7
    assert result["ai_mode"] == "local"
    # 同批次恢复：result_revision 匹配当前行 → 跳过成立。
    assert module._resumable_checkpoint_record(
        [result], batch_fingerprint="batch-9", job_id="job-1",
        file_hash="a" * 64, document_version_id=7, result_revision="rev-new-20261117",
    ) is True


def test_selection_fingerprint_changes_with_policy_and_selection(tmp_path):
    """批次指纹覆盖选集身份与运行策略；任一变化都不是同一批次。"""
    module = _load_module()
    entries = [{"job_uuid": "job-1", "slot_id": "s1", "document_version_id": 7,
                "file_hash": "a" * 64, "analysis_revision": "rev-1"}]
    base = module._selection_fingerprint(entries, ai_policy="inherit", recover_stale_active=False)
    assert base == module._selection_fingerprint(
        list(reversed(entries)), ai_policy="inherit", recover_stale_active=False)
    assert base != module._selection_fingerprint(
        entries, ai_policy="skip", recover_stale_active=False)
    changed = [dict(entries[0], analysis_revision="rev-2")]
    assert base != module._selection_fingerprint(
        changed, ai_policy="inherit", recover_stale_active=False)


def test_cli_exposes_preview_freeze_and_checkpoint_options():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0
    for token in ("--dry-run", "--preview", "--freeze-list", "--freeze-out",
                  "--checkpoint", "--output", "--report"):
        assert token in result.stdout
