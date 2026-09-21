"""迁移 0019 的真实 PostgreSQL 验证（显式 opt-in）。

默认跳过
--------
本仓库的测试默认不连任何真实数据库（``tests/conftest.py`` 无条件摘掉
``DATABASE_URL``）。本文件只在显式配置 ``GOVBUDGET_TEST_DATABASE_URL`` 时运行::

    GOVBUDGET_TEST_DATABASE_URL=postgresql://user:pass@host:5432/gbc_test \
        python -m pytest tests/test_material_slot_migration_pg.py -v

用专用环境变量而不是复用 ``DATABASE_URL``，是为了防止"顺手把开发库连上了"。

隔离方式
--------
整个文件在**独立 schema** 里执行（``PG_SCHEMA``），结束即 ``DROP SCHEMA CASCADE``。
因此它不会碰目标库 public 下的任何既有数据，跑完也不留残留表。
即使目标库是开发库，风险也仅限于"新增并删除了一个随机命名的 schema"。

它验证的是静态测试无法覆盖、只能由真库回答的问题：
语句能否真的重放、唯一约束是否真的拦得住重复、CHECK 是否真的生效、
NULL 年份在复合唯一索引下的行为是否符合预期、既有库升级路径是否只新增。
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import asyncpg
import pytest

from src.db.migrations import MIGRATIONS, ensure_migrations_table, run_migrations

pytestmark = pytest.mark.real_database

MIGRATION_ID = "2026-09-21_0019_material_slots"


@asynccontextmanager
async def _conn(schema: str, pool):
    """从池里取一条连接并把 search_path 指到测试 schema。

    必须用 ``async with pool.acquire()`` 而不是 ``await DatabaseConnection.acquire()``：
    后者拿到的连接不会被归还，``pool.close()`` 会一直等它，测试就会挂住。
    """
    async with pool.acquire() as connection:
        await connection.execute(f'SET search_path TO "{schema}", public')
        yield connection


@pytest.fixture
async def db(real_database_url, monkeypatch):
    """在独立 schema 中初始化连接池，测试结束后整体丢弃。"""
    from src.db.connection import DatabaseConnection

    schema = f"matslot_test_{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("PG_SCHEMA", schema)

    # 连接池是类级单例：先清掉可能存在的旧池，避免用错 schema
    DatabaseConnection._pool = None
    pool = await DatabaseConnection.initialize(real_database_url)
    assert DatabaseConnection.get_schema() == schema

    try:
        yield schema, pool
    finally:
        DatabaseConnection._pool = None
        try:
            async with pool.acquire() as connection:
                await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await pool.close()


async def _applied_ids(schema: str, pool) -> set:
    async with _conn(schema, pool) as connection:
        rows = await connection.fetch(f'SELECT id FROM "{schema}".schema_migrations')
    return {row["id"] for row in rows}


async def test_migrations_apply_and_second_run_is_noop(db):
    schema, pool = db

    await run_migrations()
    applied_first = await _applied_ids(schema, pool)
    assert MIGRATION_ID in applied_first
    assert len(applied_first) == len(MIGRATIONS)

    # 第二次执行：不报错、不重复记录、不重复建对象
    await run_migrations()
    assert await _applied_ids(schema, pool) == applied_first


async def test_upgrade_from_existing_database_applies_only_0019(db):
    """已有库升级路径：库已经停在 0018，跑迁移只应新增 0019 的内容。

    全新库建表与既有库升级是两条不同的风险路径。全新库出错是"建不起来"，
    一眼就能发现；既有库出错可能是"悄悄改了老数据"，很久以后才暴露。
    所以这里先把 0018 之前的状态原样搭出来，再跑迁移。
    """
    schema, pool = db
    prior = [item for item in MIGRATIONS if item["id"] != MIGRATION_ID]
    assert len(prior) == len(MIGRATIONS) - 1

    async with _conn(schema, pool) as connection:
        await ensure_migrations_table(connection, schema)
        for migration in prior:
            for statement in migration["sql"]:
                await connection.execute(statement)
            await connection.execute(
                f'INSERT INTO "{schema}".schema_migrations (id) VALUES ($1)', migration["id"]
            )
        # 升级前：material_slots 尚不存在
        assert await connection.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", f'"{schema}".material_slots'
        ) is False
        before = await connection.fetchval(f'SELECT COUNT(*) FROM "{schema}".schema_migrations')

    await run_migrations()

    async with _conn(schema, pool) as connection:
        after = await connection.fetchval(f'SELECT COUNT(*) FROM "{schema}".schema_migrations')
        assert after == before + 1, "升级路径只应新增一条迁移记录"
        assert await connection.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", f'"{schema}".material_slots'
        ) is True
        # 既有表结构未被改动：fiscal_documents 的年份仍然可空（0017 的效果还在）
        assert await connection.fetchval(
            """
            SELECT is_nullable FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = 'fiscal_documents'
              AND column_name = 'fiscal_year'
            """,
            schema,
        ) == "YES"


async def test_every_statement_replays_cleanly(db):
    """把 0019 的每条语句原样再执行一遍——真库上的幂等性验证。"""
    schema, pool = db
    await run_migrations()

    migration = next(item for item in MIGRATIONS if item["id"] == MIGRATION_ID)
    async with _conn(schema, pool) as connection:
        for statement in migration["sql"]:
            await connection.execute(statement)


async def test_schema_shape_matches_design(db):
    schema, pool = db
    await run_migrations()

    async with _conn(schema, pool) as connection:
        tables = {
            row["table_name"]
            for row in await connection.fetch(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = $1
                """,
                schema,
            )
        }
        assert {"material_slots", "material_sources"} <= tables

        columns = {
            row["column_name"]: row
            for row in await connection.fetch(
                """
                SELECT column_name, is_nullable, data_type
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = 'material_slots'
                """,
                schema,
            )
        }
        # 财政年度可空：识别不到年份时不得兜底成具体年份
        assert columns["fiscal_year"]["is_nullable"] == "YES"
        assert columns["subject_org_id"]["is_nullable"] == "NO"
        assert columns["slot_key"]["is_nullable"] == "NO"
        assert columns["current_document_version_id"]["is_nullable"] == "YES"

        version_columns = {
            row["column_name"]: row
            for row in await connection.fetch(
                """
                SELECT column_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = $1 AND table_name = 'fiscal_document_versions'
                """,
                schema,
            )
        }
        # 历史版本不允许被强行归属，绑定列必须可空
        assert version_columns["slot_id"]["is_nullable"] == "YES"


async def test_identity_unique_index_blocks_duplicate_slot(db):
    """复合唯一索引必须真的拦得住同身份重复插入。"""
    schema, pool = db
    await run_migrations()

    values = ("unit-org", "unit", "unit_self", "final", 2024, "")
    async with _conn(schema, pool) as connection:
        await connection.execute(_insert_sql("slot-key-1"), *values)
        # 断言具体异常类型而不是"任意异常"：只有唯一约束冲突才算这条闸真的拦住了，
        # 语法错误、权限错误同样会抛异常，但那些说明测试根本没跑到位。
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await connection.execute(_insert_sql("slot-key-2"), *values)


async def test_unknown_year_slots_are_distinguished_by_mapping_key(db):
    """年份未知时靠 mapping_key 区分，不能被 COALESCE(NULL,-1) 折叠成一条。"""
    schema, pool = db
    await run_migrations()

    async with _conn(schema, pool) as connection:
        await connection.execute(
            _insert_sql("slot-key-a"), "unit-org", "unit", "unit_self", "final", None, "doc:a"
        )
        await connection.execute(
            _insert_sql("slot-key-b"), "unit-org", "unit", "unit_self", "final", None, "doc:b"
        )
        assert await connection.fetchval(
            "SELECT COUNT(*) FROM material_slots WHERE fiscal_year IS NULL"
        ) == 2

    # 同样的 mapping_key 必须冲突（同一条材料不许建两个槽位）
    async with _conn(schema, pool) as connection:
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await connection.execute(
                _insert_sql("slot-key-c"),
                "unit-org",
                "unit",
                "unit_self",
                "final",
                None,
                "doc:a",
            )


async def test_status_check_constraint_rejects_unknown_value(db):
    schema, pool = db
    await run_migrations()

    async with _conn(schema, pool) as connection:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO material_slots (
                    slot_key, subject_org_id, subject_org_name,
                    subject_kind, subject_level, material_scope,
                    report_kind, status
                ) VALUES ($1, 'org', 'org', 'unit', 'unit', 'unit_self', 'final', 'not_a_status')
                """,
                "slot-bad-status",
            )


async def test_rollback_restores_previous_shape_without_losing_versions(db):
    """回滚 SQL（见 docs/MIGRATION_0019_MATERIAL_SLOTS.md）必须真的可执行。

    文档里写的回滚语句如果从没跑过，就只是"看起来能回滚"。
    这条用例把那段 SQL 原样执行，并断言两件事：
    新增对象确实消失，**原始文件版本的账一条没少**。
    """
    schema, pool = db
    await run_migrations()

    async with _conn(schema, pool) as connection:
        org_unit_id = await connection.fetchval(
            "INSERT INTO org_units (org_name) VALUES ('回滚测试单位') RETURNING id"
        )
        document_id = await connection.fetchval(
            """
            INSERT INTO fiscal_documents (org_unit_id, fiscal_year, doc_type)
            VALUES ($1, 2024, 'dept_final') RETURNING id
            """,
            org_unit_id,
        )
        version_id = await connection.fetchval(
            """
            INSERT INTO fiscal_document_versions (document_id, file_hash, storage_key)
            VALUES ($1, 'rollback-hash', 'job-rb/file.pdf') RETURNING id
            """,
            document_id,
        )
        await connection.execute(
            """
            INSERT INTO material_slots (
                slot_key, subject_org_id, subject_org_name,
                subject_kind, subject_level, material_scope, report_kind, fiscal_year,
                current_document_version_id
            ) VALUES ('slot-rollback', 'org-rb', '某单位', 'unit', 'unit', 'unit_self',
                      'final', 2024, $1)
            """,
            version_id,
        )

        # --- 文档中记录的回滚 SQL，逐条原样执行 ---
        # 顺序不能颠倒：fiscal_document_versions.slot_id 的外键指向 material_slots，
        # 先删表会被 DependentObjectsStillExist 拒绝。必须先解除引用再删表。
        await connection.execute(
            "ALTER TABLE fiscal_document_versions DROP COLUMN IF EXISTS slot_id"
        )
        await connection.execute("DROP TABLE IF EXISTS material_sources")
        await connection.execute("DROP TABLE IF EXISTS material_slots")
        await connection.execute(
            f'DELETE FROM "{schema}".schema_migrations WHERE id = $1', MIGRATION_ID
        )

        assert await connection.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", f'"{schema}".material_slots'
        ) is False
        assert await connection.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", f'"{schema}".material_sources'
        ) is False
        # 原始文件版本的账一条没少 —— 这是回滚安全性的核心断言
        assert await connection.fetchval(
            "SELECT COUNT(*) FROM fiscal_document_versions WHERE id = $1", version_id
        ) == 1
        assert await connection.fetchval(
            "SELECT COUNT(*) FROM fiscal_documents WHERE id = $1", document_id
        ) == 1
        # 迁移记录被抹掉后，重跑迁移应当能重新建起来
        assert await connection.fetchval(
            f'SELECT COUNT(*) FROM "{schema}".schema_migrations WHERE id = $1', MIGRATION_ID
        ) == 0

    await run_migrations()
    async with _conn(schema, pool) as connection:
        assert await connection.fetchval(
            "SELECT to_regclass($1) IS NOT NULL", f'"{schema}".material_slots'
        ) is True


async def test_deleting_a_slot_keeps_document_versions(db):
    """删槽位不得删掉原始文件版本记录——版本是原始 PDF 的账，不是槽位的附属物。"""
    schema, pool = db
    await run_migrations()

    async with _conn(schema, pool) as connection:
        org_unit_id = await connection.fetchval(
            "INSERT INTO org_units (org_name) VALUES ('PG 测试单位') RETURNING id"
        )
        document_id = await connection.fetchval(
            """
            INSERT INTO fiscal_documents (org_unit_id, fiscal_year, doc_type)
            VALUES ($1, 2024, 'dept_final') RETURNING id
            """,
            org_unit_id,
        )
        version_id = await connection.fetchval(
            """
            INSERT INTO fiscal_document_versions (document_id, file_hash, storage_key)
            VALUES ($1, 'hash-x', 'job-x/file.pdf') RETURNING id
            """,
            document_id,
        )
        slot_id = await connection.fetchval(
            """
            INSERT INTO material_slots (
                slot_key, subject_org_id, subject_org_name,
                subject_kind, subject_level, material_scope, report_kind, fiscal_year
            ) VALUES ('slot-del', 'org-1', '某单位', 'unit', 'unit', 'unit_self', 'final', 2024)
            RETURNING id
            """
        )
        await connection.execute(
            "UPDATE fiscal_document_versions SET slot_id = $1 WHERE id = $2", slot_id, version_id
        )
        await connection.execute(
            "UPDATE material_slots SET current_document_version_id = $1 WHERE id = $2",
            version_id,
            slot_id,
        )

        await connection.execute("DELETE FROM material_slots WHERE id = $1", slot_id)

        assert await connection.fetchval(
            "SELECT COUNT(*) FROM fiscal_document_versions WHERE id = $1", version_id
        ) == 1
        assert await connection.fetchval(
            "SELECT slot_id FROM fiscal_document_versions WHERE id = $1", version_id
        ) is None


async def test_service_allocate_and_bind_against_real_database(db):
    """服务层写入在真库上的端到端行为。

    假连接能验证"参数没用错"，但验证不了"ON CONFLICT 的冲突目标在真库里认不认"——
    复合表达式索引做冲突目标时，``ON CONFLICT (slot_key)`` 能否命中、
    自然键冲突会不会被拦住，只有真库说了算。
    """
    from src.services.material_slot_service import allocate_for_document

    schema, pool = db
    await run_migrations()

    metadata = {
        "organization_id": "unit-org-1",
        "organization_name": "上海市普陀区规划和自然资源局本级",
        "report_year": "2024",
        "report_kind": "final",
        "doc_type": "dept_final",
    }
    org_records = [
        {"id": "district-1", "name": "普陀区", "level": "district", "parent_id": None},
        {
            "id": "dept-1",
            "name": "上海市普陀区规划和自然资源局",
            "level": "department",
            "parent_id": "district-1",
        },
        {
            "id": "unit-org-1",
            "name": "上海市普陀区规划和自然资源局本级",
            "level": "unit",
            "parent_id": "dept-1",
        },
    ]

    async with _conn(schema, pool) as connection:
        org_unit_id = await connection.fetchval(
            "INSERT INTO org_units (org_name) VALUES ('服务层测试单位') RETURNING id"
        )
        document_id = await connection.fetchval(
            """
            INSERT INTO fiscal_documents (org_unit_id, fiscal_year, doc_type)
            VALUES ($1, 2024, 'dept_final') RETURNING id
            """,
            org_unit_id,
        )
        version_id = await connection.fetchval(
            """
            INSERT INTO fiscal_document_versions (document_id, file_hash, storage_key)
            VALUES ($1, 'svc-hash', 'job-svc/file.pdf') RETURNING id
            """,
            document_id,
        )

        first = await allocate_for_document(
            connection,
            metadata=metadata,
            checksum="c" * 64,
            org_records=org_records,
            document_version_id=version_id,
        )
        second = await allocate_for_document(
            connection,
            metadata=metadata,
            checksum="c" * 64,
            org_records=org_records,
            document_version_id=version_id,
        )

        assert first["status"] == "resolved"
        assert first["slot_id"] == second["slot_id"], "重复分析不得生成新的业务材料"
        assert await connection.fetchval("SELECT COUNT(*) FROM material_slots") == 1
        assert await connection.fetchval(
            "SELECT id FROM material_slots WHERE current_document_version_id = $1", version_id
        ) is not None
        assert await connection.fetchval(
            "SELECT slot_id FROM fiscal_document_versions WHERE id = $1", version_id
        ) is not None
        # 绑定之后状态从"未上传/未到期"推进到"已上传待分析"。
        # 注意不能断成 review_required：分析尚未执行，没有任何待复核的问题，
        # 把"文件到位"直接说成"待人处理"会虚增待办。
        assert await connection.fetchval("SELECT status FROM material_slots") == "uploaded"
        assert await connection.fetchval("SELECT status_reason FROM material_slots") == (
            "awaiting_analysis"
        )


async def test_service_refuses_slot_whose_natural_key_collides(db):
    """自然键冲突必须报错，而不是静默合并成一条。

    复合唯一索引是第一道闸；这里故意让它成为唯一被触发的那道（手工插一条同自然键、
    不同 slot_key 的行），确认数据库层面确实拦得住哈希以外的重复身份。
    """
    schema, pool = db
    await run_migrations()

    async with _conn(schema, pool) as connection:
        await connection.execute(
            _insert_sql("slot-existing"), "unit-org", "unit", "unit_self", "final", 2024, ""
        )
        with pytest.raises(asyncpg.exceptions.UniqueViolationError) as excinfo:
            await connection.execute(
                _insert_sql("slot-different-key"), "unit-org", "unit", "unit_self", "final", 2024, ""
            )
        # 冲突对象必须是复合身份索引，而不是 slot_key 那一条——
        # 否则"自然键拦住重复"这条结论就没有被真正验证。
        assert "uq_material_slots_identity" in str(excinfo.value)


async def test_cross_slot_rebind_is_rejected_and_rolls_back_on_real_db(db):
    """跨槽重绑在真库上被拒绝，且失败的那次分配整体回滚。

    假连接能验证语句序列，验证不了"FOR UPDATE 之后确实读到了最新值"。
    这里走完整服务层：第二次分配用不同身份指向另一个槽位，必须报冲突，
    并且新槽位不能留在库里。
    """
    from src.services.material_slot_service import (
        allocate_for_document,
        safe_allocate_for_document,
    )

    schema, pool = db
    await run_migrations()

    async with _conn(schema, pool) as connection:
        version_id = await _seed_version(connection, prefix="rebind")

        first = await allocate_for_document(
            connection,
            metadata=_metadata_for(version_prefix="rebind"),
            checksum="1" * 64,
            org_records=_ORG_RECORDS,
            document_version_id=version_id,
        )
        assert first["bound"] is True
        original_slot_id = first["slot_id"]

        conflict = await safe_allocate_for_document(
            connection,
            metadata={
                **_metadata_for(version_prefix="rebind"),
                "report_kind": "budget",
                "doc_type": "dept_budget",
            },
            checksum="1" * 64,
            org_records=_ORG_RECORDS,
            document_version_id=version_id,
        )

        assert conflict["status"] == "conflict"
        assert conflict["reason"] == "slot_binding_conflict"
        assert conflict["bound"] is False

        # 原来那个槽位仍在，指针未变
        assert await connection.fetchval(
            "SELECT current_document_version_id FROM material_slots WHERE id = $1::uuid",
            original_slot_id,
        ) == version_id
        # 版本没有被改挂
        assert str(
            await connection.fetchval(
                "SELECT slot_id FROM fiscal_document_versions WHERE id = $1", version_id
            )
        ) == original_slot_id
        # 冲突那次分配新建的槽位被整体回滚
        assert await connection.fetchval(
            "SELECT COUNT(*) FROM material_slots WHERE report_kind = 'budget'"
        ) == 0


async def test_concurrent_binding_of_one_version_has_exactly_one_winner(db):
    """同一版本被并发绑定到两个槽位：只能有一个合法归属。

    没有行锁时两个事务都会读到 ``slot_id IS NULL`` 然后各自写入，
    两边都以为自己成功，最后一个槽位的当前版本指针会指向别人家的版本。
    这条用例是行锁真正生效的证据。
    """
    from src.services.material_slot_service import (
        MaterialSlotService,
        SlotBindingConflict,
    )

    schema, pool = db
    await run_migrations()

    async with _conn(schema, pool) as connection:
        version_id = await _seed_version(connection, prefix="race")
        slot_a = await _seed_slot_row(connection, "race-slot-a", subject_org_id="race-org-a")
        slot_b = await _seed_slot_row(connection, "race-slot-b", subject_org_id="race-org-b")

    results: dict = {}

    async def _bind(key: str, slot_id: str) -> None:
        async with _conn(schema, pool) as connection:
            service = MaterialSlotService(connection)
            try:
                await service.bind_document_version(slot_id, version_id)
                results[key] = "bound"
            except SlotBindingConflict:
                results[key] = "conflict"

    await asyncio.gather(_bind("a", slot_a), _bind("b", slot_b))

    assert sorted(results.values()) == ["bound", "conflict"], results

    async with _conn(schema, pool) as connection:
        winner_slot = str(
            await connection.fetchval(
                "SELECT slot_id FROM fiscal_document_versions WHERE id = $1", version_id
            )
        )
        assert winner_slot in (slot_a, slot_b)
        # 只有一个槽位声称它是自己的当前版本
        claimers = await connection.fetch(
            "SELECT id::text FROM material_slots WHERE current_document_version_id = $1",
            version_id,
        )
        assert [row["id"] for row in claimers] == [winner_slot]


async def test_current_version_tie_break_uses_id_on_real_db(db):
    """同 created_at 的版本按 id 决定新旧——真库上的整数比较。

    若把排序键写成单独的 created_at，指针会在这两个版本之间随机停靠；
    若按字符串比较 id，``10 < 9``，结果与线上相反。
    """
    from src.services.material_slot_service import MaterialSlotService

    schema, pool = db
    await run_migrations()

    async with _conn(schema, pool) as connection:
        slot_id = await _seed_slot_row(connection, "tie-slot")
        org_unit_id = await connection.fetchval(
            "INSERT INTO org_units (org_name) VALUES ('同级时间戳单位') RETURNING id"
        )
        document_id = await connection.fetchval(
            """
            INSERT INTO fiscal_documents (org_unit_id, fiscal_year, doc_type)
            VALUES ($1, 2024, 'dept_final') RETURNING id
            """,
            org_unit_id,
        )
        # asyncpg 要求传 datetime 对象而不是时间字符串（后者会在绑定参数时报 DataError）
        same_moment = datetime(2026, 9, 21, tzinfo=timezone.utc)
        version_ids = []
        for index in range(2):
            version_ids.append(
                await connection.fetchval(
                    """
                    INSERT INTO fiscal_document_versions
                        (document_id, file_hash, storage_key, created_at)
                    VALUES ($1, $2, $3, $4) RETURNING id
                    """,
                    document_id,
                    f"tie-hash-{index}",
                    f"job-tie/file-{index}.pdf",
                    same_moment,
                )
            )

        service = MaterialSlotService(connection)
        # 先绑大 id 再绑小 id：指针不应被小 id 抢走
        await service.bind_document_version(slot_id, version_ids[1])
        await service.bind_document_version(slot_id, version_ids[0])

        assert await connection.fetchval(
            "SELECT current_document_version_id FROM material_slots WHERE id = $1::uuid",
            slot_id,
        ) == version_ids[1]


async def test_caliber_conflict_is_durable_on_real_db(db):
    """口径矛盾在真库上被持久化，且不会被后续一致观测洗掉。"""
    from src.services import material_slot_resolver as resolver
    from src.services.material_slot_service import MaterialSlotService

    schema, pool = db
    await run_migrations()

    decision = resolver.decide_slot_allocation(
        metadata=_metadata_for(version_prefix="caliber"),
        org_records=_ORG_RECORDS,
        checksum="2" * 64,
    )
    assert decision.ok, decision.reason

    async with _conn(schema, pool) as connection:
        service = MaterialSlotService(connection)
        await service.upsert_from_decision(decision, caliber="summary")
        await service.upsert_from_decision(decision, caliber="self")
        # 又一次识别回 summary，矛盾仍然存在
        row = await service.upsert_from_decision(decision, caliber="summary")

        stored = await service.get_slot_by_key(decision.identity.slot_key)
        assert stored["caliber"] == "summary"
        assert stored["caliber_conflict_candidate"] == "self"
        assert stored["status"] == "mapping_required"
        assert stored["status_reason"] == "caliber_conflict"
        # 刷新一次也不会把冲突洗掉
        refreshed = await service.refresh_status(row["id"])
        assert refreshed["status_reason"] == "caliber_conflict"


async def test_mark_not_applicable_respects_identity_gate_on_real_db(db):
    """身份未确认的槽位：标不适用仍停在待确认；身份完整的才转不适用。"""
    from src.services.material_slot_service import MaterialSlotService

    schema, pool = db
    await run_migrations()

    async with _conn(schema, pool) as connection:
        await _seed_slot_row(connection, "gate-unresolved", fiscal_year=None, mapping_key="")
        await _seed_slot_row(connection, "gate-resolved")

        service = MaterialSlotService(connection)
        first = await service.mark_not_applicable("gate-unresolved", note="依据 A")
        second = await service.mark_not_applicable("gate-resolved", note="依据 B")

        assert first["status"] == "mapping_required"
        assert first["status_reason"] == "identity_unresolved"
        assert second["status"] == "not_applicable"
        # 事实都写进去了，状态差异来自身份是否完整
        written = await connection.fetch(
            "SELECT slot_key, applicability_status FROM material_slots ORDER BY slot_key"
        )
        assert {row["applicability_status"] for row in written} == {"not_applicable"}


async def _race_allocate_against_direct_bind(
    schema: str,
    pool,
    *,
    metadata: Dict[str, Any],
    checksum: str,
    version_id: int,
    slot_id: str,
) -> list:
    """让 allocate 与直接 bind 同时指向同一个 (槽位, 版本) 对。

    抽成显式传参的函数而不是写在测试的循环体里：循环里的闭包会按引用捕获
    循环变量，那一轮跑完变量就被改掉，用例要么静默失效、要么在加下一轮时
    突然变成竞态。参数化之后每轮传进去的都是当轮的值，不存在这个问题。
    """
    from src.services.material_slot_service import (
        MaterialSlotService,
        allocate_for_document,
    )

    barrier = _ConcurrencyBarrier(2)

    async def _allocate() -> None:
        async with _conn(schema, pool) as connection:
            await barrier.wait()
            await allocate_for_document(
                connection,
                metadata=metadata,
                checksum=checksum,
                org_records=_ORG_RECORDS,
                document_version_id=version_id,
            )

    async def _direct_bind() -> None:
        async with _conn(schema, pool) as connection:
            await barrier.wait()
            await MaterialSlotService(connection).bind_document_version(slot_id, version_id)

    tasks = [asyncio.create_task(_allocate()), asyncio.create_task(_direct_bind())]
    await barrier.release()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [item for item in results if isinstance(item, BaseException)]


class _ConcurrencyBarrier:
    """让 N 个协程同时越过同一个点。

    用途是制造**真并发**：协调方等到所有参与者都到达之后才放行，
    因此不依赖 ``sleep`` 去赌时序。任何参与者提前失败都会让 ``release`` 超时，
    测试直接红，而不是安静地退化成串行执行——那会让并发用例变成假证据。
    """

    def __init__(self, parties: int) -> None:
        self._parties = parties
        self._arrived = asyncio.Semaphore(0)
        self._go = asyncio.Event()

    async def wait(self) -> None:
        self._arrived.release()
        await self._go.wait()

    async def release(self, timeout: float = 15.0) -> None:
        for _ in range(self._parties):
            await asyncio.wait_for(self._arrived.acquire(), timeout=timeout)
        self._go.set()


async def _slot_row(schema: str, pool, slot_key: str) -> Optional[asyncpg.Record]:
    async with _conn(schema, pool) as connection:
        return await connection.fetchrow(
            """
            SELECT caliber, caliber_conflict_candidate, status, status_reason
            FROM material_slots
            WHERE slot_key = $1
            """,
            slot_key,
        )


async def test_concurrent_first_slot_creation_preserves_caliber_conflict(db):
    """首次并发创建同一槽位、且两次口径相反时，冲突证据必须留下。

    这是本轮最重要的验收用例。旧写法"SELECT ... FOR UPDATE 再 ON CONFLICT
    DO UPDATE"在这里会失败：``FOR UPDATE`` **锁不住不存在的行**，
    两个事务都读到空行、各自在锁外算好口径，随后一个插入、另一个把锁外
    算出的结果盖上去——``summary`` 与 ``self`` 只留下一个，冲突证据被抹掉。

    现写法先在锁外确保行存在、再在锁内读最新值并判定，因此两个事务被数据库
    串行化，后到者一定读得到先到者写下的口径。

    同步用的是 barrier 而不是 sleep：两个协程都到达起点后才一起放行，
    否则先启动的那个会先跑完，用例就只是"两次串行调用"。
    """
    from src.services import material_slot_resolver as resolver
    from src.services.material_slot_service import MaterialSlotService

    schema, pool = db
    await run_migrations()

    decision = resolver.decide_slot_allocation(
        metadata=_metadata_for(version_prefix="caliber-race"),
        org_records=_ORG_RECORDS,
        checksum="7" * 64,
    )
    assert decision.ok, decision.reason
    slot_key = decision.identity.slot_key

    barrier = _ConcurrencyBarrier(2)

    async def _allocate(caliber: str) -> None:
        async with _conn(schema, pool) as connection:
            await barrier.wait()
            await MaterialSlotService(connection).upsert_from_decision(
                decision, caliber=caliber
            )

    tasks = [
        asyncio.create_task(_allocate("summary")),
        asyncio.create_task(_allocate("self")),
    ]
    await barrier.release()
    await asyncio.gather(*tasks)

    async with _conn(schema, pool) as connection:
        rows = await connection.fetch(
            "SELECT caliber, caliber_conflict_candidate, status, status_reason"
            " FROM material_slots WHERE slot_key = $1",
            slot_key,
        )

    assert len(rows) == 1, "首次并发创建应当只留下一条槽位"
    row = rows[0]
    assert {row["caliber"], row["caliber_conflict_candidate"]} == {"summary", "self"}, (
        f"口径冲突证据丢失: caliber={row['caliber']!r} "
        f"candidate={row['caliber_conflict_candidate']!r}"
    )
    assert row["caliber"] != row["caliber_conflict_candidate"]
    assert row["status"] == "mapping_required"
    assert row["status_reason"] == "caliber_conflict"


async def test_concurrent_first_slot_creation_with_same_caliber_has_no_false_conflict(db):
    """两次口径相同：仍然只有一条槽位，且**不能**凭空判出冲突。"""
    from src.services import material_slot_resolver as resolver
    from src.services.material_slot_service import MaterialSlotService

    schema, pool = db
    await run_migrations()

    decision = resolver.decide_slot_allocation(
        metadata=_metadata_for(version_prefix="caliber-same"),
        org_records=_ORG_RECORDS,
        checksum="8" * 64,
    )
    assert decision.ok, decision.reason
    slot_key = decision.identity.slot_key

    barrier = _ConcurrencyBarrier(2)

    async def _allocate() -> None:
        async with _conn(schema, pool) as connection:
            await barrier.wait()
            await MaterialSlotService(connection).upsert_from_decision(
                decision, caliber="summary"
            )

    tasks = [asyncio.create_task(_allocate()), asyncio.create_task(_allocate())]
    await barrier.release()
    await asyncio.gather(*tasks)

    row = await _slot_row(schema, pool, slot_key)
    assert row is not None
    assert row["caliber"] == "summary"
    assert row["caliber_conflict_candidate"] is None
    assert row["status_reason"] != "caliber_conflict"


async def test_refresh_status_reads_facts_under_its_own_row_lock(db):
    """refresh_status 必须在自己的行锁内读事实，不能按过期快照覆盖状态。

    构造方式：事务 B 先锁住槽位行，然后在持锁期间绑定文件版本并提交。
    如果没有行锁，A 的 refresh 会立刻读完旧快照并写出 ``not_due``，
    ``wait_for`` 就不会超时——**超时本身就是"锁确实生效"的断言**。
    锁生效时 A 只能等 B 提交，于是读到的是绑定之后的事实。
    """
    from src.services.material_slot_service import MaterialSlotService

    schema, pool = db
    await run_migrations()

    async with _conn(schema, pool) as connection:
        version_id = await _seed_version(connection, prefix="refresh-race")
        slot_id = await _seed_slot_row(
            connection, "refresh-race-slot", subject_org_id="refresh-race-org"
        )

    async def _refresh() -> Dict[str, Any]:
        async with _conn(schema, pool) as connection:
            return await MaterialSlotService(connection).refresh_status(slot_id)

    async with _conn(schema, pool) as holder:
        transaction = holder.transaction()
        await transaction.start()
        # B 持锁：槽位行被锁住，A 的 FOR UPDATE 会一直等
        await holder.fetchrow(
            "SELECT id FROM material_slots WHERE id = $1::uuid FOR UPDATE", slot_id
        )

        refresh_task = asyncio.create_task(_refresh())
        with pytest.raises(asyncio.TimeoutError):
            # shield：超时只用来做断言，不能把任务取消掉
            await asyncio.wait_for(asyncio.shield(refresh_task), timeout=0.5)

        # B 在持锁期间完成绑定，然后提交
        await MaterialSlotService(holder).bind_document_version(slot_id, version_id)
        await transaction.commit()

    result = await refresh_task

    assert result["status"] == "uploaded", (
        f"refresh 没有看到刚刚绑定的版本，读到的是过期事实: {result}"
    )
    async with _conn(schema, pool) as connection:
        stored = await connection.fetchrow(
            "SELECT status, current_document_version_id, applicability_status,"
            " caliber_conflict_candidate FROM material_slots WHERE id = $1::uuid",
            slot_id,
        )
    # 缓存状态与数据库事实不许互相矛盾
    assert stored["current_document_version_id"] == version_id
    assert stored["status"] == "uploaded"
    if stored["current_document_version_id"] is not None:
        assert stored["status"] not in ("missing", "not_due")


async def test_concurrent_allocate_and_direct_bind_do_not_deadlock(db):
    """并发 allocate 与直接 bind 反例：锁顺序统一后不允许出现死锁。

    这就是修复前的 ABBA 场景：``allocate_for_document`` 先锁槽位再锁版本，
    而 ``bind_document_version`` 曾经先锁版本再锁槽位。两者并发指向
    **同一个 (槽位, 版本) 对**时，一个持槽位等版本、另一个持版本等槽位。
    现在两边都是"槽位 → 版本"，全序成立，环不可能形成。

    重复多轮是为了让调度器有机会把两种交错都试出来；每轮用全新的身份与版本，
    避免上一轮的锁等待影响下一轮。
    """
    from src.services import material_slot_resolver as resolver
    from src.services.material_slot_service import (
        MaterialSlotService,
        allocate_for_document,
    )

    schema, pool = db
    await run_migrations()

    rounds = 6
    for round_index in range(rounds):
        prefix = f"deadlock-{round_index}"
        metadata = _metadata_for(version_prefix=prefix)
        decision = resolver.decide_slot_allocation(
            metadata=metadata, org_records=_ORG_RECORDS, checksum=f"{round_index}" * 8
        )
        assert decision.ok, decision.reason

        async with _conn(schema, pool) as connection:
            version_id = await _seed_version(connection, prefix=prefix)
            # 先串行建槽位，让并发的两条路径指向同一个槽位
            created = await allocate_for_document(
                connection,
                metadata=metadata,
                checksum=f"{round_index}" * 8,
                org_records=_ORG_RECORDS,
                document_version_id=None,
            )
            slot_id = created["slot_id"]

        failures = await _race_allocate_against_direct_bind(
            schema,
            pool,
            metadata=metadata,
            checksum=f"{round_index}" * 8,
            version_id=version_id,
            slot_id=slot_id,
        )
        assert failures == [], f"第 {round_index} 轮出现异常（疑似死锁）: {failures}"

        async with _conn(schema, pool) as connection:
            assert await connection.fetchval(
                "SELECT COUNT(*) FROM material_slots WHERE slot_key = $1",
                decision.identity.slot_key,
            ) == 1
            assert await connection.fetchval(
                "SELECT COUNT(*) FROM fiscal_document_versions WHERE slot_id = $1::uuid",
                slot_id,
            ) >= 1


async def test_allocate_does_not_deadlock_with_cross_slot_bind(db):
    """跨槽绑定路径也不许死锁：版本属于 S1、请求绑到 S2 时全程只等一次。"""
    from src.services.material_slot_service import (
        MaterialSlotService,
        allocate_for_document,
        safe_allocate_for_document,
    )

    schema, pool = db
    await run_migrations()

    async with _conn(schema, pool) as connection:
        version_id = await _seed_version(connection, prefix="cross-deadlock")
        first = await allocate_for_document(
            connection,
            metadata=_metadata_for(version_prefix="cross-deadlock"),
            checksum="9" * 64,
            org_records=_ORG_RECORDS,
            document_version_id=version_id,
        )
        other_slot = await _seed_slot_row(
            connection, "cross-deadlock-other", subject_org_id="cross-deadlock-org"
        )
        slot_id = first["slot_id"]

    barrier = _ConcurrencyBarrier(2)

    async def _winner_bind() -> None:
        async with _conn(schema, pool) as connection:
            await barrier.wait()
            await MaterialSlotService(connection).bind_document_version(slot_id, version_id)

    async def _loser_bind() -> Dict[str, Any]:
        async with _conn(schema, pool) as connection:
            await barrier.wait()
            return await safe_allocate_for_document(
                connection,
                metadata={
                    **_metadata_for(version_prefix="cross-deadlock"),
                    "report_kind": "budget",
                    "doc_type": "dept_budget",
                },
                checksum="9" * 64,
                org_records=_ORG_RECORDS,
                document_version_id=version_id,
            )

    tasks = [asyncio.create_task(_winner_bind()), asyncio.create_task(_loser_bind())]
    await barrier.release()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    failures = [item for item in results if isinstance(item, BaseException)]
    assert failures == [], f"跨槽路径出现异常（疑似死锁）: {failures}"
    assert results[1]["status"] == "conflict"

    async with _conn(schema, pool) as connection:
        assert await connection.fetchval(
            "SELECT COUNT(*) FROM material_slots WHERE current_document_version_id = $1",
            version_id,
        ) == 1
        assert str(
            await connection.fetchval(
                "SELECT slot_id FROM fiscal_document_versions WHERE id = $1", version_id
            )
        ) == slot_id
        assert other_slot != slot_id


# ---- 真库用例的共享夹具 -----------------------------------------------------

_ORG_RECORDS = [
    {"id": "district-1", "name": "普陀区", "level": "district", "parent_id": None},
    {
        "id": "dept-1",
        "name": "上海市普陀区规划和自然资源局",
        "level": "department",
        "parent_id": "district-1",
    },
    {
        "id": "unit-org-1",
        "name": "上海市普陀区规划和自然资源局本级",
        "level": "unit",
        "parent_id": "dept-1",
    },
]


def _metadata_for(*, version_prefix: str) -> Dict[str, Any]:
    return {
        "organization_id": "unit-org-1",
        "organization_name": "上海市普陀区规划和自然资源局本级",
        "report_year": "2024",
        "report_kind": "final",
        "doc_type": "dept_final",
        "filename": f"{version_prefix}-单位决算.pdf",
    }


async def _seed_version(connection: Any, *, prefix: str) -> int:
    org_unit_id = await connection.fetchval(
        "INSERT INTO org_units (org_name) VALUES ($1) RETURNING id", f"{prefix} 测试单位"
    )
    document_id = await connection.fetchval(
        """
        INSERT INTO fiscal_documents (org_unit_id, fiscal_year, doc_type)
        VALUES ($1, 2024, 'dept_final') RETURNING id
        """,
        org_unit_id,
    )
    return await connection.fetchval(
        """
        INSERT INTO fiscal_document_versions (document_id, file_hash, storage_key)
        VALUES ($1, $2, $3) RETURNING id
        """,
        document_id,
        f"{prefix}-hash",
        f"job-{prefix}/file.pdf",
    )


async def _seed_slot_row(
    connection: Any,
    slot_key: str,
    *,
    subject_org_id: str = "unit-org-1",
    fiscal_year: Any = 2024,
    mapping_key: str = "",
    subject_kind: str = "unit",
    material_scope: str = "unit_self",
    report_kind: str = "final",
) -> str:
    """落一条槽位行。

    需要多条槽位时**必须**让 ``subject_org_id`` 或年份、文种互不相同：
    复合唯一索引会拒绝同一身份的第二次插入（这正是它的职责），
    夹具图省事复用同一身份只会撞在约束上。
    """
    return await connection.fetchval(
        """
        INSERT INTO material_slots (
            slot_key, subject_org_id, subject_org_name,
            subject_kind, subject_level, material_scope,
            report_kind, fiscal_year, mapping_key
        ) VALUES ($1, $2, '某单位', $3, $3, $4, $5, $6, $7)
        RETURNING id::text
        """,
        slot_key,
        subject_org_id,
        subject_kind,
        material_scope,
        report_kind,
        fiscal_year,
        mapping_key,
    )


async def test_cleanup_guard_keeps_placeholder_slot_versions_on_real_db(db):
    """两个 mapping_required 占位槽位 + 旧清理链路的 DELETE 守卫（真库）。

    构造的是最容易出事的那一幕：两个任务组织名相同、年度与文种相同、
    只有文档校验和不同。旧 structured-ingest scope 只看"组织+年度+文种"，
    会把它们当成同一份材料的两个版本，于是认为旧的那个可以清理；
    而在台账里它们是两条独立材料（身份未确认时按文档校验和各自成槽）。

    这里不模拟：槽位由真实 resolver 判定、走真实服务层落库，
    最后执行的就是清理链路那条带守卫的 DELETE。
    """
    from src.services import material_slot_resolver as resolver
    from src.services.material_slot_service import allocate_for_document

    schema, pool = db
    await run_migrations()

    shared = {
        "organization_id": None,
        "organization_name": "同名占位单位",
        "report_year": "2024",
        "report_kind": "final",
        "doc_type": "dept_final",
    }
    checksums = {"a": "a" * 64, "b": "b" * 64}

    async with _conn(schema, pool) as connection:
        version_ids = {}
        for key, checksum in checksums.items():
            decision = resolver.decide_slot_allocation(
                metadata={**shared, "filename": f"placeholder-{key}.pdf"},
                org_records=[],
                checksum=checksum,
            )
            assert decision.status == resolver.DECISION_MAPPING_REQUIRED, decision.reason
            assert decision.identity.mapping_key == f"doc:{checksum}"

            version_id = await _seed_version(connection, prefix=f"placeholder-{key}")
            summary = await allocate_for_document(
                connection,
                metadata={**shared, "filename": f"placeholder-{key}.pdf"},
                checksum=checksum,
                org_records=[],
                document_version_id=version_id,
            )
            assert summary["bound"] is True
            version_ids[key] = version_id

        # 两条材料各自成槽，且各自都有当前版本
        assert await connection.fetchval(
            "SELECT COUNT(*) FROM material_slots WHERE mapping_key IS NOT NULL"
        ) == 2
        for key, version_id in version_ids.items():
            assert await connection.fetchval(
                "SELECT slot_id FROM fiscal_document_versions WHERE id = $1", version_id
            ) is not None, f"{key} 没有绑定槽位"
            assert await connection.fetchval(
                "SELECT COUNT(*) FROM material_slots"
                " WHERE current_document_version_id = $1",
                version_id,
            ) == 1

        # 旧清理链路的 DELETE：带 slot_id IS NULL 守卫
        for key, version_id in version_ids.items():
            affected = await connection.execute(
                "DELETE FROM fiscal_document_versions WHERE id = $1 AND slot_id IS NULL",
                version_id,
            )
            assert str(affected).strip().endswith("0"), (
                f"占位槽位 {key} 的版本被清理链路删掉了"
            )

        # 版本与槽位指针都还在
        for key, version_id in version_ids.items():
            assert await connection.fetchval(
                "SELECT COUNT(*) FROM fiscal_document_versions WHERE id = $1", version_id
            ) == 1, f"{key} 的版本不见了"
            assert await connection.fetchval(
                "SELECT current_document_version_id FROM material_slots"
                " WHERE mapping_key = $1",
                f"doc:{checksums[key]}",
            ) == version_id


async def test_cleanup_guard_still_deletes_unbound_version_on_real_db(db):
    """未绑定槽位的版本仍可被旧链路正常删除——保护不是把清理整条禁掉。"""
    schema, pool = db
    await run_migrations()

    async with _conn(schema, pool) as connection:
        bound_version = await _seed_version(connection, prefix="guard-bound")
        free_version = await _seed_version(connection, prefix="guard-free")
        slot_id = await _seed_slot_row(
            connection, "guard-slot", subject_org_id="guard-org"
        )
        await connection.execute(
            "UPDATE fiscal_document_versions SET slot_id = $1 WHERE id = $2",
            slot_id,
            bound_version,
        )

        blocked = await connection.execute(
            "DELETE FROM fiscal_document_versions WHERE id = $1 AND slot_id IS NULL",
            bound_version,
        )
        assert str(blocked).strip().endswith("0")

        removed = await connection.execute(
            "DELETE FROM fiscal_document_versions WHERE id = $1 AND slot_id IS NULL",
            free_version,
        )
        assert str(removed).strip().endswith("1")

        assert await connection.fetchval(
            "SELECT COUNT(*) FROM fiscal_document_versions WHERE id = $1", bound_version
        ) == 1
        assert await connection.fetchval(
            "SELECT COUNT(*) FROM fiscal_document_versions WHERE id = $1", free_version
        ) == 0


def _insert_sql(slot_key: str) -> str:
    """槽位身份唯一性用例的最小插入语句。

    slot_key 直接拼进 SQL 只是为了让每条用例一眼能看出插的是哪条；
    值全部来自本文件内的常量，不含外部输入。
    """
    return f"""
        INSERT INTO material_slots (
            slot_key, subject_org_id, subject_org_name,
            subject_kind, subject_level, material_scope,
            report_kind, fiscal_year, mapping_key
        ) VALUES ('{slot_key}', $1, '某单位', $2, $2, $3, $4, $5, $6)
    """
