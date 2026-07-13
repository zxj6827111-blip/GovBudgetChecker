"""P1 reliability tests: atomic write_json_file and pipeline timeout."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("TESTING", "true")

from api import runtime


# ---------------------------------------------------------------------------
# write_json_file — atomic write
# ---------------------------------------------------------------------------


class TestAtomicWriteJsonFile:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        target = tmp_path / "data.json"
        payload = {"key": "value", "count": 42, "nested": {"a": [1, 2]}}
        runtime.write_json_file(target, payload)
        assert target.exists()
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded == payload

    def test_overwrite_preserves_valid_json(self, tmp_path: Path) -> None:
        target = tmp_path / "data.json"
        runtime.write_json_file(target, {"v": 1})
        runtime.write_json_file(target, {"v": 2})
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded == {"v": 2}

    def test_no_temp_file_left_on_success(self, tmp_path: Path) -> None:
        target = tmp_path / "data.json"
        runtime.write_json_file(target, {"ok": True})
        tmp_candidates = list(tmp_path.glob("*.tmp"))
        assert tmp_candidates == [], f"stale temp files: {tmp_candidates}"

    def test_old_file_preserved_on_write_error(self, tmp_path: Path) -> None:
        target = tmp_path / "data.json"
        runtime.write_json_file(target, {"original": True})
        original_content = target.read_text(encoding="utf-8")

        class BadPayload:
            pass

        with pytest.raises(TypeError):
            runtime.write_json_file(target, {"bad": BadPayload()})

        assert target.read_text(encoding="utf-8") == original_content

    def test_no_temp_file_left_on_error(self, tmp_path: Path) -> None:
        target = tmp_path / "data.json"
        target.write_text("{}", encoding="utf-8")

        class BadPayload:
            pass

        with pytest.raises(TypeError):
            runtime.write_json_file(target, {"bad": BadPayload()})

        tmp_candidates = list(tmp_path.glob("*.tmp"))
        assert tmp_candidates == [], f"stale temp files after error: {tmp_candidates}"

    def test_unicode_content_roundtrip(self, tmp_path: Path) -> None:
        target = tmp_path / "data.json"
        payload = {"org": "上海市普陀区财政局", "issues": ["勾稽错误", "求和不平"]}
        runtime.write_json_file(target, payload)
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded == payload

    def test_concurrent_writes_no_conflict(self, tmp_path: Path) -> None:
        """Multiple write_json_file calls to different files in same dir."""
        targets = [tmp_path / f"job_{i}.json" for i in range(10)]
        for i, target in enumerate(targets):
            runtime.write_json_file(target, {"index": i})
        for i, target in enumerate(targets):
            loaded = json.loads(target.read_text(encoding="utf-8"))
            assert loaded == {"index": i}
        assert list(tmp_path.glob("*.tmp")) == []


def test_rejected_upload_cleanup_does_not_replace_business_error(
    tmp_path: Path, monkeypatch
) -> None:
    job_dir = tmp_path / "locked-job"
    job_dir.mkdir()
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)

    def _locked_remove(_job_dir: Path, **_kwargs) -> None:
        raise PermissionError("file is locked")

    monkeypatch.setattr(runtime, "_remove_job_dir", _locked_remove)

    assert runtime.cleanup_uploaded_job("locked-job") is False
    assert job_dir.exists()


# ---------------------------------------------------------------------------
# Pipeline timeout
# ---------------------------------------------------------------------------


class TestPipelineTimeout:
    @pytest.mark.asyncio
    async def test_pipeline_timeout_writes_error_status(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from api import main as pipeline_mod

        job_dir = tmp_path / "job-timeout-test"
        job_dir.mkdir(parents=True)

        async def _slow_inner(_job_dir: Path) -> None:
            await asyncio.sleep(999)

        monkeypatch.setattr(pipeline_mod, "_run_pipeline_inner", _slow_inner)
        monkeypatch.setattr(
            pipeline_mod, "persist_analysis_job_snapshot", AsyncMock()
        )
        monkeypatch.setenv("PIPELINE_TIMEOUT_SEC", "1")

        await pipeline_mod._run_pipeline(job_dir)

        status_file = job_dir / "status.json"
        assert status_file.exists()
        payload = json.loads(status_file.read_text(encoding="utf-8"))
        assert payload["status"] == "error"
        assert "pipeline_timeout" in payload["error"]

    @pytest.mark.asyncio
    async def test_pipeline_normal_completion_untouched(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Normal pipeline that finishes quickly should not be affected."""
        from api import main as pipeline_mod

        job_dir = tmp_path / "job-normal-test"
        job_dir.mkdir(parents=True)
        (job_dir / "dummy.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")

        called = False

        async def _fast_inner(_job_dir: Path) -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(pipeline_mod, "_run_pipeline_inner", _fast_inner)
        monkeypatch.setattr(
            pipeline_mod, "persist_analysis_job_snapshot", AsyncMock()
        )
        monkeypatch.setenv("PIPELINE_TIMEOUT_SEC", "600")

        await pipeline_mod._run_pipeline(job_dir)

        assert called is True

    @pytest.mark.asyncio
    async def test_rule_failure_writes_error_instead_of_empty_done_result(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from api import main as pipeline_mod
        from src.services.rule_process import RuleExecutionError

        job_dir = tmp_path / "job-rule-failure"
        job_dir.mkdir()
        (job_dir / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
        runtime.write_json_file(
            job_dir / "status.json",
            {"status": "queued", "mode": "legacy", "use_local_rules": True},
        )

        class _Pdf:
            pages = [object()]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        monkeypatch.setattr(pipeline_mod.pdfplumber, "open", lambda _path: _Pdf())
        monkeypatch.setattr(
            pipeline_mod, "_extract_visible_text_from_page", lambda _page: "公开材料"
        )
        monkeypatch.setattr(
            pipeline_mod, "_extract_tables_from_page", lambda _page: []
        )
        monkeypatch.setattr(
            pipeline_mod,
            "run_rules_in_process",
            AsyncMock(side_effect=RuleExecutionError("rule crashed")),
        )
        monkeypatch.setattr(
            pipeline_mod, "persist_analysis_job_snapshot", AsyncMock(return_value=True)
        )
        monkeypatch.setattr(pipeline_mod.settings, "get", lambda *_args: False)

        await pipeline_mod._run_pipeline_inner(job_dir)

        payload = runtime.read_json_file(job_dir / "status.json", default={})
        assert payload["status"] == "error"
        assert "local_rules_failed" in payload["error"]
        assert payload.get("result") is None
