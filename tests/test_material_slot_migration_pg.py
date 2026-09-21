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

import uuid
from contextlib import asynccontextmanager

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
