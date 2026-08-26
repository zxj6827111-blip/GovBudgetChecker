"""report_id 残留碰撞收敛测试（Task 5，P0-09）。

背景：唯一约束 (department_id, unit_id, year, report_type) + ON CONFLICT upsert，
在"组织只匹配到 fallback_name"或"年度无法识别"时不足以唯一标识一份材料，
不同文档会被强行并进同一个 report_id。

断言意图：
1. 组织与年份都可靠 -> scope_key 为空，保持原有按 (dept,unit,year,type) 归并；
2. match_mode=fallback_name 或年份为 None -> scope_key 取 checksum；
3. 两份不同 checksum、同 fallback 组织、未知年份 -> 落在不同的唯一键上，
   拿到两个不同 report_id（原来会被并成一个）；
4. 同一份文档重复入库仍然只有一行（不能因为改了维度就退化成每次新增）；
5. 身份维度退化时打出可检索的审计日志；
6. 迁移 0018 加了 scope_key 列、建了含 scope_key 的唯一索引、
   并删除被取代的 0017 索引。
"""

import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.db.migrations import MIGRATIONS
from src.services.ps_schema_sync import PSSharedSchemaSync

MIGRATION_ID = "2026-08-26_0018_report_scope_key"


class _FakeReportTable:
    """按 (dept, unit, COALESCE(year,-1), report_type, scope_key) 唯一键模拟 upsert。"""

    def __init__(self):
        self.rows: dict[tuple, str] = {}
        self.insert_count = 0

    async def fetchval(self, query: str, *args):
        assert "INSERT INTO org_dept_annual_report" in query
        assert "COALESCE(year, -1), report_type, scope_key" in query
        department_id, unit_id, year, report_type = args[0], args[1], args[2], args[3]
        scope_key = args[8]
        key = (department_id, unit_id, -1 if year is None else year, report_type, scope_key)
        if key not in self.rows:
            self.insert_count += 1
            self.rows[key] = f"report-{self.insert_count}"
        return self.rows[key]


def _write_pdf(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    return path


@pytest.mark.parametrize(
    "match_mode",
    ["organization_id", "name_unit", "name_unit_promoted", "name_department"],
)
def test_confident_scope_keeps_original_merge_dimension(match_mode, tmp_path):
    sync = PSSharedSchemaSync(_FakeReportTable())
    scope_key = sync._resolve_report_scope_key(
        match_mode=match_mode,
        fiscal_year=2025,
        checksum="hash-a",
        pdf_path=_write_pdf(tmp_path, "a.pdf"),
    )
    assert scope_key == ""


def test_fallback_name_switches_to_document_dimension(tmp_path):
    sync = PSSharedSchemaSync(_FakeReportTable())
    scope_key = sync._resolve_report_scope_key(
        match_mode="fallback_name",
        fiscal_year=2025,
        checksum="hash-a",
        pdf_path=_write_pdf(tmp_path, "a.pdf"),
    )
    assert scope_key == "hash-a"


def test_unknown_year_switches_to_document_dimension(tmp_path):
    sync = PSSharedSchemaSync(_FakeReportTable())
    scope_key = sync._resolve_report_scope_key(
        match_mode="organization_id",
        fiscal_year=None,
        checksum="hash-a",
        pdf_path=_write_pdf(tmp_path, "a.pdf"),
    )
    assert scope_key == "hash-a"


def test_missing_checksum_falls_back_to_filename(tmp_path):
    sync = PSSharedSchemaSync(_FakeReportTable())
    scope_key = sync._resolve_report_scope_key(
        match_mode="fallback_name",
        fiscal_year=None,
        checksum="",
        pdf_path=_write_pdf(tmp_path, "某单位预算.pdf"),
    )
    assert scope_key == "某单位预算.pdf"


@pytest.mark.asyncio
async def test_two_distinct_documents_get_distinct_report_ids(tmp_path):
    """核心回归：同 fallback 组织 + 未知年份 + 不同 checksum -> 两个 report_id。"""
    table = _FakeReportTable()
    sync = PSSharedSchemaSync(table)

    first = await sync._upsert_report(
        department_id="dept-1",
        unit_id="unit-1",
        fiscal_year=None,
        report_type="budget",
        pdf_path=_write_pdf(tmp_path, "doc-a.pdf"),
        checksum="hash-a",
        scope_key="hash-a",
    )
    second = await sync._upsert_report(
        department_id="dept-1",
        unit_id="unit-1",
        fiscal_year=None,
        report_type="budget",
        pdf_path=_write_pdf(tmp_path, "doc-b.pdf"),
        checksum="hash-b",
        scope_key="hash-b",
    )

    assert first != second
    assert table.insert_count == 2


@pytest.mark.asyncio
async def test_same_document_reingested_stays_single_row(tmp_path):
    # 改了维度也不能退化成"每次入库都新增一行"
    table = _FakeReportTable()
    sync = PSSharedSchemaSync(table)
    pdf_path = _write_pdf(tmp_path, "doc-a.pdf")

    ids = set()
    for _ in range(3):
        ids.add(
            await sync._upsert_report(
                department_id="dept-1",
                unit_id="unit-1",
                fiscal_year=None,
                report_type="budget",
                pdf_path=pdf_path,
                checksum="hash-a",
                scope_key="hash-a",
            )
        )
    assert len(ids) == 1
    assert table.insert_count == 1


@pytest.mark.asyncio
async def test_confident_scope_still_merges_different_files(tmp_path):
    # 组织与年份可靠时保持原语义：同一 (dept,unit,year,type) 的新版本覆盖旧行
    table = _FakeReportTable()
    sync = PSSharedSchemaSync(table)

    first = await sync._upsert_report(
        department_id="dept-1",
        unit_id="unit-1",
        fiscal_year=2025,
        report_type="budget",
        pdf_path=_write_pdf(tmp_path, "v1.pdf"),
        checksum="hash-a",
        scope_key="",
    )
    second = await sync._upsert_report(
        department_id="dept-1",
        unit_id="unit-1",
        fiscal_year=2025,
        report_type="budget",
        pdf_path=_write_pdf(tmp_path, "v2.pdf"),
        checksum="hash-b",
        scope_key="",
    )
    assert first == second
    assert table.insert_count == 1


async def _run_sync(sync: PSSharedSchemaSync, scope: dict, pdf_path: Path, checksum: str, **kwargs):
    """驱动真实 sync() 流程，只把与本测试无关的 DB 交互替换掉。"""
    sync.resolve_scope = lambda **_kw: scope  # type: ignore[method-assign]
    sync._ensure_department = AsyncMock(return_value="dept-1")  # type: ignore[method-assign]
    sync._ensure_unit = AsyncMock(return_value="unit-1")  # type: ignore[method-assign]
    sync._sync_table_data = AsyncMock(return_value=0)  # type: ignore[method-assign]
    sync._sync_line_items = AsyncMock(return_value=0)  # type: ignore[method-assign]
    return await sync.sync(
        document_version_id=1,
        org_name="某区某单位",
        doc_type="budget",
        pdf_path=pdf_path,
        checksum=checksum,
        **kwargs,
    )


_FALLBACK_SCOPE = {
    "department_name": "某区某单位",
    "unit_name": "某区某单位",
    "department_code": None,
    "unit_code": None,
    "matched_organization_id": None,
    "match_mode": "fallback_name",
}
_CONFIDENT_SCOPE = {
    "department_name": "某区财政局",
    "unit_name": "某区财政局本级",
    "department_code": "D01",
    "unit_code": "U01",
    "matched_organization_id": "org-1",
    "match_mode": "organization_id",
}


@pytest.mark.asyncio
async def test_sync_audits_and_separates_fallback_reports(tmp_path, caplog):
    """端到端：同 fallback 组织 + 未知年份的两份文档，report_id 不再串行合并。"""
    table = _FakeReportTable()
    with caplog.at_level(logging.WARNING, logger="src.services.ps_schema_sync"):
        first = await _run_sync(
            PSSharedSchemaSync(table),
            _FALLBACK_SCOPE,
            _write_pdf(tmp_path, "doc-a.pdf"),
            "hash-a",
            fiscal_year=None,
        )
        second = await _run_sync(
            PSSharedSchemaSync(table),
            _FALLBACK_SCOPE,
            _write_pdf(tmp_path, "doc-b.pdf"),
            "hash-b",
            fiscal_year=None,
        )

    assert first["report_id"] != second["report_id"]
    assert first["scope_key"] == "hash-a"
    assert first["report_identity_mode"] == "document"
    assert table.insert_count == 2
    # 审计日志必须由真实代码路径打出，且带上可检索的关键字段
    assert caplog.text.count("report identity fell back to document dimension") == 2
    assert "match_mode=fallback_name" in caplog.text
    assert "scope_key=hash-a" in caplog.text


@pytest.mark.asyncio
async def test_sync_keeps_scope_dimension_and_stays_quiet_when_confident(tmp_path, caplog):
    table = _FakeReportTable()
    with caplog.at_level(logging.WARNING, logger="src.services.ps_schema_sync"):
        first = await _run_sync(
            PSSharedSchemaSync(table),
            _CONFIDENT_SCOPE,
            _write_pdf(tmp_path, "v1.pdf"),
            "hash-a",
            fiscal_year=2025,
            organization_id="org-1",
        )
        second = await _run_sync(
            PSSharedSchemaSync(table),
            _CONFIDENT_SCOPE,
            _write_pdf(tmp_path, "v2.pdf"),
            "hash-b",
            fiscal_year=2025,
            organization_id="org-1",
        )

    # 组织与年份可靠时保持原语义：新版本覆盖同一行，不产生额外 report_id
    assert first["report_id"] == second["report_id"]
    assert first["scope_key"] == ""
    assert first["report_identity_mode"] == "scope"
    assert table.insert_count == 1
    assert "fell back to document dimension" not in caplog.text


def _migration_0018():
    for migration in MIGRATIONS:
        if migration["id"] == MIGRATION_ID:
            return migration
    raise AssertionError(f"migration {MIGRATION_ID} not registered")


def test_migration_0018_adds_scope_key_column():
    sql = " ".join(_migration_0018()["sql"])
    assert "ADD COLUMN IF NOT EXISTS scope_key TEXT NOT NULL DEFAULT ''" in sql


def test_migration_0018_unique_index_includes_scope_key():
    sql = " ".join(_migration_0018()["sql"])
    assert "uq_dept_report_scope_type_key" in sql
    assert "(department_id, unit_id, COALESCE(year, -1), report_type, scope_key)" in sql


def test_migration_0018_drops_superseded_index():
    sql = " ".join(_migration_0018()["sql"])
    # 0017 的索引是新索引去掉 scope_key 的前缀，留着会阻止按文档区分的多行插入
    assert "DROP INDEX IF EXISTS uq_dept_report_scope_type" in sql


def test_migration_0018_runs_after_0017():
    ids = [migration["id"] for migration in MIGRATIONS]
    assert ids.index("2026-08-26_0017_nullable_report_year") < ids.index(MIGRATION_ID)
