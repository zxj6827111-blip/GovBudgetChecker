"""iter_job_dirs 任务目录过滤测试。

回归场景：uploads/ 下遗留的非任务目录（如 QC 报告输出目录 reports/，只有
HTML 没有 status.json/PDF）曾被当作任务目录，collect_job_summary 对其给出
status="unknown"，前端 normalizeUiTaskStatus 兜底归为 analyzing，导致
处理队列角标恒为 1（一个并不存在的"正在处理"任务）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api import runtime


def _make_uploads(tmp_path: Path) -> Path:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    return uploads


def test_iter_job_dirs_excludes_non_job_dirs_without_status_or_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    uploads = _make_uploads(tmp_path)
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", uploads)

    # 合法任务目录：有 status.json（上传流程会立即写入）
    job_a = uploads / "aaaa"
    job_a.mkdir()
    (job_a / "status.json").write_text('{"status": "done"}', encoding="utf-8")

    # 合法任务目录：只有 PDF（status.json 尚未写入的极短窗口）
    job_b = uploads / "bbbb"
    job_b.mkdir()
    (job_b / "sample.pdf").write_bytes(b"%PDF-1.4")

    # 遗留非任务目录：只有 HTML，无 status.json 无 PDF（reports/ 同类）
    reports = uploads / "reports"
    reports.mkdir()
    (reports / "qc_report_run_1.html").write_text("<html></html>", encoding="utf-8")

    # 点开头目录照旧排除
    hidden = uploads / ".worker-heartbeats"
    hidden.mkdir()
    (hidden / "beat.json").write_text("{}", encoding="utf-8")

    dirs = {d.name for d in runtime.iter_job_dirs()}
    assert dirs == {"aaaa", "bbbb"}
    assert "reports" not in dirs
    assert ".worker-heartbeats" not in dirs


def test_iter_job_dirs_returns_empty_when_root_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path / "does-not-exist")
    assert runtime.iter_job_dirs() == []
