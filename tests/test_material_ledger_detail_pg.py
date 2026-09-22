"""材料详情四个接口在**真实 PostgreSQL** 上的验证（显式 opt-in）。

为什么必须有这一层
------------------
接口层用假连接跑通"路由 → 授权 → 服务 → 投影 → DTO"，但假连接不解析 SQL、
不执行 JOIN、不关心 JSONB 的存储形态与类型推导。以下问题**只有真库能回答**：

A. ``LEFT JOIN`` 的三态（版本有运行 / 有运行没结果 / 没有运行）在真库上
   是否真如预期，版本会不会因为"没有运行"而从历史里消失；
B. ``(j.metadata -> 'structured_ingest' ->> 'document_version_id')::bigint``
   这个表达式在真库上的类型推导与 NULL / 非数字行为 —— 这正是"运行归属"
   的唯一依据，猜错一次就会把别的文件的结论挂到这条材料上；
C. ``jsonb_array_length`` / ``jsonb`` 列经 asyncpg 取回后的真实形态
   （本仓库没有注册 JSON codec，取回来是字符串），以及
   ``metadata.result_meta.obligation_coverage`` 的嵌套读取；
D. 版本排序与 ``is_current`` 在真库返回顺序不确定时是否仍然稳定；
E. 权限谓词与 ``material_slots`` 三列 OR 的联合行为在真 SQL 上成立；
F. UUID → text、TIMESTAMPTZ → tz-aware datetime 经 Pydantic 序列化后
   是否符合契约（时间必须是 ISO 8601 带时区）。

隔离方式（沿用 WP1/WP2-A 的 PG 用例约定）
-----------------------------------------
整个文件在**随机命名的独立 schema** 里执行：先跑真实迁移建出全部表，
灌入测试数据，跑完 ``DROP SCHEMA CASCADE``。即使目标库是开发库，
也不会碰 public 下的任何既有数据，不留残留表。

运行方式::

    GOVBUDGET_TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/fiscal_db \
        python -m pytest tests/test_material_ledger_detail_pg.py -v
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from src.db.migrations import run_migrations
from src.services.material_detail_query_service import MaterialDetailQueryService

pytestmark = pytest.mark.real_database

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(days=3)

DISTRICT = "district-putuo"
DEPT = "dept-planning"
SUB_UNIT = "unit-planning-enforcement"
SIBLING_UNIT = "unit-planning-affairs"


async def _insert_slot(conn, **fields: Any) -> str:
    """插入一条槽位，返回 slot_id（text）。字段名与真库列一一对应。"""
    payload: Dict[str, Any] = {
        "slot_key": f"key-{uuid.uuid4().hex}",
        "mapping_key": "",
        "jurisdiction_org_id": DISTRICT,
        "jurisdiction_name": "上海市普陀区",
        "department_org_id": DEPT,
        "department_name": "上海市普陀区规划和自然资源局",
        "subject_org_id": SUB_UNIT,
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
    conn,
    *,
    slot_id: str,
    file_hash: str,
    created_at: datetime,
    original_filename: str = "material.pdf",
) -> int:
    """插入一条挂在该槽位下的文件版本，返回 document_version_id。

    ``fiscal_document_versions.document_id`` 是 NOT NULL 外键，
    因此必须先有一条 ``fiscal_documents``（其 ``org_unit_id`` 又指向 ``org_units``）。
    """
    org_unit_id = await conn.fetchval(
        "INSERT INTO org_units (org_name) VALUES ($1) ON CONFLICT (org_name)"
        " DO UPDATE SET region = EXCLUDED.region RETURNING id",
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
                (document_id, file_hash, storage_key, created_at,
                 slot_id, original_filename, file_size_bytes, content_type)
            VALUES ($1, $2, $3, $4, $5::uuid, $6, $7, $8)
            RETURNING id
            """,
            document_id,
            file_hash,
            f"{uuid.uuid4().hex}/{original_filename}",
            created_at,
            slot_id,
            original_filename,
            2048,
            "application/pdf",
        )
    )


async def _insert_job(conn, *, job_uuid: str, metadata: Dict[str, Any], status: str = "done",
                      completed_at: datetime = NOW) -> int:
    """插入一条分析任务，返回 analysis_jobs.id。"""
    return int(
        await conn.fetchval(
            """
            INSERT INTO analysis_jobs
                (job_uuid, filename, status, mode, started_at, completed_at, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            RETURNING id
            """,
            job_uuid,
            "material.pdf",
            status,
            "dual",
            completed_at - timedelta(minutes=2),
            completed_at,
            json.dumps(metadata, ensure_ascii=False),
        )
    )


async def _insert_result(
    conn,
    *,
    job_id: int,
    ai_findings: List[Dict[str, Any]],
    rule_findings: List[Dict[str, Any]] | None = None,
    merged_total: int = 0,
) -> None:
    await conn.execute(
        """
        INSERT INTO analysis_results
            (job_id, ai_findings, rule_findings, merged_result, raw_response)
        VALUES ($1, $2::jsonb, $3::jsonb, $4::jsonb, '{}'::jsonb)
        """,
        job_id,
        json.dumps(ai_findings, ensure_ascii=False),
        json.dumps(rule_findings or [], ensure_ascii=False),
        json.dumps({"totals": {"merged": merged_total}}, ensure_ascii=False),
    )


@pytest.fixture
async def detail_db(real_database_url, monkeypatch):
    """独立 schema + 真实迁移，产出可直接查询的连接池。"""
    from src.db.connection import DatabaseConnection

    schema = f"matdetail_test_{uuid.uuid4().hex[:12]}"
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


# ==== A：V1 + V2，current = V2 ===============================================


async def test_versions_keep_both_and_mark_current(detail_db):
    """A. 槽位两个版本都返回；当前指针指向 V2 时只有 V2 ``is_current``。"""
    schema, pool = detail_db
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(conn, current_document_version_id=None)
        v1 = await _insert_version(
            conn, slot_id=slot_id, file_hash="1" * 64, created_at=EARLIER,
            original_filename="v1.pdf",
        )
        v2 = await _insert_version(
            conn, slot_id=slot_id, file_hash="2" * 64, created_at=NOW,
            original_filename="v2.pdf",
        )
        # 版本先插完再回填当前指针（真库的外键顺序与生产一致）
        await conn.execute(
            "UPDATE material_slots SET current_document_version_id = $1 WHERE id = $2::uuid",
            v2,
            slot_id,
        )

        service = MaterialDetailQueryService(conn)
        slot_row = await service.load_slot_row(slot_id)
        assert slot_row is not None
        versions = await service.versions(slot_row)

    assert [item.document_version_id for item in versions] == [v2, v1]
    assert [item.is_current for item in versions] == [True, False]
    assert versions[0].original_filename == "v2.pdf"
    assert versions[0].created_at is not None
    assert versions[0].created_at.tzinfo is not None
    # storage_key 从查询里就不取，DTO 上更没有
    assert not hasattr(versions[0], "storage_key")


# ==== B：V1 有分析、V2 没有 ==================================================


async def test_current_analysis_never_falls_back_to_previous_version(detail_db):
    """B. V1 有分析结果、V2 是当前版本且没有分析 → 详情不得显示 V1 的 finding。"""
    schema, pool = detail_db
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(conn, current_document_version_id=None)
        v1 = await _insert_version(
            conn, slot_id=slot_id, file_hash="3" * 64, created_at=EARLIER
        )
        v2 = await _insert_version(
            conn, slot_id=slot_id, file_hash="4" * 64, created_at=NOW
        )
        job_id = await _insert_job(
            conn,
            job_uuid=f"job-v1-{uuid.uuid4().hex[:6]}",
            metadata={"structured_ingest": {"document_version_id": v1, "status": "done"}},
        )
        await _insert_result(
            conn,
            job_id=job_id,
            ai_findings=[
                {"id": f"v1-{n}", "severity": "high", "title": "V1 的问题"} for n in range(3)
            ],
            merged_total=3,
        )
        await conn.execute(
            "UPDATE material_slots SET current_document_version_id = $1 WHERE id = $2::uuid",
            v2,
            slot_id,
        )

        service = MaterialDetailQueryService(conn)
        slot_row = await service.load_slot_row(slot_id)
        assert slot_row is not None
        detail = await service.slot_detail(slot_row)
        runs = await service.run_history(slot_row)

    assert detail.current_version is not None
    assert detail.current_version.document_version_id == v2
    assert detail.version_total == 2
    assert detail.historical_version_count == 1

    # 关键：当前分析不可用，正式问题数必须是 None（不是 3，也不是 0）
    assert detail.current_analysis.available is False
    assert detail.current_analysis.reason == "no_persisted_analysis_for_current_version"
    assert detail.current_analysis.formal_issue_count is None
    assert detail.current_analysis.formal_findings is None
    assert detail.current_analysis.coverage.available is False

    # V1 的运行仍然可见，但明确标成历史版本
    assert [(item.document_version_id, item.is_current_document_version) for item in runs] == [
        (v1, False)
    ]


# ==== C：同版本两个 run，选择稳定 ============================================


async def test_two_runs_on_same_version_choose_current_stably(detail_db):
    """C. 同一 V2 上两个运行：当前分析稳定选"有效完成时刻最新"的那个。"""
    schema, pool = detail_db
    old_uuid = f"job-old-{uuid.uuid4().hex[:6]}"
    new_uuid = f"job-new-{uuid.uuid4().hex[:6]}"
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(conn, current_document_version_id=None)
        v2 = await _insert_version(
            conn, slot_id=slot_id, file_hash="5" * 64, created_at=EARLIER
        )
        older_job = await _insert_job(
            conn,
            job_uuid=old_uuid,
            metadata={"structured_ingest": {"document_version_id": v2, "status": "done"}},
            completed_at=EARLIER,
        )
        await _insert_result(
            conn,
            job_id=older_job,
            ai_findings=[{"id": "old-1", "severity": "high", "title": "旧运行的问题"}],
            merged_total=1,
        )
        newer_job = await _insert_job(
            conn,
            job_uuid=new_uuid,
            metadata={"structured_ingest": {"document_version_id": v2, "status": "done"}},
            completed_at=NOW,
        )
        await _insert_result(
            conn,
            job_id=newer_job,
            ai_findings=[
                {"id": "new-formal", "severity": "high", "title": "新运行的正式问题"},
                {
                    "id": "new-degraded",
                    "severity": "manual_review",
                    "evidence_status": "degraded_missing_evidence",
                    "title": "新运行的待核验项",
                },
            ],
            merged_total=2,
        )
        await conn.execute(
            "UPDATE material_slots SET current_document_version_id = $1 WHERE id = $2::uuid",
            v2,
            slot_id,
        )

        service = MaterialDetailQueryService(conn)
        slot_row = await service.load_slot_row(slot_id)
        assert slot_row is not None
        # 连查三次：真库的返回顺序不确定，选择必须仍然稳定
        picks = set()
        for _ in range(3):
            detail = await service.slot_detail(slot_row)
            picks.add(detail.current_analysis.run.job_uuid if detail.current_analysis.run else "")

    assert picks == {new_uuid}, "同一份数据连查三次必须选中同一个运行"
    assert newer_job != older_job
    assert detail.current_analysis.available is True
    assert detail.current_analysis.run is not None
    assert detail.current_analysis.run.job_uuid == new_uuid
    # 正式问题只算正式门禁通过的：降级项进"需人工核验"
    assert detail.current_analysis.formal_issue_count == 1
    assert [item.finding_id for item in detail.current_analysis.formal_findings] == [
        "new-formal"
    ]
    assert [item.finding_id for item in detail.current_analysis.manual_review_items] == [
        "new-degraded"
    ]


# ==== D：精确关联 + 覆盖读取 =================================================


async def test_structured_version_id_links_and_coverage_is_read_from_jsonb(detail_db):
    """D. ``metadata.structured_ingest.document_version_id`` 精确关联，
    且 ``result_meta.obligation_coverage`` 能从真库 JSONB 里完整读出来
    （asyncpg 没有注册 JSON codec，取回来是字符串 —— 这一层只有真库能验）。
    """
    schema, pool = detail_db
    coverage_payload = {
        "catalog_version": "v3.3",
        "catalog_fingerprint": "fp-real",
        "applicable_total": 42,
        "completed_total": 26,
        "not_applicable_total": 1,
        "unresolved_total": 16,
        "blocking_total": 15,
        "coverage_rate": 0.619,
        "auto_completion_rate": 0.6429,
        "by_reason": {"not_implemented": 12, "insufficient_data": 3, "ai_not_run": 1},
        "by_group": [
            {
                "group_id": "table_internal",
                "group_title": "表内关系",
                "applicable": 7,
                "completed": 6,
                "not_applicable": 0,
                "unresolved": 1,
            }
        ],
        "instances": [
            {
                "obligation_id": "OBL-T-004",
                "group_id": "table_internal",
                "group_title": "表内关系",
                "title": "表内合计与分项一致",
                "status": "insufficient_data",
                "reason": "insufficient_data",
                "reason_label": "取数不足",
                "detail": "未能定位合计行",
                "blocks_gate": True,
                "requires_ai": False,
                "input_gaps": ["table_total"],
            }
        ],
    }
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(conn, current_document_version_id=None)
        version_id = await _insert_version(
            conn, slot_id=slot_id, file_hash="6" * 64, created_at=NOW
        )
        job_id = await _insert_job(
            conn,
            job_uuid=f"job-cover-{uuid.uuid4().hex[:6]}",
            metadata={
                "structured_ingest": {"document_version_id": version_id, "status": "done"},
                "result_meta": {
                    "elapsed_ms": {"total": 4242},
                    "quality_gate": {
                        "status": "review_required",
                        "quality_status": "review_required",
                        "analysis_conclusion": "incomplete",
                    },
                    "obligation_coverage": coverage_payload,
                },
            },
        )
        await _insert_result(
            conn,
            job_id=job_id,
            ai_findings=[
                {
                    "id": "real-formal",
                    "severity": "high",
                    "title": "表内金额勾稽不一致",
                    "rule_id": "V33-121",
                    "page_number": 15,
                    "bbox": [100.0, 220.0, 420.0, 300.0],
                    "obligation_ids": ["OBL-T-004"],
                }
            ],
            merged_total=1,
        )
        await conn.execute(
            "UPDATE material_slots SET current_document_version_id = $1 WHERE id = $2::uuid",
            version_id,
            slot_id,
        )

        service = MaterialDetailQueryService(conn)
        slot_row = await service.load_slot_row(slot_id)
        assert slot_row is not None
        detail = await service.slot_detail(slot_row)

    analysis = detail.current_analysis
    assert analysis.available is True
    assert analysis.run is not None
    assert analysis.run.document_version_id == version_id
    assert analysis.run.is_current_document_version is True
    assert analysis.run.elapsed_total_ms == 4242
    assert analysis.run.quality_status == "review_required"
    assert analysis.run.merged_findings_count == 1

    coverage = analysis.coverage
    assert coverage.available is True
    summary = coverage.summary
    assert summary is not None
    assert summary.applicable_total == 42
    assert summary.blocking_total == 15
    assert summary.by_reason == {
        "not_implemented": 12,
        "insufficient_data": 3,
        "ai_not_run": 1,
    }
    assert summary.by_group[0].group_title == "表内关系"
    assert coverage.items[0].obligation_id == "OBL-T-004"
    assert coverage.items[0].blocks_gate is True

    assert analysis.formal_issue_count == 1
    finding = analysis.formal_findings[0]
    assert finding.evidence_page == 15
    assert finding.evidence_bbox == [100.0, 220.0, 420.0, 300.0]


async def test_runs_do_not_carry_raw_response(detail_db):
    """D（续）。runs 摘要里不含 raw_response / 完整 finding（§四十七）。"""
    schema, pool = detail_db
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(conn, current_document_version_id=None)
        version_id = await _insert_version(
            conn, slot_id=slot_id, file_hash="7" * 64, created_at=NOW
        )
        job_id = await _insert_job(
            conn,
            job_uuid=f"job-raw-{uuid.uuid4().hex[:6]}",
            metadata={"structured_ingest": {"document_version_id": version_id}},
        )
        await _insert_result(
            conn, job_id=job_id, ai_findings=[{"id": "f", "severity": "high"}], merged_total=1
        )
        await conn.execute(
            "UPDATE material_slots SET current_document_version_id = $1 WHERE id = $2::uuid",
            version_id,
            slot_id,
        )
        service = MaterialDetailQueryService(conn)
        slot_row = await service.load_slot_row(slot_id)
        assert slot_row is not None
        runs = await service.run_history(slot_row)
        # 从连接池取回的行确实带着大字段，但 DTO 里必须没有
        raw_rows = await service.load_version_run_rows(slot_id)

    assert runs[0].has_results is True
    payload = runs[0].model_dump()
    assert "raw_response" not in payload
    assert "ai_findings" not in payload
    assert "rule_findings" not in payload
    assert "metadata" not in payload
    assert any(row.get("ai_findings") for row in raw_rows)


# ==== E：legacy 运行不猜归属 =================================================


async def test_legacy_job_without_version_id_never_enters_runs(detail_db):
    """E. 没有 ``document_version_id`` 的 legacy 任务（即使文件名完全相同）
    不得进入该槽位的处理记录。
    """
    schema, pool = detail_db
    linked_uuid = f"job-linked-{uuid.uuid4().hex[:6]}"
    legacy_uuid = f"job-legacy-{uuid.uuid4().hex[:6]}"
    broken_uuid = f"job-broken-{uuid.uuid4().hex[:6]}"
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(conn, current_document_version_id=None)
        version_id = await _insert_version(
            conn, slot_id=slot_id, file_hash="8" * 64, created_at=NOW,
            original_filename="同名文件.pdf",
        )
        linked = await _insert_job(
            conn,
            job_uuid=linked_uuid,
            metadata={"structured_ingest": {"document_version_id": version_id}},
        )
        await _insert_result(conn, job_id=linked, ai_findings=[], merged_total=0)
        legacy = await _insert_job(
            conn,
            job_uuid=legacy_uuid,
            metadata={"organization_name": "执法大队", "report_year": "2024"},
        )
        await _insert_result(conn, job_id=legacy, ai_findings=[{"id": "legacy"}], merged_total=1)
        broken = await _insert_job(
            conn,
            job_uuid=broken_uuid,
            metadata={"structured_ingest": {"document_version_id": "not-a-number"}},
        )
        await conn.execute(
            "UPDATE material_slots SET current_document_version_id = $1 WHERE id = $2::uuid",
            version_id,
            slot_id,
        )

        service = MaterialDetailQueryService(conn)
        slot_row = await service.load_slot_row(slot_id)
        assert slot_row is not None
        runs = await service.run_history(slot_row)

    assert [item.job_uuid for item in runs] == [linked_uuid]
    assert legacy != broken
    # 非数字的 metadata 不会让整条查询抛 cast 异常（SQL 守卫生效），
    # 但也不会把这条运行挂到槽位上。
    assert legacy_uuid not in [item.job_uuid for item in runs]
    assert broken_uuid not in [item.job_uuid for item in runs]


# ==== F：跨单位权限 =========================================================


async def test_detail_queries_do_not_cross_unit_scope(detail_db):
    """F. 跨单位：对方单位的槽位在真库层就不该被取回来，时间轴也只会拿到自己的。"""
    from api.routes.materials import MaterialAccessScope, slot_row_is_visible

    schema, pool = detail_db
    async with _conn(schema, pool) as conn:
        mine = await _insert_slot(conn, subject_org_id=SUB_UNIT, fiscal_year=2024)
        theirs = await _insert_slot(
            conn,
            subject_org_id=SIBLING_UNIT,
            subject_org_name="上海市普陀区规划和自然资源局事务中心",
            fiscal_year=2025,
            report_kind="budget",
        )
        service = MaterialDetailQueryService(conn)

        # ① 取回的槽位行带着三列组织 id —— 纯谓词据此判定
        my_row = await service.load_slot_row(mine)
        their_row = await service.load_slot_row(theirs)
        assert my_row is not None and their_row is not None
        scope = MaterialAccessScope(visible_org_ids=[SUB_UNIT])
        assert slot_row_is_visible(my_row, scope) is True
        assert slot_row_is_visible(their_row, scope) is False
        assert slot_row_is_visible(my_row, MaterialAccessScope(visible_org_ids=None)) is True
        assert slot_row_is_visible(my_row, MaterialAccessScope(visible_org_ids=[])) is False

        # ② 时间轴 SQL 带上可见范围后，对方单位的槽位在真 SQL 层就被排除
        timeline = await service.unit_timeline(
            unit_id=SUB_UNIT, visible_org_ids=[SUB_UNIT]
        )
        ids = [
            slot.slot_id
            for row in timeline.years
            for slot in row.budget_slots + row.final_slots + row.unclassified_slots
        ]
        assert ids == [mine], "时间轴在真 SQL 层就排除了不可见单位的槽位"
        assert theirs not in str(timeline.model_dump())


async def test_timeline_orders_by_fiscal_year_on_real_postgres(detail_db):
    """F（续）。真库上按 fiscal_year 降序；未知年度不进任何具体年份。"""
    schema, pool = detail_db
    async with _conn(schema, pool) as conn:
        for year, kind in ((2023, "budget"), (2026, "budget"), (2024, "final")):
            await _insert_slot(
                conn, subject_org_id=SUB_UNIT, fiscal_year=year, report_kind=kind
            )
        await _insert_slot(
            conn,
            subject_org_id=SUB_UNIT,
            fiscal_year=None,
            report_kind="budget",
            mapping_key="doc:unknown-year",
            status="mapping_required",
            status_reason="identity_unresolved",
        )
        service = MaterialDetailQueryService(conn)
        timeline = await service.unit_timeline(unit_id=SUB_UNIT)

    assert [row.fiscal_year for row in timeline.years] == [2026, 2024, 2023]
    assert len(timeline.unresolved_year_slots) == 1
    assert timeline.unresolved_year_slots[0].fiscal_year is None
    assert all(
        slot.fiscal_year is not None
        for row in timeline.years
        for slot in row.budget_slots + row.final_slots + row.unclassified_slots
    )
