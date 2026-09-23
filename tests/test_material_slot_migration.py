"""迁移 0019（material_slots）的安全性与幂等性。

本仓库的测试默认不连真实数据库，因此这里验证的是**静态可证的部分**：
语句是否全部可重复执行、有没有碰既有核心表的数据、唯一键与索引是否按设计存在。
"真库里第二次执行确实是 no-op"由 ``tests/test_material_slot_migration_pg.py``
在配置 ``GOVBUDGET_TEST_DATABASE_URL`` 时验证。两者分工明确，不能互相替代。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import pytest

from src.db.migrations import MIGRATIONS

MIGRATION_ID = "2026-09-21_0019_material_slots"

#: 迁移 0019 之前已经存在于生产库里的核心表。
#: 本轮只允许"新增"，不允许改动它们的数据。
PREEXISTING_CORE_TABLES = (
    "fiscal_documents",
    "fiscal_document_versions",
    "fiscal_table_cells",
    "fact_fiscal_line_items",
    "org_units",
    "org_department",
    "org_unit",
    "org_dept_annual_report",
    "analysis_jobs",
    "analysis_results",
    "issues",
    "organizations",
)


def _migration() -> Dict[str, Any]:
    for migration in MIGRATIONS:
        if migration["id"] == MIGRATION_ID:
            return migration
    raise AssertionError(f"migration {MIGRATION_ID} not registered")


def _sql_blob() -> str:
    return "\n".join(_migration()["sql"])


# ==== 注册与顺序 ============================================================


def test_migration_0019_registered_once():
    ids = [migration["id"] for migration in MIGRATIONS]
    assert ids.count(MIGRATION_ID) == 1


def test_migration_0019_runs_after_0018():
    ids = [migration["id"] for migration in MIGRATIONS]
    assert ids.index("2026-08-26_0018_report_scope_key") < ids.index(MIGRATION_ID)
    # 0019 曾经是最后一条。WP3-A 之后允许出现**更晚的附加迁移**（0020 复核生命周期），
    # 但绝不允许有编号更小的迁移插到它后面——那意味着历史迁移被改动过，
    # 而已入库的环境不会重放它，schema 会静默分叉。
    tail = ids[ids.index(MIGRATION_ID) + 1 :]
    assert all(item > MIGRATION_ID for item in tail), tail


def test_migration_ids_are_unique_and_ordered():
    ids = [migration["id"] for migration in MIGRATIONS]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids), "迁移必须按 id 字典序排列，否则执行顺序与编号不一致"


# ==== 幂等：每条语句都必须可重复执行 ========================================


@pytest.mark.parametrize("index", range(len(MIGRATIONS[-1]["sql"])))
def test_every_statement_is_idempotent(index: int):
    """逐条检查：不允许出现"再跑一次就报错"的语句。

    允许的形态只有四种：CREATE ... IF NOT EXISTS、ALTER TABLE ... ADD COLUMN
    IF NOT EXISTS、DROP INDEX IF EXISTS，以及不需要 IF NOT EXISTS 也能重复执行的
    CREATE OR REPLACE / SET 类语句。这里选择"白名单"而不是"黑名单"：
    新增语句时必须显式想清楚它能不能重放。
    """
    statement = MIGRATIONS[-1]["sql"][index]
    normalized = " ".join(statement.split())

    allowed_prefixes = (
        "CREATE EXTENSION IF NOT EXISTS",
        "CREATE TABLE IF NOT EXISTS",
        "CREATE UNIQUE INDEX IF NOT EXISTS",
        "CREATE INDEX IF NOT EXISTS",
        "ALTER TABLE",
        "DROP INDEX IF EXISTS",
    )
    assert normalized.startswith(allowed_prefixes), f"语句形态不在白名单内: {normalized[:80]}"

    if normalized.startswith("ALTER TABLE"):
        assert "IF NOT EXISTS" in normalized, f"ALTER TABLE 必须带 IF NOT EXISTS: {normalized[:120]}"


# ==== 安全性：不得改动既有数据 ==============================================


def test_migration_0019_contains_no_data_statements():
    """只允许 DDL。回填历史数据是独立动作，绝不混在迁移里自动执行。"""
    for statement in _migration()["sql"]:
        normalized = " ".join(statement.split()).upper()
        for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "TRUNCATE"):
            assert not normalized.startswith(forbidden), statement[:120]
            assert f"; {forbidden}" not in normalized, statement[:120]


def test_migration_0019_drops_nothing_existing():
    sql = _sql_blob().upper()
    for table in PREEXISTING_CORE_TABLES:
        assert f"DROP TABLE {table.upper()}" not in sql
        assert f"DROP TABLE IF EXISTS {table.upper()}" not in sql
    # 唯一允许的 DROP 是索引，且必须带 IF EXISTS
    for match in re.finditer(r"DROP\s+(\w+)\s+(?:IF EXISTS\s+)?(\w+)", sql):
        assert match.group(1) == "INDEX", match.group(0)
        assert "IF EXISTS" in match.group(0), match.group(0)


def test_migration_0019_does_not_recreate_production_schema():
    """禁止 DROP SCHEMA / CREATE SCHEMA：迁移只能往既有 schema 里加东西。"""
    sql = _sql_blob().upper()
    assert "DROP SCHEMA" not in sql
    assert "CREATE SCHEMA" not in sql
    assert "ALTER TABLE FISCAL_DOCUMENTS " not in sql, "不得改动 fiscal_documents 结构"


# ==== 结构与约束 ============================================================


def test_material_slots_table_and_identity_keys_exist():
    sql = _sql_blob()
    assert "CREATE TABLE IF NOT EXISTS material_slots" in sql
    # 身份键：列级唯一 + 复合唯一索引双重保证
    assert "slot_key TEXT NOT NULL UNIQUE" in sql
    assert "uq_material_slots_identity" in sql
    assert (
        "subject_org_id, subject_kind, material_scope, report_kind, "
        "COALESCE(fiscal_year, -1), mapping_key"
    ) in sql


def test_material_sources_table_and_keys_exist():
    sql = _sql_blob()
    assert "CREATE TABLE IF NOT EXISTS material_sources" in sql
    assert "uq_material_sources_slot_url" in sql
    # 无 URL 的手工来源要多行共存，必须用 COALESCE 折叠 NULL
    assert "COALESCE(source_url, '')" in sql


def test_version_binding_is_nullable_and_keeps_versions_on_slot_delete():
    sql = _sql_blob()
    assert "ADD COLUMN IF NOT EXISTS slot_id UUID" in sql
    assert "REFERENCES material_slots(id) ON DELETE SET NULL" in sql
    # 版本绑定列必须是可空的：身份不可靠的历史版本不允许被强行归属。
    binding_statement = next(
        statement
        for statement in _migration()["sql"]
        if "ADD COLUMN IF NOT EXISTS slot_id" in statement
    )
    assert "NOT NULL" not in " ".join(binding_statement.split()), (
        "fiscal_document_versions.slot_id 必须可空"
    )


def test_status_vocabulary_matches_domain_module():
    """数据库 CHECK 与 Python 取值域必须一致，否则会出现"代码认为合法、库拒绝"。"""
    from src.schemas.material_slot import (
        APPLICABILITY_STATUSES,
        CALIBERS,
        MATERIAL_SCOPES,
        SLOT_REPORT_KINDS,
        SLOT_STATUSES,
        SUBJECT_KINDS,
    )

    sql = _sql_blob()
    for value in SLOT_STATUSES:
        assert f"'{value}'" in sql, f"槽位状态 {value} 未出现在 CHECK 约束里"
    for value in SLOT_REPORT_KINDS:
        assert f"'{value}'" in sql, f"文种 {value} 未出现在 CHECK 约束里"
    for value in SUBJECT_KINDS:
        assert f"'{value}'" in sql, f"主体层级 {value} 未出现在 CHECK 约束里"
    for value in MATERIAL_SCOPES:
        assert f"'{value}'" in sql, f"材料范围 {value} 未出现在 CHECK 约束里"
    for value in CALIBERS:
        assert f"'{value}'" in sql, f"口径 {value} 未出现在 CHECK 约束里"
    for value in APPLICABILITY_STATUSES:
        assert f"'{value}'" in sql, f"适用性 {value} 未出现在 CHECK 约束里"


def test_ledger_query_indexes_exist():
    sql = _sql_blob()
    for index_name in (
        "idx_material_slots_subject",
        "idx_material_slots_department",
        "idx_material_slots_jurisdiction",
        "idx_material_slots_scope",
        "idx_material_slots_status",
        "idx_material_slots_updated",
        "idx_material_slots_due",
        "idx_material_sources_slot",
        "idx_document_versions_slot",
    ):
        assert index_name in sql, f"缺少台账查询索引 {index_name}"


def test_current_version_pointer_does_not_cascade_delete_versions():
    sql = _sql_blob()
    assert "current_document_version_id INTEGER" in sql
    assert "REFERENCES fiscal_document_versions(id) ON DELETE SET NULL" in sql


# ==== run_migrations 的第二次执行必须是 no-op ==============================


class _FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeMigrationConn:
    """只实现 run_migrations 用到的那几条语句。"""

    def __init__(self, applied: set) -> None:
        self.applied = applied
        self.executed: List[str] = []

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append(sql)
        if "INSERT INTO" in sql and "schema_migrations" in sql and args:
            self.applied.add(args[0])
        return "OK"

    async def fetch(self, sql: str, *args: Any) -> List[Dict[str, Any]]:
        if "schema_migrations" in sql:
            return [{"id": entry} for entry in sorted(self.applied)]
        return []

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()


class _FakePool:
    def __init__(self, conn: _FakeMigrationConn) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc_info):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_second_run_is_a_no_op(monkeypatch):
    """同一套迁移跑两遍：第二遍不允许执行任何迁移语句。

    这条用假连接验证的是 ``run_migrations`` 的"已应用则跳过"逻辑；
    真库上"语句本身也能重放"由 opt-in 的真实库测试覆盖。
    """
    from src.db import migrations as migrations_module
    from src.db.connection import DatabaseConnection

    applied: set = set()
    conn = _FakeMigrationConn(applied)
    monkeypatch.setattr(DatabaseConnection, "get_pool", classmethod(lambda cls: _wrap(conn)))
    monkeypatch.setattr(DatabaseConnection, "get_schema", classmethod(lambda cls: "public"))

    await migrations_module.run_migrations()
    first_pass = list(conn.executed)
    assert any(MIGRATION_ID in sql or "schema_migrations" in sql for sql in first_pass)
    assert applied == {migration["id"] for migration in MIGRATIONS}

    conn.executed.clear()
    await migrations_module.run_migrations()
    # 第二遍只允许有"设置 search_path + 建迁移表 + 查已应用"这类语句，
    # 不允许再执行任何迁移 SQL。逐条列出允许形态而不是"看有没有包含关键字"，
    # 否则任何新语句只要夹带 CREATE TABLE 字样就能蒙混过关。
    for sql in conn.executed:
        normalized = " ".join(sql.split())
        assert normalized.startswith(
            ("SET search_path", "CREATE TABLE IF NOT EXISTS", "SELECT id FROM")
        ), normalized[:120]
        assert MIGRATION_ID not in normalized
    assert applied == {migration["id"] for migration in MIGRATIONS}


async def _wrap(conn):
    return _FakePool(conn)
