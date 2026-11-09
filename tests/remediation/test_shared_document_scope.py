"""同名部门/单位的已发布快照必须可共存，仍禁止覆盖另一文档的专属槽。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
import uuid

import pytest
from src.services.ps_schema_sync import PSSharedSchemaSync


@pytest.mark.asyncio
@pytest.mark.parametrize('values,expected', [
    ([7, None], ('', None)),
    ([7, 'existing', False], ('', 'existing')),
    ([7, 'unit-report', True, None], ('fiscal-document:7', None)),
    ([7, 'unit-report', True, 'dept-report', False], ('fiscal-document:7', 'dept-report')),
])
async def test_snapshot_target_preserves_existing_owner_or_allocates_stable_document_slot(values, expected):
    conn = SimpleNamespace(fetchval=AsyncMock(side_effect=values), execute=AsyncMock())
    actual = await PSSharedSchemaSync(conn)._snapshot_report_target('dept', 'unit', 2025, 'FINAL', '', 9)
    assert actual == expected
    assert 'pg_advisory_xact_lock' in conn.execute.await_args.args[0]
    # 改用文档槽只由已发布文档身份冲突触发，不依赖金额或科目相似。
    if expected[0]:
        assert conn.fetchval.await_args_list[-1].args[-1] in (7, 'fiscal-document:7')


@pytest.mark.asyncio
@pytest.mark.parametrize('values,message', [
    ([None], 'no fiscal document'),
    ([7, 'foreign-base', True, 'foreign-private', True], 'different fiscal document'),
])
async def test_missing_identity_and_conflicting_document_slot_still_fail_closed(values, message):
    conn = SimpleNamespace(fetchval=AsyncMock(side_effect=values), execute=AsyncMock())
    with pytest.raises(ValueError, match=message):
        await PSSharedSchemaSync(conn)._snapshot_report_target('dept', 'unit', 2025, 'FINAL', '', 9)


@pytest.mark.real_database
@pytest.mark.asyncio
async def test_postgres_distinct_documents_share_names_but_keep_separate_reports(real_database_url):
    import asyncpg
    conn = await asyncpg.connect(real_database_url)
    try:
        async with conn.transaction():
            # 会话临时表遮蔽公共表；测试不会改动历史报告和生产数据库。
            await conn.execute('''CREATE TEMP TABLE fiscal_document_versions(id int,document_id int) ON COMMIT DROP;
                CREATE TEMP TABLE org_dept_annual_report(id uuid,department_id uuid,unit_id uuid,year int,
                  report_type text,scope_key text) ON COMMIT DROP;
                CREATE TEMP TABLE published_fact_projections(document_id int,receipt jsonb) ON COMMIT DROP''')
            dept, unit, original, independent = [uuid.uuid4() for _ in range(4)]
            await conn.execute('INSERT INTO fiscal_document_versions VALUES(11,1),(22,2),(23,2)')
            await conn.execute("INSERT INTO org_dept_annual_report VALUES($1,$2,$3,2025,'FINAL','')", original, dept, unit)
            await conn.execute('INSERT INTO published_fact_projections VALUES(1,$1::jsonb)', json.dumps({'shared_report_id':str(original)}))
            sync = PSSharedSchemaSync(conn)
            assert await sync._snapshot_report_target(dept,unit,2025,'FINAL','',11) == ('',original)
            key, prior = await sync._snapshot_report_target(dept,unit,2025,'FINAL','',22)
            assert key == 'fiscal-document:2' and prior is None
            await conn.execute("INSERT INTO org_dept_annual_report VALUES($1,$2,$3,2025,'FINAL',$4)", independent,dept,unit,key)
            await conn.execute('INSERT INTO published_fact_projections VALUES(2,$1::jsonb)',json.dumps({'shared_report_id':str(independent)}))
            # 新版本仍指向同一财政文档，重跑不能每次再创建一个共享报告。
            assert await sync._snapshot_report_target(dept,unit,2025,'FINAL','',23) == (key,independent)
            assert await sync._snapshot_report_target(dept,unit,2025,'FINAL','',11) == ('',original)
            assert await conn.fetchval('SELECT count(*) FROM published_fact_projections') == 2
    finally:
        await conn.close()
