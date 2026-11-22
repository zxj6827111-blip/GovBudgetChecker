"""2026-11-18 批量重跑事故的加固回归：僵尸锁即时回收、默认并发收敛、
连接池上限可配置、入库范围判定按签名缓存。

事故链：32 worker × 池上限 10 → acquire 超时；98% 阶段同步全量扫描
status.json 冻住事件循环；看门狗 SIGKILL 后任务锁残留，重启队列干等
900 秒 TTL，形成"干等→涌入→强杀"死循环。这里逐项锁住修复行为。
"""
import json
import os
import subprocess
import sys
import time

import pytest

from api import runtime
from api.job_queue import DurableJobQueue


async def _noop_runner(_job_dir):
    return None


def _write_status(job_dir, **fields):
    job_dir.mkdir(exist_ok=True)
    (job_dir / "status.json").write_text(json.dumps({"job_id": job_dir.name, **fields}))


def _spawn_dead_pid() -> int:
    """启动并回收一个短命子进程，返回一个确定已退出的 PID。"""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


@pytest.mark.asyncio
async def test_stale_claim_from_dead_owner_is_reclaimed_immediately(tmp_path, monkeypatch):
    """属主进程已死的锁要立即回收，不能干等 TTL（SIGKILL 僵尸锁）。"""
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    job_dir = tmp_path / "zombie-job"
    _write_status(job_dir, status="queued")

    dead_pid = _spawn_dead_pid()
    claim_path = job_dir / ".worker-claim.json"
    # 旧版锁不含 pid 字段，worker_id 前缀即属主 PID——兼容这条回退路径。
    claim_path.write_text(
        json.dumps({"token": f"{dead_pid}-deadbeef", "worker_id": f"{dead_pid}-deadbeef", "ts": time.time()})
    )
    # 把 mtime 拨回 TTL 之内，证明回收靠的是属主死亡判定而非过期。
    past = time.time() - 5
    os.utime(claim_path, (past, past))

    queue = DurableJobQueue(_noop_runner, max_workers=1, resume_on_start=False)
    assert queue._acquire_claim(job_dir) is not None, "属主已死的锁应立即回收"


@pytest.mark.asyncio
async def test_live_owner_claim_is_not_stolen_before_ttl(tmp_path, monkeypatch):
    """属主进程仍活着的锁，TTL 之内不能被其他 worker 抢走。"""
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    job_dir = tmp_path / "live-job"
    _write_status(job_dir, status="queued")

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        claim_path = job_dir / ".worker-claim.json"
        claim_path.write_text(
            json.dumps({"token": "live-token", "worker_id": f"{proc.pid}-aa", "pid": proc.pid, "ts": time.time()})
        )
        queue = DurableJobQueue(_noop_runner, max_workers=1, resume_on_start=False)
        assert queue._acquire_claim(job_dir) is None, "属主活着的锁不能抢"
    finally:
        proc.kill()
        proc.wait()


def test_compute_queue_workers_defaults_are_conservative(monkeypatch):
    """未显式配置时默认并发收敛在 4–6，不再随核数飙到 32。"""
    from api.queue_runtime import compute_queue_workers

    monkeypatch.delenv("JOB_QUEUE_WORKERS", raising=False)
    monkeypatch.delenv("AI_SEQUENTIAL_MODE", raising=False)
    workers, sequential = compute_queue_workers()
    assert sequential is True  # 默认仍是 AI 串行模式
    assert 4 <= workers <= 6, f"默认 worker 应在 4–6，实际 {workers}"

    monkeypatch.setenv("JOB_QUEUE_WORKERS", "3")
    assert compute_queue_workers()[0] == 3, "显式 JOB_QUEUE_WORKERS 必须优先"


@pytest.mark.asyncio
async def test_pool_max_size_default_and_env_override(monkeypatch):
    """池上限默认 20，可被 GOVBUDGET_POOL_MAX_SIZE 覆盖，坏值回退默认。"""
    from src.db import connection as connection_module

    captured = {}

    class _FakeConn:
        async def execute(self, *_args, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return None

    class _FakePool:
        def __init__(self, max_size):
            self._max_size = max_size

        def acquire(self):
            # asyncpg 的 acquire() 返回可 await 也可 async-with 的上下文对象
            return _FakeConn()

        async def release(self, _conn):
            return None

    async def _fake_create_pool(_url, *, min_size, max_size, command_timeout):
        captured.update(min_size=min_size, max_size=max_size, command_timeout=command_timeout)
        return _FakePool(max_size)

    monkeypatch.setattr(connection_module.asyncpg, "create_pool", _fake_create_pool)
    monkeypatch.setattr(connection_module.DatabaseConnection, "_pool", None)
    monkeypatch.setattr(connection_module.DatabaseConnection, "_schema", "public")

    try:
        monkeypatch.delenv("GOVBUDGET_POOL_MAX_SIZE", raising=False)
        await connection_module.DatabaseConnection.initialize("postgres://u:p@localhost/db")
        assert captured["max_size"] == 20
        assert captured["min_size"] == 2

        monkeypatch.setattr(connection_module.DatabaseConnection, "_pool", None)
        monkeypatch.setenv("GOVBUDGET_POOL_MAX_SIZE", "37")
        await connection_module.DatabaseConnection.initialize("postgres://u:p@localhost/db")
        assert captured["max_size"] == 37

        monkeypatch.setattr(connection_module.DatabaseConnection, "_pool", None)
        monkeypatch.setenv("GOVBUDGET_POOL_MAX_SIZE", "not-a-number")
        await connection_module.DatabaseConnection.initialize("postgres://u:p@localhost/db")
        assert captured["max_size"] == 20, "坏配置回退默认而不是崩溃"
    finally:
        monkeypatch.setattr(connection_module.DatabaseConnection, "_pool", None)


@pytest.mark.asyncio
async def test_resolve_latest_structured_ingest_job_parses_each_status_once(tmp_path, monkeypatch):
    """入库范围判定按签名缓存：未变化的 status.json 不再重复全量解析。"""
    monkeypatch.setattr(runtime, "UPLOAD_ROOT", tmp_path)
    for job_id, version in (("job-a", 100.0), ("job-b", 200.0)):
        _write_status(
            tmp_path / job_id,
            status="done",
            filename=f"{job_id}.pdf",
            organization_id="org-1",
            organization_name="Org One",
            fiscal_year="2025",
            report_year=2025,
            doc_type="dept_final",
            report_kind="final",
            version_created_at=version,
            job_created_at=version,
        )

    parsed = []
    original_read = runtime.read_json_file
    monkeypatch.setattr(
        runtime,
        "read_json_file",
        lambda path, default=None: parsed.append(path.parent.name) or original_read(path, default=default),
    )

    kwargs = dict(
        organization_id="org-1",
        organization_name="Org One",
        fiscal_year="2025",
        report_year=2025,
        doc_type="dept_final",
        report_kind="final",
    )
    current_payload = {"filename": "job-a.pdf", "version_created_at": 100.0, "job_created_at": 100.0, **kwargs}
    first = runtime.resolve_latest_structured_ingest_job(
        "job-a", filename="job-a.pdf", current_status_payload=dict(current_payload), **kwargs
    )
    assert first["is_latest"] is False
    assert first["latest_job_id"] == "job-b"

    second = runtime.resolve_latest_structured_ingest_job(
        "job-a", filename="job-a.pdf", current_status_payload=dict(current_payload), **kwargs
    )
    assert second == first
    # job-b 只在第一次被完整解析；job-a 走调用方传入的 payload，本就不读盘。
    assert parsed.count("job-b") == 1, "未变化的任务不应重复解析 status.json"
