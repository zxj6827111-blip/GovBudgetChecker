"""队列轮询只在状态文件变化时重新解析，且不在事件循环里读文件。"""
import json

import pytest

from api import runtime
from api.job_queue import DurableJobQueue


async def _noop_runner(_job_dir):
    return None


def _write_status(job_dir, **fields):
    job_dir.mkdir(exist_ok=True)
    (job_dir / "status.json").write_text(json.dumps({"job_id": job_dir.name, **fields}))


@pytest.mark.asyncio
async def test_discovery_parses_each_status_file_only_when_it_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    _write_status(tmp_path / "done-job", status="done")
    _write_status(tmp_path / "queued-job", status="queued")
    parsed = []
    original_read = runtime.read_json_file
    monkeypatch.setattr(runtime, "read_json_file",
                        lambda path, default=None: parsed.append(path.parent.name) or original_read(path, default=default))
    queue = DurableJobQueue(_noop_runner, max_workers=1, resume_on_start=False)

    first = queue._scan_resumable_jobs()
    assert [job_dir.name for job_dir, _ in first] == ["queued-job"]
    assert sorted(parsed) == ["done-job", "queued-job"]

    parsed.clear()
    restarted = DurableJobQueue(_noop_runner, max_workers=1, resume_on_start=False)
    assert [job_dir.name for job_dir, _ in restarted._scan_resumable_jobs()] == ["queued-job"]
    assert parsed == [], "重启后复用磁盘索引，无需解析历史审查结果"

    second = queue._scan_resumable_jobs()
    assert [job_dir.name for job_dir, _ in second] == ["queued-job"]
    assert parsed == [], "未变化的状态文件不再解析"

    _write_status(tmp_path / "done-job", status="queued", retry_not_before=123.0)
    (tmp_path / "queued-job" / "status.json").unlink()
    (tmp_path / "queued-job" / "queue_status.json").unlink()
    (tmp_path / "queued-job").rmdir()
    third = queue._scan_resumable_jobs()
    assert [(job_dir.name, retry) for job_dir, retry in third] == [("done-job", 123.0)]
    assert parsed == ["done-job"]
    assert set(queue._status_probe_cache) == {"done-job"}, "已删除任务的缓存要清掉"


@pytest.mark.asyncio
async def test_discovery_enqueues_resumable_jobs_and_marks_requeued(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    _write_status(tmp_path / "queued-job", status="queued")
    _write_status(tmp_path / "later-job", status="queued", retry_not_before=10**12)
    queue = DurableJobQueue(_noop_runner, max_workers=1, resume_on_start=False)
    queue._scan_resumable_jobs()  # 预先缓存，续跑标记必须仍能拿到完整状态
    queue._started = True
    assert await queue.discover_pending_jobs(mark_requeued=True) == 1
    recovered = runtime.read_json_file(tmp_path / "queued-job" / "status.json")
    assert recovered["status"] == "queued"
    assert recovered["message"] == "analysis task resumed after service restart"
    assert queue._queue.get_nowait() == "queued-job"


@pytest.mark.asyncio
async def test_status_writer_updates_queue_index_and_recovers_if_index_is_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    job = tmp_path / "job"
    job.mkdir()
    status = job / "status.json"
    runtime.write_json_file(status, {"status": "queued", "retry_not_before": 123})
    queue = DurableJobQueue(_noop_runner, max_workers=1, resume_on_start=False)
    assert queue._scan_resumable_jobs() == [(job, 123)]

    runtime.merge_job_status(job, {"status": "done", "result": {"issues": [1, 2]}})
    assert not DurableJobQueue(_noop_runner)._scan_resumable_jobs()
    (job / "queue_status.json").write_text("invalid JSON")
    assert not DurableJobQueue(_noop_runner)._scan_resumable_jobs()
    signature = runtime.queue_status_signature(status.stat())
    assert runtime.read_queue_status_index(status, signature) == ("done", 123)
