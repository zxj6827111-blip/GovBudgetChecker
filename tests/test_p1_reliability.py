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
