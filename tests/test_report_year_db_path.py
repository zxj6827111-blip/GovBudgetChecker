"""DB 年份兜底 2000 修复测试（Task 4，B-02 / P0-03）。

断言意图：
1. `_parse_year` 识别失败返回 None，不再返回 2000（这是缺口的直接复现点）；
2. 结构化入库路径与内存路径（runtime.parse_report_year）逐例同源，防止再次漂移；
3. `_ensure_document_version` 在年份为 None 时把 NULL 传给 DB，
   且 ON CONFLICT 用 COALESCE 表达式，不用裸 fiscal_year（多 NULL 互不冲突会造重复行）；
4. `_upsert_report` 同上；
5. 迁移 0017 存在且包含放开 NOT NULL、建表达式唯一索引、删旧约束三件事，
   且不含自动改写历史数据的 UPDATE（不可逆操作不静默执行）。
"""

from pathlib import Path

import pytest

from api import runtime
from src.db.migrations import MIGRATIONS
from src.services.ps_schema_sync import PSSharedSchemaSync
from src.services.structured_ingest_runner import _ensure_document_version, _parse_year

MIGRATION_ID = "2026-08-26_0017_nullable_report_year"


@pytest.mark.parametrize(
    "raw",
    [None, "", "   ", "无法识别", "未知年度", "abcd", "1999", "2100", {}, []],
)
def test_parse_year_returns_none_instead_of_2000(raw):
    assert _parse_year(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (2025, 2025),
        ("2025", 2025),
        ("2025年部门预算", 2025),
        ("25 budget", 2025),
        ("office-24 final.pdf", 2024),
    ],
)
def test_parse_year_still_recognizes_valid_years(raw, expected):
    assert _parse_year(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "无法识别",
        "1999",
        "2100",
        2025,
        "2025",
        "2025年部门决算",
        "25 budget",
        "office-24 final.pdf",
        "普陀区某单位2023年度预算公开.pdf",
    ],
)
def test_db_path_and_memory_path_agree(raw):
    # 两条路径必须逐例一致，否则 B-02 会以另一种形式复发
    assert _parse_year(raw) == runtime.parse_report_year(raw)


class _RecordingConn:
    """记录 SQL 与参数的最小假连接。"""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []
        self._next_id = 100

    async def fetchval(self, query: str, *args):
        self.calls.append((query, args))
        self._next_id += 1
        return self._next_id

    async def execute(self, query: str, *args):
        self.calls.append((query, args))
        return "OK"

    def find(self, needle: str):
        for query, args in self.calls:
            if needle in query:
                return query, args
        raise AssertionError(f"no SQL containing {needle!r}; got {[c[0][:60] for c in self.calls]}")


@pytest.mark.asyncio
async def test_ensure_document_version_passes_null_year(tmp_path):
    conn = _RecordingConn()
    await _ensure_document_version(
        conn=conn,
        org_name="某区某单位",
        fiscal_year=None,
        doc_type="budget",
        checksum="deadbeef",
        storage_key="uploads/job-1/source.pdf",
        storage_backend="filesystem",
        original_filename="source.pdf",
        file_size_bytes=1024,
        content_type="application/pdf",
    )
    query, args = conn.find("INSERT INTO fiscal_documents")
    # 年份必须以 None（-> SQL NULL）传入，不能被兜底成 2000
    assert args[1] is None
    assert 2000 not in args
    # 冲突目标必须是 COALESCE 表达式，否则未知年份会不断新增重复行
    assert "COALESCE(fiscal_year, -1)" in query
    assert "ON CONFLICT (org_unit_id, fiscal_year, doc_type)" not in query


@pytest.mark.asyncio
async def test_ensure_document_version_still_passes_known_year(tmp_path):
    conn = _RecordingConn()
    await _ensure_document_version(
        conn=conn,
        org_name="某区某单位",
        fiscal_year=2025,
        doc_type="final",
        checksum="cafe",
        storage_key="uploads/job-2/source.pdf",
        storage_backend="filesystem",
        original_filename="source.pdf",
        file_size_bytes=2048,
        content_type="application/pdf",
    )
    _query, args = conn.find("INSERT INTO fiscal_documents")
    assert args[1] == 2025


@pytest.mark.asyncio
async def test_upsert_report_is_null_year_safe(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    conn = _RecordingConn()
    sync = PSSharedSchemaSync(conn)

    await sync._upsert_report(
        department_id="dept-1",
        unit_id="unit-1",
        fiscal_year=None,
        report_type="budget",
        pdf_path=pdf_path,
        checksum="hash-a",
    )
    query, args = conn.find("INSERT INTO org_dept_annual_report")
    assert args[2] is None
    assert "COALESCE(year, -1)" in query
    assert "ON CONFLICT (department_id, unit_id, year, report_type)" not in query


def _migration_0017():
    for migration in MIGRATIONS:
        if migration["id"] == MIGRATION_ID:
            return migration
    raise AssertionError(f"migration {MIGRATION_ID} not registered")


def test_migration_0017_is_registered():
    ids = [migration["id"] for migration in MIGRATIONS]
    assert MIGRATION_ID in ids
    assert len(ids) == len(set(ids))


def test_migration_0017_drops_not_null_on_all_year_columns():
    sql = " ".join(_migration_0017()["sql"])
    for table, column in (
        ("fiscal_documents", "fiscal_year"),
        ("org_dept_annual_report", "year"),
        ("org_dept_table_data", "year"),
        ("org_dept_line_items", "year"),
    ):
        assert f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL" in sql


def test_migration_0017_creates_coalesce_unique_indexes():
    sql = " ".join(_migration_0017()["sql"])
    assert "uq_fiscal_documents_org_year_type" in sql
    assert "COALESCE(fiscal_year, -1)" in sql
    assert "uq_dept_report_scope_type" in sql
    assert "COALESCE(year, -1)" in sql


def test_migration_0017_drops_old_unique_constraints_by_definition():
    statements = _migration_0017()["sql"]
    # 约束名由 Postgres 自动生成并可能被截断，必须按定义反查而不是硬编码名字
    do_blocks = [s for s in statements if "DO $$" in s]
    assert len(do_blocks) == 2
    joined = " ".join(do_blocks)
    assert "pg_get_constraintdef" in joined
    assert "UNIQUE (org_unit_id, fiscal_year, doc_type)" in joined
    assert "UNIQUE (department_id, unit_id, year, report_type)" in joined


def test_migration_0017_does_not_silently_rewrite_history():
    # 2000 -> NULL 的回填不可逆，只能人工执行，不许写进自动迁移
    sql = " ".join(_migration_0017()["sql"]).upper()
    assert "UPDATE " not in sql
    assert "DELETE " not in sql


def test_migration_0017_creates_index_before_dropping_constraint():
    statements = _migration_0017()["sql"]
    first_create = min(i for i, s in enumerate(statements) if "CREATE UNIQUE INDEX" in s)
    first_drop = min(i for i, s in enumerate(statements) if "DROP CONSTRAINT" in s)
    assert first_create < first_drop


def test_migration_rollback_doc_exists():
    # 相对测试文件定位，避免依赖 pytest 的工作目录（CI 与本地 CWD 可能不同）
    doc = Path(__file__).resolve().parent.parent / "docs" / "MIGRATION_0017_NULLABLE_YEAR.md"
    assert doc.exists()
    text = doc.read_text(encoding="utf-8")
    assert MIGRATION_ID in text
    # 迁移类改动必须同时留下回滚方式与实测结论，否则无法支撑发布决策
    assert "回滚" in text
    assert "实测验证记录" in text
    assert "PostgreSQL 15.17" in text
