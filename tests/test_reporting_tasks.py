"""年度任务验收：纯口径与随机 PostgreSQL schema，绝不连接业务库。"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
import json
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient, ASGITransport

from src.services.reporting_task_model import candidate_roster, default_components, roster_errors, region_members, classify_item, summarize, filter_items
from src.services.reporting_task_service import ReportingTaskService
from src.services import issue_workflow_store
from tests.test_material_ledger_pg import ledger_db, _conn
from tests.test_material_ledger_detail_pg import _insert_version, _insert_job

CATALOG = [
    dict(id='city',name='上海市',level='city',parent_id=None),
    dict(id='city-dept',name='市财政局',level='department',parent_id='city'),
    dict(id='city-unit',name='市财政局',level='unit',parent_id='city-dept'),
    dict(id='district',name='普陀区',level='district',parent_id='city'),
    dict(id='district-dept',name='区财政局',level='department',parent_id='district'),
    dict(id='district-unit',name='区财政局',level='unit',parent_id='district-dept'),
    dict(id='district-unit2',name='区服务中心',level='unit',parent_id='district-dept'),
]
NOW=datetime.now(timezone.utc)


def test_region_boundary_candidates_and_unknowns():
    assert {o['id'] for o in region_members('city',CATALOG)}=={'city-dept','city-unit'}
    rows=candidate_roster('city',CATALOG,default_components(2026))
    assert len(rows)==4 and len({r['key'] for r in rows})==4
    assert {r['fiscal_year'] for r in rows}=={2025,2026}
    assert len(roster_errors(rows,CATALOG,'city',default_components(2026)))==4
    assert summarize([],False)['expected'] is None
    assert summarize([],True)['coverage_rate'] is None
    for r in rows:r['decision']='required'
    assert not roster_errors(rows,CATALOG,'city',default_components(2026))
    assert len({r['subject_kind'] for r in rows if r['subject_org_name']=='市财政局'})==2
    rows[0]['decision']='excluded'
    assert roster_errors(rows,CATALOG,'city',default_components(2026))


def test_unpublished_observations_do_not_invent_submission_obligations():
    observed = [dict(organization_id='city-unit', department_id='city-dept',
                     organization_name='市财政局', department_name='市财政局',
                     subject_kind='unit', uploaded=True, analyzed=True,
                     review_status='completed', confirmed_count=2, pending_count=1,
                     pending_checks=0, status='completed')]
    summary = summarize([], False, observed_items=observed)
    assert (summary['uploaded'], summary['analyzed'], summary['reviewed'], summary['confirmed'], summary['pending']) == (1, 1, 1, 2, 1)
    for key in ['expected', 'missing', 'coverage_rate', 'overdue_missing', 'empty_departments', 'partial_departments', 'complete_units']:
        assert summary[key] is None, key
    for status in ['empty', 'partial', 'complete', 'unit_complete', 'missing', 'overdue']:
        assert filter_items(observed, status=status, keyword='', department_id='', baseline_ready=False) == []
    assert filter_items(observed, status='received', keyword='', department_id='', baseline_ready=False) == observed
    empty = summarize([], False, observed_items=[])
    assert empty['uploaded'] == empty['analyzed'] == empty['reviewed'] == 0
    assert empty['coverage_rate'] is None


@pytest.mark.real_database
@pytest.mark.asyncio
async def test_directory_scope_lists_all_expected_and_missing_with_current_review_facts(ledger_db, monkeypatch, tmp_path):  # noqa: F811 -- 复用导入的 pytest fixture
    from tests.test_material_ledger_pg import _insert_slot
    schema, pool = ledger_db
    monkeypatch.setattr(issue_workflow_store, '_store_path', lambda: tmp_path/'workflow.json')
    async with _conn(schema, pool) as conn:
        s = ReportingTaskService(conn, CATALOG)
        task = await s.create('city', 2026, 'admin')
        draft = await s.generate(str(task['id']), task['draft_revision'])
        async def slot(org, kind='final', year=2025, region='city', department='city-dept'):
            return await _insert_slot(conn, subject_org_id=org, subject_org_name=org,
                subject_kind='department' if org.endswith('-dept') else 'unit',
                jurisdiction_org_id=region, department_org_id=department,
                department_name=department, fiscal_year=year, report_kind=kind,
                material_scope='department_summary' if org.endswith('-dept') else 'unit_self',
                caliber='unknown' if org.endswith('-dept') else 'self')
        reviewed = await slot('city-dept')
        version = await receive(conn, {'id':reviewed}, 'observed-reviewed', 'completed')
        job = await _insert_job(conn, job_uuid='observed-reviewed', metadata={'structured_ingest':{'document_version_id':version}})
        await conn.execute('UPDATE analysis_jobs SET analysis_revision=1 WHERE id=$1', job)
        await conn.execute("INSERT INTO analysis_results(job_id,rule_findings,raw_response) VALUES($1,'[]','{}')", job)
        findings = [{'issue_id':'one','title':'已确认问题一','severity':'high'}, {'issue_id':'two','title':'已确认问题二','severity':'medium'}]
        await conn.execute("INSERT INTO review_sessions(slot_id,document_version_id,analysis_job_uuid,analysis_basis_token,status,started_by,completed_by,completed_at,review_result) VALUES($1::uuid,$2,'observed-reviewed','observed-reviewed:1','completed','admin','admin',NOW(),$3::jsonb)", reviewed, version, json.dumps({'confirmed_findings':findings}))
        pending = await slot('city-unit')
        pending_version = await receive(conn, {'id':pending}, 'observed-pending')
        pending_job = await _insert_job(conn, job_uuid='observed-pending', metadata={'structured_ingest':{'document_version_id':pending_version}})
        await conn.execute('UPDATE analysis_jobs SET analysis_revision=1 WHERE id=$1', pending_job)
        await conn.execute("INSERT INTO analysis_results(job_id,ai_findings,raw_response) VALUES($1,$2::jsonb,'{}')", pending_job, json.dumps([{'id':'lead','title':'待复核线索','severity':'high'}]))
        await slot('city-unit', 'budget', 2026)  # 有无槽位都不影响目录应交分母。
        other = await slot('district-unit', region='district', department='district-dept')
        await receive(conn, {'id':other}, 'different-region')
        data = await s.statistics(str(task['id']), visible_ids=None, view='departments')
        assert data['meta']['baseline_ready'] and data['meta']['data_basis'] == 'organization_catalog'
        assert not data['meta']['roster_published'] and not data['meta']['catalog_changed']
        assert (data['summary']['uploaded'], data['summary']['analyzed'], data['summary']['reviewed'], data['summary']['confirmed'], data['summary']['pending']) == (2, 2, 1, 2, 1)
        assert (data['summary']['expected'], data['summary']['missing'], data['summary']['coverage_rate']) == (4, 2, .5)
        assert data['summary']['extra_uploaded'] == 0
        assert data['summary']['due_unknown_missing'] == 2 and data['summary']['overdue_missing'] is None
        assert (data['summary']['department_count'], data['summary']['unit_count']) == (1, 1)
        assert data['list']['total'] == 1 and data['list']['items'][0]['uploaded'] == 2
        assert {c['report_kind']:c['summary']['uploaded'] for c in data['components']} == {'budget':0, 'final':2}
        for view, total in [('subjects',4), ('issues',2), ('leads',1), ('extras',0)]:
            part = await s.statistics(str(task['id']), visible_ids=None, view=view, export=True, page_size=1)
            assert part['list']['total'] == total and len(part['export_rows']) == total, view
            assert part['summary'] == data['summary']
        waiting = await s.statistics(str(task['id']), visible_ids=None, status='waiting')
        assert waiting['list']['total'] == 1
        assert (await s.statistics(str(task['id']), visible_ids=None, status='analyzed'))['list']['total'] == 2
        assert (await s.statistics(str(task['id']), visible_ids=None, status='reviewed'))['list']['total'] == 1
        scoped = await s.statistics(str(task['id']), visible_ids=['city-unit'])
        assert scoped['summary']['uploaded'] == 1 and scoped['summary']['confirmed'] == 0
        assert (scoped['summary']['department_count'], scoped['summary']['unit_count']) == (0, 1)
        assert scoped['list']['items'][0]['organization_id'] == 'city-unit'
        assert (await s.statistics(str(task['id']), visible_ids=None, status='missing'))['list']['total'] == 2
        assert (await s.statistics(str(task['id']), visible_ids=None, status='partial'))['list']['total'] == 4
        for status in ['overdue','complete','unit_complete','empty']:
            assert (await s.statistics(str(task['id']), visible_ids=None, status=status))['list']['total'] == 0
        missing = await s.statistics(str(task['id']), visible_ids=None, status='missing', export=True, page_size=1)
        assert len(missing['export_rows']) == 2 and missing['list']['total'] == 2
        assert all(r['expected'] == r['missing'] == 1 and r['overdue'] is None for r in missing['export_rows'])
        assert {r['organization_id'] for r in missing['export_rows']} == {'city-dept', 'city-unit'}
        assert all(r['report_kind'] == 'budget' and not r['uploaded'] for r in missing['export_rows'])
        assert (await s.statistics(str(task['id']), visible_ids=None, component='final'))['summary']['missing'] == 0
        assert (await s.statistics(str(task['id']), visible_ids=[]))['summary']['expected'] == 0
        assert (await s.get(str(task['id'])))['draft_roster'] == draft['items']
        assert await conn.fetchval('SELECT count(*) FROM supervision_baselines WHERE task_id=$1', task['id']) == 0
        # 目录新增单位立即进入默认应交范围，不修改草稿或建立槽位。
        enlarged = ReportingTaskService(conn, CATALOG + [dict(id='new-unit', name='新增单位', level='unit', parent_id='city-dept')])
        expanded = await enlarged.statistics(str(task['id']), visible_ids=None, status='missing')
        assert (expanded['summary']['expected'], expanded['summary']['uploaded'], expanded['summary']['missing']) == (6, 2, 4)
        assert expanded['list']['total'] == 4
        # 只用真实配置的截止时间判逾期。
        await conn.execute("UPDATE material_slots SET due_at=$1 WHERE subject_org_id='city-unit' AND report_kind='budget'", NOW-timedelta(days=1))
        overdue = await s.statistics(str(task['id']), visible_ids=None, status='overdue')
        assert overdue['summary']['overdue_missing'] == 1 and overdue['list']['total'] == 1
        assert overdue['summary']['due_unknown_missing'] == 1
        await conn.execute("UPDATE material_slots SET mapping_key='needs-check' WHERE id::text=$1", pending)
        conflicted = await s.statistics(str(task['id']), visible_ids=None, status='missing')
        assert (conflicted['summary']['expected'], conflicted['summary']['uploaded'], conflicted['summary']['missing']) == (4, 1, 3)


async def publish(service,region,decide=None):
    task=await service.create(region,2026,'administrator')
    data=await service.generate(str(task['id']),task['draft_revision'])
    changes=[]
    for i,r in enumerate(data['items']):
        change=dict(key=r['key'],decision='required',reason='已逐项核对组织目录')
        if decide:change.update(decide(i,r))
        changes.append(change)
    data=await service.edit(str(task['id']),data['task']['draft_revision'],changes)
    preview=await service.preview(await service.get(str(task['id'])))
    assert preview['valid'],preview
    await service.publish(str(task['id']),data['task']['draft_revision'],preview['preview_token'],'已确认完整应交名单','administrator')
    return await service.get(str(task['id']))


async def receive(conn,slot,tag,status='review_required'):
    version=await _insert_version(conn,slot_id=str(slot['id']),file_hash=tag,created_at=NOW)
    await conn.execute('UPDATE material_slots SET current_document_version_id=$2,status=$3 WHERE id=$1',slot['id'],version,status)
    return version


@pytest.mark.real_database
@pytest.mark.asyncio
async def test_two_regions_missing_progress_and_filters(ledger_db,monkeypatch,tmp_path):  # noqa: F811 -- 复用导入的 pytest fixture
    schema,pool=ledger_db
    monkeypatch.setattr(issue_workflow_store,'_store_path',lambda:tmp_path/'workflow.json')
    async with _conn(schema,pool) as conn:
        s=ReportingTaskService(conn,CATALOG)
        regions=await s.list_regions(2026,None,set())
        assert len(regions)==2 and all(r['task'] is None for r in regions)
        unconfirmed=await s.create('city',2026,'admin')
        unknown=await s.statistics(str(unconfirmed['id']),visible_ids=None)
        assert unknown['summary']['missing'] == unknown['summary']['expected'] == 4
        assert unknown['meta']['data_basis'] == 'organization_catalog'
        assert unknown['list']['total'] == 4 and unknown['summary']['uploaded'] == 0
        assert await conn.fetchval('SELECT count(*) FROM material_slots') == 0, 'GET 不写入目录虚拟应交行'
        city=await publish(s,'city')
        district=await publish(s,'district',lambda i,r:{'due_at':(NOW-timedelta(days=2)).isoformat() if r['subject_org_id']=='district-dept' else None})
        cityslots=await conn.fetch('SELECT * FROM material_slots WHERE jurisdiction_org_id=$1 ORDER BY subject_org_id,report_kind','city')
        districtslots=await conn.fetch('SELECT * FROM material_slots WHERE jurisdiction_org_id=$1 ORDER BY subject_org_id,report_kind','district')
        for i,slot in enumerate(cityslots[:3]):await receive(conn,slot,f'city{i}',status=['processing','completed','failed'][i])
        for i,slot in enumerate(districtslots[:2]):await receive(conn,slot,f'district{i}')
        a=await s.statistics(str(city['id']),visible_ids=None)
        b=await s.statistics(str(district['id']),visible_ids=None)
        assert (a['summary']['expected'],a['summary']['uploaded'],a['summary']['missing'])==(4,3,1)
        assert (b['summary']['expected'],b['summary']['uploaded'],b['summary']['missing'])==(6,2,4)
        assert b['summary']['partial_departments']==1
        assert a['summary']['confirmed'] is None,'未分析不是零问题'
        assert (await s.statistics(str(city['id']),visible_ids=None,status='received'))['list']['total']==3
        assert b['summary']['overdue_missing']==0,'已交的部门不逾期；未知截止日期的单位不逾期'
        missing=await s.statistics(str(district['id']),visible_ids=None,status='missing',keyword='服务',page_size=1,export=True)
        assert missing['summary']==b['summary'],'列表过滤不得改变区域分母'
        assert missing['list']['total']==2 and len(missing['export_rows'])==2
        assert missing['list']['items'][0]['organization_id']=='district-unit2'
        assert (await s.statistics(str(city['id']),visible_ids=None,component='final'))['summary']['expected']==2
        scoped=await s.statistics(str(district['id']),visible_ids=['district-unit'])
        assert scoped['summary']['expected']==2 and scoped['meta']['partial_scope']
        assert all(r['organization_id']=='district-unit' for r in scoped['list']['items'])
        # 截止时间是名册事实，读取时计算，不依赖槽位缓存状态。
        await conn.execute('UPDATE material_slots SET current_document_version_id=NULL,status=$2 WHERE id=$1',districtslots[0]['id'],'not_due')
        overdue=await s.statistics(str(district['id']),visible_ids=None,status='overdue')
        assert overdue['summary']['overdue_missing']==1 and overdue['list']['total']==1


@pytest.mark.real_database
@pytest.mark.asyncio
async def test_versions_extras_publish_revision_snapshot_and_conflicts(ledger_db,monkeypatch,tmp_path):  # noqa: F811 -- 复用导入的 pytest fixture
    schema,pool=ledger_db
    monkeypatch.setattr(issue_workflow_store,'_store_path',lambda:tmp_path/'workflow.json')
    async with _conn(schema,pool) as conn:
        s=ReportingTaskService(conn,CATALOG)
        task=await publish(s,'city',lambda i,r:{'decision':'not_applicable','reason':'本期不适用'} if r['subject_org_id']=='city-unit' and r['report_kind']=='final' else {})
        slot=await conn.fetchrow('SELECT * FROM material_slots WHERE jurisdiction_org_id=$1 ORDER BY slot_key LIMIT 1','city')
        version=await receive(conn,slot,'v1','completed')
        job=await _insert_job(conn,job_uuid='review-job',metadata={'structured_ingest':{'document_version_id':version}})
        await conn.execute('UPDATE analysis_jobs SET analysis_revision=1 WHERE id=$1',job)
        await conn.execute("INSERT INTO analysis_results(job_id,rule_findings,raw_response) VALUES($1,'[]','{}')",job)
        await conn.execute("INSERT INTO review_sessions(slot_id,document_version_id,analysis_job_uuid,analysis_basis_token,status,started_by,completed_by,completed_at,review_result) VALUES($1,$2,'review-job','review-job:1','completed','admin','admin',NOW(),$3::jsonb)",slot['id'],version,json.dumps({'confirmed_findings':[{'issue_id':'issue1','title':'人工确认问题','severity':'high'}]}))
        first=await s.statistics(str(task['id']),visible_ids=None,view='issues')
        assert first['summary']['expected']==3 and first['summary']['confirmed']==1 and first['list']['total']==1
        before=dict(await conn.fetchrow('SELECT * FROM material_slots WHERE id=$1',slot['id']))
        # 修订名册不修改已有材料，尤其不能把 completed 改回 uploaded。
        prev=await s.preview(await s.get(str(task['id'])))
        await s.publish(str(task['id']),task['draft_revision'],prev['preview_token'],'再次核对完整名单','admin')
        assert before==dict(await conn.fetchrow('SELECT * FROM material_slots WHERE id=$1',slot['id']))
        # 已发布快照和正在编辑的草稿隔离。
        roster=await s.roster(str(task['id']))
        await s.edit(str(task['id']),roster['task']['draft_revision'],[dict(key=roster['items'][0]['key'],decision='excluded',reason='草稿待确认')])
        staff=await s.roster(str(task['id']),include_draft=False)
        assert staff['items'][0]['decision']!='excluded'
        await receive(conn,slot,'v2','processing')
        second=await s.statistics(str(task['id']),visible_ids=None,view='issues')
        assert second['summary']['expected']==3 and second['summary']['uploaded']==1 and second['list']['total']==0
        assert second['summary']['confirmed'] is None,'旧版结论不能冒充新版本结果'
        latest=await conn.fetchval('SELECT current_document_version_id FROM material_slots WHERE id=$1',slot['id'])
        job2=await _insert_job(conn,job_uuid='lead-job',metadata={'structured_ingest':{'document_version_id':latest}})
        await conn.execute('UPDATE analysis_jobs SET analysis_revision=1 WHERE id=$1',job2)
        await conn.execute("INSERT INTO analysis_results(job_id,ai_findings,raw_response) VALUES($1,$2::jsonb,'{}')",job2,json.dumps([{'id':'lead1','title':'待人工判断的线索','severity':'high'}]))
        leads=await s.statistics(str(task['id']),visible_ids=None,view='leads')
        assert leads['summary']['pending']==leads['list']['total']==1
        assert leads['summary']['confirmed']==0 and leads['list']['items'][0]['title']=='待人工判断的线索'
        # 新目录/新上传仅为清单外材料，不破坏已发布基线。
        catalog=CATALOG+[dict(id='extra',name='新单位',level='unit',parent_id='city-dept')]
        other=ReportingTaskService(conn,catalog)
        from src.services.material_slot_service import MaterialSlotService
        from src.services.material_slot_resolver import decide_slot_allocation
        decision=decide_slot_allocation(metadata={'organization_id':'extra','report_year':2026,'report_kind':'budget'},org_records=catalog)
        await MaterialSlotService(conn)._ensure_slot_row(identity=decision.identity,decision=decision,caliber='self',due_at=None,expected_source_url=None)
        extra=await conn.fetchrow("SELECT * FROM material_slots WHERE subject_org_id='extra'")
        await receive(conn,extra,'outside-list')
        after=await other.statistics(str(task['id']),visible_ids=None,view='extras')
        assert after['meta']['baseline_ready'] and after['meta']['catalog_changed']
        assert after['summary']['expected']==3 and after['summary']['extra_uploaded']==1 and after['list']['total']==1
        # 刷新草稿不能清除已发布目录变更提示。
        updated=await other.get(str(task['id']))
        await other.generate(str(task['id']),updated['draft_revision'])
        assert (await other.statistics(str(task['id']),visible_ids=None))['meta']['catalog_changed']
        # 乐观锁及预览令牌必须拒绝变化。
        current=await other.get(str(task['id']))
        with pytest.raises(HTTPException) as err:await other.edit(str(task['id']),0,[])
        assert err.value.status_code==409
        invalid=await other.preview(current)
        assert not invalid['valid'],'新候选尚未人工确认，不允许发布'


@pytest.mark.real_database
@pytest.mark.asyncio
async def test_api_authorization_csv_and_feature_switch(ledger_db,monkeypatch,tmp_path):  # noqa: F811 -- 复用导入的 pytest fixture
    # Windows 无 fcntl；直接装载真实新路由，避免导入无关的 macOS 纠错模块。
    import importlib.util
    import sys
    spec=importlib.util.spec_from_file_location("gbc_reporting_routes_test",Path(__file__).parents[1]/"api/routes/reporting_tasks.py")
    routes=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,routes)
    spec.loader.exec_module(routes)
    from api import runtime
    monkeypatch.setenv('GOVBUDGET_REPORTING_TASKS_ENABLED','true')
    from api.material_access import MaterialAccessScope
    schema,pool=ledger_db
    monkeypatch.setattr(issue_workflow_store,'_store_path',lambda:tmp_path/'workflow.json')
    monkeypatch.setenv('AUDIT_LOG_PATH',str(tmp_path/'audit.jsonl'))
    orgs={r['id']:SimpleNamespace(**r) for r in CATALOG}
    storage=SimpleNamespace(get_all=lambda:list(orgs.values()),get_by_id=lambda key:orgs.get(key))
    monkeypatch.setattr(runtime,'require_org_storage',lambda:storage)
    users={'admin':{'username':'admin','is_admin':True},'unit':{'username':'unit','is_admin':False,'organization_ids':['district-unit']},'boss':{'username':'boss','is_admin':False,'organization_ids':['district'],'is_read_only':True}}
    monkeypatch.setattr(runtime,'require_user_store',lambda:SimpleNamespace(get_user_by_token=lambda token:users.get(token)))
    app=FastAPI()
    app.include_router(routes.router)
    async with _conn(schema,pool) as conn:
        async def with_connection(callback):return await callback(conn)
        monkeypatch.setattr(routes.material_access,'with_connection',with_connection)
        s=ReportingTaskService(conn,CATALOG)
        city=await publish(s,'city')
        district=await publish(s,'district')
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            headers={'X-Session-Token':'unit'}
            result=await client.get(f'/api/reporting-tasks/{district["id"]}/statistics',headers=headers)
            assert result.status_code==200,result.text
            assert result.json()['data']['summary']['expected']==2
            denied=await client.get(f'/api/reporting-tasks/{city["id"]}/statistics',headers=headers)
            assert denied.status_code==403
            regions=await client.get('/api/reporting-tasks?reporting_year=2026',headers=headers)
            assert [r['id'] for r in regions.json()['data']['regions']]==['district']
            csv=await client.get(f'/api/reporting-tasks/{district["id"]}/statistics?format=csv&status=missing&page_size=1',headers=headers)
            assert csv.status_code==200 and '区服务中心' not in csv.text and '上海市本级' not in csv.text
            assert csv.text.count('unit_self')==0
            for token in ['unit','boss']:
                response=await client.post('/api/reporting-tasks',headers={'X-Session-Token':token},json={'jurisdiction_org_id':'city','reporting_year':2027})
                assert response.status_code==403
            cases=await client.get(f'/api/reporting-tasks/{district["id"]}/identity-cases',headers={'X-Session-Token':'boss'})
            assert cases.status_code==403
            staff_cases=await client.get(f'/api/reporting-tasks/{district["id"]}/identity-cases',headers=headers)
            assert staff_cases.status_code==200
            all_history=await client.get(f'/api/reporting-tasks/{district["id"]}/identity-cases?include_history=true',headers={'X-Session-Token':'boss'})
            assert all_history.status_code==403
            preview_denied=await client.post(f'/api/reporting-tasks/{district["id"]}/identity-preview',headers={'X-Session-Token':'boss'},json={'old_id':'a','new_id':'b','slot_ids':['a']})
            assert preview_denied.status_code==403
            invalid=await client.post(f'/api/reporting-tasks/{district["id"]}/roster/publish',headers={'X-Session-Token':'admin'},json={'revision':0,'preview_token':'a'*64,'note':'未经完整确认'})
            assert invalid.status_code==422
            monkeypatch.setenv('GOVBUDGET_REPORTING_TASKS_ENABLED','false')
            closed=await client.get(f'/api/reporting-tasks/{district["id"]}/statistics',headers=headers)
            assert closed.status_code==503

@pytest.mark.real_database
@pytest.mark.asyncio
async def test_identity_preview_apply_and_recovery_preserve_versions(ledger_db,monkeypatch,tmp_path):  # noqa: F811 -- 复用导入的 pytest fixture
    from src.services.reporting_identity import preview,apply,rollback_ancestors
    from tests.test_material_ledger_pg import _insert_slot
    from src.schemas.material_slot import SlotIdentity
    schema,pool=ledger_db
    async with _conn(schema,pool) as conn:
        s=ReportingTaskService(conn,CATALOG)
        task=await s.create('district',2026,'admin')
        identity=SlotIdentity('district-unit','unit','unit_self','budget',2026)
        slot=await _insert_slot(conn,subject_org_id='district-unit',subject_org_name='区财政局',jurisdiction_org_id=None,department_org_id=None,fiscal_year=2026,report_kind='budget',slot_key=identity.slot_key)
        version=await _insert_version(conn,slot_id=slot,file_hash='preserved',created_at=NOW)
        await conn.execute('UPDATE material_slots SET current_document_version_id=$2 WHERE id::text=$1',slot,version)
        before=dict(await conn.fetchrow('SELECT * FROM material_slots WHERE id::text=$1',slot))
        p=await preview(conn,task,CATALOG,'district-unit','district-unit',[slot])
        result=await apply(conn,task,CATALOG,old_id='district-unit',new_id='district-unit',slot_ids=[slot],token=p['preview_token'],note='经组织目录及文件原文确认同一主体',actor='admin',upload_root=tmp_path,links_file=tmp_path/'links.json',receipt_root=tmp_path/'receipts')
        path=tmp_path/'receipts'/result['receipt_id']/'receipt.json'
        assert await conn.fetchval('SELECT jurisdiction_org_id FROM material_slots WHERE id::text=$1',slot)=='district'
        assert await conn.fetchval('SELECT file_hash FROM fiscal_document_versions WHERE id=$1',version)=='preserved'
        await rollback_ancestors(conn,path)
        assert dict(await conn.fetchrow('SELECT * FROM material_slots WHERE id::text=$1',slot))==before
        # 后续业务变更后必须拒绝恢复覆盖。
        p=await preview(conn,task,CATALOG,'district-unit','district-unit',[slot])
        result=await apply(conn,task,CATALOG,old_id='district-unit',new_id='district-unit',slot_ids=[slot],token=p['preview_token'],note='经组织目录及文件原文确认同一主体',actor='admin',upload_root=tmp_path,links_file=tmp_path/'links.json',receipt_root=tmp_path/'receipts')
        await conn.execute("UPDATE material_slots SET status='processing' WHERE id::text=$1",slot)
        with pytest.raises(ValueError,match='后续变更'):await rollback_ancestors(conn,tmp_path/'receipts'/result['receipt_id']/'receipt.json')
        # 未明确口径时拒绝按名字强行修复。
        await conn.execute("UPDATE material_slots SET mapping_key='uncertain' WHERE id::text=$1",slot)
        with pytest.raises(HTTPException):await preview(conn,task,CATALOG,'district-unit','district-unit',[slot])


@pytest.mark.real_database
@pytest.mark.asyncio
async def test_fiscal_configuration_takes_effect_only_on_publish(ledger_db,monkeypatch,tmp_path):  # noqa: F811 -- 复用导入的 pytest fixture
    schema,pool=ledger_db
    monkeypatch.setattr(issue_workflow_store,'_store_path',lambda:tmp_path/'workflow.json')
    async with _conn(schema,pool) as conn:
        s=ReportingTaskService(conn,CATALOG)
        task=await publish(s,'city')
        from src.services.supervision_query import query
        legacy=await query(conn,visible_org_ids=None,scope_ids=['city-dept','city-unit'],scope_id='city',fiscal_year=2026,report_kind='budget',view='materials')
        assert not legacy['meta']['baseline_ready'], '新任务名单不能自动成为旧查询的完整基线'
        await s.update(str(task['id']),task['draft_revision'],'admin',components=[{'report_kind':'budget','fiscal_year':2026},{'report_kind':'final','fiscal_year':2024}])
        before=await s.statistics(str(task['id']),visible_ids=None)
        assert before['summary']['expected']==4 and before['meta']['baseline_ready']
        assert {r['fiscal_year'] for r in before['components']}=={2026,2025}
        task=await s.get(str(task['id']))
        draft=await s.generate(str(task['id']),task['draft_revision'])
        await s.edit(str(task['id']),draft['task']['draft_revision'],[dict(key=r['key'],decision='required',reason='核实年度配置') for r in draft['items']])
        task=await s.get(str(task['id']))
        preview=await s.preview(task)
        await s.publish(str(task['id']),task['draft_revision'],preview['preview_token'],'确认本次财政年度修订','admin')
        after=await s.statistics(str(task['id']),visible_ids=None)
        assert after['summary']['expected']==4 and {r['fiscal_year'] for r in after['components']}=={2026,2024}
        assert await conn.fetchval('SELECT COUNT(*) FROM supervision_baselines WHERE task_id=$1',task['id'])==4


def test_task_csv_allows_unknown_deadline_without_changing_legacy_import():
    from src.services.expected_material_import import preview_csv,COLUMNS
    text=','.join(COLUMNS)+'\ncity-unit,unit,2026,budget,unit_self,self,,\n'
    assert preview_csv(text,CATALOG,None,allow_unknown_due=True)['valid']
    assert not preview_csv(text,CATALOG,None)['valid']


@pytest.mark.real_database
@pytest.mark.asyncio
async def test_empty_directory_has_zero_obligations_without_fake_completeness(ledger_db, monkeypatch, tmp_path):  # noqa: F811 -- 复用导入的 pytest fixture
    schema, pool = ledger_db
    monkeypatch.setattr(issue_workflow_store, '_store_path', lambda: tmp_path/'workflow.json')
    async with _conn(schema, pool) as conn:
        s = ReportingTaskService(conn, CATALOG + [dict(id='empty-region', name='空地区', level='district', parent_id='city')])
        task = await s.create('empty-region', 2026, 'admin')
        data = await s.statistics(str(task['id']), visible_ids=None, export=True)
        assert data['meta']['data_basis'] == 'organization_catalog'
        assert (data['summary']['expected'], data['summary']['uploaded'], data['summary']['missing']) == (0, 0, 0)
        assert data['summary']['coverage_rate'] is None
        assert data['list']['total'] == 0 and data['export_rows'] == []
        assert (await s.get(str(task['id'])))['draft_roster'] == []
        assert await conn.fetchval('SELECT count(*) FROM material_slots') == 0
