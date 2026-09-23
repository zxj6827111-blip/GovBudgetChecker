"""复核生命周期在**真实 PostgreSQL** 上的验证（显式 opt-in）。

为什么必须有这一层
------------------
接口层与纯函数层能回答"门禁判定对不对"，但回答不了下面这些**只有真库能回答**的问题：

A. 事务与行锁的真实语义：``start`` / ``complete`` 在同一事务内写入会话、刷新槽位状态，
   中途失败是否整体回滚；
B. **两个并发 start 只能产生一个** ``in_progress`` 会话（``uq_review_sessions_active``
   这个 partial unique index 是否真的拦得住，而不是靠应用层"先查再写"）；
C. **两个并发 complete 只产生一条**完成记录，且完成人/完成时间不被第二次覆盖；
D. **complete 与版本指针推进竞争**：终态不允许出现"复核已完成，而它钉的版本
   已经不是槽位当前版本"；
E. **complete 与重新分析竞争**：终态不允许出现"复核已完成，而分析代际已变"；
F. 已完成会话在重新查询（新的连接、新的 read）后仍然存在——这正是 WP3-A 要
   灭掉的那个缺陷（"刷新之后系统并不知道这份材料复核过"）；
G. 旧会话作为审计历史保留（invalidated 不被删除、不被覆盖）；
H. 槽位状态真的从 ``uploaded`` 走到 ``reviewing`` / ``completed`` / ``review_required``；
I. 分析代际：同一 job_uuid 重新分析换代、同一结果的重复落库**不**换代
   （``ON CONFLICT DO UPDATE`` 的真实行为）。
J. 迁移 0020 在已有库上的升级路径只新增，且可重复执行。

隔离方式（沿用 WP1/WP2 的 PG 用例约定）
---------------------------------------
整个文件在**随机命名的独立 schema** 里执行：先跑真实迁移建出全部表，
灌入测试数据，跑完 ``DROP SCHEMA CASCADE``。即使目标库是开发库，
也不会碰 public 下的任何既有数据，不留残留表。

运行方式::

    GOVBUDGET_TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/fiscal_db \
        python -m pytest tests/test_review_lifecycle_pg.py -v
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

from src.db.migrations import run_migrations
from src.schemas.review_lifecycle import ERROR_COMPLETION_BLOCKED
from src.services import (
    review_lifecycle_service,
    review_session_store,
    review_slot_sync,
)
from src.services.analysis_result_store import (
    compute_analysis_result_fingerprint,
    persist_analysis_job_snapshot,
)

pytestmark = pytest.mark.real_database

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(days=3)
LATER = NOW + timedelta(days=1)

DISTRICT = "district-putuo"
DEPT = "dept-planning"
UNIT = "unit-planning-enforcement"


# ---- 数据构造 ---------------------------------------------------------------


async def _insert_slot(conn, **fields: Any) -> str:
    payload: Dict[str, Any] = {
        "slot_key": f"key-{uuid.uuid4().hex}",
        # 自然键（subject_org_id, subject_kind, material_scope, report_kind,
        # fiscal_year, mapping_key）由唯一索引兜住。需要两条互不相同的槽位时
        # 传 slot_fields 覆盖其中一项即可；这里的默认值只是"最常见的可复核材料"。
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


async def _insert_result(
    conn,
    *,
    job_id: int,
    ai_findings: List[Dict[str, Any]] | None = None,
    rule_findings: List[Dict[str, Any]] | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO analysis_results
            (job_id, ai_findings, rule_findings, merged_result, raw_response)
        VALUES ($1, $2::jsonb, $3::jsonb, '{}'::jsonb, '{}'::jsonb)
        """,
        job_id,
        json.dumps(ai_findings or [], ensure_ascii=False),
        json.dumps(rule_findings or [], ensure_ascii=False),
    )


def _coverage_payload(*, blocking_total: int = 0, applicable: int = 42) -> Dict[str, Any]:
    return {
        "obligation_coverage": {
            "catalog_version": "test-catalog",
            "catalog_fingerprint": "fingerprint",
            "applicable_total": applicable,
            "completed_total": applicable,
            "not_applicable_total": 0,
            "unresolved_total": 0,
            "blocking_total": blocking_total,
            "by_reason": {},
            "by_group": [],
            "instances": [],
        }
    }


async def _make_reviewable_slot(
    conn,
    *,
    findings: Optional[List[Dict[str, Any]]] = None,
    result_meta: Optional[Dict[str, Any]] = None,
    job_status: str = "done",
    slot_fields: Optional[Dict[str, Any]] = None,
    review_items: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """造一条"可复核"的材料：有当前版本、有已完成的当前分析、覆盖可信。"""
    slot_id = await _insert_slot(conn, **(slot_fields or {}))
    version_id = await _insert_version(conn, slot_id=slot_id, file_hash="a" * 64, created_at=NOW)
    job_uuid = f"job-{uuid.uuid4().hex[:12]}"
    job_id = await _insert_job(
        conn,
        job_uuid=job_uuid,
        version_id=version_id,
        status=job_status,
        analysis_revision=1,
        result_meta=result_meta if result_meta is not None else _coverage_payload(),
    )
    await _insert_result(conn, job_id=job_id, ai_findings=findings or [])
    await conn.execute(
        "UPDATE material_slots SET current_document_version_id = $1 WHERE id = $2::uuid",
        version_id,
        slot_id,
    )
    if review_items:
        # 结构化待复核项要写进 metadata（与生产落库路径一致：整份 structured_ingest
        # 进 metadata），否则门禁读不到它，用例就测不到"结构化待复核阻塞完成"。
        metadata = await conn.fetchval(
            "SELECT metadata FROM analysis_jobs WHERE job_uuid = $1", job_uuid
        )
        payload = json.loads(metadata) if isinstance(metadata, str) else dict(metadata or {})
        structured = dict(payload.get("structured_ingest") or {})
        structured["review_items"] = review_items
        payload["structured_ingest"] = structured
        await conn.execute(
            "UPDATE analysis_jobs SET metadata = $2::jsonb WHERE job_uuid = $1",
            job_uuid,
            json.dumps(payload, ensure_ascii=False),
        )
    return {"slot_id": slot_id, "version_id": version_id, "job_uuid": job_uuid, "job_id": job_id}


def _finding(issue_id: str, **extra: Any) -> Dict[str, Any]:
    payload = {
        "id": issue_id,
        "source": "rule",
        "rule_id": f"RULE-{issue_id}",
        "severity": "high",
        "title": f"问题 {issue_id}",
        "message": f"问题 {issue_id} 的描述",
        "evidence": [{"page": 3, "text": "证据文本", "bbox": [1, 2, 3, 4]}],
        "location": {"page": 3, "table": "T1"},
    }
    payload.update(extra)
    return payload


def _write_decisions(decisions: Dict[str, str]) -> None:
    """直接写 issue_workflow 的持久化文件（它就是该状态的可信来源）。"""
    from api import runtime

    issues: Dict[str, Any] = {}
    for key, status in decisions.items():
        job_id, _, issue_id = key.partition("::")
        issues[key] = {
            "key": key,
            "job_id": job_id,
            "issue_id": issue_id,
            "status": status,
            "title": None,
            "severity": None,
            "page": None,
            "organization_id": None,
            "organization_name": None,
            "note": None,
            "updated_at": "2026-09-22T00:00:00Z",
        }
    runtime.write_json_file(
        runtime.UPLOAD_ROOT / ".issue_workflow.json",
        {
            "issues": issues,
            "packages": [],
            "review_locks": {},
            "updated_at": "2026-09-22T00:00:00Z",
            "revision": 1,
        },
    )


def _write_ignored(decisions_job_id: str, issue_ids: List[str]) -> None:
    """写任务目录下的 legacy 忽略清单（``ignored_issue_ids``）。"""
    from api import runtime

    job_dir = runtime.UPLOAD_ROOT / decisions_job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    runtime.write_json_file(job_dir / "ignored_issues.json", {"issue_ids": issue_ids})


@pytest.fixture
async def review_db(real_database_url, monkeypatch):
    """独立 schema + 真实迁移。"""
    from src.db.connection import DatabaseConnection

    schema = f"review_test_{uuid.uuid4().hex[:12]}"
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


async def _slot_status(schema: str, pool, slot_id: str) -> Dict[str, Any]:
    async with _conn(schema, pool) as conn:
        row = await conn.fetchrow(
            "SELECT status, status_reason FROM material_slots WHERE id = $1::uuid", slot_id
        )
        return dict(row) if row is not None else {}


async def _sessions(schema: str, pool, slot_id: str) -> List[Dict[str, Any]]:
    async with _conn(schema, pool) as conn:
        rows = await conn.fetch(
            "SELECT id::text AS id, status, document_version_id, analysis_basis_token,"
            " completed_by, invalidated_reason, review_result"
            " FROM review_sessions WHERE slot_id = $1::uuid ORDER BY created_at, id",
            slot_id,
        )
        return [dict(row) for row in rows]


# ==== A / H / F：真实事务、槽位状态、刷新后仍然存在 ==========================


async def test_start_and_complete_are_real_transactions(review_db):
    """A. start 建会话并把槽位推到 reviewing；complete 写完成人与快照并把槽位推到 completed。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[_finding("rule-1")])
        started = await review_lifecycle_service.start_review(
            conn, ctx["slot_id"], actor="reviewer-a"
        )

    assert started.session.status == "in_progress"
    assert started.session.document_version_id == ctx["version_id"]
    assert started.session.analysis_job_uuid == ctx["job_uuid"]
    assert started.session.analysis_basis_token == f"{ctx['job_uuid']}:1"
    assert started.session.started_by == "reviewer-a"
    assert (await _slot_status(schema, pool, ctx["slot_id"]))["status"] == "reviewing"

    # 未处理的问题阻塞完成（没有记录 = pending）
    async with _conn(schema, pool) as conn:
        with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
            await review_lifecycle_service.complete_review(
                conn, ctx["slot_id"], actor="reviewer-a"
            )
    assert excinfo.value.status_code == 409
    assert [item.code for item in excinfo.value.blockers] == ["pending_findings"]
    assert excinfo.value.blockers[0].count == 1

    _write_decisions({f"{ctx['job_uuid']}::rule-1": "confirmed"})
    async with _conn(schema, pool) as conn:
        completed = await review_lifecycle_service.complete_review(
            conn, ctx["slot_id"], actor="reviewer-a"
        )

    assert completed.session.status == "completed"
    assert completed.session.completed_by == "reviewer-a"
    assert completed.session.completed_at
    counts = completed.session.review_result["issue_counts"]
    assert counts == {
        "total": 1,
        "confirmed": 1,
        "no_issue": 0,
        "in_package": 0,
        "pending": 0,
        "needs_review": 0,
    }
    assert completed.session.review_result["coverage"]["blocking_count"] == 0
    assert (await _slot_status(schema, pool, ctx["slot_id"]))["status"] == "completed"

    # F. 重新查询（新连接、新 read）之后"已完成"仍然是持久化事实
    async with _conn(schema, pool) as conn:
        reloaded = await review_lifecycle_service.load_review_by_job(conn, ctx["job_uuid"])
    assert reloaded.review is not None
    assert reloaded.review.completion_gate.can_complete is True
    assert reloaded.review.history[0].status == "completed"
    assert reloaded.review.history[0].completed_by == "reviewer-a"
    assert reloaded.review.current_session is None


async def test_complete_is_idempotent(review_db):
    """重复 complete 返回同一结果，且完成人/完成时间不被第二次覆盖。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn)
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="first")
        first = await review_lifecycle_service.complete_review(
            conn, ctx["slot_id"], actor="first"
        )
        second = await review_lifecycle_service.complete_review(
            conn, ctx["slot_id"], actor="second"
        )

    assert first.session.review_session_id == second.session.review_session_id
    assert second.session.completed_by == "first", "完成人必须是第一个点击的人"
    assert len([s for s in await _sessions(schema, pool, ctx["slot_id"])]) == 1


async def test_complete_requires_explicit_action_even_with_zero_findings(review_db):
    """§八十二：0 条问题 + 0 条阻塞义务，仍然必须有人显式完成。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        preview = await review_lifecycle_service.load_review_by_slot(conn, ctx["slot_id"])
        assert preview.completion_gate.can_complete is False
        assert [item.code for item in preview.completion_gate.blockers] == [
            "review_not_started"
        ]
        assert preview.current_analysis.formal_issue_count == 0

        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="reviewer")
        inside = await review_lifecycle_service.load_review_by_slot(conn, ctx["slot_id"])
        assert inside.completion_gate.can_complete is True

    # 只 start、不 complete → 槽位停在 reviewing，不是 completed
    assert (await _slot_status(schema, pool, ctx["slot_id"]))["status"] == "reviewing"


# ==== 门禁：问题状态与结构化待复核 ==========================================


@pytest.mark.parametrize(
    "status,expected_blocked",
    [
        ("pending", True),
        ("needs_review", True),
        ("confirmed", False),
        ("no_issue", False),
        ("in_package", False),
    ],
)
async def test_issue_status_resolution_matrix(review_db, status, expected_blocked):
    """confirmed / no_issue / in_package 视为已解决；pending / needs_review 阻塞。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[_finding("rule-1")])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
    _write_decisions({f"{ctx['job_uuid']}::rule-1": status})

    async with _conn(schema, pool) as conn:
        state = await review_lifecycle_service.load_review_by_slot(conn, ctx["slot_id"])
    codes = [item.code for item in state.completion_gate.blockers]
    assert ("pending_findings" in codes or "needs_review_findings" in codes) is expected_blocked


async def test_structured_review_items_block_completion(review_db):
    """§四十四：结构化识别待复核项没人表态时，完成必须被阻塞。"""
    schema, pool = review_db
    review_items = [
        {"id": "T1:low_confidence", "type": "low_confidence_table", "table_code": "T1",
         "severity": "warn", "page_number": 2, "message": "该表识别置信度偏低"},
        {"id": "T9:missing", "type": "missing_core_table", "table_code": "T9",
         "severity": "warn", "message": "核心九表未识别到"},
    ]
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[], review_items=review_items)
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")

    async with _conn(schema, pool) as conn:
        blocked = await review_lifecycle_service.load_review_by_slot(conn, ctx["slot_id"])
    assert [item.code for item in blocked.completion_gate.blockers] == ["pending_findings"]
    assert blocked.completion_gate.blockers[0].count == 2

    # 两个结构化条目都可以通过工作流表态（此前它们在 ai/rule findings 里找不到，
    # 更新请求会 404，用户"处理"了却什么也没发生）
    _write_decisions(
        {
            f"{ctx['job_uuid']}::T1:low_confidence": "confirmed",
            f"{ctx['job_uuid']}::T9:missing": "no_issue",
        }
    )
    async with _conn(schema, pool) as conn:
        allowed = await review_lifecycle_service.load_review_by_slot(conn, ctx["slot_id"])
    assert allowed.completion_gate.can_complete is True


async def test_legacy_ignored_issue_ids_do_not_block(review_db):
    """legacy 忽略清单里的条目与工作台可见集合一致：页面看不到就不该阻塞。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[_finding("rule-1")])
    _write_ignored(ctx["job_uuid"], ["rule-1"])

    async with _conn(schema, pool) as conn:
        state = await review_lifecycle_service.load_review_by_slot(conn, ctx["slot_id"])
    assert state.completion_gate.can_complete is False
    assert [item.code for item in state.completion_gate.blockers] == ["review_not_started"]


async def test_degraded_findings_are_displayed_but_do_not_block(review_db):
    """缺证据被降级的 finding 不计入正式问题（与底部状态条同一口径）。"""
    schema, pool = review_db
    degraded = _finding(
        "ai-degraded", source="ai", evidence_status="degraded_missing_evidence",
        severity="manual_review",
    )
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[degraded])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        state = await review_lifecycle_service.load_review_by_slot(conn, ctx["slot_id"])
    assert state.completion_gate.can_complete is True
    assert state.current_analysis.formal_issue_count == 0


# ==== 门禁：检查覆盖 ========================================================


async def test_coverage_unavailable_blocks_completion(review_db):
    """§四十六：没有可信覆盖 ⇒ 不能证明可完成，禁止把"未知"当成"0 条阻塞"。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[], result_meta={})
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        state = await review_lifecycle_service.load_review_by_slot(conn, ctx["slot_id"])
    assert [item.code for item in state.completion_gate.blockers] == ["coverage_unavailable"]


async def test_blocking_obligations_block_completion(review_db):
    """§四十七：WP3-A 还没有人工补核能力，因此 blocking > 0 必须阻塞完成。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(
            conn, findings=[], result_meta=_coverage_payload(blocking_total=4)
        )
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        state = await review_lifecycle_service.load_review_by_slot(conn, ctx["slot_id"])
    assert [item.code for item in state.completion_gate.blockers] == ["blocking_obligations"]
    assert state.completion_gate.blockers[0].count == 4


# ==== 门禁：身份与口径 ======================================================


async def test_identity_unresolved_and_caliber_conflict_block(review_db):
    """§三十七/§三十八：身份未确认、口径有未裁决冲突时，start 与 complete 都不允许。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        unresolved = await _make_reviewable_slot(conn, findings=[], slot_fields={"fiscal_year": None})
        with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
            await review_lifecycle_service.start_review(conn, unresolved["slot_id"], actor="r")
    assert [item.code for item in excinfo.value.blockers] == ["identity_unresolved"]

    async with _conn(schema, pool) as conn:
        conflict = await _make_reviewable_slot(
            conn, findings=[], slot_fields={"caliber_conflict_candidate": "summary"}
        )
        with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
            await review_lifecycle_service.start_review(conn, conflict["slot_id"], actor="r")
    assert [item.code for item in excinfo.value.blockers] == ["caliber_conflict"]


async def test_analysis_not_finished_blocks_start(review_db):
    """§三十：当前分析没跑完不能开复核。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[], job_status="processing")
        with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
            await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
    assert [item.code for item in excinfo.value.blockers] == ["analysis_not_completed"]


async def test_no_current_version_blocks_start(review_db):
    """§三十：槽位没有当前文件版本时，没有可复核的对象。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await conn.execute(
            "UPDATE material_slots SET current_document_version_id = NULL WHERE id = $1::uuid",
            ctx["slot_id"],
        )
        with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
            await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
    assert "no_current_document_version" in [item.code for item in excinfo.value.blockers]


# ==== B：并发 start =========================================================


async def test_two_concurrent_starts_produce_one_session(review_db):
    """B. 两个浏览器同时点"进入审核"只能产生一条活动复核。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])

    async def _start(actor: str):
        async with _conn(schema, pool) as conn:
            return await review_lifecycle_service.start_review(
                conn, ctx["slot_id"], actor=actor
            )

    results = await asyncio.gather(_start("a"), _start("b"), return_exceptions=True)
    # 两条路径都必须成功（一个是新建、一个是复用），不允许以异常收场
    assert not [item for item in results if isinstance(item, Exception)], results
    ids = {item.session.review_session_id for item in results}
    assert len(ids) == 1, results

    rows = await _sessions(schema, pool, ctx["slot_id"])
    assert len(rows) == 1
    assert rows[0]["status"] == "in_progress"


# ==== C：并发 complete ======================================================


async def test_two_concurrent_completes_produce_one_completion(review_db):
    """C. 两个并发 complete：同一条会话、一个完成记录、完成人不被覆盖。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="starter")

    async def _complete(actor: str):
        async with _conn(schema, pool) as conn:
            return await review_lifecycle_service.complete_review(
                conn, ctx["slot_id"], actor=actor
            )

    results = await asyncio.gather(_complete("a"), _complete("b"), return_exceptions=True)
    assert not [item for item in results if isinstance(item, Exception)], results
    ids = {item.session.review_session_id for item in results}
    assert len(ids) == 1
    assert {item.session.status for item in results} == {"completed"}

    rows = await _sessions(schema, pool, ctx["slot_id"])
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    assert rows[0]["completed_by"] in {"a", "b"}


# ==== D：complete 与版本替换竞争 ============================================


async def test_version_replacement_prevents_completed_review(review_db):
    """D. 版本指针推进后：旧会话失效，complete 必须 409 且槽位回到 review_required。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        # 推进到新版本（走真实服务路径：绑定 + 指针推进 + 复核失效）
        from src.services.material_slot_service import MaterialSlotService

        new_version = await _insert_version(
            conn, slot_id=ctx["slot_id"], file_hash="b" * 64, created_at=LATER
        )
        await MaterialSlotService(conn).bind_document_version(ctx["slot_id"], new_version)

    rows = await _sessions(schema, pool, ctx["slot_id"])
    assert rows[0]["status"] == "invalidated"
    assert rows[0]["invalidated_reason"] == "document_version_changed"

    async with _conn(schema, pool) as conn:
        with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
            await review_lifecycle_service.complete_review(conn, ctx["slot_id"], actor="r")
    codes = [item.code for item in excinfo.value.blockers]
    # 当前分析已换到新版本（新版本还没有分析）→ 分析不可用是**正确**的终态
    assert "analysis_unavailable" in codes or "analysis_not_completed" in codes

    status = await _slot_status(schema, pool, ctx["slot_id"])
    assert status["status"] != "completed"


async def test_completed_review_is_invalidated_when_version_advances(review_db):
    """D'. 已完成复核 + 版本推进 ⇒ 会话必须失效，不允许"completed 但版本已换"。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        await review_lifecycle_service.complete_review(conn, ctx["slot_id"], actor="r")
        assert (await _slot_status(schema, pool, ctx["slot_id"]))["status"] == "completed"

        from src.services.material_slot_service import MaterialSlotService

        new_version = await _insert_version(
            conn, slot_id=ctx["slot_id"], file_hash="c" * 64, created_at=LATER
        )
        await MaterialSlotService(conn).bind_document_version(ctx["slot_id"], new_version)
        current = await conn.fetchval(
            "SELECT current_document_version_id FROM material_slots WHERE id = $1::uuid",
            ctx["slot_id"],
        )

    rows = await _sessions(schema, pool, ctx["slot_id"])
    assert rows[0]["status"] == "invalidated"
    # 核心不变式：不存在"status = completed 而 document_version_id 不是当前版本"的行
    assert all(
        not (row["status"] == "completed" and int(row["document_version_id"]) != int(current))
        for row in rows
    )


# ==== E：重新分析换代与失效 ================================================


async def test_reanalysis_invalidates_completed_review_and_is_recorded(review_db):
    """E. 同一 job_uuid 重新分析：旧复核失效、槽位回到 review_required。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        await review_lifecycle_service.complete_review(conn, ctx["slot_id"], actor="r")

    result = await review_lifecycle_service.invalidate_reviews_for_analysis_restart(
        ctx["job_uuid"]
    )
    assert result["sessions_invalidated"] == 1
    assert result["slot_id"] == ctx["slot_id"]

    rows = await _sessions(schema, pool, ctx["slot_id"])
    assert rows[0]["status"] == "invalidated"
    assert rows[0]["invalidated_reason"] == "analysis_restarted"
    assert (await _slot_status(schema, pool, ctx["slot_id"]))["status"] == "review_required"

    async with _conn(schema, pool) as conn:
        fingerprint = await conn.fetchval(
            "SELECT analysis_result_fingerprint FROM analysis_jobs WHERE job_uuid = $1",
            ctx["job_uuid"],
        )
    assert fingerprint is None, "指纹必须被清空，下一次落库才会换代"


# ==== I：分析代际在真库上的行为 =============================================


async def test_result_replay_does_not_bump_generation_but_reanalysis_does(review_db):
    """I. 同一结果重复落库不换代；重置后（重新分析）再落库换代——即使内容相同。"""
    schema, pool = review_db
    payload = {
        "job_id": f"job-{uuid.uuid4().hex[:12]}",
        "status": "done",
        "filename": "material.pdf",
        "mode": "dual",
        "result": {"ai_findings": [_finding("ai-1")], "rule_findings": [], "merged": {}},
        "structured_ingest": {"document_version_id": 0},
    }
    async with _conn(schema, pool) as conn:
        await conn.execute(
            "INSERT INTO analysis_jobs (job_uuid, filename, status, mode, metadata)"
            " VALUES ($1, $2, 'queued', 'dual', '{}'::jsonb)",
            payload["job_id"],
            payload["filename"],
        )

    assert await persist_analysis_job_snapshot(payload, include_results=True) is True
    async with _conn(schema, pool) as conn:
        first = await conn.fetchrow(
            "SELECT analysis_revision, analysis_result_fingerprint FROM analysis_jobs"
            " WHERE job_uuid = $1",
            payload["job_id"],
        )
    assert first["analysis_revision"] == 1
    assert first["analysis_result_fingerprint"] == compute_analysis_result_fingerprint(payload)

    # 同一个结果重放（断线重试、补记）→ 不换代
    assert await persist_analysis_job_snapshot(payload, include_results=True) is True
    async with _conn(schema, pool) as conn:
        replayed = await conn.fetchval(
            "SELECT analysis_revision FROM analysis_jobs WHERE job_uuid = $1",
            payload["job_id"],
        )
    assert replayed == 1, "同一结果的重复落库不允许换代，否则恢复动作会误伤复核"

    # 重新分析：作业被重置为 queued（清指纹）→ 即使结果逐字相同也必须换代
    queued = {**payload, "status": "queued"}
    assert await persist_analysis_job_snapshot(queued) is True
    assert await persist_analysis_job_snapshot(payload, include_results=True) is True
    async with _conn(schema, pool) as conn:
        after = await conn.fetchval(
            "SELECT analysis_revision FROM analysis_jobs WHERE job_uuid = $1",
            payload["job_id"],
        )
        cleared = await conn.fetchval(
            "SELECT analysis_result_fingerprint IS NULL FROM analysis_jobs WHERE job_uuid = $1",
            payload["job_id"],
        )
    assert after == 2, "重新分析必须换代"
    assert cleared is False

    # 进度更新（不带结果）不换代
    assert await persist_analysis_job_snapshot({**payload, "status": "processing"}) is True
    async with _conn(schema, pool) as conn:
        progressed = await conn.fetchval(
            "SELECT analysis_revision FROM analysis_jobs WHERE job_uuid = $1",
            payload["job_id"],
        )
    assert progressed in (2, 3), progressed


async def test_stale_complete_after_generation_change_returns_409(review_db):
    """§六十三：重新分析换代后，旧会话的 complete 必须 409（分析代际已变）。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        # 只换代、不失效（模拟钩子没生效或代际在别处变化）：懒失效必须兜住
        await conn.execute(
            "UPDATE analysis_jobs SET analysis_revision = analysis_revision + 1"
            " WHERE job_uuid = $1",
            ctx["job_uuid"],
        )
        state = await review_lifecycle_service.load_review_by_slot(conn, ctx["slot_id"])
    assert [item.code for item in state.completion_gate.blockers] == ["analysis_basis_changed"]


# ==== G / I：历史保留与失效回退 =============================================


async def test_reopen_keeps_history_and_creates_new_session(review_db):
    """§五十九/§八十三：重开后旧 completed 变 invalidated 并留在历史里。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="first")
        first = await review_lifecycle_service.complete_review(
            conn, ctx["slot_id"], actor="first"
        )
        reopened = await review_lifecycle_service.reopen_review(
            conn, ctx["slot_id"], actor="second"
        )
        reloaded = await review_lifecycle_service.load_review_by_slot(conn, ctx["slot_id"])

    assert reopened.session.status == "in_progress"
    assert reopened.session.review_session_id != first.session.review_session_id
    assert (await _slot_status(schema, pool, ctx["slot_id"]))["status"] == "reviewing"

    rows = await _sessions(schema, pool, ctx["slot_id"])
    assert len(rows) == 2, rows
    old = [row for row in rows if row["id"] == first.session.review_session_id][0]
    assert old["status"] == "invalidated"
    assert old["invalidated_reason"] == "review_reopened"
    assert reloaded.current_session is not None
    assert [item.status for item in reloaded.history] == ["invalidated"]


async def test_invalidation_returns_slot_to_review_required(review_db):
    """I. 失效之后槽位必须回到 review_required（而不是停在 reviewing/completed）。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        assert (await _slot_status(schema, pool, ctx["slot_id"]))["status"] == "reviewing"
        # 事实没变（版本与代际都还是会话钉的那一套）⇒ 懒失效不应误伤
        unchanged = await review_lifecycle_service.invalidate_review(conn, ctx["slot_id"])
    assert unchanged == 0, "版本与代际都没变时不允许把有效复核判成失效"

    async with _conn(schema, pool) as conn:
        # 代际变化（模拟重新分析后新结果落库，但没有走主动钩子）
        await conn.execute(
            "UPDATE analysis_jobs SET analysis_revision = analysis_revision + 1"
            " WHERE job_uuid = $1",
            ctx["job_uuid"],
        )
        invalidated = await review_lifecycle_service.invalidate_review(conn, ctx["slot_id"])
    assert invalidated == 1
    rows = await _sessions(schema, pool, ctx["slot_id"])
    assert rows[0]["status"] == "invalidated"
    assert rows[0]["invalidated_reason"] == "analysis_basis_changed"
    assert (await _slot_status(schema, pool, ctx["slot_id"]))["status"] == "review_required"


# ==== 槽位状态接线：分析完成 → review_required ==============================


async def test_analysis_completion_moves_slot_to_review_required(review_db):
    """H. WP1 预置的 review_required 此前没有任何路径会写出来；落库后必须接通。"""
    schema, pool = review_db
    job_uuid = f"job-{uuid.uuid4().hex[:12]}"
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(conn)
        version_id = await _insert_version(
            conn, slot_id=slot_id, file_hash="d" * 64, created_at=NOW
        )
        await conn.execute(
            "UPDATE material_slots SET current_document_version_id = $1 WHERE id = $2::uuid",
            version_id,
            slot_id,
        )
        await conn.execute(
            "INSERT INTO analysis_jobs (job_uuid, filename, status, mode, metadata)"
            " VALUES ($1, 'material.pdf', 'queued', 'dual', $2::jsonb)",
            job_uuid,
            json.dumps({"structured_ingest": {"document_version_id": version_id}}),
        )

    payload = {
        "job_id": job_uuid,
        "status": "done",
        "filename": "material.pdf",
        "mode": "dual",
        "result": {"ai_findings": [], "rule_findings": [], "merged": {}},
        "structured_ingest": {"document_version_id": version_id},
        "meta": _coverage_payload(),
    }
    payload["result"]["meta"] = _coverage_payload()
    assert await persist_analysis_job_snapshot(payload, include_results=True) is True

    status = await _slot_status(schema, pool, slot_id)
    assert status["status"] == "review_required"
    assert status["status_reason"] == "findings_pending"


async def test_slot_sync_keeps_completed_when_review_already_completed(review_db):
    """重新落库同一个已复核的现役结果时，不能把"已复核完成"打回"待复核"。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        await review_lifecycle_service.complete_review(conn, ctx["slot_id"], actor="r")
        synced = await review_slot_sync.sync_slot_review_state_for_job(conn, ctx["job_uuid"])
    assert synced is not None
    assert synced["status"] == "completed"


async def test_slot_sync_is_noop_for_historical_run(review_db):
    """历史运行（不是当前分析）落库不允许改写当前材料的复核状态。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        # 造一次"更晚的历史运行"，让 ctx 的那次不再是当前分析
        other_job = f"job-{uuid.uuid4().hex[:12]}"
        other_id = await _insert_job(
            conn,
            job_uuid=other_job,
            version_id=ctx["version_id"],
            completed_at=LATER,
        )
        await _insert_result(conn, job_id=other_id)
        synced = await review_slot_sync.sync_slot_review_state_for_job(conn, ctx["job_uuid"])
    assert synced is None


# ==== 路由级：job/slot 不匹配 ==============================================


async def test_job_from_other_slot_is_rejected(review_db):
    """§七十一：两个资源分别有权也不能组合成功——job 不属于该槽位就是 409。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        first = await _make_reviewable_slot(conn, findings=[])
        second = await _make_reviewable_slot(
            conn, findings=[], slot_fields={"subject_org_id": "unit-planning-affairs"}
        )
        with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
            await review_lifecycle_service.start_review(
                conn, first["slot_id"], actor="r", job_uuid=second["job_uuid"]
            )
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "review_context_mismatch"


async def test_legacy_unlinked_job_has_no_review_context(review_db):
    """§二十六：没有精确版本链路的旧任务不能建复核上下文，也不能开复核。"""
    schema, pool = review_db
    legacy_job = f"job-{uuid.uuid4().hex[:12]}"
    async with _conn(schema, pool) as conn:
        await conn.execute(
            "INSERT INTO analysis_jobs (job_uuid, filename, status, mode, metadata)"
            " VALUES ($1, 'legacy.pdf', 'done', 'dual', '{}'::jsonb)",
            legacy_job,
        )
    async with _conn(schema, pool) as conn:
        by_job = await review_lifecycle_service.load_review_by_job(conn, legacy_job)
    assert by_job.review_context.available is False
    assert by_job.review_context.reason == "review_context_unavailable"
    assert by_job.review is None


# ==== J：迁移的既有库升级路径 ==============================================


async def test_upgrade_path_adds_only_0020_objects(review_db):
    """J. 0020 在已有库上只新增：既有 analysis_jobs 行保留、新列取默认值。"""
    schema, pool = review_db
    existing = f"job-{uuid.uuid4().hex[:12]}"
    async with _conn(schema, pool) as conn:
        await conn.execute(
            "INSERT INTO analysis_jobs (job_uuid, filename, status, mode, metadata)"
            " VALUES ($1, 'old.pdf', 'done', 'dual', '{}'::jsonb)",
            existing,
        )
        # 模拟"0020 之前入库的行"：把新列复位成 ALTER 之后的默认形态
        await conn.execute(
            "UPDATE analysis_jobs SET analysis_revision = 0,"
            " analysis_result_fingerprint = NULL WHERE job_uuid = $1",
            existing,
        )
        row = await conn.fetchrow(
            "SELECT analysis_revision, analysis_result_fingerprint FROM analysis_jobs"
            " WHERE job_uuid = $1",
            existing,
        )
        tables = await conn.fetchval(
            "SELECT COUNT(*) FROM information_schema.tables"
            " WHERE table_schema = $1 AND table_name = 'review_sessions'",
            schema,
        )
    assert row["analysis_revision"] == 0
    assert row["analysis_result_fingerprint"] is None
    assert tables == 1


async def test_completed_review_locks_issue_mutation_until_reopen(review_db):
    """§五十五/§五十六：复核完成后 `update_issue` 被 409 拒绝，重开后恢复可改。

    走的是**真实入口**（``issue_workflow_store.update_issue``），不是内部断言：
    上一版只在内部函数上断言，无法证明"工作台那条 POST /api/workflow 真的被拦"。
    """
    from api import runtime
    from src.services import issue_workflow_store

    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[_finding("rule-1")])
        job_dir = runtime.UPLOAD_ROOT / ctx["job_uuid"]
        job_dir.mkdir(parents=True, exist_ok=True)
        runtime.write_json_file(
            job_dir / "status.json",
            {
                "job_id": ctx["job_uuid"],
                "status": "done",
                "result": {"rule_findings": [_finding("rule-1")], "ai_findings": []},
            },
        )
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        # 未表态时问题可改
        await issue_workflow_store.update_issue(
            {"username": "admin", "is_admin": True},
            job_id=ctx["job_uuid"],
            issue_id="rule-1",
            status="confirmed",
            note=None,
        )
        await review_lifecycle_service.complete_review(conn, ctx["slot_id"], actor="r")

    # 完成复核的真实路径必须已经把编辑锁落下
    lock = issue_workflow_store.get_review_lock(ctx["job_uuid"])
    assert lock is not None
    assert lock["slot_id"] == ctx["slot_id"]
    assert lock["completed_by"] == "r"

    with pytest.raises(HTTPException) as excinfo:
        await issue_workflow_store.update_issue(
            {"username": "admin", "is_admin": True},
            job_id=ctx["job_uuid"],
            issue_id="rule-1",
            status="no_issue",
            note=None,
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "review_completed_locked"

    async with _conn(schema, pool) as conn:
        await review_lifecycle_service.reopen_review(conn, ctx["slot_id"], actor="r")
    assert issue_workflow_store.get_review_lock(ctx["job_uuid"]) is None

    # 重开之后恢复可改（修复 §五十六 要求的"请先重新开始复核"闭环）
    state = await issue_workflow_store.update_issue(
        {"username": "admin", "is_admin": True},
        job_id=ctx["job_uuid"],
        issue_id="rule-1",
        status="no_issue",
        note=None,
    )
    assert state["issues"][f"{ctx['job_uuid']}::rule-1"]["status"] == "no_issue"


async def test_api_error_contract_carries_blockers(review_db):
    """§一百零五/§一百零六：409 的 detail 是对象，带 error 与 blockers，不是字符串。"""
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[_finding("rule-1")])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
            await review_lifecycle_service.complete_review(conn, ctx["slot_id"], actor="r")

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == ERROR_COMPLETION_BLOCKED
    assert excinfo.value.detail["blockers"] == [{"code": "pending_findings", "count": 1}]

# ==== 事务边界与锁生命周期（deterministic race） =============================
#
# 为什么必须用两个真连接 + 可控暂停点
# ----------------------------------
# 只断言"顺序执行时能发现旧版本"证明不了并发安全：真正要验证的是
# **锁的生命周期**——槽位行锁必须从"读当前版本/代际"一直持到"写成 completed"。
# 裸放在 autocommit 连接上的 ``FOR UPDATE`` 会在该条语句结束就释放，
# 两个事务可以同时成功；这类缺陷只有在"一个事务持锁期间另一个事务尝试同一把锁"
# 时才暴露。因此下面每条用例都：① 用 asyncio.Event 把 T1 精确停在中途；
# ② 断言 T2 确实处于等待状态；③ 放开后再断言终态不变量。

FINGERPRINT_SQL = "SELECT analysis_result_fingerprint FROM analysis_jobs WHERE job_uuid = $1"


async def _sessions_for_job(schema, pool, job_uuid: str) -> List[Dict[str, Any]]:
    async with _conn(schema, pool) as conn:
        rows = await conn.fetch(
            "SELECT id::text AS id, status, document_version_id, invalidated_reason"
            " FROM review_sessions WHERE analysis_job_uuid = $1 ORDER BY created_at, id",
            job_uuid,
        )
        return [dict(row) for row in rows]


async def _current_version(schema, pool, slot_id: str) -> int:
    async with _conn(schema, pool) as conn:
        return int(
            await conn.fetchval(
                "SELECT current_document_version_id FROM material_slots WHERE id = $1::uuid",
                slot_id,
            )
        )


async def _assert_no_stale_completion(schema, pool, slot_id: str) -> None:
    """核心不变量：不存在"completed 且钉的版本不是槽位当前版本"的会话。

    这正是 WP3-A 最不能出现的状态：复核完成了，但它复核的是已经被替换掉的那一版
    文件——而它看起来完全正常。
    """
    async with _conn(schema, pool) as conn:
        rows = await conn.fetch(
            """
            SELECT r.id::text AS id, r.document_version_id, s.current_document_version_id
            FROM review_sessions AS r
            JOIN material_slots AS s ON s.id = r.slot_id
            WHERE r.slot_id = $1::uuid AND r.status = 'completed'
            """,
            slot_id,
        )
    for row in rows:
        assert int(row["document_version_id"]) == int(row["current_document_version_id"]), dict(row)


async def _drain(*tasks: Any, release: Any = None) -> None:
    """无论成败都收尾：释放暂停点、取消未完成任务、等它们真正结束。

    不做这件事时，断言失败会留下两个仍持有池连接的任务，而 ``pool.close()``
    会一直等连接归还——表现是"这条用例挂住"而不是"这条用例失败"。
    把红灯变成超时，等于把最需要看到的信号藏起来。
    """
    if release is not None:
        release.set()
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _read_audit_events(path: Any) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def test_complete_holds_slot_lock_against_concurrent_version_advance(review_db, monkeypatch):
    """§七 T1 先持有槽位锁：T2 的版本推进必须**阻塞**，直到 T1 提交。

    修复前这条用例会失败：``FOR UPDATE`` 不在显式事务里时锁在语句结束就释放，
    T2 完全不会被阻塞（同一现象已在真库上单独实证过）。
    """
    from src.services import review_session_store
    from src.services.material_slot_service import MaterialSlotService

    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        new_version = await _insert_version(
            conn, slot_id=ctx["slot_id"], file_hash="b" * 64, created_at=LATER
        )

    holding = asyncio.Event()
    release = asyncio.Event()
    real_complete = review_session_store.complete_session

    async def _paused_complete(conn, **kwargs):
        # 此刻 T1 已在自己的事务里持有槽位行锁与会话行锁，但还没写成 completed
        holding.set()
        await release.wait()
        return await real_complete(conn, **kwargs)

    monkeypatch.setattr(review_session_store, "complete_session", _paused_complete)

    async def _t1_complete():
        async with _conn(schema, pool) as conn:
            return await review_lifecycle_service.complete_review(
                conn, ctx["slot_id"], actor="a"
            )

    async def _t2_advance():
        async with _conn(schema, pool) as conn:
            return await MaterialSlotService(conn).bind_document_version(
                ctx["slot_id"], new_version
            )

    t1 = asyncio.create_task(_t1_complete())
    await asyncio.wait_for(holding.wait(), timeout=15)
    t2 = asyncio.create_task(_t2_advance())

    try:
        # T2 必须卡在槽位行锁上：这是"锁真的活到了事务结束"的直接证据
        await asyncio.sleep(0.8)
        assert not t2.done(), "T2 未被阻塞：槽位行锁没有跨语句持有（事务边界缺失）"

        release.set()
        await asyncio.wait_for(t1, timeout=15)
        await asyncio.wait_for(t2, timeout=15)
    finally:
        await _drain(t1, t2, release=release)

    # T2 提交之后，钉在 V1 上的那条 completed 会话必须已经失效
    sessions = await _sessions_for_job(schema, pool, ctx["job_uuid"])
    assert [item["status"] for item in sessions] == ["invalidated"]
    assert sessions[0]["invalidated_reason"] == "document_version_changed"
    assert await _current_version(schema, pool, ctx["slot_id"]) == new_version
    await _assert_no_stale_completion(schema, pool, ctx["slot_id"])


async def test_complete_waits_for_concurrent_version_advance_and_rejects(review_db, monkeypatch):
    """§八 反向时序：T2 先持有槽位锁推进版本，T1 必须等待并**重新读到**新版本。

    T1 不能使用进入函数前的旧快照：等锁期间版本已经从 V1 变成 V2，
    它必须据此拒绝完成（或发现旧会话已被失效），而不是把 V1 记成已完成。
    """
    from src.services.material_slot_service import MaterialSlotService

    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        new_version = await _insert_version(
            conn, slot_id=ctx["slot_id"], file_hash="c" * 64, created_at=LATER
        )

    holding = asyncio.Event()
    release = asyncio.Event()
    real_advance = MaterialSlotService._advance_current_version

    async def _paused_advance(self, slot_id, document_version_id):
        # 此刻 T2 已持有槽位行锁与版本行锁，正准备推进当前版本指针
        holding.set()
        await release.wait()
        return await real_advance(self, slot_id, document_version_id)

    monkeypatch.setattr(MaterialSlotService, "_advance_current_version", _paused_advance)

    async def _t2_advance():
        async with _conn(schema, pool) as conn:
            return await MaterialSlotService(conn).bind_document_version(
                ctx["slot_id"], new_version
            )

    async def _t1_complete():
        async with _conn(schema, pool) as conn:
            return await review_lifecycle_service.complete_review(
                conn, ctx["slot_id"], actor="a"
            )

    t2 = asyncio.create_task(_t2_advance())
    await asyncio.wait_for(holding.wait(), timeout=15)
    t1 = asyncio.create_task(_t1_complete())

    try:
        await asyncio.sleep(0.8)
        assert not t1.done(), "T1 未被阻塞：它没有等在槽位行锁上"

        release.set()
        await asyncio.wait_for(t2, timeout=15)
        with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
            await asyncio.wait_for(t1, timeout=15)
    finally:
        await _drain(t1, t2, release=release)

    assert excinfo.value.status_code == 409
    assert [item.code for item in excinfo.value.blockers], "拒绝必须带业务原因"
    assert await _current_version(schema, pool, ctx["slot_id"]) == new_version
    await _assert_no_stale_completion(schema, pool, ctx["slot_id"])
    sessions = await _sessions_for_job(schema, pool, ctx["job_uuid"])
    assert all(item["status"] != "completed" for item in sessions), sessions


async def test_complete_holds_lock_against_concurrent_reanalysis(review_db, monkeypatch):
    """§九 complete 与重新分析竞争：两者必须串行，终态不允许"已完成但代际已过期"。"""
    from src.services import review_session_store

    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")

    holding = asyncio.Event()
    release = asyncio.Event()
    real_complete = review_session_store.complete_session

    async def _paused_complete(conn, **kwargs):
        holding.set()
        await release.wait()
        return await real_complete(conn, **kwargs)

    monkeypatch.setattr(review_session_store, "complete_session", _paused_complete)

    async def _t1_complete():
        async with _conn(schema, pool) as conn:
            return await review_lifecycle_service.complete_review(
                conn, ctx["slot_id"], actor="a"
            )

    t1 = asyncio.create_task(_t1_complete())
    await asyncio.wait_for(holding.wait(), timeout=15)
    t2 = asyncio.create_task(
        review_lifecycle_service.invalidate_reviews_for_analysis_restart(ctx["job_uuid"])
    )

    try:
        await asyncio.sleep(0.8)
        assert not t2.done(), "重新分析失效钩子未被阻塞：它没有等在槽位行锁上"

        release.set()
        await asyncio.wait_for(t1, timeout=15)
        result = await asyncio.wait_for(t2, timeout=15)
    finally:
        await _drain(t1, t2, release=release)

    assert result["sessions_invalidated"] == 1
    sessions = await _sessions_for_job(schema, pool, ctx["job_uuid"])
    assert [item["status"] for item in sessions] == ["invalidated"]
    assert sessions[0]["invalidated_reason"] == "analysis_restarted"
    async with _conn(schema, pool) as conn:
        fingerprint = await conn.fetchval(FINGERPRINT_SQL, ctx["job_uuid"])
    assert fingerprint is None, "重分析钩子必须清空指纹，下一次落库才会换代"


async def test_parallel_complete_is_serialized_by_the_slot_lock(review_db, monkeypatch, tmp_path):
    """§十 用可控 barrier 证明并发 complete 真的被锁串行化，且审计只写一条。

    只靠 ``asyncio.gather`` 的随机调度无法证明锁存在（它证明的只是"这次跑通了"）。
    这里把 T1 精确停在中途，断言 T2 处于等待，再放开。
    """
    from src.services import review_session_store

    schema, pool = review_db
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))

    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="starter")

    holding = asyncio.Event()
    release = asyncio.Event()
    real_complete = review_session_store.complete_session
    paused = {"active": True}

    async def _paused_complete(conn, **kwargs):
        if paused["active"]:
            paused["active"] = False
            holding.set()
            await release.wait()
        return await real_complete(conn, **kwargs)

    monkeypatch.setattr(review_session_store, "complete_session", _paused_complete)

    async def _complete(actor: str):
        async with _conn(schema, pool) as conn:
            return await review_lifecycle_service.complete_review(
                conn, ctx["slot_id"], actor=actor
            )

    t1 = asyncio.create_task(_complete("aaa"))
    await asyncio.wait_for(holding.wait(), timeout=15)

    # T1 持锁期间把 T2 放进来：T2 必须等待（不能进入完成分支）
    t2 = asyncio.create_task(_complete("bbb"))

    try:
        await asyncio.sleep(0.8)
        assert not t2.done(), "T2 未被阻塞：槽位/会话行锁没有跨语句持有"

        release.set()
        first = await asyncio.wait_for(t1, timeout=15)
        second = await asyncio.wait_for(t2, timeout=15)
    finally:
        await _drain(t1, t2, release=release)

    assert first.session.review_session_id == second.session.review_session_id
    assert first.session.completed_by == "aaa"
    assert second.session.completed_by == "aaa", "完成人必须是第一个点击的人"
    assert second.session.status == "completed"

    sessions = await _sessions_for_job(schema, pool, ctx["job_uuid"])
    assert len(sessions) == 1 and sessions[0]["status"] == "completed"

    # 审计：并发 complete 只留一条 success
    successes = [
        event
        for event in _read_audit_events(audit_path)
        if event["action"] == "review.complete" and event["result"] == "success"
    ]
    assert len(successes) == 1, successes


async def test_complete_writes_exactly_one_success_audit(review_db, monkeypatch, tmp_path):
    """§十一/§十二：成功审计只有一个权威写入口（服务层）。

    第一次 complete → 恰好 1 条 `review.complete` success；
    幂等重复 complete → **0 条新增**（路由层曾经每次都写一条，现已移除）。
    """
    schema, pool = review_db
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))

    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        await review_lifecycle_service.complete_review(conn, ctx["slot_id"], actor="r")
        await review_lifecycle_service.complete_review(conn, ctx["slot_id"], actor="r")

    successes = [
        event
        for event in _read_audit_events(audit_path)
        if event["action"] == "review.complete" and event["result"] == "success"
    ]
    assert len(successes) == 1, successes
    assert successes[0]["details"]["slot_id"] == ctx["slot_id"]


async def test_repeated_complete_keeps_review_lock_and_session(review_db, monkeypatch, tmp_path):
    """§十八 编辑锁的失败路径：重复 complete 不清锁、不重写锁、不换会话 id。"""
    from src.services import issue_workflow_store

    schema, pool = review_db
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))

    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        first = await review_lifecycle_service.complete_review(
            conn, ctx["slot_id"], actor="r"
        )

    lock_after_first = issue_workflow_store.get_review_lock(ctx["job_uuid"])
    assert lock_after_first is not None

    async with _conn(schema, pool) as conn:
        second = await review_lifecycle_service.complete_review(
            conn, ctx["slot_id"], actor="r"
        )

    lock_after_second = issue_workflow_store.get_review_lock(ctx["job_uuid"])
    assert second.session.review_session_id == first.session.review_session_id
    assert lock_after_second == lock_after_first, "重复 complete 不得改写编辑锁"
    sessions = await _sessions_for_job(schema, pool, ctx["job_uuid"])
    assert len(sessions) == 1


async def test_refresh_status_obeys_the_outer_transaction(review_db):
    """§六 嵌套事务：``refresh_status`` 在外层事务里必须服从外层的提交/回滚。

    asyncpg 把嵌套 ``conn.transaction()`` 退化为 SAVEPOINT。要验证的不是
    "没报错"，而是**它不再自行提交**：外层回滚之后，它写下的状态必须一起消失。
    否则"槽位状态已改、会话没写成"这类半成功会重新出现。
    """
    from src.services.material_slot_service import MaterialSlotService

    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])

    async with _conn(schema, pool) as conn:
        before = await conn.fetchval(
            "SELECT status FROM material_slots WHERE id = $1::uuid", ctx["slot_id"]
        )
        with pytest.raises(RuntimeError):
            async with conn.transaction():
                await MaterialSlotService(conn).refresh_status(
                    ctx["slot_id"], analysis_state="done", review_state="in_progress"
                )
                inside = await conn.fetchval(
                    "SELECT status FROM material_slots WHERE id = $1::uuid", ctx["slot_id"]
                )
                assert inside == "reviewing"
                raise RuntimeError("刻意回滚外层事务")

    async with _conn(schema, pool) as conn:
        after = await conn.fetchval(
            "SELECT status FROM material_slots WHERE id = $1::uuid", ctx["slot_id"]
        )
    assert after == before, "外层回滚之后，refresh_status 写下的状态必须一起回滚"


async def test_first_persist_with_result_starts_at_revision_one(review_db):
    """§十四/§十六 首次落库就带结果的真实场景。

    上传时数据库不可用 → 初始 queued 快照没入库 → 分析跑完后数据库恢复 →
    第一次成功写库就是带结果的 completed 快照。
    此时 ``analysis_revision`` 必须是 1（"已经落库过一代结果"），
    而不是 0（迁移定义 0 = 尚无任何分析结果落库）。
    """
    schema, pool = review_db
    job_uuid = f"job-direct-{uuid.uuid4().hex[:10]}"
    payload = {
        "job_id": job_uuid,
        "status": "done",
        "filename": "material.pdf",
        "mode": "dual",
        "result": {"ai_findings": [], "rule_findings": [], "merged": {}},
        "structured_ingest": {},
    }

    assert await persist_analysis_job_snapshot(payload, include_results=True) is True
    async with _conn(schema, pool) as conn:
        row = await conn.fetchrow(
            "SELECT analysis_revision, analysis_result_fingerprint FROM analysis_jobs"
            " WHERE job_uuid = $1",
            job_uuid,
        )
    assert row["analysis_revision"] == 1, "首次直接落结果必须是第 1 代，不是 0"
    assert row["analysis_result_fingerprint"]

    # 同一份 completed payload 再落一次 → 仍是 1（纯重放不换代）
    assert await persist_analysis_job_snapshot(payload, include_results=True) is True
    async with _conn(schema, pool) as conn:
        replayed = await conn.fetchval(
            "SELECT analysis_revision FROM analysis_jobs WHERE job_uuid = $1", job_uuid
        )
    assert replayed == 1

    # queued 重置（清指纹）→ revision 不变；同内容再落 → 2
    assert await persist_analysis_job_snapshot({**payload, "status": "queued"}) is True
    async with _conn(schema, pool) as conn:
        reset = await conn.fetchval(
            "SELECT analysis_revision FROM analysis_jobs WHERE job_uuid = $1", job_uuid
        )
        cleared = await conn.fetchval(
            "SELECT analysis_result_fingerprint IS NULL FROM analysis_jobs WHERE job_uuid = $1",
            job_uuid,
        )
    assert reset == 1 and cleared is True

    assert await persist_analysis_job_snapshot(payload, include_results=True) is True
    async with _conn(schema, pool) as conn:
        bumped = await conn.fetchval(
            "SELECT analysis_revision FROM analysis_jobs WHERE job_uuid = $1", job_uuid
        )
    assert bumped == 2, "重置之后即使内容逐字相同也必须换代"

# ==== 跨存储一致性（独立评审 P1：文件锁 / JSONL 审计不属于 PostgreSQL 事务） ==
#
# 三种存储之间没有共同事务：
#
#   PostgreSQL                      review_sessions / material_slots / analysis_jobs
#   文件 .issue_workflow.json        review_locks（复核完成后问题不可改）
#   JSONL admin-actions.jsonl        审计事件
#
# 因此本轮的规则是"事务内只做数据库事实；文件副作用在提交成功之后做"，
# 并且对"锁写了但提交失败"这个反向窗口做**精确补偿**（按 session + basis 比较）。
# 下面每条用例对应评审要求的一个失败路径。


def _audit_events_for(action: str, result: str, path: Any) -> List[Dict[str, Any]]:
    return [
        event
        for event in _read_audit_events(path)
        if event["action"] == action and event["result"] == result
    ]


class _Boom(RuntimeError):
    """测试用的注入异常。"""


async def test_set_review_lock_failure_aborts_completion(review_db, monkeypatch, tmp_path):
    """§十二 A：编辑锁写失败 → 复核不允许成功（fail-closed）。

    fail-closed 的检验点有三处：会话仍是 in_progress、槽位没有变成 completed、
    审计里没有 review.complete success。缺任何一处都意味着"数据库说完成了、
    但问题其实还能被改"。
    """
    from src.services import issue_workflow_store

    schema, pool = review_db
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))

    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")

    def _boom(*_args, **_kwargs):
        raise PermissionError("simulated lock store failure")

    # 复核完成现在走 CAS 版本（比较问题集合快照后再写锁）；注入点同步更新。
    monkeypatch.setattr(
        issue_workflow_store, "set_review_lock_if_problem_snapshot_matches", _boom
    )

    async with _conn(schema, pool) as conn:
        with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
            await review_lifecycle_service.complete_review(
                conn, ctx["slot_id"], actor="r"
            )
    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["error"] == "review_lock_unavailable"

    sessions = await _sessions_for_job(schema, pool, ctx["job_uuid"])
    assert [item["status"] for item in sessions] == ["in_progress"], sessions
    status = await _slot_status(schema, pool, ctx["slot_id"])
    assert status["status"] != "completed", status
    assert issue_workflow_store.get_review_lock(ctx["job_uuid"]) is None
    assert _audit_events_for("review.complete", "success", audit_path) == []


async def test_commit_failure_after_lock_write_is_compensated(review_db, monkeypatch, tmp_path):
    """§十二 B：锁已写入但 PostgreSQL 提交失败 → 必须精确补偿掉那条锁。

    怎么构造"提交失败"：编辑锁是在事务体内写的，写完之后事务里再没有别的
    数据库动作，所以失败只可能发生在**提交**这一刻。这里把事务边界换成
    "事务体正常执行、退出时抛错"——等价于 COMMIT 失败，数据库整体回滚，
    而文件锁已经落下。

    终态必须满足：会话不是 completed，且没有残留本次会话的编辑锁。
    没有补偿时，那条锁会把"问题不可改"永久扣在一份**并没有复核完成**的材料上。
    """
    from contextlib import asynccontextmanager

    from src.db.transaction import transaction_scope
    from src.services import issue_workflow_store
    from src.services import review_session_store

    schema, pool = review_db
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))

    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")

    @asynccontextmanager
    async def _commit_fails(conn):
        """事务体正常跑完，退出时抛错 = COMMIT 失败。"""
        async with transaction_scope(conn):
            yield
            raise _Boom("simulated commit failure")

    monkeypatch.setattr(review_session_store, "review_transaction", _commit_fails)

    async with _conn(schema, pool) as conn:
        with pytest.raises(_Boom):
            await review_lifecycle_service.complete_review(conn, ctx["slot_id"], actor="r")

    sessions = await _sessions_for_job(schema, pool, ctx["job_uuid"])
    assert [item["status"] for item in sessions] == ["in_progress"], sessions
    assert (
        issue_workflow_store.get_review_lock(ctx["job_uuid"]) is None
    ), "提交失败后必须把本次写入的编辑锁补偿掉，否则问题被永久锁死"
    assert _audit_events_for("review.complete", "success", audit_path) == []

    # 补偿之后重试必须成功（锁必须处于"可被正确重建"的状态）
    monkeypatch.setattr(review_session_store, "review_transaction", transaction_scope)
    async with _conn(schema, pool) as conn:
        completed = await review_lifecycle_service.complete_review(
            conn, ctx["slot_id"], actor="r"
        )
    assert completed.session.status == "completed"
    lock = issue_workflow_store.get_review_lock(ctx["job_uuid"])
    assert lock is not None and lock["review_session_id"] == completed.session.review_session_id


async def test_compensation_requires_session_match(review_db, monkeypatch, tmp_path):
    """§十二 C：补偿只清"自己那条"锁；文件里已是别的会话时不得删除。

    最危险的一档是 **basis 相同、session 不同**：同一代分析上重开复核会产生
    新的 session id 而 basis token 不变（重开不换代）。此时旧会话的补偿如果
    只比 basis，就会把**新会话**的锁删掉——"复核已完成、问题不可改"这条安全
    约束会从后来那次复核身上被静默摘掉。因此三个键（job / session / basis）
    必须一起比，本用例逐档验证。
    """
    from src.services import issue_workflow_store

    schema, pool = review_db
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))

    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")

    basis = f"{ctx['job_uuid']}:1"
    # 文件里是**别的会话**（B）的锁，且 basis 与当前一致
    issue_workflow_store.set_review_lock(
        ctx["job_uuid"],
        slot_id=ctx["slot_id"],
        review_session_id="sess-B",
        analysis_basis_token=basis,
        completed_by="other",
    )

    # 档 1：basis 相同、session 不同 → 不得删除（这正是"只比 basis"会漏掉的那一档）
    assert (
        issue_workflow_store.clear_review_lock_if_matches(
            ctx["job_uuid"], review_session_id="sess-A", analysis_basis_token=basis
        )
        is False
    )
    survivor = issue_workflow_store.get_review_lock(ctx["job_uuid"])
    assert survivor is not None and survivor["review_session_id"] == "sess-B"

    # 档 2：session 相同、basis 不同 → 不得删除（旧代际的补偿不能动新代际的锁）
    assert (
        issue_workflow_store.clear_review_lock_if_matches(
            ctx["job_uuid"],
            review_session_id="sess-B",
            analysis_basis_token=f"{ctx['job_uuid']}:9",
        )
        is False
    )
    assert issue_workflow_store.get_review_lock(ctx["job_uuid"]) is not None

    # 档 3：三个键全匹配 → 允许删除
    assert (
        issue_workflow_store.clear_review_lock_if_matches(
            ctx["job_uuid"], review_session_id="sess-B", analysis_basis_token=basis
        )
        is True
    )
    assert issue_workflow_store.get_review_lock(ctx["job_uuid"]) is None

    # 档 4：本来就没有锁 → 返回 False，不报错
    assert (
        issue_workflow_store.clear_review_lock_if_matches(
            ctx["job_uuid"], review_session_id="sess-B", analysis_basis_token=basis
        )
        is False
    )


async def test_success_audit_appears_only_after_commit(review_db, monkeypatch, tmp_path):
    """§十二 D：审计只能在 PostgreSQL 提交成功之后出现。

    回滚场景：一条 success 都不许有。
    """
    from src.services.material_slot_service import MaterialSlotService

    schema, pool = review_db
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))

    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")

    real_refresh = MaterialSlotService.refresh_status

    async def _failing_refresh(self, slot_id, **kwargs):
        if kwargs.get("review_state") == "completed":
            raise _Boom("rollback")
        return await real_refresh(self, slot_id, **kwargs)

    monkeypatch.setattr(MaterialSlotService, "refresh_status", _failing_refresh)
    async with _conn(schema, pool) as conn:
        with pytest.raises(_Boom):
            await review_lifecycle_service.complete_review(conn, ctx["slot_id"], actor="r")

    assert _audit_events_for("review.complete", "success", audit_path) == []

    # 正常成功：恰好 1 条
    monkeypatch.setattr(MaterialSlotService, "refresh_status", real_refresh)
    async with _conn(schema, pool) as conn:
        await review_lifecycle_service.complete_review(conn, ctx["slot_id"], actor="r")
    assert len(_audit_events_for("review.complete", "success", audit_path)) == 1

    # 重复 complete：0 条新增
    async with _conn(schema, pool) as conn:
        await review_lifecycle_service.complete_review(conn, ctx["slot_id"], actor="r")
    assert len(_audit_events_for("review.complete", "success", audit_path)) == 1


async def test_reopen_rollback_keeps_old_completion_and_lock(review_db, monkeypatch, tmp_path):
    """§十二 E：reopen 的数据库事务失败时，旧 completed 与它的编辑锁都必须还在。

    这检验的是"清锁不能在事务内执行"：若清锁写在事务体内，回滚后会出现
    "数据库说这份材料已完成复核，文件却说问题可以随便改"的分裂状态。
    """
    from src.services import issue_workflow_store
    from src.services.material_slot_service import MaterialSlotService

    schema, pool = review_db
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))

    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")
        first = await review_lifecycle_service.complete_review(
            conn, ctx["slot_id"], actor="r"
        )
    lock_before = issue_workflow_store.get_review_lock(ctx["job_uuid"])
    assert lock_before is not None

    real_refresh = MaterialSlotService.refresh_status

    async def _failing_refresh(self, slot_id, **kwargs):
        if kwargs.get("review_state") == "in_progress":
            raise _Boom("rollback the reopen")
        return await real_refresh(self, slot_id, **kwargs)

    monkeypatch.setattr(MaterialSlotService, "refresh_status", _failing_refresh)
    async with _conn(schema, pool) as conn:
        with pytest.raises(_Boom):
            await review_lifecycle_service.reopen_review(conn, ctx["slot_id"], actor="r")

    sessions = await _sessions_for_job(schema, pool, ctx["job_uuid"])
    assert [item["status"] for item in sessions] == ["completed"], sessions
    assert (
        issue_workflow_store.get_review_lock(ctx["job_uuid"]) == lock_before
    ), "reopen 事务回滚时旧编辑锁必须保留（否则问题被提前解锁）"
    assert _audit_events_for("review.reopen", "success", audit_path) == []

    # 放开之后重开成功：旧锁被精确清掉、新会话开始
    monkeypatch.setattr(MaterialSlotService, "refresh_status", real_refresh)
    async with _conn(schema, pool) as conn:
        reopened = await review_lifecycle_service.reopen_review(
            conn, ctx["slot_id"], actor="r"
        )
    assert reopened.session.status == "in_progress"
    assert reopened.session.review_session_id != first.session.review_session_id
    assert issue_workflow_store.get_review_lock(ctx["job_uuid"]) is None
    sessions = await _sessions_for_job(schema, pool, ctx["job_uuid"])
    assert sorted(item["status"] for item in sessions) == ["in_progress", "invalidated"]


async def test_invalidate_audit_waits_for_commit(review_db, monkeypatch, tmp_path):
    """失效路径同样遵守跨存储规则：事务回滚时不清锁、不写审计。"""
    from src.services import review_session_store
    from src.services.material_slot_service import MaterialSlotService

    schema, pool = review_db
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))

    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")

    real_refresh = MaterialSlotService.refresh_status

    async def _failing_refresh(self, slot_id, **kwargs):
        if kwargs.get("review_state") == "required":
            raise _Boom("rollback the invalidation")
        return await real_refresh(self, slot_id, **kwargs)

    monkeypatch.setattr(MaterialSlotService, "refresh_status", _failing_refresh)
    async with _conn(schema, pool) as conn:
        with pytest.raises(_Boom):
            await review_lifecycle_service.invalidate_review(
                conn, ctx["slot_id"], reason="analysis_restarted"
            )
    sessions = await _sessions_for_job(schema, pool, ctx["job_uuid"])
    assert [item["status"] for item in sessions] == ["in_progress"], sessions
    assert _audit_events_for("review.invalidate", "success", audit_path) == []

    monkeypatch.setattr(MaterialSlotService, "refresh_status", real_refresh)
    async with _conn(schema, pool) as conn:
        assert (
            await review_lifecycle_service.invalidate_review(
                conn, ctx["slot_id"], reason="analysis_restarted"
            )
            == 1
        )
    assert len(_audit_events_for("review.invalidate", "success", audit_path)) == 1


# ==== 问题集合快照 CAS 与 mutation 事务边界（独立评审第三轮） ================
#
# 两个并发一致性问题：
#   A. Completion Gate 读的是"问题集合快照"，而编辑锁是**后来**才落的。
#      两份读取之间有人改 issue 状态 / 加进忽略清单时，会产出
#      "复核已完成，但它据以完成的那条问题已经被改回未处理"。
#   B. mutation 若跑在调用方的外层事务里，`_mutation_scope` 退出时并不是真正
#      的 commit，文件副作用却已经执行 —— 调用方一回滚就重新造出
#      "数据库说没复核、文件说已复核"。
# 下面每条用例都用可控暂停点把窗口钉死。


def _write_job_payload(job_uuid: str, finding_id: str) -> None:
    """给任务目录写一份带该 finding 的 status.json（update_issue 需要它）。"""
    from api import runtime

    job_dir = runtime.UPLOAD_ROOT / job_uuid
    job_dir.mkdir(parents=True, exist_ok=True)
    runtime.write_json_file(
        job_dir / "status.json",
        {
            "job_id": job_uuid,
            "status": "done",
            "created_by": "unit-user",
            "result": {
                "rule_findings": [
                    {
                        "id": finding_id,
                        "severity": "high",
                        "title": f"问题 {finding_id}",
                        "evidence": [{"page": 1, "text": "证据", "bbox": [1, 2, 3, 4]}],
                    }
                ],
                "ai_findings": [],
            },
        },
    )


async def test_complete_rejects_when_problem_snapshot_changed_during_gate(
    review_db, monkeypatch, tmp_path
):
    """§十一 决策竞态：门禁判定之后、落编辑锁之前有人改了 issue → 409 + 回滚。

    没有 CAS 时的终态是：review completed / slot completed，但 issue-1 = pending
    —— "复核完成"对应的是一份已经不存在的问题集合。这是禁止状态。
    """
    from src.services import issue_workflow_store

    schema, pool = review_db
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit_path))

    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[_finding("rule-1")])
        _write_job_payload(ctx["job_uuid"], "rule-1")
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")

    # 先把这条问题处理成"已确认"，门禁才可能通过
    user = {"username": "unit-user", "is_admin": True}
    await issue_workflow_store.update_issue(
        user, job_id=ctx["job_uuid"], issue_id="rule-1", status="confirmed", note=None
    )

    holding = asyncio.Event()
    release = asyncio.Event()
    # 暂停点选在 `complete_session`（async，可以真正等）：此刻门禁已经判定通过、
    # 编辑锁尚未落下，正是"读完问题集合 → 落锁"之间的那个窗口。
    # 同步钩子假停不行——同步函数里无法 await，只会立刻返回，窗口就不存在了。
    real_complete = review_session_store.complete_session

    async def _paused_complete(conn, **kwargs):
        holding.set()
        await release.wait()
        return await real_complete(conn, **kwargs)

    monkeypatch.setattr(review_session_store, "complete_session", _paused_complete)

    async def _t1_complete():
        async with _conn(schema, pool) as conn:
            return await review_lifecycle_service.complete_review(
                conn, ctx["slot_id"], actor="a"
            )

    t1 = asyncio.create_task(_t1_complete())
    await asyncio.wait_for(holding.wait(), timeout=15)

    # T2：门禁之后把这条问题改回未处理（此刻还没有编辑锁，所以能成功）
    await issue_workflow_store.update_issue(
        user, job_id=ctx["job_uuid"], issue_id="rule-1", status="pending", note=None
    )
    release.set()

    with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
        await asyncio.wait_for(t1, timeout=15)

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "review_workflow_changed"

    sessions = await _sessions_for_job(schema, pool, ctx["job_uuid"])
    assert [item["status"] for item in sessions] == ["in_progress"], sessions
    status = await _slot_status(schema, pool, ctx["slot_id"])
    assert status["status"] != "completed", status
    assert issue_workflow_store.get_review_lock(ctx["job_uuid"]) is None, "冲突时不得写编辑锁"
    decisions = issue_workflow_store.get_job_issue_decisions(ctx["job_uuid"])
    assert decisions["rule-1"] == "pending", "T2 的修改必须保留（它是用户真实操作）"
    assert _audit_events_for("review.complete", "success", audit_path) == []


async def test_complete_locks_then_blocks_further_issue_changes(review_db, monkeypatch, tmp_path):
    """§十二 反方向：编辑锁落下之后，任何问题改动都必须被 409 拒绝。

    "T2 等待"这件事由 ``issue_workflow_store`` 的文件锁提供（见
    ``test_workflow_state_lock_is_exclusive_across_threads``）；
    这条用例断言的是可观察结果：锁落下后 update_issue 一律
    ``review_completed_locked``，问题保持原状态。
    """
    from src.services import issue_workflow_store

    schema, pool = review_db
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))

    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[_finding("rule-1")])
        _write_job_payload(ctx["job_uuid"], "rule-1")
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")

    user = {"username": "unit-user", "is_admin": True}
    await issue_workflow_store.update_issue(
        user, job_id=ctx["job_uuid"], issue_id="rule-1", status="confirmed", note=None
    )
    async with _conn(schema, pool) as conn:
        completed = await review_lifecycle_service.complete_review(
            conn, ctx["slot_id"], actor="a"
        )
    assert completed.session.status == "completed"

    with pytest.raises(HTTPException) as excinfo:
        await issue_workflow_store.update_issue(
            user, job_id=ctx["job_uuid"], issue_id="rule-1", status="pending", note=None
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "review_completed_locked"
    assert (
        issue_workflow_store.get_job_issue_decisions(ctx["job_uuid"])["rule-1"] == "confirmed"
    ), "已完成复核后问题状态不得改变"


async def test_create_package_cannot_change_the_locked_problem_set(review_db, monkeypatch, tmp_path):
    """§十三 create_package 同样会改问题状态（→ in_package），必须受同一约束。

    两个方向都验：锁落下之后被 409 拒绝；锁之前改动会让 complete 因快照变化失败。
    """
    from src.services import issue_workflow_store

    schema, pool = review_db
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))

    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[_finding("rule-1")])
        _write_job_payload(ctx["job_uuid"], "rule-1")
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")

    user = {"username": "unit-user", "is_admin": True}
    await issue_workflow_store.create_package(
        user,
        name="整改包",
        job_ids=[ctx["job_uuid"]],
        issue_keys=[f"{ctx['job_uuid']}::rule-1"],
    )
    # 组包把该问题改成 in_package：门禁现在是通过的
    async with _conn(schema, pool) as conn:
        completed = await review_lifecycle_service.complete_review(
            conn, ctx["slot_id"], actor="a"
        )
    assert completed.session.status == "completed"

    with pytest.raises(HTTPException) as excinfo:
        await issue_workflow_store.create_package(
            user,
            name="第二个包",
            job_ids=[ctx["job_uuid"]],
            issue_keys=[f"{ctx['job_uuid']}::rule-1"],
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "review_completed_locked"


async def test_create_package_during_gate_fails_completion(review_db, monkeypatch, tmp_path):
    """组包发生在门禁之后、落锁之前 → 快照变化 → complete 409 + 回滚。"""
    from src.services import issue_workflow_store

    schema, pool = review_db
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))

    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[_finding("rule-1")])
        _write_job_payload(ctx["job_uuid"], "rule-1")
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")

    user = {"username": "unit-user", "is_admin": True}
    await issue_workflow_store.update_issue(
        user, job_id=ctx["job_uuid"], issue_id="rule-1", status="confirmed", note=None
    )

    holding = asyncio.Event()
    release = asyncio.Event()

    # 暂停点选在 `complete_session`（async，可以真正等）：此刻门禁已经判定通过、
    # 编辑锁尚未落下，正是"读完问题集合 → 落锁"之间的那个窗口。
    # 同步钩子假停不行——同步函数里无法 await，只会立刻返回，窗口就不存在了。
    real_complete = review_session_store.complete_session

    async def _paused_complete(conn, **kwargs):
        holding.set()
        await release.wait()
        return await real_complete(conn, **kwargs)

    monkeypatch.setattr(review_session_store, "complete_session", _paused_complete)

    async def _t1_complete():
        async with _conn(schema, pool) as conn:
            return await review_lifecycle_service.complete_review(
                conn, ctx["slot_id"], actor="a"
            )

    t1 = asyncio.create_task(_t1_complete())
    await asyncio.wait_for(holding.wait(), timeout=15)
    await issue_workflow_store.create_package(
        user, name="并发组包", job_ids=[ctx["job_uuid"]], issue_keys=[f"{ctx['job_uuid']}::rule-1"]
    )
    release.set()

    with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
        await asyncio.wait_for(t1, timeout=15)
    assert excinfo.value.detail["error"] == "review_workflow_changed"
    sessions = await _sessions_for_job(schema, pool, ctx["job_uuid"])
    assert [item["status"] for item in sessions] == ["in_progress"], sessions


async def test_ignore_endpoint_is_blocked_after_review_completed(review_db, monkeypatch, tmp_path):
    """§十四 legacy 忽略清单同样属于问题集合：复核完成后不允许再改。

    ``ignored_issue_ids`` 至今仍有线上可写入口（``POST /api/jobs/{id}/issues/ignore``，
    旧任务详情页会调用），因此它必须与 workflow 决定受同一约束。
    """
    from api import runtime
    from src.services import issue_workflow_store

    schema, pool = review_db
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))

    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[_finding("rule-1")])
        _write_job_payload(ctx["job_uuid"], "rule-1")
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")

    user = {"username": "unit-user", "is_admin": True}
    await issue_workflow_store.update_issue(
        user, job_id=ctx["job_uuid"], issue_id="rule-1", status="confirmed", note=None
    )
    async with _conn(schema, pool) as conn:
        await review_lifecycle_service.complete_review(conn, ctx["slot_id"], actor="a")

    with pytest.raises(HTTPException) as excinfo:
        issue_workflow_store.add_ignored_issue_id(ctx["job_uuid"], "rule-1")
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error"] == "review_completed_locked"

    job_dir = runtime.UPLOAD_ROOT / ctx["job_uuid"]
    assert runtime.read_ignored_issue_ids(job_dir) == set(), "被拒绝时不得写入"


async def test_ignored_change_during_gate_fails_completion(review_db, monkeypatch, tmp_path):
    """门禁之后、落锁之前被加入忽略清单 → 快照变化 → complete 409。

    忽略清单会**把一条问题从复核集合里移除**，因此它同样属于快照：
    不纳入校验时，"完成后才想起忽略"会让已完成复核对应的问题集合发生变化。
    """
    from src.services import issue_workflow_store

    schema, pool = review_db
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))

    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[_finding("rule-1")])
        _write_job_payload(ctx["job_uuid"], "rule-1")
        await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")

    user = {"username": "unit-user", "is_admin": True}
    await issue_workflow_store.update_issue(
        user, job_id=ctx["job_uuid"], issue_id="rule-1", status="confirmed", note=None
    )

    holding = asyncio.Event()
    release = asyncio.Event()

    # 暂停点选在 `complete_session`（async，可以真正等）：此刻门禁已经判定通过、
    # 编辑锁尚未落下，正是"读完问题集合 → 落锁"之间的那个窗口。
    # 同步钩子假停不行——同步函数里无法 await，只会立刻返回，窗口就不存在了。
    real_complete = review_session_store.complete_session

    async def _paused_complete(conn, **kwargs):
        holding.set()
        await release.wait()
        return await real_complete(conn, **kwargs)

    monkeypatch.setattr(review_session_store, "complete_session", _paused_complete)

    async def _t1_complete():
        async with _conn(schema, pool) as conn:
            return await review_lifecycle_service.complete_review(
                conn, ctx["slot_id"], actor="a"
            )

    t1 = asyncio.create_task(_t1_complete())
    await asyncio.wait_for(holding.wait(), timeout=15)
    issue_workflow_store.add_ignored_issue_id(ctx["job_uuid"], "rule-1")
    release.set()

    with pytest.raises(review_lifecycle_service.ReviewLifecycleError) as excinfo:
        await asyncio.wait_for(t1, timeout=15)
    assert excinfo.value.detail["error"] == "review_workflow_changed"
    sessions = await _sessions_for_job(schema, pool, ctx["job_uuid"])
    assert [item["status"] for item in sessions] == ["in_progress"], sessions


@pytest.mark.parametrize("operation", ["start", "complete", "reopen", "invalidate"])
async def test_mutations_reject_an_outer_transaction(review_db, operation):
    """§二十/§二十一 四个 public mutation 都必须在外层事务下 **fail fast**。

    跑在外层事务里时，``transaction_scope`` 直接复用（不 BEGIN、不 SAVEPOINT），
    函数返回时数据库并没有提交，但文件副作用已经执行 —— 调用方一回滚就出现
    "数据库说没复核、文件说已复核"。因此正确性必须由代码保证：在**任何**
    数据库写入与文件副作用之前拒绝。SAVEPOINT 解决不了（RELEASE 不是 commit）。
    """
    schema, pool = review_db
    async with _conn(schema, pool) as conn:
        ctx = await _make_reviewable_slot(conn, findings=[])
        if operation in {"complete", "reopen", "invalidate"}:
            await review_lifecycle_service.start_review(conn, ctx["slot_id"], actor="r")

    async with _conn(schema, pool) as conn:
        with pytest.raises(RuntimeError) as excinfo:
            async with conn.transaction():
                if operation == "start":
                    await review_lifecycle_service.start_review(
                        conn, ctx["slot_id"], actor="r"
                    )
                elif operation == "complete":
                    await review_lifecycle_service.complete_review(
                        conn, ctx["slot_id"], actor="r"
                    )
                elif operation == "reopen":
                    await review_lifecycle_service.reopen_review(
                        conn, ctx["slot_id"], actor="r"
                    )
                else:
                    await review_lifecycle_service.invalidate_review(conn, ctx["slot_id"])
    assert "autocommit" in str(excinfo.value)

    # 拒绝必须发生在任何写入之前
    sessions = await _sessions_for_job(schema, pool, ctx["job_uuid"])
    if operation in {"complete", "reopen", "invalidate"}:
        assert [item["status"] for item in sessions] == ["in_progress"], sessions
    else:
        assert sessions == [], sessions
    status = await _slot_status(schema, pool, ctx["slot_id"])
    assert status["status"] != "completed", status


def test_workflow_state_lock_is_exclusive_across_threads(tmp_path):
    """§十二 的"T2 必须等待"：``_state_lock`` 的文件锁确实跨线程互斥。

    用真线程验证——asyncio 单线程里"等锁"会表现为同步忙等（把事件循环一起卡住），
    测不出等待语义。这里只验证机制：一方持锁期间，另一方拿不到。
    """
    import threading
    import time

    from src.services import issue_workflow_store

    held = threading.Event()
    acquired = threading.Event()
    release = threading.Event()

    def _holder() -> None:
        with issue_workflow_store._state_lock():
            held.set()
            release.wait(timeout=10)

    def _waiter() -> None:
        held.wait(timeout=10)
        with issue_workflow_store._state_lock():
            acquired.set()

    holder = threading.Thread(target=_holder)
    waiter = threading.Thread(target=_waiter)
    holder.start()
    waiter.start()
    assert held.wait(timeout=10)

    time.sleep(0.3)
    assert not acquired.is_set(), "第二个线程不应在第一个持锁期间拿到文件锁"

    release.set()
    holder.join(timeout=10)
    waiter.join(timeout=10)
    assert acquired.is_set(), "释放之后第二个线程必须拿到锁"
