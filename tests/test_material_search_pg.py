"""全局材料搜索在**真实 PostgreSQL** 上的验证（显式 opt-in）。

为什么必须有这一层
------------------
接口层用假连接跑通"路由 → 授权 → 服务 → 契约"，但假连接不解析 SQL、不执行
JOIN、不关心 JSONB 的存储形态。以下问题**只有真库能回答**：

A. token 的 AND/OR 语义在真 SQL 上成立：每个 token 命中至少一个允许字段、
   字段之间 OR、token 之间 AND（§十九）；
B. ``%`` ``_`` 是否真的按普通字符处理（``ESCAPE '\\'`` 生效），
   搜 ``100%`` 不会退化成"全库"（§二十）；
C. ``ILIKE`` 与 ``ESCAPE`` 组合在真库上的实际行为；
D. 排序表达式（job 精确 > 文件名精确 > 单位名精确 > 部门名精确 > updated_at
   > id）在真库上是否稳定：同一份数据连查三次顺序必须一致（§三十）；
E. ``COUNT(*) OVER ()`` 给出的 total 与分页是否一致，越界页的兜底计数是否正确；
F. 权限谓词（三列 OR）在真 SQL 上是否真的排除了越权槽位（§三十五）——
   这是"搜不到就是搜不到，不泄露存在性"的最后一道门；
G. ``fiscal_document_versions.slot_id`` / ``analysis_jobs.job_uuid`` 索引是否被
   用上（EXPLAIN 不做门禁，但查询必须能跑）；
H. 写坏的 ``metadata``（``structured_ingest.document_version_id`` 不是数字）
   不会让整条搜索抛 cast 异常（守卫表达式在真库上的行为）；
I. 历史文件名 / 历史 job 命中后，``current_document_version_id`` 是否仍是当前指针。

隔离方式（沿用 WP1/WP2-A/WP2-B 的 PG 用例约定）
-----------------------------------------------
整个文件在**随机命名的独立 schema** 里执行：先跑真实迁移建出全部表，
灌入测试数据，跑完 ``DROP SCHEMA CASCADE``。即使目标库是开发库，
也不会碰 public 下的任何既有数据。

运行方式::

    GOVBUDGET_TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/fiscal_db \
        python -m pytest tests/test_material_search_pg.py -v
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import pytest

from src.db.migrations import run_migrations
from src.services.material_search_query_service import (
    MaterialSearchFilters,
    MaterialSearchQueryService,
    head_unit_subject_ids,
    parse_search_query,
)

pytestmark = pytest.mark.real_database

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(days=10)
LONG_AGO = NOW - timedelta(days=400)

DISTRICT = "district-putuo"
OTHER_DISTRICT = "district-jingan"
DEPT = "dept-planning"
OTHER_DEPT = "dept-education"
HEAD_UNIT = "unit-planning-head"
SUB_UNIT = "unit-planning-enforcement"
SIBLING_UNIT = "unit-planning-affairs"
OTHER_UNIT = "unit-education-1"

DEPT_NAME = "上海市普陀区规划和自然资源局"
HEAD_UNIT_NAME = "上海市普陀区规划和自然资源局本级"
SUB_UNIT_NAME = "上海市普陀区规划和自然资源局执法大队"
SIBLING_UNIT_NAME = "上海市普陀区规划和自然资源局事务中心"
OTHER_UNIT_NAME = "上海市静安区教育局第一小学"


class _Org:
    """组织目录记录的最小替身（``head_unit_subject_ids`` 只读这四个属性）。"""

    def __init__(self, org_id: str, name: str, level: str, parent_id: Optional[str]) -> None:
        self.id = org_id
        self.name = name
        self.level = level
        self.parent_id = parent_id


def _org_catalog() -> List[_Org]:
    """区 / 部门 / 三个单位的组织目录。

    刻意让部门与本部单位**完全同名**（真实形态：财政局（部门）与财政局（本级单位）
    id 与 level 不同、名字一模一样），只靠"名称含有本级"是认不出来的。
    """
    return [
        _Org(DISTRICT, "上海市普陀区", "district", None),
        _Org(OTHER_DISTRICT, "上海市静安区", "district", None),
        _Org(DEPT, DEPT_NAME, "department", DISTRICT),
        _Org(OTHER_DEPT, "上海市静安区教育局", "department", OTHER_DISTRICT),
        _Org(HEAD_UNIT, HEAD_UNIT_NAME, "unit", DEPT),
        _Org(SUB_UNIT, SUB_UNIT_NAME, "unit", DEPT),
        _Org(SIBLING_UNIT, SIBLING_UNIT_NAME, "unit", DEPT),
        _Org(OTHER_UNIT, OTHER_UNIT_NAME, "unit", OTHER_DEPT),
    ]


async def _insert_slot(conn, **fields: Any) -> str:
    payload: Dict[str, Any] = {
        "slot_key": f"key-{uuid.uuid4().hex}",
        "mapping_key": "",
        "jurisdiction_org_id": DISTRICT,
        "jurisdiction_name": "上海市普陀区",
        "department_org_id": DEPT,
        "department_name": DEPT_NAME,
        "subject_org_id": SUB_UNIT,
        "subject_org_name": SUB_UNIT_NAME,
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
    original_filename: str,
) -> int:
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


async def _insert_job(
    conn,
    *,
    job_uuid: str,
    metadata: Dict[str, Any],
    status: str = "done",
    completed_at: datetime = NOW,
) -> int:
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


def _linked_metadata(version_id: int, *, organization_id: str = SUB_UNIT) -> Dict[str, Any]:
    """入库时真正会写进去的 metadata 形态（精确关联字段 + 组织归属）。"""
    return {
        "organization_id": organization_id,
        "structured_ingest": {"status": "done", "document_version_id": version_id},
    }


@pytest.fixture
async def search_db(real_database_url, monkeypatch):
    """独立 schema + 真实迁移，产出可直接查询的连接池。"""
    from src.db.connection import DatabaseConnection

    schema = f"matsearch_test_{uuid.uuid4().hex[:12]}"
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


async def _search(
    conn,
    raw_query: str,
    *,
    filters: Optional[MaterialSearchFilters] = None,
    page: int = 1,
    page_size: int = 20,
    job_access=None,
):
    service = MaterialSearchQueryService(conn)
    parsed = parse_search_query(raw_query)
    effective = filters or MaterialSearchFilters()
    return await service.search(
        query=parsed,
        filters=effective,
        page=page,
        page_size=page_size,
        job_access=job_access,
    )


def _head_unit_ids() -> List[str]:
    return head_unit_subject_ids(_org_catalog())


# ==== A. 目标业务查询：同名部门与本部单位不串 ===============================


async def test_head_unit_query_hits_only_the_head_unit_slot(search_db):
    """``规划和自然资源局 本部 2024 决算`` 只命中本部单位那条槽位。

    同一部门下三条槽位（部门汇总 / 本部单位 / 直属单位）的名称都含
    "规划和自然资源局"，只有"本部"这一维能把它们分开：
    部门汇总的 subject 是部门自己（不在本部集合里），直属单位不是本部。
    """
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        summary_slot = await _insert_slot(
            conn,
            subject_org_id=DEPT,
            subject_org_name=DEPT_NAME,
            subject_kind="department",
            material_scope="department_summary",
        )
        head_slot = await _insert_slot(
            conn,
            subject_org_id=HEAD_UNIT,
            subject_org_name=HEAD_UNIT_NAME,
            subject_kind="unit",
            material_scope="unit_self",
        )
        sub_slot = await _insert_slot(
            conn,
            subject_org_id=SUB_UNIT,
            subject_org_name=SUB_UNIT_NAME,
            fiscal_year=2025,
            report_kind="budget",
        )

        filters = MaterialSearchFilters(head_unit_org_ids=_head_unit_ids())
        items, total = await _search(conn, "规划和自然资源局 本部 2024 决算", filters=filters)

        assert total == 1
        assert [item.slot_id for item in items] == [head_slot]
        assert items[0].slot_id not in {summary_slot, sub_slot}
        assert items[0].relationship == "head_unit"
        # 部门名快照（"上海市普陀区规划和自然资源局"）本身也含该 token，
        # 命中原因如实列出两处命中，不做"只留一个"的美化。
        assert items[0].matched_fields == [
            "unit",
            "department",
            "fiscal_year",
            "report_kind",
            "relationship",
        ]


async def test_head_unit_query_without_org_catalog_matches_nothing(search_db):
    """组织目录不可用（fail-closed）时"本部"不给任何命中（§十八）。

    关键：这里传的是**空 id 集合**（不是 None、更不是"所有单位"）。
    直属单位也是 unit，退化处理会把整个部门的材料都搜出来。
    """
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        await _insert_slot(
            conn,
            subject_org_id=HEAD_UNIT,
            subject_org_name=HEAD_UNIT_NAME,
        )
        filters = MaterialSearchFilters(head_unit_org_ids=[])
        items, total = await _search(conn, "本部 2024", filters=filters)
        assert (items, total) == ([], 0)


# ==== B. 历史文件名：能找到，但不改变当前真值 ================================


async def test_historical_filename_hit_keeps_current_pointer(search_db):
    """搜索旧文件名命中同一槽位，当前指针与当前文件名都不变（§二十一/§二十二）。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(conn, subject_org_id=HEAD_UNIT, subject_org_name=HEAD_UNIT_NAME)
        old_version = await _insert_version(
            conn,
            slot_id=slot_id,
            file_hash="a" * 64,
            created_at=LONG_AGO,
            original_filename="old-final.pdf",
        )
        new_version = await _insert_version(
            conn,
            slot_id=slot_id,
            file_hash="b" * 64,
            created_at=EARLIER,
            original_filename="current-final.pdf",
        )
        await conn.execute(
            "UPDATE material_slots SET current_document_version_id = $1 WHERE id = $2::uuid",
            new_version,
            slot_id,
        )

        items, total = await _search(conn, "old-final.pdf")
        assert total == 1
        item = items[0]
        assert item.slot_id == slot_id
        assert item.matched_filename == "old-final.pdf"
        assert item.matched_document_version_id == old_version
        assert item.matched_version_is_current is False
        # 当前真值仍是 V2：搜索命中历史版本不得把它切回 V1。
        assert item.current_document_version_id == new_version
        assert item.current_filename == "current-final.pdf"
        assert "historical_filename" in item.matched_fields
        assert "current_filename" not in item.matched_fields


async def test_current_filename_hit_is_marked_current(search_db):
    """命中当前文件名时明确标记为当前版本（与历史命中形成对照）。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(conn, subject_org_id=HEAD_UNIT, subject_org_name=HEAD_UNIT_NAME)
        old_version = await _insert_version(
            conn,
            slot_id=slot_id,
            file_hash="c" * 64,
            created_at=LONG_AGO,
            original_filename="old-final.pdf",
        )
        new_version = await _insert_version(
            conn,
            slot_id=slot_id,
            file_hash="d" * 64,
            created_at=EARLIER,
            original_filename="current-final.pdf",
        )
        await conn.execute(
            "UPDATE material_slots SET current_document_version_id = $1 WHERE id = $2::uuid",
            new_version,
            slot_id,
        )
        assert old_version != new_version

        items, _ = await _search(conn, "current-final.pdf")
        assert items[0].matched_version_is_current is True
        assert items[0].matched_fields == ["current_filename"]


# ==== C. Job 精确关联：历史 job 不是 current review candidate =================


async def test_historical_job_is_found_but_not_a_review_candidate(search_db):
    """搜历史版本的 job 仍能找到材料，但它不能成为复核候选（§四十七）。

    用例把历史版本的运行设成"最近完成"的一次，专门排除"按时间取最近运行"的错误实现：
    复核候选只能来自 ``current_document_version_id`` 对应的运行，与谁更新无关。
    """
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(conn, subject_org_id=HEAD_UNIT, subject_org_name=HEAD_UNIT_NAME)
        old_version = await _insert_version(
            conn,
            slot_id=slot_id,
            file_hash="e" * 64,
            created_at=LONG_AGO,
            original_filename="old-final.pdf",
        )
        current_version = await _insert_version(
            conn,
            slot_id=slot_id,
            file_hash="f" * 64,
            created_at=EARLIER,
            original_filename="current-final.pdf",
        )
        # 刻意让**历史版本**上的运行比当前版本的运行更新：这样"取最近一次运行"
        # 这种错误实现会给出历史 job，而正确实现（只看当前版本的运行）不受时间影响。
        await _insert_job(
            conn,
            job_uuid="job-history-001",
            metadata=_linked_metadata(old_version),
            completed_at=NOW,
        )
        await _insert_job(
            conn,
            job_uuid="job-current-001",
            metadata=_linked_metadata(current_version),
            completed_at=EARLIER,
        )
        await conn.execute(
            "UPDATE material_slots SET current_document_version_id = $1 WHERE id = $2::uuid",
            current_version,
            slot_id,
        )

        items, total = await _search(conn, "job-history-001", job_access=lambda payload: True)
        assert total == 1
        item = items[0]
        assert item.matched_job_uuid == "job-history-001"
        assert item.matched_job_version_is_current is False
        assert "job_id" in item.matched_fields
        # 复核候选只能来自当前版本的当前运行。
        assert item.review_candidate is not None
        assert item.review_candidate.job_uuid == "job-current-001"

        items, _ = await _search(conn, "job-current-001", job_access=lambda payload: True)
        assert items[0].matched_job_uuid == "job-current-001"
        assert items[0].matched_job_version_is_current is True
        assert items[0].review_candidate is not None
        assert items[0].review_candidate.job_uuid == "job-current-001"


async def test_job_access_denied_hides_review_entry_only(search_db):
    """任务不可见时只隐藏"进入复核"，「打开材料」不受影响（§二十九）。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(conn, subject_org_id=HEAD_UNIT, subject_org_name=HEAD_UNIT_NAME)
        version_id = await _insert_version(
            conn,
            slot_id=slot_id,
            file_hash="1" * 64,
            created_at=EARLIER,
            original_filename="final-2024.pdf",
        )
        await _insert_job(conn, job_uuid="job-private-001", metadata=_linked_metadata(version_id))
        await conn.execute(
            "UPDATE material_slots SET current_document_version_id = $1 WHERE id = $2::uuid",
            version_id,
            slot_id,
        )

        items, _ = await _search(conn, "job-private-001", job_access=lambda payload: False)
        assert items[0].slot_id == slot_id
        assert items[0].matched_job_uuid == "job-private-001"
        assert items[0].review_candidate is None


async def test_job_uuid_match_is_exact_not_prefix(search_db):
    """job id 只做精确匹配：前缀/子串都不算命中（§二十三）。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(conn, subject_org_id=HEAD_UNIT, subject_org_name=HEAD_UNIT_NAME)
        version_id = await _insert_version(
            conn,
            slot_id=slot_id,
            file_hash="2" * 64,
            created_at=EARLIER,
            original_filename="final-2024.pdf",
        )
        await _insert_job(conn, job_uuid="job-exact-0001", metadata=_linked_metadata(version_id))

        items, total = await _search(conn, "job-exact")
        assert (items, total) == ([], 0)


async def test_legacy_unlinked_job_is_not_guessed_onto_a_slot(search_db):
    """没有精确关联字段的 legacy 任务不参与命中，也不猜它属于哪个槽位（§二十四）。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        await _insert_slot(conn, subject_org_id=HEAD_UNIT, subject_org_name=HEAD_UNIT_NAME)
        await _insert_job(
            conn,
            job_uuid="job-legacy-001",
            metadata={"organization_id": HEAD_UNIT},  # 没有任何 structured_ingest
        )

        items, total = await _search(conn, "job-legacy-001")
        assert (items, total) == ([], 0)


async def test_malformed_metadata_does_not_break_the_search(search_db):
    """写坏的 ``document_version_id`` 不会让整条搜索抛 cast 异常。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(conn, subject_org_id=HEAD_UNIT, subject_org_name=HEAD_UNIT_NAME)
        version_id = await _insert_version(
            conn,
            slot_id=slot_id,
            file_hash="3" * 64,
            created_at=EARLIER,
            original_filename="final-2024.pdf",
        )
        await _insert_job(
            conn,
            job_uuid="job-broken-001",
            metadata={"structured_ingest": {"document_version_id": "not-a-number"}},
        )
        await conn.execute(
            "UPDATE material_slots SET current_document_version_id = $1 WHERE id = $2::uuid",
            version_id,
            slot_id,
        )

        items, total = await _search(conn, "规划和自然资源局本级", job_access=lambda payload: True)
        assert total == 1
        assert items[0].slot_id == slot_id
        # 写坏的运行既不能被关联，也不能冒充复核候选。
        assert items[0].review_candidate is None


# ==== D. 权限：越权槽位在真 SQL 上就查不到 ==================================


async def test_visible_scope_excludes_sibling_slots(search_db):
    """unit-A1 账号搜 unit-A2：真 SQL 返回 0，兄弟单位的数据不泄露（§三十五）。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        await _insert_slot(
            conn,
            subject_org_id=SUB_UNIT,
            subject_org_name=SUB_UNIT_NAME,
        )
        await _insert_slot(
            conn,
            subject_org_id=SIBLING_UNIT,
            subject_org_name=SIBLING_UNIT_NAME,
        )

        # A1 只授权了 SUB_UNIT；搜兄弟单位的名字。
        scoped = MaterialSearchFilters(visible_org_ids=[SUB_UNIT])
        items, total = await _search(conn, "事务中心", filters=scoped)
        assert (items, total) == ([], 0)

        # 同一个账号搜自己的名字能搜到 —— 证明上一条不是"整个查询坏了"。
        items, total = await _search(conn, "执法大队", filters=scoped)
        assert total == 1
        assert items[0].subject_org_id == SUB_UNIT


async def test_visible_scope_covers_department_and_jurisdiction_columns(search_db):
    """权限谓词覆盖槽位的三个组织列（区县/部门/主体）且是 OR（与 WP2-A 同源）。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        district_slot = await _insert_slot(
            conn,
            subject_org_id=DISTRICT,
            subject_org_name="上海市普陀区",
            subject_kind="government",
            material_scope="government",
            department_org_id=None,
            department_name=None,
        )
        department_slot = await _insert_slot(
            conn,
            subject_org_id=DEPT,
            subject_org_name=DEPT_NAME,
            subject_kind="department",
            material_scope="department_summary",
        )

        by_district = MaterialSearchFilters(visible_org_ids=[DISTRICT])
        items, total = await _search(conn, "普陀", filters=by_district, page_size=50)
        assert total == 2
        assert {item.slot_id for item in items} == {district_slot, department_slot}

        by_department = MaterialSearchFilters(visible_org_ids=[DEPT])
        items, total = await _search(conn, "普陀", filters=by_department, page_size=50)
        assert total == 1
        assert items[0].slot_id == department_slot

        # 无关区划：一条都看不到。
        items, total = await _search(
            conn, "普陀", filters=MaterialSearchFilters(visible_org_ids=[OTHER_DEPT]), page_size=50
        )
        assert (items, total) == ([], 0)


# ==== E. 通配符按普通字符处理 ================================================


async def test_percent_and_underscore_are_literal_characters(search_db):
    """``%`` ``_`` 一律按普通字符处理，不得变成通配符（§二十）。

    三条槽位构成两组反例：

    - ``%`` 若被当通配符，``决算100完成_2024.pdf``（没有 % 字符）也会被
      ``100%完成`` 搜到；
    - ``_`` 若被当通配符，``决算100%完成X2024.pdf``（下划线位置是 X）也会被
      ``完成_2024`` 搜到。
    """
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        percent_slot = await _insert_slot(
            conn, subject_org_id=HEAD_UNIT, subject_org_name=HEAD_UNIT_NAME
        )
        no_percent_slot = await _insert_slot(
            conn, subject_org_id=SUB_UNIT, subject_org_name=SUB_UNIT_NAME
        )
        no_underscore_slot = await _insert_slot(
            conn, subject_org_id=SIBLING_UNIT, subject_org_name=SIBLING_UNIT_NAME
        )
        await _insert_version(
            conn,
            slot_id=percent_slot,
            file_hash="4" * 64,
            created_at=EARLIER,
            original_filename="决算100%完成_2024.pdf",
        )
        await _insert_version(
            conn,
            slot_id=no_percent_slot,
            file_hash="5" * 64,
            created_at=EARLIER,
            original_filename="决算100完成_2024.pdf",
        )
        await _insert_version(
            conn,
            slot_id=no_underscore_slot,
            file_hash="6" * 64,
            created_at=EARLIER,
            original_filename="决算100%完成X2024.pdf",
        )

        items, _ = await _search(conn, "100%完成", page_size=50)
        hits = {item.slot_id for item in items}
        assert percent_slot in hits
        assert no_underscore_slot in hits  # 它确实含字面量 "100%完成"
        assert no_percent_slot not in hits, "% 被当成了通配符"

        items, _ = await _search(conn, "完成_2024", page_size=50)
        hits = {item.slot_id for item in items}
        assert percent_slot in hits
        assert no_percent_slot in hits  # 它确实含字面量 "完成_2024"
        assert no_underscore_slot not in hits, "_ 被当成了通配符"

        # 纯通配符查询不得退化成"全库"。
        items, total = await _search(conn, "%%")
        assert (items, total) == ([], 0)


async def test_backslash_is_literal(search_db):
    """反斜杠自身也按普通字符处理（否则用户能用 ``\\%`` 绕过转义）。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(conn, subject_org_id=HEAD_UNIT, subject_org_name=HEAD_UNIT_NAME)
        await _insert_version(
            conn,
            slot_id=slot_id,
            file_hash="6" * 64,
            created_at=EARLIER,
            original_filename="决算\\2024.pdf",
        )
        items, total = await _search(conn, "决算\\2024")
        assert total == 1
        assert items[0].slot_id == slot_id

        # 转义字符本身不是通配符：单独一个反斜杠搜不到任何东西。
        items, total = await _search(conn, "\\\\")
        assert (items, total) == ([], 0)


# ==== F. token 语义：字段 OR、token AND =====================================


async def test_tokens_are_anded_across_fields(search_db):
    """每个 token 必须命中某字段，字段之间 OR（§十九）。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        await _insert_slot(
            conn,
            subject_org_id=SUB_UNIT,
            subject_org_name=SUB_UNIT_NAME,
            jurisdiction_name="上海市普陀区",
        )

        # 两个 token 分别命中区县名与主体名 -> 命中。
        items, total = await _search(conn, "普陀 执法大队")
        assert total == 1

        # 第二个 token 谁都不命中 -> 整条查询无结果（不是"命中任意一个即可"）。
        items, total = await _search(conn, "普陀 静安区教育局")
        assert (items, total) == ([], 0)


async def test_fiscal_year_never_comes_from_timestamps(search_db):
    """财政年度只来自槽位字段：``fiscal_year IS NULL`` 的槽位不会被任何时间"补"出年度（§十四）。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        year_known = await _insert_slot(
            conn, subject_org_id=HEAD_UNIT, subject_org_name=HEAD_UNIT_NAME, fiscal_year=2024
        )
        year_unknown = await _insert_slot(
            conn,
            subject_org_id=SUB_UNIT,
            subject_org_name=SUB_UNIT_NAME,
            fiscal_year=None,
            status="mapping_required",
        )
        # 年度未知的槽位，其运行是"2024 年创建"的：不得因此被 2024 搜出来。
        version_id = await _insert_version(
            conn,
            slot_id=year_unknown,
            file_hash="7" * 64,
            created_at=datetime(2024, 5, 1, tzinfo=timezone.utc),
            original_filename="unknown-year.pdf",
        )
        await _insert_job(
            conn,
            job_uuid="job-2024-created",
            metadata=_linked_metadata(version_id),
            completed_at=datetime(2024, 5, 2, tzinfo=timezone.utc),
        )

        items, total = await _search(conn, "2024", page_size=50)
        assert total == 1
        assert items[0].slot_id == year_known


# ==== G. 排序稳定 + 分页 ====================================================


async def test_ranking_is_priority_ordered_and_stable(search_db):
    """排序优先级：job 精确 > 文件名精确 > 单位名精确 > 部门名精确 > 部分匹配。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        token = "gongzuo-report"

        partial = await _insert_slot(
            conn,
            subject_org_id=OTHER_UNIT,
            subject_org_name=f"某单位 {token} 附件",
            jurisdiction_org_id=OTHER_DISTRICT,
            jurisdiction_name="上海市静安区",
            department_org_id=OTHER_DEPT,
            department_name="上海市静安区教育局",
            updated_at=NOW,
        )
        department_exact = await _insert_slot(
            conn,
            subject_org_id=DEPT,
            subject_org_name=DEPT_NAME,
            department_name=token,
            updated_at=NOW,
        )
        unit_exact = await _insert_slot(
            conn,
            subject_org_id=HEAD_UNIT,
            subject_org_name=token,
            updated_at=NOW,
        )
        filename_exact = await _insert_slot(
            conn,
            subject_org_id=SUB_UNIT,
            subject_org_name=SUB_UNIT_NAME,
            updated_at=NOW,
        )
        await _insert_version(
            conn,
            slot_id=filename_exact,
            file_hash="8" * 64,
            created_at=EARLIER,
            original_filename=token,
        )
        job_exact = await _insert_slot(
            conn,
            subject_org_id=SIBLING_UNIT,
            subject_org_name=SIBLING_UNIT_NAME,
            updated_at=NOW,
        )
        version_id = await _insert_version(
            conn,
            slot_id=job_exact,
            file_hash="9" * 64,
            created_at=EARLIER,
            original_filename="another.pdf",
        )
        await _insert_job(conn, job_uuid=token, metadata=_linked_metadata(version_id))

        expected = [job_exact, filename_exact, unit_exact, department_exact, partial]
        for _ in range(3):
            items, total = await _search(conn, token, page_size=50)
            assert total == 5
            assert [item.slot_id for item in items] == expected


async def test_ordering_falls_back_to_updated_at_then_id(search_db):
    """同一精确级别内：``updated_at DESC`` 优先，再以 ``slot_id`` 兜底（顺序唯一）。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        older = await _insert_slot(
            conn,
            subject_org_id=HEAD_UNIT,
            subject_org_name=HEAD_UNIT_NAME,
            updated_at=LONG_AGO,
        )
        newer = await _insert_slot(
            conn,
            subject_org_id=SUB_UNIT,
            subject_org_name=SUB_UNIT_NAME,
            updated_at=NOW,
        )
        items, total = await _search(conn, "规划和自然资源局", page_size=50)
        assert total == 2
        assert [item.slot_id for item in items] == [newer, older]

        # 时间完全相同时按 slot_id 稳定兜底：连查三次顺序一致。
        await conn.execute(
            "UPDATE material_slots SET updated_at = $1 WHERE id = ANY($2::uuid[])",
            NOW,
            [older, newer],
        )
        first = [item.slot_id for item in (await _search(conn, "规划和自然资源局", page_size=50))[0]]
        for _ in range(2):
            again = [item.slot_id for item in (await _search(conn, "规划和自然资源局", page_size=50))[0]]
            assert again == first
        assert set(first) == {older, newer}


async def test_pagination_and_total_are_consistent(search_db):
    """``COUNT(*) OVER ()`` 的 total 与分页一致；越界页不把总数报成 0。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        created = []
        for index in range(5):
            created.append(
                await _insert_slot(
                    conn,
                    subject_org_id=SUB_UNIT,
                    subject_org_name=f"{SUB_UNIT_NAME}第{index}分队",
                    # 身份唯一键含 (主体, 层级, 材料范围, 文种, 财政年度)，
                    # 五个"分队"必须落在不同年度才不会撞唯一约束。
                    fiscal_year=2020 + index,
                    updated_at=NOW - timedelta(hours=index),
                )
            )

        first, total = await _search(conn, "执法大队", page=1, page_size=2)
        assert total == 5
        assert [item.slot_id for item in first] == created[:2]

        second, total = await _search(conn, "执法大队", page=2, page_size=2)
        assert total == 5
        assert [item.slot_id for item in second] == created[2:4]

        beyond, total = await _search(conn, "执法大队", page=4, page_size=2)
        assert beyond == []
        # 越界页由兜底计数给出真实 total（否则会被读成"一条都没搜到"）。
        assert total == 5


# ==== H. 当前运行的选择复用 canonical helper ================================


async def test_review_candidate_uses_current_run_helper(search_db):
    """同一当前版本跑过多次时，复核候选取"当前运行"（与处理记录同一判定）。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(conn, subject_org_id=HEAD_UNIT, subject_org_name=HEAD_UNIT_NAME)
        version_id = await _insert_version(
            conn,
            slot_id=slot_id,
            file_hash="a1" + "0" * 62,
            created_at=EARLIER,
            original_filename="final-2024.pdf",
        )
        # 旧运行（更早完成）与新运行（更晚完成）挂在同一个当前版本上。
        old_job = await _insert_job(
            conn,
            job_uuid="job-run-old",
            metadata=_linked_metadata(version_id),
            completed_at=LONG_AGO,
        )
        new_job = await _insert_job(
            conn,
            job_uuid="job-run-new",
            metadata=_linked_metadata(version_id),
            completed_at=NOW,
        )
        assert new_job > old_job
        await conn.execute(
            "UPDATE material_slots SET current_document_version_id = $1 WHERE id = $2::uuid",
            version_id,
            slot_id,
        )

        items, _ = await _search(conn, "规划和自然资源局本级", job_access=lambda payload: True)
        assert items[0].review_candidate is not None
        assert items[0].review_candidate.job_uuid == "job-run-new"


async def test_slots_without_current_version_have_no_review_candidate(search_db):
    """没有当前版本指针的槽位照样能被搜到，但不给出复核入口（也不猜版本）。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        slot_id = await _insert_slot(
            conn,
            subject_org_id=HEAD_UNIT,
            subject_org_name=HEAD_UNIT_NAME,
            status="missing",
            status_reason="due_reached_missing",
        )
        await _insert_version(
            conn,
            slot_id=slot_id,
            file_hash="b2" + "0" * 62,
            created_at=LONG_AGO,
            original_filename="historical.pdf",
        )

        items, total = await _search(conn, "规划和自然资源局本级", job_access=lambda payload: True)
        assert total == 1
        assert items[0].current_document_version_id is None
        assert items[0].current_filename is None
        assert items[0].review_candidate is None


# ==== I. 与接口层共享的收尾：relationship 兜底不猜 ===========================


async def test_relationship_unknown_is_reported_not_guessed(search_db):
    """主体层级认不出来时 relationship 如实为"待确认"，不猜成直属单位。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        await _insert_slot(
            conn,
            subject_org_id="unresolved:abc123",
            subject_org_name="某来源不明的材料",
            subject_kind="unknown",
            material_scope="unknown",
            status="mapping_required",
            status_reason="identity_unresolved",
        )
        items, total = await _search(conn, "来源不明")
        assert total == 1
        assert items[0].relationship == "relationship_unknown"


async def test_page_details_query_handles_many_slots_in_one_statement(search_db):
    """当前页明细只打一条 SQL：用"按主查询行数计数"的方式证明没有 N+1。

    这里用一个记录语句数量的连接包装器：搜索 3 条结果时，主查询 + 明细
    一共只允许 2 条语句（越界页兜底不算，本用例不触发）。
    """
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        for index in range(3):
            slot_id = await _insert_slot(
                conn,
                subject_org_id=SUB_UNIT,
                subject_org_name=f"{SUB_UNIT_NAME}第{index}分队",
                fiscal_year=2020 + index,
            )
            version_id = await _insert_version(
                conn,
                slot_id=slot_id,
                file_hash=f"{index:064d}",
                created_at=EARLIER,
                original_filename=f"final-{index}.pdf",
            )
            await _insert_job(
                conn,
                job_uuid=f"job-count-{index:03d}",
                metadata=_linked_metadata(version_id),
            )

        class _CountingConnection:
            def __init__(self, inner) -> None:
                self._inner = inner
                self.statements: Sequence[str] = []

            async def fetch(self, sql, *args):
                self.statements = list(self.statements) + [sql]
                return await self._inner.fetch(sql, *args)

        counting = _CountingConnection(conn)
        service = MaterialSearchQueryService(counting)
        items, total = await service.search(
            query=parse_search_query("执法大队"),
            filters=MaterialSearchFilters(),
            page=1,
            page_size=20,
        )
        assert total == 3
        assert len(items) == 3
        # 2 条语句：主查询（含 total 窗口函数）+ 当前页明细。
        assert len(counting.statements) == 2
        assert "material_search:main" in counting.statements[0]
        assert "material_search:page" in counting.statements[1]
        # 明细语句按槽位数组一次取完（不是逐条查）。
        assert "ANY($1::uuid[])" in counting.statements[1]


async def test_empty_result_is_not_an_error(search_db):
    """有授权但没命中：空列表 + total=0，不是 403、也不是报错。"""
    schema, pool = search_db
    async with _conn(schema, pool) as conn:
        items, total = await _search(conn, "完全不存在的材料名称")
        assert (items, total) == ([], 0)
        assert isinstance(items, list)
