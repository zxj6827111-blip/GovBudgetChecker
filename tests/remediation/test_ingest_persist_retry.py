"""R1 修复回归：统一产物入库重试的事务/连接所有权。

Codex 独立审核 R1：旧实现在瞬时错误时更换连接，而外层 storage_tx 仍指向
原连接——提交落在已释放连接上（InterfaceError）。修复后：

- 重试**永不更换连接**（同一连接上重试；asyncpg 命令超时会重置连接使其回到
  可用状态）；调用方的事务/连接引用与 finally 释放责任不发生转移。
- 外层事务激活（conn.is_in_transaction()）时瞬时失败立即上抛——保存点已被
  服务端重置摧毁，任何续跑都会在失效事务上操作；由调用方回滚整个工作单元。
- 独立事务场景：重试前按 parse_run_uid 回查提交状态；命中采纳，回查失败
  视为状态未知、凭 uid 幂等性安全重发。

真实 PG 故障注入（隔离库）：验证回滚、外层事务中止、连接可释放、部分写入
不可发布、重试耗尽后连接仍可用。
"""

import asyncio
import os
from types import SimpleNamespace as NS

import pytest

from src.schemas.parse_artifact import ParseArtifact
from src.services import structured_ingest_runner as runner

TEST_DSN = os.getenv("GOVBUDGET_RETRY_TEST_DATABASE_URL", "")


def _artifact():
    return ParseArtifact(source_file_hash="a" * 64, parse_run_uid="run-uid-r1", page_count=1)


@pytest.mark.parametrize("exc,expected", [
    (TimeoutError(), True),
    (ConnectionError(), True),
    (OSError(), True),
    (ValueError("constraint"), False),
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


def _patch_persist(monkeypatch, outcomes):
    calls = NS(count=0)

    async def fake_persist(conn, artifact, **_kwargs):
        calls.count += 1
        outcome = outcomes[min(calls.count - 1, len(outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(runner, "persist_parse_artifact", fake_persist)
    return calls


def test_outer_transaction_abort_raises_and_never_swaps(monkeypatch):
    """外层事务激活时瞬时失败：立即上抛，连接对象保持不变（不换连接）。"""
    calls = _patch_persist(monkeypatch, [TimeoutError()])
    released = []

    async def fake_release(conn):
        released.append(conn)

    monkeypatch.setattr(runner.DatabaseConnection, "release", staticmethod(fake_release))

    class _Conn:
        def is_in_transaction(self):
            return True  # storage_tx 场景

    conn = _Conn()
    with pytest.raises(TimeoutError):
        asyncio.run(runner._persist_artifact_with_retry(
            conn, _artifact(), job_id="job-x",
            document_version_id=1, org_name="甲局", fiscal_year=2025))
    assert calls.count == 1, "外层事务内不得重试（保存点已被服务端重置）"
    assert not released, "连接释放责任留在调用方，helper 不得代为释放"


def test_standalone_retry_same_connection_with_uid_recheck(monkeypatch):
    """独立事务场景：同连接重试；回查命中已完成运行则采纳。"""
    calls = _patch_persist(monkeypatch, [TimeoutError(), TimeoutError(), 555])
    fetchrow_calls = NS(count=0)

    class _Conn:
        def is_in_transaction(self):
            return False

        async def fetchrow(self, *_args, **_kwargs):
            fetchrow_calls.count += 1
            if fetchrow_calls.count == 1:
                return None  # 第一次回查：未提交
            return {"id": 777, "status": "completed"}  # 第二次回查命中

    conn = _Conn()
    result = asyncio.run(runner._persist_artifact_with_retry(
        conn, _artifact(), job_id="job-x",
        document_version_id=1, org_name="甲局", fiscal_year=2025))
    assert result == 777
    assert calls.count == 2, "回查命中后采纳，不再重发写入"
    assert fetchrow_calls.count == 2


def test_recheck_failure_treated_as_unknown_and_retries(monkeypatch):
    """回查失败=提交状态未知：凭 uid 幂等性安全重发，不盲目假设未提交。"""
    calls = _patch_persist(monkeypatch, [TimeoutError(), 556])

    class _Conn:
        def is_in_transaction(self):
            return False

        async def fetchrow(self, *_args, **_kwargs):
            raise ConnectionError("recheck connection lost")

    conn = _Conn()
    result = asyncio.run(runner._persist_artifact_with_retry(
        conn, _artifact(), job_id="job-x",
        document_version_id=1, org_name="甲局", fiscal_year=2025))
    assert result == 556 and calls.count == 2


def test_attempts_exhausted_raises_last_transient_error(monkeypatch):
    monkeypatch.setenv("GOVBUDGET_INGEST_PERSIST_ATTEMPTS", "2")
    calls = _patch_persist(monkeypatch, [TimeoutError(), TimeoutError()])

    class _Conn:
        def is_in_transaction(self):
            return False

        async def fetchrow(self, *_args, **_kwargs):
            return None

    conn = _Conn()
    with pytest.raises(TimeoutError):
        asyncio.run(runner._persist_artifact_with_retry(
            conn, _artifact(), job_id="job-x",
            document_version_id=1, org_name="甲局", fiscal_year=2025))
    assert calls.count == 2


def test_non_transient_error_raises_immediately(monkeypatch):
    calls = _patch_persist(monkeypatch, [ValueError("constraint violation")])

    class _Conn:
        def is_in_transaction(self):
            return False

    conn = _Conn()
    with pytest.raises(ValueError):
        asyncio.run(runner._persist_artifact_with_retry(
            conn, _artifact(), job_id="job-x",
            document_version_id=1, org_name="甲局", fiscal_year=2025))
    assert calls.count == 1


# ---------------------------------------------------------------------------
# 真实 PG 故障注入（隔离库）：外层事务回滚、部分写入不可发布、连接可释放。
# 需要 GOVBUDGET_RETRY_TEST_DATABASE_URL 指向隔离库；未配置则跳过。
# ---------------------------------------------------------------------------

pytestmark_real = pytest.mark.skipif(
    not TEST_DSN, reason="GOVBUDGET_RETRY_TEST_DATABASE_URL 未配置隔离库")


@pytest.mark.asyncio
@pytestmark_real
async def test_real_pg_outer_tx_abort_rolls_back_and_connection_reusable(monkeypatch):
    import asyncpg

    pool = await asyncpg.create_pool(TEST_DSN, min_size=1, max_size=2, command_timeout=5)
    try:
        conn = await pool.acquire()
        released = []
        monkeypatch.setattr(runner.DatabaseConnection, "release",
                            staticmethod(lambda c: released.append(c)))
        # 真实连接上启动外层事务并先写一行"部分写入"（金丝雀）。
        await conn.execute("CREATE TEMP TABLE IF NOT EXISTS r1_canary (id int)")
        await conn.execute("INSERT INTO r1_canary VALUES (1)")
        outer = conn.transaction()
        await outer.start()
        await conn.execute("INSERT INTO r1_canary VALUES (2)")
        calls = _patch_persist(monkeypatch, [TimeoutError()])
        with pytest.raises(TimeoutError):
            await runner._persist_artifact_with_retry(
                conn, _artifact(), job_id="job-real",
                document_version_id=1, org_name="甲局", fiscal_year=2025)
        assert calls.count == 1 and not released
        assert conn.is_in_transaction(), "helper 不得替调用方结束外层事务"
        await outer.rollback()
        assert not conn.is_in_transaction()
        canary_after = await conn.fetchval("SELECT count(*) FROM r1_canary")
        assert canary_after == 1, "外层事务回滚后部分写入不可发布"
        assert await conn.fetchval("SELECT 1") == 1, "连接仍可用"
        await pool.release(conn)
    finally:
        await pool.close()


@pytest.mark.asyncio
@pytestmark_real
async def test_real_pg_standalone_retry_succeeds_on_same_connection(monkeypatch):
    import asyncpg

    pool = await asyncpg.create_pool(TEST_DSN, min_size=1, max_size=2, command_timeout=5)
    try:
        conn = await pool.acquire()
        calls = _patch_persist(monkeypatch, [TimeoutError(), 888])
        result = await runner._persist_artifact_with_retry(
            conn, _artifact(), job_id="job-real2",
            document_version_id=1, org_name="甲局", fiscal_year=2025)
        assert result == 888 and calls.count == 2
        assert await conn.fetchval("SELECT 1") == 1, "重试后的连接仍可用"
        await pool.release(conn)
    finally:
        await pool.close()
