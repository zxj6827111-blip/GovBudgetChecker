"""在独立PG schema中，以真实原件验证统一事实、快照和数据库往返。"""
from __future__ import annotations
import argparse,asyncio,hashlib,json,os,sys
from pathlib import Path
from copy import deepcopy

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

async def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--schema',default='trusted_pdf_20261020')
    ap.add_argument('--output',type=Path,default=ROOT/'outputs/pdf_trusted_ingest_20261020/runtime_m1')
    ap.add_argument('--source-root',type=Path,default=Path('/Users/zhangxingjin/Desktop/决算测试上海市'))
    args=ap.parse_args()
    import re
    if not re.fullmatch(r'trusted_pdf_[a-z0-9_]+',args.schema):raise ValueError('dedicated validation schema required')
    args.output.mkdir(parents=True,exist_ok=True)
    from dotenv import dotenv_values
    import asyncpg
    dsn=dotenv_values(ROOT/'.env').get('DATABASE_URL')
    os.environ.update(DATABASE_URL=dsn,PG_SCHEMA=args.schema,
        ORG_DATA_DIR=str(args.output/'organizations'),UPLOAD_DIR=str(args.output/'uploads'),TESTING='true')
    from src.db.migrations import MIGRATIONS
    c=await asyncpg.connect(dsn,timeout=10)
    try:
        await c.execute(f'CREATE SCHEMA IF NOT EXISTS "{args.schema}"')
        await c.execute(f'SET search_path TO "{args.schema}"')
        await c.execute('CREATE TABLE IF NOT EXISTS trusted_migrations(id TEXT PRIMARY KEY)')
        for migration in MIGRATIONS:
            if await c.fetchval('SELECT 1 FROM trusted_migrations WHERE id=$1',migration['id']):continue
            async with c.transaction():
                for sql in migration['sql']:await c.execute(sql)
                await c.execute('INSERT INTO trusted_migrations VALUES($1)',migration['id'])
    finally:await c.close()
    from src.db.connection import DatabaseConnection
    await DatabaseConnection.initialize(dsn)
    from src.services import structured_ingest_runner as runner
    # 专用schema已应用迁移；不触发针对业务环境的初始化旁路。
    runner._DB_READY=True
    from src.services.pdf_parser import PDFParser
    from src.services.input_processing_identity import processing_manifest
    implementation_identity=processing_manifest()
    from src.engine.fact_context import build_fact_context_from_artifact
    from src.services.analysis_fact_snapshot import canonical_facts,make_snapshot,write_snapshot
    original_verify = runner.verify_persisted_facts
    async def diagnose_verify(conn, *, parse_run_id, facts):
        try:
            return await original_verify(conn, parse_run_id=parse_run_id, facts=facts)
        except ValueError:
            actual = await conn.fetch('SELECT * FROM parse_facts WHERE parse_run_id=$1', parse_run_id)
            expected_map={f['fact_uid']:f for f in canonical_facts(facts)}
            actual_map={f['fact_uid']:f for f in canonical_facts(actual)}
            differences=[{'fact_uid':k, 'fields':{field:{'expected':v,'actual':actual_map.get(k,{}).get(field)}
                for field,v in expected_map.get(k,{}).items() if v!=actual_map.get(k,{}).get(field)}}
                for k in expected_map if expected_map[k]!=actual_map.get(k)]
            (args.output/'readback_differences.json').write_text(json.dumps(differences,ensure_ascii=False,indent=2))
            raise
    runner.verify_persisted_facts = diagnose_verify
    results=[]
    for year,name in [('2023','上海市交通委员会'),('2025','上海市商务委员会')]:
        path=args.source_root/year/(name+'.pdf')
        artifact=PDFParser(None).build_parse_artifact(str(path))
        raw=build_fact_context_from_artifact(artifact,org_name=name,fiscal_year=int(year),report_kind='final')
        job='trusted-'+year+'-'+hashlib.sha256(name.encode()).hexdigest()[:12]
        rev='rev-runtime-m1-'+artifact.processing_manifest['sha256'][:16]
        meta={'organization_name':name,'fiscal_year':int(year),'report_year':int(year),
              'doc_type':'dept_final','report_kind':'final','checksum':artifact.source_file_hash}
        result=await runner.run_structured_ingest(job,path,meta,parse_artifact=artifact,
            raw_fact_context=raw,effective_fact_context=raw,analysis_revision=rev,candidate_only=True)
        (args.output/(year+'__'+name+'__result.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
        if result['status']!='done':raise RuntimeError(str({k:result.get(k)for k in ['status','ingest_stage','error']}))
        conn=await DatabaseConnection.acquire()
        try:
            actual=await conn.fetch('SELECT * FROM parse_facts WHERE parse_run_id=$1',result['parse_run_id'])
            assert canonical_facts(actual)==canonical_facts(raw.facts)
            # 实际数据库故障注入：同值资格变化必须阻止完成，并能事务回滚。
            tx=conn.transaction();await tx.start()
            try:
                await conn.execute("UPDATE parse_facts SET recognition_status='low_confidence' WHERE parse_run_id=$1 AND fact_uid=$2",
                                   result['parse_run_id'],raw.facts[0].fact_uid)
                try:
                    await original_verify(conn,parse_run_id=result['parse_run_id'],facts=raw.facts)
                except ValueError:
                    pass
                else:
                    raise AssertionError('qualification mismatch was not rejected')
            finally:
                await tx.rollback()
            assert (await original_verify(conn,parse_run_id=result['parse_run_id'],facts=raw.facts))['verified']
            try:
                async with conn.transaction():
                    await conn.execute("UPDATE analysis_fact_snapshots SET payload=payload || '{\"tampered\":true}'::jsonb WHERE job_uuid=$1 AND revision_id=$2",job,rev)
            except asyncpg.RaiseError:
                pass
            else:
                raise AssertionError('database immutable payload guard did not reject mutation')
            # 热缓存重复调用必须仍保持同一内容、同一运行以及完整快照。
            warm=await runner.run_structured_ingest(job,path,meta,parse_artifact=artifact,
                raw_fact_context=raw,effective_fact_context=raw,analysis_revision=rev,candidate_only=True)
            assert warm['status']=='done',warm
            assert warm['parse_run_id']==result['parse_run_id']
            assert warm['fact_snapshot']==result['fact_snapshot']
        finally:await DatabaseConnection.release(conn)
        snapshot=make_snapshot(job_uuid=job,revision_id=rev,artifact=artifact,raw_context=raw,effective_context=raw)
        directory=args.output/'uploads'/job
        write_snapshot(directory/'fact_snapshots'/(rev+'.json'),snapshot)
        conn=await DatabaseConnection.acquire()
        try:
            expected_current=await conn.fetchval('SELECT current_fact_revision_id FROM analysis_jobs WHERE job_uuid=$1',job)
        finally:await DatabaseConnection.release(conn)
        payload={'job_id':job,'analysis_revision':rev,'status':'done','structured_ingest':result,
                 'expected_current_fact_revision_id':expected_current,
                 'filename':path.name,'checksum':artifact.source_file_hash,
                 'result':{'meta':{'fact_snapshot':result['fact_snapshot']},'rule_findings':[],
                           'ai_findings':[],'merged':{}}}
        from src.services import analysis_result_store as result_store
        result_store._DB_READY=True
        assert await result_store.persist_analysis_job_snapshot(payload,include_results=True)
        from api import runtime
        from src.services.analysis_fact_snapshot import read_database_snapshot, synchronize_published_mirror
        mirror = await synchronize_published_mirror(job,directory)
        assert mirror['status']=='synchronized' and mirror['revision_id']==rev
        # 文件指针丢失是可修复镜像故障，不能改变数据库当前版本。
        (directory/'analysis_versions/current.json').unlink()
        assert await read_database_snapshot(job)==snapshot
        assert (await synchronize_published_mirror(job,directory))==mirror
        conn=await DatabaseConnection.acquire()
        try:
            assert await conn.fetchval('SELECT mirror_pending FROM analysis_fact_snapshots WHERE job_uuid=$1 AND revision_id=$2',job,rev) is False
        finally:await DatabaseConnection.release(conn)
        published=await read_database_snapshot(job)
        assert published==snapshot
        conn=await DatabaseConnection.acquire()
        try:
            from src.services.fact_compatibility_projection import verify_legacy_fact_projection,verify_shared_fact_projection
            projection=await conn.fetchrow('SELECT receipt FROM published_fact_projections WHERE document_version_id=$1',result['document_version_id'])
            assert projection
            projection_receipt=json.loads(projection['receipt']) if isinstance(projection['receipt'],str)else projection['receipt']
            import uuid
            report_id=uuid.UUID(projection_receipt['shared_report_id'])
            await verify_legacy_fact_projection(conn,result['document_version_id'],snapshot)
            await verify_shared_fact_projection(conn,report_id,snapshot)
            # 发布后段故障必须回滚前段已写的兼容事实和表格，当前版本不变。
            from src.services.analysis_fact_snapshot import persist_snapshot,publish_snapshot
            from src.services.ps_schema_sync import PSSharedSchemaSync
            failed_revision=rev+'-rollback-proof'
            corrected=deepcopy(raw)
            corrected.facts[0].normalized_value+=1
            failed_snapshot=make_snapshot(job_uuid=job,revision_id=failed_revision,artifact=artifact,
                                          raw_context=raw,effective_context=corrected)
            await persist_snapshot(conn,snapshot=failed_snapshot,parse_run_id=result['parse_run_id'])
            failed_payload=deepcopy(payload)
            failed_payload['analysis_revision']=failed_revision
            failed_payload['expected_current_fact_revision_id']=rev
            failed_receipt={k:failed_snapshot[k]for k in ('revision_id','snapshot_hash','raw_facts_hash','effective_facts_hash')}
            failed_receipt['storage_consistent']=True
            failed_payload['structured_ingest']['fact_snapshot']=failed_receipt
            original_shared_sync=PSSharedSchemaSync.sync
            async def fail_shared(*a,**kw):raise RuntimeError('injected_shared_publication_failure')
            PSSharedSchemaSync.sync=fail_shared
            try:
                try:
                    async with conn.transaction():await publish_snapshot(conn,failed_payload)
                except RuntimeError as exc:assert str(exc)=='injected_shared_publication_failure'
                else:raise AssertionError('publication fault was ignored')
            finally:PSSharedSchemaSync.sync=original_shared_sync
            assert await conn.fetchval('SELECT current_fact_revision_id FROM analysis_jobs WHERE job_uuid=$1',job)==rev
            assert await conn.fetchval('SELECT publication_state FROM analysis_fact_snapshots WHERE job_uuid=$1 AND revision_id=$2',job,failed_revision)=='validated'
            await verify_legacy_fact_projection(conn,result['document_version_id'],snapshot)
            await verify_shared_fact_projection(conn,report_id,snapshot)
            # 字符与快照未变时，兼容金额或资格漂移仍必须被实际读回检出。
            tx=conn.transaction();await tx.start()
            try:
                await conn.execute('UPDATE fact_fiscal_line_items SET amount=amount+1 WHERE id=(SELECT MIN(id) FROM fact_fiscal_line_items WHERE document_version_id=$1)',result['document_version_id'])
                try:await verify_legacy_fact_projection(conn,result['document_version_id'],snapshot)
                except ValueError:pass
                else:raise AssertionError('legacy projection amount fault was not rejected')
                await conn.execute("UPDATE org_dept_line_items SET values_json=jsonb_set(values_json,'{_meta,recognition_status}','\"verified\"'::jsonb) WHERE report_id=$1",report_id)
                try:await verify_shared_fact_projection(conn,report_id,snapshot)
                except ValueError:pass
                else:raise AssertionError('shared qualification fault was not rejected')
            finally:await tx.rollback()
            await verify_legacy_fact_projection(conn,result['document_version_id'],snapshot)
            await verify_shared_fact_projection(conn,report_id,snapshot)
        finally:await DatabaseConnection.release(conn)
        (directory/'status.json').write_text(json.dumps(payload,ensure_ascii=False))
        # 查询真实路由；这里只替代登录上下文，事实查询仍访问隔离数据库。
        from fastapi import FastAPI
        import httpx
        from api.routes import jobs
        original_access=jobs.require_job_access
        jobs.require_job_access=lambda *a,**kw:(None,None,{'username':'isolated_acceptance'},payload)
        app=FastAPI();app.include_router(jobs.router)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test')as client:
                received=[]
                for offset in range(0,len(raw.facts),200):
                    response=await client.get(f'/api/jobs/{job}/fact-snapshot',params={'view':'raw','offset':offset,'limit':200})
                    assert response.status_code==200,response.text
                    received.extend(response.json()['facts'])
                assert canonical_facts(received)==canonical_facts(raw.facts)
                effective_response=await client.get(f'/api/jobs/{job}/fact-snapshot',params={'view':'effective','limit':1000})
                assert effective_response.status_code==200,effective_response.text
                assert canonical_facts(effective_response.json()['facts'])==canonical_facts(raw.facts)
        finally:
            jobs.require_job_access=original_access
        selected=[f for f in raw.facts if f.table_code==('FIN_07_three_public'if year=='2023'else'FIN_04_fiscal_grant_total')]
        targeted_statuses=[]
        if year=='2025':
            audit=json.loads((ROOT/'outputs/workbuddy_independent_audit_20261020/database_all103_readback.json').read_text())
            document=next(d for d in audit['documents']if str(d['year'])=='2025' and '商务'in d['file'])
            targets={d['fact_uid'] for d in document['fact_mismatch_details']}
            targeted_statuses=[{'fact_uid':f.fact_uid,'recognition_status':f.recognition_status,
                               'confidence':f.confidence,'qualification_evidence':f.extra_dims.get('qualification_evidence')}
                              for f in raw.facts if f.fact_uid in targets]
            assert len(targeted_statuses)==46
            assert all(f['recognition_status']=='unverified'for f in targeted_statuses)
            (args.output/'commerce46_exact_readback.json').write_text(json.dumps(targeted_statuses,ensure_ascii=False,indent=2))
        record={'year':year,'file':path.name,'schema':args.schema,'job_id':job,'revision_id':rev,
            'parse_run_id':result['parse_run_id'],'facts':len(raw.facts),'focus_facts':len(selected),
            'focus_statuses':sorted({f.recognition_status for f in selected}),
            'targeted_qualification_readback_count':len(targeted_statuses),
            'storage_verification':result['storage_verification'],'fact_snapshot':result['fact_snapshot'],
            'hot_cache_verified':True,'database_publication_verified':True,'http_raw_effective_readback_verified':True,
            'database_qualification_fault_rejected':True,'database_snapshot_mutation_rejected':True}
        record.update(effective_legacy_and_shared_projection_verified=True,
                      projection_amount_and_qualification_faults_rejected=True,
                      publication_failure_rolled_back_all_projections=True)
        results.append(record);print(json.dumps(record,ensure_ascii=False),flush=True)
    assert all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest
               for path,digest in implementation_identity['code'].items()), 'source changed during runtime validation'
    (args.output/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    await DatabaseConnection.close()

if __name__=='__main__':asyncio.run(main())
