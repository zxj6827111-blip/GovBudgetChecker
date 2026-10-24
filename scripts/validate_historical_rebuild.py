"""仅在临时服务栈验证历史候选、故障回滚、发布及撤回；不构成真实材料验收。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


async def current_view(dsn,jobs):
    import asyncpg
    conn=await asyncpg.connect(dsn)
    try:
        # 只比较当前消费投影。新增候选解析与不可变历史应保留，不能要求全库不变。
        values={}
        for job in jobs:
            data=await conn.fetchval("SELECT jsonb_build_object('job',to_jsonb(j),'result',"
                "(SELECT to_jsonb(r)FROM analysis_results r WHERE r.job_id=j.id),'projection',"
                "(SELECT jsonb_agg(to_jsonb(p))FROM published_fact_projections p WHERE p.job_uuid=j.job_uuid),"
                "'facts',(SELECT jsonb_agg(to_jsonb(f)ORDER BY f.id)FROM fact_fiscal_line_items f "
                "JOIN published_fact_projections p ON p.document_version_id=f.document_version_id WHERE p.job_uuid=j.job_uuid))::text "
                "FROM analysis_jobs j WHERE j.job_uuid=$1",job)
            values[job]=data
        return values
    finally:await conn.close()


async def seed_legacy_control(env,uploads,job):
    """在临时库构造无快照指针的旧协议控制，保留原始事实和所有历史快照。"""
    import asyncpg
    from src.services.analysis_result_store import compute_analysis_result_fingerprint
    conn=await asyncpg.connect(env['DATABASE_URL'])
    try:
        async with conn.transaction():
            head=await conn.fetchrow('SELECT * FROM published_fact_projections WHERE job_uuid=$1',job)
            receipt=json.loads(head['receipt'])
            report=receipt['shared_report_id']
            payload=json.loads(await conn.fetchval('SELECT r.raw_response FROM analysis_results r '
                'JOIN analysis_jobs j ON j.id=r.job_id WHERE j.job_uuid=$1',job))
            for parent in [payload,(payload.get('result')or{}).get('meta')or{}]:
                parent.pop('fact_snapshot',None)
                parent.pop('recognition_state',None)
                (parent.get('structured_ingest')or{}).pop('fact_snapshot',None)
            payload.pop('analysis_revision',None)
            await conn.execute('UPDATE analysis_results SET raw_response=$2::jsonb WHERE job_id='
                '(SELECT id FROM analysis_jobs WHERE job_uuid=$1)',job,json.dumps(payload))
            await conn.execute('UPDATE analysis_jobs SET current_fact_revision_id=NULL,analysis_result_fingerprint=$2 WHERE job_uuid=$1',
                               job,compute_analysis_result_fingerprint(payload))
            await conn.execute('DELETE FROM published_fact_projections WHERE job_uuid=$1',job)
            import uuid
            await conn.execute('UPDATE org_dept_annual_report SET fact_projection=NULL WHERE id=$1',uuid.UUID(report))
        (uploads/job/'status.json').write_text(json.dumps(payload,ensure_ascii=False))
        (uploads/job/'analysis_versions/current.json').unlink(missing_ok=True)
        return head['document_version_id'],report
    finally:await conn.close()


async def legacy_view(dsn,job,version,report):
    import asyncpg
    import uuid
    conn=await asyncpg.connect(dsn)
    try:
        fields={}
        for table in ['fiscal_table_cells','fiscal_table_instances','fact_fiscal_line_items']:
            fields[table]=await conn.fetchval(f'SELECT COALESCE(jsonb_agg(to_jsonb(t)ORDER BY id),\'[]\'::jsonb)::text '
                f'FROM {table} t WHERE document_version_id=$1',version)
        for table,key in [('org_dept_annual_report','id'),('org_dept_table_data','report_id'),('org_dept_line_items','report_id')]:
            fields[table]=await conn.fetchval(f'SELECT COALESCE(jsonb_agg(to_jsonb(t)ORDER BY id),\'[]\'::jsonb)::text '
                f'FROM {table} t WHERE {key}=$1',uuid.UUID(report))
        fields['current']=await current_view(dsn,[job])
        return fields
    finally:await conn.close()


def run_history_validation(*,env,work,out,jobs,legacy_job=None):
    all_jobs=sorted(jobs)
    legacy_job=legacy_job or(all_jobs[2]if len(all_jobs)>2 else None)
    jobs=[job for job in all_jobs if job!=legacy_job][:2]
    if len(jobs)!=2:raise ValueError('history control requires two distinct jobs')
    root=out/'history_controls'
    root.mkdir()
    script=ROOT/'scripts/rebuild_trusted_pdf_history.py'
    uploads=work/'uploads'
    def command(name,*args,expected=0):
        with (root/(name+'.log')).open('w')as log:
            result=subprocess.run([sys.executable,str(script),*map(str,args)],env=env,cwd=ROOT,
                                  stdout=log,stderr=subprocess.STDOUT,timeout=600)
        if result.returncode!=expected and not(expected=='failure'and result.returncode!=0):
            raise AssertionError('history control '+name+' failed; inspect log')
    before=asyncio.run(current_view(env['DATABASE_URL'],jobs))
    command('inventory','inventory','--uploads',uploads,'--jobs',*jobs,'--output',root/'inventory.json')
    command('build','build','--uploads',uploads,'--inventory',root/'inventory.json','--output',root/'candidates')
    assert asyncio.run(current_view(env['DATABASE_URL'],jobs))==before,'candidate changed currently consumed results'
    manifest=json.loads((root/'candidates/candidate_manifest.json').read_text())
    assert all(i['status']=='candidate_ready'for i in manifest['items'])
    untouched=root/'unaccepted.json'
    untouched.write_text(json.dumps(manifest))
    command('reject_unaccepted','publish','--uploads',uploads,'--manifest',untouched,expected='failure')
    assert asyncio.run(current_view(env['DATABASE_URL'],jobs))==before
    # 这是临时栈内故障控制凭据，明确不能迁移为业务验收记录。
    for item in manifest['items']:
        item['acceptance'].update(accepted=True,reviewer='ISOLATED_CONTROL_TEST_ONLY',
                                  reviewed_at='2026-10-20T00:00:00Z',errors=[])
    bad=json.loads(json.dumps(manifest))
    bad['items'][-1]['acceptance']['accepted']=False
    bad_path=root/'late_failure.json'
    bad_path.write_text(json.dumps(bad))
    command('late_failure_rollback','publish','--uploads',uploads,'--manifest',bad_path,expected='failure')
    assert asyncio.run(current_view(env['DATABASE_URL'],jobs))==before,'failed batch partly published'
    accepted=root/'accepted_control.json'
    accepted.write_text(json.dumps(manifest))
    batch='control-history-'+jobs[0][:12]
    command('publish','publish','--uploads',uploads,'--manifest',accepted,'--batch-id',batch)
    after=asyncio.run(current_view(env['DATABASE_URL'],jobs))
    assert all(after[j]!=before[j]for j in jobs)
    for item in manifest['items']:
        status=json.loads((uploads/item['job_id']/'status.json').read_text())
        assert status['analysis_revision']==item['revision_id'],'current file mirror not switched'
    command('rollback','rollback','--uploads',uploads,'--batch-id',batch)
    assert asyncio.run(current_view(env['DATABASE_URL'],jobs))==before,'rollback did not restore current rows/IDs/content'
    command('sync','sync','--uploads',uploads,'--batch-id',batch)
    for item in manifest['items']:
        status=json.loads((uploads/item['job_id']/'status.json').read_text())
        assert status['analysis_revision']==item['expected_current_revision']
    if legacy_job:
        version,report=asyncio.run(seed_legacy_control(env,uploads,legacy_job))
        legacy_before=asyncio.run(legacy_view(env['DATABASE_URL'],legacy_job,version,report))
        command('legacy_inventory','inventory','--uploads',uploads,'--jobs',legacy_job,'--output',root/'legacy_inventory.json')
        command('legacy_build','build','--uploads',uploads,'--inventory',root/'legacy_inventory.json','--output',root/'legacy_candidates')
        legacy_manifest=json.loads((root/'legacy_candidates/candidate_manifest.json').read_text())
        for item in legacy_manifest['items']:
            item['acceptance'].update(accepted=True,reviewer='ISOLATED_CONTROL_TEST_ONLY',reviewed_at='2026-10-20',errors=[])
        accepted_legacy=root/'legacy_accepted_control.json'
        accepted_legacy.write_text(json.dumps(legacy_manifest))
        legacy_batch='legacy-'+batch
        command('legacy_publish','publish','--uploads',uploads,'--manifest',accepted_legacy,'--batch-id',legacy_batch)
        command('legacy_rollback','rollback','--uploads',uploads,'--batch-id',legacy_batch)
        assert asyncio.run(legacy_view(env['DATABASE_URL'],legacy_job,version,report))==legacy_before
        restored=json.loads((uploads/legacy_job/'status.json').read_text())
        assert not (restored.get('structured_ingest')or{}).get('fact_snapshot')
        assert not(uploads/legacy_job/'analysis_versions/current.json').exists()
    result={'passed':True,'jobs':jobs,'candidate_preserved_current':True,'unaccepted_rejected':True,
        'late_failure_whole_batch_rollback':True,'published_and_mirrored':True,'rollback_exact_current_rows':True,
        'scope':'isolated_controls_not_source_accuracy_acceptance'}
    result['legacy_without_snapshot_rollback_verified']=bool(legacy_job)
    (root/'receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result
