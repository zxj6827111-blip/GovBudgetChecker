"""材料台账三个读接口在**真实 PostgreSQL** 上的验证（显式 opt-in）。

为什么必须有这一层
------------------
接口层用假连接跑通"路由 → 服务 → 聚合 → DTO"，但假连接不解析 SQL、
不执行 GROUP BY、不关心类型。以下问题只有真库能回答，而它们恰好是
"页面数字对不对"的直接来源：

1. ``GROUP BY jurisdiction_org_id, report_kind, status`` 里的
   ``COUNT(*) FILTER (WHERE due_at IS NULL)``、``= ANY($n::text[])``、
   ``id::text AS slot_id`` 能否被 PostgreSQL 接受（语法与参数类型推导）；
2. NULL 的分组与过滤行为：``fiscal_year IS NULL`` 的槽位在"按年度筛选"时
   必须被排除、在没有年度筛选时必须计入；``jurisdiction_org_id IS NULL``
   的槽位不能凭空变成一张区县卡片；
3. 返回类型：UUID → text、TIMESTAMPTZ → tz-aware datetime、INTEGER 计数，
   经 Pydantic 序列化后是否符合契约（时间必须是 ISO 8601 带时区）。

隔离方式（沿用 WP1 的 PG 用例约定）
-----------------------------------
整个文件在**随机命名的独立 schema** 里执行：先跑真实迁移建出 ``material_slots``，
灌入测试数据，跑完 ``DROP SCHEMA CASCADE``。因此即使目标库是开发库，
也不会碰 public 下的任何既有数据，不留残留表。

运行方式::

    GOVBUDGET_TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/fiscal_db \
        python -m pytest tests/test_material_ledger_pg.py -v
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from src.db.migrations import run_migrations
from src.schemas.material_ledger import build_meta
from src.services.material_ledger_query_service import (
    MaterialLedgerQueryService,
    MaterialSlotFilters,
)

pytestmark = pytest.mark.real_database

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)

DISTRICT = "district-putuo"
OTHER_DISTRICT = "district-jingan"
DEPT = "dept-planning"
HEAD_UNIT = "unit-planning-head"
SUB_UNIT = "unit-planning-enforcement"
OTHER_DEPT = "dept-civil"


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
        "fiscal_year": 2025,
        "report_kind": "budget",
        "due_at": None,
        "status": "uploaded",
        "status_reason": "awaiting_analysis",
        "updated_at": NOW,
    }
    payload.update(fields)
    columns = ", ".join(payload)
    placeholders = ", ".join(f"${index}" for index in range(1, len(payload) + 1))
    row = await conn.fetchrow(
        f"INSERT INTO material_slots ({columns}) VALUES ({placeholders}) RETURNING id::text AS slot_id",
        *payload.values(),
    )
    return str(row["slot_id"])


@pytest.fixture
async def ledger_db(real_database_url, monkeypatch):
    """独立 schema + 真实迁移，产出可直接查询的连接。"""
    from src.db.connection import DatabaseConnection

    schema = f"matledger_test_{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("PG_SCHEMA", schema)
    DatabaseConnection._pool = None
    pool = await DatabaseConnection.initialize(real_database_url)
    assert DatabaseConnection.get_schema() == schema

    async with pool.acquire() as conn:
        await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    # run_migrations() 自己从连接池取连接并按 PG_SCHEMA 设 search_path，
    # 因此这里不传连接（传了会 TypeError）。
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


@pytest.fixture
async def seeded(ledger_db):
    """一份有代表性的数据。

    关键约束：**每个（主体 × 文种 × 年度）只能有一条身份已确认的槽位**
    （``uq_material_slots_identity``）。因此"10 种状态各一条"必须落在 10 个不同主体上，
    而不是同一主体写 10 行——后者在真库上直接违反唯一约束，正是本用例存在的意义之一。

    数据构成（全部 fiscal_year=2025，除注明外）：

    - 规划局本体：部门汇总（completed，预算，caliber=summary）
    - 规划局本级：决算（review_required，status_reason=findings_pending）
    - 执法大队：文种未识别（mapping_required，mapping_key=doc:...）
    - 7 个状态样例单位：各承一种状态（预算）
    - 无行政区划的未识别材料（mapping_required）
    - 静安区民政局：决算（其他区，不得混入普陀区）
    - 执法大队 2024 年度决算（跨年度，不得混入 2025）
    """
    schema, pool = ledger_db
    async with _conn(schema, pool) as conn:
        # 部门汇总：completed
        await _insert_slot(
            conn,
            subject_org_id=DEPT,
            subject_org_name="上海市普陀区规划和自然资源局",
            subject_kind="department",
            subject_level="department",
            material_scope="department_summary",
            report_kind="budget",
            caliber="summary",
            status="completed",
            status_reason="review_completed",
            due_at=NOW + timedelta(days=10),
        )
        # 本部单位的决算：review_required
        await _insert_slot(
            conn,
            subject_org_id=HEAD_UNIT,
            subject_org_name="上海市普陀区规划和自然资源局本级",
            report_kind="final",
            status="review_required",
            status_reason="findings_pending",
            due_at=NOW + timedelta(days=5),
        )
        # 文种未识别：既不进预算也不进决算
        await _insert_slot(
            conn,
            subject_org_id=SUB_UNIT,
            subject_org_name="上海市普陀区规划和自然资源局执法大队",
            report_kind="unknown",
            status="mapping_required",
            status_reason="identity_unresolved",
            mapping_key="doc:deadbeef",
        )
        # 其余状态各由独立主体承载（真库唯一约束不允许同一主体重复建预算槽位）
        for index, status in enumerate(
            ("not_due", "missing", "uploaded", "processing", "reviewing", "not_applicable", "failed")
        ):
            await _insert_slot(
                conn,
                subject_org_id=f"unit-sample-{index}",
                subject_org_name=f"规划局状态样例单位{index}",
                status=status,
                status_reason="awaiting_analysis",
                due_at=NOW + timedelta(days=1) if index % 2 == 0 else None,
            )
        # 政府级材料：有行政区划、没有主管部门（区级政府本级材料）
        await _insert_slot(
            conn,
            department_org_id=None,
            department_name=None,
            subject_org_id=DISTRICT,
            subject_org_name="上海市普陀区人民政府",
            subject_kind="government",
            subject_level="government",
            material_scope="government",
            report_kind="budget",
            caliber="summary",
            status="uploaded",
        )
        # 没有行政区划、也没有主管部门的未识别材料
        await _insert_slot(
            conn,
            jurisdiction_org_id=None,
            jurisdiction_name=None,
            department_org_id=None,
            department_name=None,
            subject_org_id="unresolved:abc",
            subject_org_name="一份说不清的材料",
            subject_kind="unknown",
            subject_level="unknown",
            material_scope="unknown",
            report_kind="unknown",
            status="mapping_required",
        )
        # 另一个区
        await _insert_slot(
            conn,
            jurisdiction_org_id=OTHER_DISTRICT,
            jurisdiction_name="上海市静安区",
            department_org_id=OTHER_DEPT,
            department_name="上海市静安区民政局",
            subject_org_id=OTHER_DEPT,
            subject_org_name="上海市静安区民政局",
            subject_kind="department",
            subject_level="department",
            material_scope="department_summary",
            report_kind="final",
            due_at=NOW,
        )
        # 上一年度：不得混入 2025 的查询
        await _insert_slot(
            conn,
            fiscal_year=2024,
            report_kind="final",
            subject_org_id="unit-2024",
            subject_org_name="2024 年度单位",
        )

    yield ledger_db


async def test_coverage_queries_run_on_real_postgres_and_isolate_years(seeded):
    schema, pool = seeded
    async with _conn(schema, pool) as conn:
        service = MaterialLedgerQueryService(conn)

        data = await service.coverage(filters=MaterialSlotFilters(fiscal_year=2025))
        all_years = await service.coverage(filters=MaterialSlotFilters())

    summary = data.summary
    # 2025：部门汇总 1 + 本部决算 1 + 文种未识别 1 + 7 个样例单位 + 政府级 1 + 无区划 1 + 静安 1 = 13
    assert summary.slot_total == 13
    assert sum(summary.status_counts.model_dump().values()) == summary.slot_total
    assert summary.budget_total == 9
    assert summary.final_total == 2
    assert summary.unknown_kind_total == 2
    assert summary.budget_total + summary.final_total + summary.unknown_kind_total == summary.slot_total
    assert summary.jurisdiction_unknown_total == 1
    assert summary.status_counts.mapping_required == 2
    assert summary.status_counts.uploaded == 3
    assert summary.status_counts.not_applicable == 1
    assert summary.status_counts.missing == 1
    assert summary.status_counts.failed == 1
    assert all_years.summary.slot_total == 14, "不带年度筛选时把 2024 那条也算进来"

    # 排序按 Unicode 码点：普(U+666E) < 静(U+9759)
    district_names = [item.district_name for item in data.districts]
    assert district_names == ["上海市普陀区", "上海市静安区"]
    assert sum(item.slot_total for item in data.districts) == 12
    putuo = data.districts[0]
    assert putuo.slot_total == 11
    assert putuo.due_at_unknown == 5, "普陀区 11 条里 6 条带截止时间"

    # 时间字段必须带时区（前端按本地时间格式化，不能拿到朴素时间）
    assert putuo.updated_at is not None
    assert putuo.updated_at.tzinfo is not None
    assert build_meta().expected_materials_ready is False
    assert data.summary.expected_total is None and data.summary.coverage_rate is None


async def test_district_departments_isolates_district_and_keeps_same_name_subjects_apart(seeded):
    schema, pool = seeded
    async with _conn(schema, pool) as conn:
        service = MaterialLedgerQueryService(conn)
        data, total = await service.district_departments(
            district_id=DISTRICT,
            district_name="上海市普陀区",
            filters=MaterialSlotFilters(fiscal_year=2025),
            page=1,
            page_size=50,
        )

    assert total == 2, "普陀区只有规划局有槽位，静安区那条不能混进来"
    assert data.district.district_name == "上海市普陀区"

    departments = {item.department_id: item for item in data.items}
    assert set(departments) == {DEPT, None}, "政府级材料（无主管部门）单独成行，不并入任何部门"
    planning = departments[DEPT]
    assert planning.subject_count == 10, "部门本身 + 本部单位 + 执法大队 + 7 个样例单位，按 id 计数"
    assert planning.budget.slot_total == 8
    assert planning.final.slot_total == 1
    assert planning.unknown_kind_total == 1
    assert planning.slot_total == 10
    assert planning.due_at_unknown == 4
    assert planning.missing == 1 and planning.status_counts.missing == 1
    assert planning.coverage_rate is None and planning.expected_total is None

    government = departments[None]
    assert government is not None
    assert government.department_name is None
    assert government.slot_total == 1 and government.budget.slot_total == 1
    assert government.subject_count == 1
    assert data.items[-1].department_id is None, "无主管部门的行排在最后"


async def test_district_departments_pagination_slices_after_aggregation(seeded):
    schema, pool = seeded
    async with _conn(schema, pool) as conn:
        service = MaterialLedgerQueryService(conn)
        first_page, total = await service.district_departments(
            district_id=DISTRICT,
            filters=MaterialSlotFilters(fiscal_year=2025),
            page=1,
            page_size=1,
        )
        second_page, _ = await service.district_departments(
            district_id=DISTRICT,
            filters=MaterialSlotFilters(fiscal_year=2025),
            page=2,
            page_size=1,
        )
        keyword_page, keyword_total = await service.district_departments(
            district_id=DISTRICT,
            filters=MaterialSlotFilters(fiscal_year=2025),
            q="规划",
        )

    assert total == 2
    assert len(first_page.items) == 1 and len(second_page.items) == 1
    assert first_page.items[0].department_id != second_page.items[0].department_id
    assert keyword_total == 1 and keyword_page.items[0].department_id == DEPT, (
        "关键词按聚合后的显示名过滤：没有名字的政府级行不参与名称搜索"
    )


async def test_department_matrix_groups_and_budget_final_separation_on_real_pg(seeded):
    schema, pool = seeded
    async with _conn(schema, pool) as conn:
        service = MaterialLedgerQueryService(conn)
        data = await service.department_matrix(
            department_id=DEPT,
            fiscal_year=2025,
            department_name="上海市普陀区规划和自然资源局",
            jurisdiction_id=DISTRICT,
            jurisdiction_name="上海市普陀区",
        )

        other_year = await service.department_matrix(
            department_id=DEPT, fiscal_year=2024, department_name="上海市普陀区规划和自然资源局"
        )

    assert [row.subject_org_id for row in data.groups.department_summary] == [DEPT]
    assert [row.subject_org_id for row in data.groups.head_unit] == [HEAD_UNIT]
    assert [row.subject_org_id for row in data.groups.subordinate_units] == [
        SUB_UNIT,
        *[f"unit-sample-{index}" for index in range(7)],
    ], "排序按 Unicode 码点：单位名后缀为数字，顺序稳定即可（这里显式钉住）"
    assert data.groups.relationship_unknown == []

    summary_row = data.groups.department_summary[0]
    assert summary_row.budget.exists is True
    assert summary_row.budget.slot is not None
    assert summary_row.budget.slot.report_kind == "budget"
    assert summary_row.budget.slot.caliber == "summary"
    assert summary_row.budget.slot.caliber_conflict_candidate is None
    assert summary_row.final.exists is False

    head_row = data.groups.head_unit[0]
    assert head_row.final.slot is not None and head_row.final.slot.report_kind == "final"
    assert head_row.final.slot.status_reason == "findings_pending"
    assert head_row.final.slot.formal_issue_count is None

    # 同一主体（执法大队）下：预算与决算各占一位，文种未识别落在 unclassified
    sub_row = data.groups.subordinate_units[0]
    assert sub_row.subject_org_id == SUB_UNIT
    assert sub_row.budget.exists is False and sub_row.final.exists is False
    assert sub_row.unclassified.slot is not None
    assert sub_row.unclassified.slot.report_kind == "unknown"
    assert sub_row.unclassified.exists is True
    assert sub_row.unclassified.slot is not None
    assert sub_row.unclassified.slot.report_kind == "unknown"

    # 2024 年度那一条不属于 2025 的矩阵
    assert other_year.fiscal_year == 2024
    assert other_year.groups.department_summary == []


async def test_slot_rows_carry_uuid_keys_and_tzaware_timestamps(seeded):
    schema, pool = seeded
    async with _conn(schema, pool) as conn:
        service = MaterialLedgerQueryService(conn)
        data = await service.department_matrix(department_id=DEPT, fiscal_year=2025)

    slots: List[Any] = [
        row.budget.slot
        for rows in data.groups.__dict__.values()
        for row in rows
        if row.budget.slot is not None
    ]
    assert slots, "至少应有一条预算槽位"
    for slot in slots:
        assert isinstance(slot.slot_id, str) and len(slot.slot_id) > 0, "id 必须序列化为字符串"
        assert isinstance(slot.slot_key, str) and slot.slot_key
        assert slot.updated_at is not None and slot.updated_at.tzinfo is not None


async def test_covered_route_filters_execute_on_real_postgres(seeded):
    """路由层会拼出的每种筛选组合都要能在真库上执行（含状态/文种/区划/部门）。"""
    schema, pool = seeded
    async with _conn(schema, pool) as conn:
        service = MaterialLedgerQueryService(conn)
        for filters in (
            MaterialSlotFilters(),
            MaterialSlotFilters(fiscal_year=2025),
            MaterialSlotFilters(report_kind="budget"),
            MaterialSlotFilters(status="missing"),
            MaterialSlotFilters(jurisdiction_id=DISTRICT),
            MaterialSlotFilters(jurisdiction_ids=[DISTRICT, OTHER_DISTRICT]),
            MaterialSlotFilters(jurisdiction_ids=[]),
            MaterialSlotFilters(department_id=DEPT),
            MaterialSlotFilters(
                fiscal_year=2025, report_kind="final", status="review_required", jurisdiction_id=DISTRICT
            ),
        ):
            data = await service.coverage(filters=filters)
            assert data.summary.slot_total >= 0
            assert sum(data.summary.status_counts.model_dump().values()) == data.summary.slot_total

        empty = await service.coverage(filters=MaterialSlotFilters(jurisdiction_ids=[]))
        assert empty.summary.slot_total == 0, "空授权范围必须查出 0 条，而不是不过滤"
        assert empty.districts == []
