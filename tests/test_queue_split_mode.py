import asyncio
import io
import os
import time
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "true")

from api.main import app
from api import queue_runtime, runtime
from api.job_queue import DurableJobQueue


def _pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >> endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"trailer << /Size 4 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
    )


def test_api_role_keeps_job_queued_without_inline_fallback(monkeypatch):
    monkeypatch.setenv("JOB_QUEUE_ENABLED", "true")
    monkeypatch.setenv("JOB_QUEUE_ROLE", "api")
    monkeypatch.setenv("JOB_QUEUE_INLINE_FALLBACK", "false")

    client = TestClient(app)

    upload = client.post(
        "/api/documents/upload",
        files={"file": ("split_mode.pdf", io.BytesIO(_pdf_bytes()), "application/pdf")},
    )
    assert upload.status_code == 200
    job_id = upload.json()["job_id"]

    run = client.post(f"/api/documents/{job_id}/run", json={"mode": "dual"})
    assert run.status_code == 200
    payload = run.json()
    assert payload["status"] == "started"
    assert payload["dispatch"] == "queued_waiting_worker"

    status = client.get(f"/api/jobs/{job_id}/status")
    assert status.status_code == 200
    assert status.json()["status"] == "queued"


def test_ready_endpoint_requires_recent_worker_heartbeat_in_api_role(monkeypatch, tmp_path):
    monkeypatch.setenv("JOB_QUEUE_ENABLED", "true")
    monkeypatch.setenv("JOB_QUEUE_ROLE", "api")
    monkeypatch.setenv("JOB_QUEUE_INLINE_FALLBACK", "false")

    upload_root = tmp_path / "uploads"
    heartbeat_dir = upload_root / ".worker-heartbeats"
    heartbeat_dir.mkdir(parents=True)
    runtime.write_json_file(
        heartbeat_dir / "worker.json",
        {"worker_id": "worker", "ts": time.time()},
    )
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", upload_root)

    client = TestClient(app)
    ready = client.get("/ready")
    assert ready.status_code == 200
    payload = ready.json()
    assert payload["details"]["redacted"] is True
    assert payload["checks"]["job_queue_started"] is True


def test_ready_endpoint_rejects_api_role_without_worker(monkeypatch, tmp_path):
    monkeypatch.setenv("JOB_QUEUE_ENABLED", "true")
    monkeypatch.setenv("JOB_QUEUE_ROLE", "api")
    monkeypatch.setenv("JOB_QUEUE_INLINE_FALLBACK", "false")
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", upload_root)

    ready = TestClient(app).get("/ready")

    assert ready.status_code == 503
    assert ready.json()["status"] == "not_ready"


def test_inline_fallback_defaults_off_outside_testing(monkeypatch):
    monkeypatch.delenv("JOB_QUEUE_INLINE_FALLBACK", raising=False)
    monkeypatch.delenv("TESTING", raising=False)

    assert queue_runtime.allow_inline_fallback() is False


async def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition was not met before timeout")


@pytest.mark.asyncio
async def test_worker_discovers_job_created_after_start(monkeypatch, tmp_path: Path):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", upload_root)

    async def runner(job_dir: Path) -> None:
        runtime.merge_job_status(job_dir, {"status": "done"})

    queue = DurableJobQueue(runner, max_workers=1, poll_interval_seconds=0.05)
    await queue.start()
    try:
        job_dir = upload_root / "late-job"
        job_dir.mkdir()
        runtime.write_json_file(job_dir / "status.json", {"status": "queued"})
        await _wait_until(
            lambda: runtime.read_json_file(job_dir / "status.json", default={}).get("status") == "done"
        )
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_two_workers_claim_persisted_job_once(monkeypatch, tmp_path: Path):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", upload_root)
    job_dir = upload_root / "shared-job"
    job_dir.mkdir()
    runtime.write_json_file(job_dir / "status.json", {"status": "queued"})
    executions: list[str] = []

    async def runner(claimed_job_dir: Path) -> None:
        executions.append(claimed_job_dir.name)
        await asyncio.sleep(0.1)
        runtime.merge_job_status(claimed_job_dir, {"status": "done"})

    first = DurableJobQueue(runner, max_workers=1, poll_interval_seconds=0.05)
    second = DurableJobQueue(runner, max_workers=1, poll_interval_seconds=0.05)
    await first.start()
    await second.start()
    try:
        await _wait_until(
            lambda: runtime.read_json_file(job_dir / "status.json", default={}).get("status") == "done"
        )
        assert executions == ["shared-job"]
    finally:
        await first.stop()
        await second.stop()

