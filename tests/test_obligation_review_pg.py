"""人工补核（WP3-B）在**真实 PostgreSQL** 上的验证（显式 opt-in）。

为什么必须有这一层
------------------
纯函数层（``tests/test_review_lifecycle_service.py``）能回答"门禁口径对不对"，
接口层（``tests/test_review_lifecycle_api.py``）能回答"鉴权与错误体对不对"，
但下面这些只有真库能回答：

A. 迁移 0021 真的建出 ``review_obligation_decisions`` 表，带唯一约束与 CHECK，
   且整套迁移**只新增、可重复执行**（CI 硬门禁覆盖从空库到最新）；
B. 人工决定**真的持久化**：新连接、新一次读取里仍然存在（本批次的立项缺陷
   就是"复核完成只活在浏览器里"——补核结论不许犯同一个错）；
C. **完成门禁集成**：未处理的阻塞义务卡 complete；逐条补核后放行；
   决定属于**具体复核会话**——旧会话（失效/已完成）的决定不会漏进新复核；
D. **并发不静默覆盖**：两个复核人同时对同一义务首次表态，唯一约束拦住
   后到者；拿过期 revision 改写会被乐观锁拒掉；
E. 审计与业务同生共死的方向正确：成功审计在**提交之后**才写，
   且恰好一条（``obligation.reviewed`` / ``obligation.updated``）。

隔离方式（沿用 WP3-A 的 PG 用例约定）
-------------------------------------
整个文件在**随机命名的独立 schema** 里执行：先跑真实迁移建出全部表，
灌入测试数据，跑完 ``DROP SCHEMA CASCADE``。即使目标库是开发库，
也不会碰 public 下的任何既有数据，不留残留表。

运行方式::

    GOVBUDGET_TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/fiscal_db \
        python -m pytest tests/test_obligation_review_pg.py -v
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

from src.db.migrations import MIGRATIONS, run_migrations
from src.schemas.review_lifecycle import ERROR_COMPLETION_BLOCKED
from src.services import review_lifecycle_service, review_obligation_store

pytestmark = pytest.mark.real_database

MIGRATION_ID = "2026-09-23_0021_review_obligation_decisions"

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)

DISTRICT = "district-putuo"
DEPT = "dept-planning"
UNIT = "unit-planning-enforcement"


# ---- 数据构造（与 tests/test_review_lifecycle_pg.py 同一套手法，刻意重复：
# 测试文件不相互 import，避免一个文件的夹具改动悄悄炸掉另一个文件） --------


async def _insert_slot(conn, **fields: Any) -> str:
    payload: Dict[str, Any] = {
        "slot_key": f"key-{uuid.uuid4().hex}",
        "mapping_key": "",
        "jurisdiction_org_id": DISTRICT,
        "jurisdiction_name": "上海市普陀区",
        "department_org_id": DEPT,
        "department_name": "上海市普陀区规划和自然资源局",
        "subject_org_id": UNIT,
        "subject_org_name": "上海市普陀区规划和自然资源局执法大队",
        "subject_kind": "unit",
        "subject_level": "unit",
        "material_scope": "unit_self",
        "caliber": "self",
        "fiscal_year": 2024,
        "report_kind": "final",
        "due_at": None,
        "status": "uploaded",
        "status_reason": "awaiting_analysis",
        "updated_at": NOW,
    }
    payload.update(fields)
    columns = ", ".join(payload)
    placeholders = ", ".join(f"${index}" for index in range(1, len(payload) + 1))
    row = await conn.fetchrow(
        f"INSERT INTO material_slots ({columns}) VALUES ({placeholders})"
        " RETURNING id::text AS slot_id",
        *payload.values(),
    )
    return str(row["slot_id"])


async def _insert_version(
    conn, *, slot_id: str, file_hash: str, created_at: datetime
) -> int:
    org_unit_id = await conn.fetchval(
        "INSERT INTO org_units (org_name) VALUES ($1) RETURNING id",
        f"unit-{uuid.uuid4().hex[:8]}",
    )
    document_id = await conn.fetchval(
        "INSERT INTO fiscal_documents (org_unit_id, fiscal_year, doc_type)"
        " VALUES ($1, $2, $3) RETURNING id",
        org_unit_id,
        2024,
        f"final-{uuid.uuid4().hex[:8]}",
    )
    return int(
        await conn.fetchval(
            """
            INSERT INTO fiscal_document_versions
                (document_id, file_hash, storage_key, created_at, slot_id)
            VALUES ($1, $2, $3, $4, $5::uuid)
            RETURNING id
            """,
            document_id,
            file_hash,
            f"{uuid.uuid4().hex}/material.pdf",
            created_at,
            slot_id,
        )
    )


async def _insert_job(
    conn,
    *,
    job_uuid: str,
    version_id: int,
    status: str = "done",
    completed_at: datetime = NOW,
    analysis_revision: int = 0,
    result_meta: Optional[Dict[str, Any]] = None,
) -> int:
    metadata: Dict[str, Any] = {"structured_ingest": {"document_version_id": version_id}}
    if result_meta is not None:
        metadata["result_meta"] = result_meta
    return int(
        await conn.fetchval(
            """
            INSERT INTO analysis_jobs
                (job_uuid, filename, status, mode, started_at, completed_at,
                 metadata, analysis_revision)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
            RETURNING id
            """,
            job_uuid,
            "material.pdf",
            status,
            "dual",
            completed_at - timedelta(minutes=2),
            completed_at,
            json.dumps(metadata, ensure_ascii=False),
            analysis_revision,
        )
    )


async def _insert_result(conn, *, job_id: int) -> None:
    await conn.execute(
        """
        INSERT INTO analysis_results
            (job_id, ai_findings, rule_findings, merged_result, raw_response)
        VALUES ($1, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb, '{}'::jsonb)
        """,
        job_id,
    )


def _coverage_payload(blocking_ids: List[str]) -> Dict[str, Any]:
    """构造落库形态的覆盖台账，``blocking_ids`` 即引擎判阻塞未完成的义务。

    完成门禁的"待补核"口径从 ``instances`` 推导（不再只读计数），
    所以这里的实例必须是账本的真实形状。
    """
    instances = [
        {
            "obligation_id": obligation_id,
            "group_id": "TABLE_CROSS",
            "group_title": "表间关系",
            "title": f"阻塞义务 {obligation_id}",
            "status": "not_executed",
            "reason": "not_executed",
            "reason_label": "未执行",
            "detail": "测试用阻塞义务（尚无规则实现）",
            "checkers": [],
            "missing_checkers": ["V33-TEST-MISSING"],
            "depends_on": [],
            "input_gaps": [],
            "evidence_kind": "document_level",
            "basis": "测试依据",
            "gap_note": "测试缺口",
            "requires_ai": False,
            "blocks_gate": True,
        }
        for obligation_id in blocking_ids
    ]
    return {
        "obligation_coverage": {
            "catalog_version": "test-catalog",
            "catalog_fingerprint": "fingerprint",
            "applicable_total": 42,
            "completed_total": 42 - len(instances),
            "not_applicable_total": 0,
            "unresolved_total": len(instances),
            "blocking_total": len(instances),
            "by_reason": ({"not_executed": len(instances)} if instances else {}),
            "by_group": [],
            "instances": instances,
        }
    }


async def _make_reviewable_slot(
    conn, *, blocking_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    """造一条"可复核"的材料：有当前版本、有已完成的当前分析、覆盖可信。"""
    slot_id = await _insert_slot(conn)
    version_id = await _insert_version(conn, slot_id=slot_id, file_hash="a" * 64, created_at=NOW)
    job_uuid = f"job-{uuid.uuid4().hex[:12]}"
    job_id = await _insert_job(
        conn,
        job_uuid=job_uuid,
        version_id=version_id,
        analysis_revision=1,
        result_meta=_coverage_payload(list(blocking_ids or [])),
    )
    await _insert_result(conn, job_id=job_id)
    await conn.execute(
        "UPDATE material_slots SET current_document_version_id = $1 WHERE id = $2::uuid",
        version_id,
        slot_id,
    )
    return {"slot_id": slot_id, "version_id": version_id, "job_uuid": job_uuid, "job_id": job_id}


@pytest.fixture
async def review_db(real_database_url, monkeypatch):
    """独立 schema + 真实迁移（含 0021）。"""
    from src.db.connection import DatabaseConnection

    schema = f"obligation_test_{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("PG_SCHEMA", schema)
    DatabaseConnection._pool = None
    pool = await DatabaseConnection.initialize(real_database_url)
    assert DatabaseConnection.get_schema() == schema

    async with pool.acquire() as conn:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    await run_migrations()

    try:
        yield schema, pool
    finally:
        DatabaseConnection._pool = None
        try:
            async with pool.acquire() as conn:
                await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await pool.close()


@asynccontextmanager
async def _conn(schema: str, pool):
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path TO "{schema}", public')
        yield conn


async def _decisions(schema: str, pool, review_session_id: str) -> List[Dict[str, Any]]:
    async with _conn(schema, pool) as conn:
        rows = await conn.fetch(
            "SELECT review_session_id::text AS review_session_id, obligation_id, decision,"
            " note, evidence_reference, reviewer, revision"
            " FROM review_obligation_decisions"
            " WHERE review_session_id = $1::uuid ORDER BY created_at, id",
            review_session_id,
        )
        return [dict(row) for row in rows]


def _read_audit_events(path: Any) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ==== A：迁移 0021 ============================================================


async def test_migration_0021_creates_decision_table(review_db):
    """0021 已随整套迁移应用：表、唯一约束、CHECK 都在。"""
    schema, pool = review_db
    assert MIGRATIONS[-1]["id"] == MIGRATION_ID  # 追加在末尾，没有插到历史迁移中间

    async with _conn(schema, pool) as conn:
        applied = await conn.fetchval(
            f'SELECT COUNT(*) FROM "{schema}".schema_migrations WHERE id = $1',
            MIGRATION_ID,
        )
        assert applied == 1

        columns = {
            str(row["column_name"])
            for row in await conn.fetch(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema = $1 AND table_name = 'review_obligation_decisions'",
                schema,
            )
        }
        assert {
            "id",
            "review_session_id",
            "slot_id",
            "obligation_id",
            "decision",
            "note",
            "evidence_reference",
            "reviewer",
            "reviewed_at",
            "revision",
            "created_at",
            "updated_at",
        } <= columns

        indexes = {
            str(row["indexname"])
            for row in await conn.fetch(
                "SELECT indexname FROM pg_indexes"
                " WHERE schemaname = $1 AND tablename = 'review_obligation_decisions'",
                schema,
            )
        }
        assert "uq_review_obligation_decisions_scope" in indexes


async def test_migration_0021_is_additive_and_idempotent(review_db):
    """再跑一遍迁移：什么都不再应用（幂等）；既有迁移记录不受影响（只新增）。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        before = {
            str(row["id"])
            for row in await conn.fetch(f'SELECT id FROM "{schema}".schema_migrations')
        }
    await run_migrations()
    async with _conn(schema, pool) as conn:
        after = {
            str(row["id"])
            for row in await conn.fetch(f'SELECT id FROM "{schema}".schema_migrations')
        }
    assert after == before


# ==== B / C：决定持久化 + 完成门禁集成 =========================================


async def test_manual_decision_unblocks_completion(review_db):
    """blocking > 0 → 卡完成；逐条人工补核后放行；决定写进了当前会话。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, blocking_ids=["OBL-A", "OBL-B"])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="reviewer-a")

        # 未处理 ⇒ 409 blocking_obligations（任务书 §十一）
        with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
            await review_lifecycle_service.complete_review(conn, ctx["slot_id"], actor="r")
        assert excinfo.value.error == ERROR_COMPLETION_BLOCKED
        assert [(b.code, b.count) for b in excinfo.value.blockers] == [
            ("blocking_obligations", 2)
        ]

        first = await review_lifecycle_service.decide_obligation(
            conn,
            ctx["slot_id"],
            obligation_id="OBL-A",
            decision="verified_ok",
            actor="reviewer-a",
            note="已对照三公表逐项核对",
            evidence_reference="第 12 页",
        )
        assert first.decision.decision == "verified_ok"
        assert first.decision.revision == 1
        # 还剩 OBL-B 未处理：返回的门禁仍然阻塞且计数为 1
        assert [(b.code, b.count) for b in first.completion_gate.blockers] == [
            ("blocking_obligations", 1)
        ]

        second = await review_lifecycle_service.decide_obligation(
            conn,
            ctx["slot_id"],
            obligation_id="OBL-B",
            decision="verified_issue",
            actor="reviewer-a",
            note="确认为真实问题",
        )
        assert second.completion_gate.can_complete is True

        completed = await review_lifecycle_service.complete_review(
            conn, ctx["slot_id"], actor="reviewer-a"
        )

    # 快照里两本账要同时说真话：引擎判出的阻塞数与人工处理方式（§十六）
    coverage = completed.session.review_result["coverage"]
    assert coverage["blocking_count"] == 0  # 完成时未处理数恒为 0
    assert coverage["blocking_engine_total"] == 2
    assert coverage["blocking_obligations_closed_by"] == "automatic_and_manual"
    assert coverage["manual_decisions"] == {
        "verified_ok": 1,
        "verified_issue": 1,
        "not_applicable": 0,
        "pending": 0,
    }

    # 决定真的持久化了：新连接读回来（而不是只活在调用方的内存里）
    rows = await _decisions(schema, pool, completed.session.review_session_id)
    assert {row["obligation_id"]: row["decision"] for row in rows} == {
        "OBL-A": "verified_ok",
        "OBL-B": "verified_issue",
    }
    assert all(row["reviewer"] == "reviewer-a" for row in rows)

    # GET 视角同样可见（当前代际上已完成 ⇒ 决定随完成会话继续生效，§十六）
    async with _conn(schema, pool) as conn:
        data = await review_lifecycle_service.load_review_by_slot(conn, ctx["slot_id"])
    assert data.completion_gate.can_complete is True
    assert data.obligation_review.available is True
    assert data.obligation_review.pending_total == 0
    by_id = {item.obligation_id: item for item in data.obligation_review.items}
    assert by_id["OBL-A"].decision == "verified_ok"
    assert by_id["OBL-A"].decision_revision == 1
    assert by_id["OBL-A"].note == "已对照三公表逐项核对"
    assert by_id["OBL-B"].decision == "verified_issue"


async def test_decisions_belong_to_the_session(review_db):
    """任务书 §十：重开复核后，旧会话的决定留在库里（审计追溯），
    但**不**漏进新会话的门禁——否则"换人复核"会凭空继承上一轮的结论。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, blocking_ids=["OBL-A"])
        started = await review_lifecycle_service.start_review(
            conn, ctx["slot_id"], actor="reviewer-a"
        )
        old_session = started.session.review_session_id
        await review_lifecycle_service.decide_obligation(
            conn, ctx["slot_id"], obligation_id="OBL-A", decision="verified_ok", actor="reviewer-a"
        )
        await review_lifecycle_service.complete_review(conn, ctx["slot_id"], actor="reviewer-a")

        reopened = await review_lifecycle_service.reopen_review(
            conn, ctx["slot_id"], actor="reviewer-b"
        )
        assert reopened.session.review_session_id != old_session
        # 新会话没有决定 ⇒ 同一义务重新阻塞
        assert [(b.code, b.count) for b in reopened.completion_gate.blockers] == [
            ("blocking_obligations", 1)
        ]

    old_rows = await _decisions(schema, pool, old_session)
    assert len(old_rows) == 1  # 旧决定保留为审计事实
    assert (await _decisions(schema, pool, reopened.session.review_session_id)) == []


async def test_rejected_decision_entry_points(review_db):
    """没有活动会话 / 已完成复核 / 未知义务 / 非阻塞义务——四类入口都要拒。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, blocking_ids=["OBL-A"])

        # 还没开始复核 ⇒ 409 review_not_active
        with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
            await review_lifecycle_service.decide_obligation(
                conn, ctx["slot_id"], obligation_id="OBL-A", decision="verified_ok", actor="r"
            )
        assert excinfo.value.error == "review_not_active"

        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")

        # 未知义务 ⇒ 404
        with pytest.raises(HTTPException) as excinfo404:
            await review_lifecycle_service.decide_obligation(
                conn, ctx["slot_id"], obligation_id="OBL-NOPE", decision="verified_ok", actor="r"
            )
        assert excinfo404.value.status_code == 404

        # 非法决定值 ⇒ 400（路由层额外还有 422 的参数校验兜底）
        with pytest.raises(HTTPException) as excinfo400:
            await review_lifecycle_service.decide_obligation(
                conn, ctx["slot_id"], obligation_id="OBL-A", decision="approved", actor="r"
            )
        assert excinfo400.value.status_code == 400

        # 决定之后完成；已完成 ⇒ 409 review_completed_locked
        await review_lifecycle_service.decide_obligation(
            conn, ctx["slot_id"], obligation_id="OBL-A", decision="verified_ok", actor="r"
        )
        await review_lifecycle_service.complete_review(conn, ctx["slot_id"], actor="r")
        with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
            await review_lifecycle_service.decide_obligation(
                conn, ctx["slot_id"], obligation_id="OBL-A", decision="not_applicable", actor="r"
            )
        assert excinfo.value.error == "review_completed_locked"


async def test_non_blocking_obligation_cannot_be_decided(review_db):
    """引擎判完成的义务没有东西需要人工表态（任务书 §十二的另一面）。"""
    schema, pool = review_db
    payload = _coverage_payload(["OBL-A"])
    payload["obligation_coverage"]["instances"].append(
        {
            "obligation_id": "OBL-DONE",
            "group_id": "TABLE_CROSS",
            "group_title": "表间关系",
            "title": "已完成义务",
            "status": "completed",
            "reason": None,
            "reason_label": None,
            "detail": "规则已全部执行并到达终态",
            "checkers": ["V33-200"],
            "missing_checkers": [],
            "depends_on": [],
            "input_gaps": [],
            "evidence_kind": "locatable",
            "basis": "测试依据",
            "gap_note": "",
            "requires_ai": False,
            "blocks_gate": True,
        }
    )
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, blocking_ids=[])
        # 手工把这条"已完成"实例替换进账本
        meta = await conn.fetchval(
            "SELECT metadata FROM analysis_jobs WHERE job_uuid = $1", ctx["job_uuid"]
        )
        meta_obj = json.loads(meta) if isinstance(meta, str) else dict(meta or {})
        meta_obj["result_meta"] = payload
        await conn.execute(
            "UPDATE analysis_jobs SET metadata = $2::jsonb WHERE job_uuid = $1",
            ctx["job_uuid"],
            json.dumps(meta_obj, ensure_ascii=False),
        )
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
            await review_lifecycle_service.decide_obligation(
                conn, ctx["slot_id"], obligation_id="OBL-DONE", decision="verified_ok", actor="r"
            )
        assert excinfo.value.error == "obligation_not_blocking"


# ==== D：并发与乐观锁 =========================================================


async def test_concurrent_first_decisions_do_not_overwrite(review_db):
    """任务书 §十五：两个复核人同时对同一义务首次表态，唯一约束拦住后到者。

    终态必须满足：恰好一条决定行、恰好一个成功者；失败方拿到 409，
    而不是"后到者的结论悄悄覆盖先到者"。
    """
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, blocking_ids=["OBL-A"])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="reviewer-a")

    async def _decide(actor: str, decision: str):
        async with _conn(schema, pool) as conn:
            return await review_lifecycle_service.decide_obligation(
                conn, ctx["slot_id"], obligation_id="OBL-A", decision=decision, actor=actor
            )

    results = await asyncio.gather(
        _decide("reviewer-a", "verified_ok"),
        _decide("reviewer-b", "not_applicable"),
        return_exceptions=True,
    )
    winners = [item for item in results if not isinstance(item, Exception)]
    losers = [item for item in results if isinstance(item, Exception)]
    assert len(winners) == 1, results
    assert len(losers) == 1, results
    assert isinstance(losers[0], review_lifecycle_service.ReviewLifecycleError)
    assert losers[0].status_code == 409
    assert losers[0].error == "obligation_decision_conflict"

    async with _conn(schema, pool) as conn:
        rows = await review_obligation_store.list_session_decisions(
            conn, winners[0].session.review_session_id
        )
    assert len(rows) == 1
    assert rows[0]["decision"] == winners[0].decision.decision
    assert rows[0]["reviewer"] == winners[0].decision.reviewer


async def test_stale_revision_update_is_rejected(review_db):
    """乐观锁：改写必须带当前 revision；带旧值/不带一律 409，绝不静默覆盖。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, blocking_ids=["OBL-A"])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="reviewer-a")
        first = await review_lifecycle_service.decide_obligation(
            conn, ctx["slot_id"], obligation_id="OBL-A", decision="verified_ok", actor="reviewer-a"
        )
        updated = await review_lifecycle_service.decide_obligation(
            conn,
            ctx["slot_id"],
            obligation_id="OBL-A",
            decision="verified_issue",
            actor="reviewer-b",
            expected_revision=first.decision.revision,
        )
        assert updated.decision.decision == "verified_issue"
        assert updated.decision.revision == 2

        # 拿已经作废的 revision=1 再来 ⇒ 409 且答案没有变
        with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
            await review_lifecycle_service.decide_obligation(
                conn,
                ctx["slot_id"],
                obligation_id="OBL-A",
                decision="not_applicable",
                actor="reviewer-c",
                expected_revision=1,
            )
        assert excinfo.value.error == "obligation_decision_conflict"
        # 不带 revision 改写 ⇒ 同样 409
        with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
            await review_lifecycle_service.decide_obligation(
                conn,
                ctx["slot_id"],
                obligation_id="OBL-A",
                decision="not_applicable",
                actor="reviewer-c",
            )
        assert excinfo.value.error == "obligation_decision_conflict"

    async with _conn(schema, pool) as conn:
        rows = await review_obligation_store.list_session_decisions(
            conn, updated.session.review_session_id
        )
    assert len(rows) == 1
    assert rows[0]["decision"] == "verified_issue"
    assert rows[0]["revision"] == 2
    assert rows[0]["reviewer"] == "reviewer-b"


# ==== E：审计 ================================================================


async def test_decision_audits_are_written_once_after_commit(review_db, monkeypatch, tmp_path):
    """成功审计：恰好一条、在服务层写、提交之后落盘（与 review.complete 同一纪律）。"""
    schema, pool = review_db
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))

    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, blocking_ids=["OBL-A"])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        await review_lifecycle_service.decide_obligation(
            conn, ctx["slot_id"], obligation_id="OBL-A", decision="verified_ok", actor="r"
        )
        await review_lifecycle_service.decide_obligation(
            conn,
            ctx["slot_id"],
            obligation_id="OBL-A",
            decision="verified_issue",
            actor="r",
            expected_revision=1,
        )

    events = _read_audit_events(audit_path)
    reviewed = [e for e in events if e["action"] == "obligation.reviewed"]
    updated = [e for e in events if e["action"] == "obligation.updated"]
    assert len(reviewed) == 1
    assert len(updated) == 1
    assert reviewed[0]["actor"] == "r"
    assert reviewed[0]["details"]["obligation_id"] == "OBL-A"
    assert reviewed[0]["details"]["decision"] == "verified_ok"
    assert reviewed[0]["details"]["review_session_id"]
    assert updated[0]["details"]["revision"] == 2
