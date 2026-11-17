"""RR-05：存量重分析工具的只读预览、冻结选集与断点续跑接口。"""

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


def test_checkpoint_resume_reads_only_completed(tmp_path):
    module = _load_module()
    path = tmp_path / "checkpoint.jsonl"
    path.write_text(
        json.dumps({"job_id": "job-1", "outcome": "completed"}) + "\n"
        + json.dumps({"job_id": "job-2", "outcome": "failed"}) + "\n",
        encoding="utf-8",
    )
    assert module._load_checkpoint(path) == {"job-1"}


def test_cli_exposes_preview_freeze_and_checkpoint_options():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0
    for token in ("--dry-run", "--preview", "--freeze-list", "--freeze-out",
                  "--checkpoint", "--output", "--report"):
        assert token in result.stdout
