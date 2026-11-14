"""D5 入库可靠性：统一产物写入的幂等重试与错误可见性。

背景（2026-11-12 民政批次诊断·婚姻收养登记中心/救助事务中心）：两任务在
unified_artifact_write 阶段 parse_runs INSERT/ON CONFLICT 处 asyncpg 抛
TimeoutError，str(exc) 为空串，错误封装一路丢失类型与阶段，最终显示成
"trusted_ingest_not_committed: None"。
"""

import asyncio
from types import SimpleNamespace as NS

import pytest

from src.schemas.parse_artifact import ParseArtifact
from src.services import structured_ingest_runner as runner


def _artifact():
    return ParseArtifact(source_file_hash="a" * 64, parse_run_uid="run-uid-1", page_count=1)


class _FakeConn:
    def __init__(self, row=None):
        self.row = row
        self.fetchrow_calls = 0

    async def fetchrow(self, *_args, **_kwargs):
        self.fetchrow_calls += 1
        return self.row


@pytest.mark.parametrize("exc,expected", [
    (TimeoutError(), True),
    (ConnectionError(), True),
    (OSError(), True),
    (ValueError("constraint"), False),
    (KeyError("x"), False),
])
def test_transient_error_classification(exc, expected):
    assert runner._is_transient_ingest_error(exc) is expected


def test_transient_pg_sqlstate_classes():
    import asyncpg

    assert runner._is_transient_ingest_error(asyncpg.PostgresConnectionError()) is True
    resource = asyncpg.PostgresError("insufficient resources")
    resource.sqlstate = "53300"
    assert runner._is_transient_ingest_error(resource) is True
    syntax_error = asyncpg.PostgresError("bad sql")
    syntax_error.sqlstate = "42601"
    assert runner._is_transient_ingest_error(syntax_error) is False


@pytest.fixture
def _patched_pool(monkeypatch):
    """DatabaseConnection.acquire/release 与 persist_parse_artifact 可控替换。"""
    conns = []

    async def fake_acquire():
        conn = _FakeConn()
        conns.append(conn)
        return conn

    async def fake_release(conn):
        pass

    monkeypatch.setattr(runner.DatabaseConnection, "acquire", classmethod(lambda cls: fake_acquire()))
    monkeypatch.setattr(runner.DatabaseConnection, "release", staticmethod(fake_release))
    return conns


def _patch_persist(monkeypatch, outcomes):
    """outcomes: 依次返回（抛异常 or 返回值）。

    patch 落在 structured_ingest_runner 的模块全局名上——生产代码按该全局名
    调用（monkeypatch 可控），与既有测试的替换方式一致。
    """
    calls = NS(count=0)

    async def fake_persist(conn, artifact, **_kwargs):
        calls.count += 1
        outcome = outcomes[min(calls.count - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(runner, "persist_parse_artifact", fake_persist)
    return calls


def test_timeout_but_committed_adopts_existing_run(monkeypatch, _patched_pool):
    """命令超时但服务端实际已提交：回查命中后采纳既有运行，不盲目重发。"""
    calls = _patch_persist(monkeypatch, [TimeoutError(), 999])
    conns = []

    async def acquire_row():
        conn = _FakeConn(row={"id": 777, "status": "completed"})
        conns.append(conn)
        return conn

    monkeypatch.setattr(runner.DatabaseConnection, "acquire", classmethod(lambda cls: acquire_row()))

    async def run():
        conn = await runner.DatabaseConnection.acquire()
        return await runner._persist_artifact_with_retry(
            conn, _artifact(), job_id="job-x",
            document_version_id=1, org_name="甲局", fiscal_year=2025)

    parse_run_id, _ = asyncio.run(run())
    assert parse_run_id == 777
    assert calls.count == 1, "提交状态回查命中后不得再发写入"
    assert conns, "重试必须换新连接回查"


def test_transient_failure_without_commit_retries_write(monkeypatch, _patched_pool):
    """回查未命中（未提交）→ 换新连接重试写入并成功。"""
    calls = _patch_persist(monkeypatch, [TimeoutError(), 555])
    conns = []

    async def acquire_row():
        conn = _FakeConn(row=None)
        conns.append(conn)
        return conn

    monkeypatch.setattr(runner.DatabaseConnection, "acquire", classmethod(lambda cls: acquire_row()))

    async def run():
        conn = await runner.DatabaseConnection.acquire()
        return await runner._persist_artifact_with_retry(
            conn, _artifact(), job_id="job-x",
            document_version_id=1, org_name="甲局", fiscal_year=2025)

    parse_run_id, _ = asyncio.run(run())
    assert parse_run_id == 555 and calls.count == 2


def test_non_transient_error_raises_immediately(monkeypatch, _patched_pool):
    calls = _patch_persist(monkeypatch, [ValueError("constraint violation")])
    conns = []

    async def acquire_row():
        conn = _FakeConn(row=None)
        conns.append(conn)
        return conn

    monkeypatch.setattr(runner.DatabaseConnection, "acquire", classmethod(lambda cls: acquire_row()))

    async def run():
        conn = await runner.DatabaseConnection.acquire()
        return await runner._persist_artifact_with_retry(
            conn, _artifact(), job_id="job-x",
            document_version_id=1, org_name="甲局", fiscal_year=2025)

    with pytest.raises(ValueError):
        asyncio.run(run())
    assert calls.count == 1, "非瞬时错误不得重试"


def test_attempts_exhausted_raises_last_transient_error(monkeypatch, _patched_pool):
    monkeypatch.setenv("GOVBUDGET_INGEST_PERSIST_ATTEMPTS", "2")
    calls = _patch_persist(monkeypatch, [TimeoutError(), TimeoutError()])
    conns = []

    async def acquire_row():
        conn = _FakeConn(row=None)
        conns.append(conn)
        return conn

    monkeypatch.setattr(runner.DatabaseConnection, "acquire", classmethod(lambda cls: acquire_row()))

    async def run():
        conn = await runner.DatabaseConnection.acquire()
        return await runner._persist_artifact_with_retry(
            conn, _artifact(), job_id="job-x",
            document_version_id=1, org_name="甲局", fiscal_year=2025)

    with pytest.raises(TimeoutError):
        asyncio.run(run())
    assert calls.count == 2, "重试上限由 GOVBUDGET_INGEST_PERSIST_ATTEMPTS 控制"
