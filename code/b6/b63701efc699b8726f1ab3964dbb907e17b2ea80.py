"""专属空库验证；拒绝任何非本轮 localhost:19018 DSN。"""
import asyncio,copy,json,os
from pathlib import Path
from decimal import Decimal
import asyncpg
from src.db.connection import DatabaseConnection
from src.db.migrations import run_migrations,MIGRATIONS
from src.engine.fact_context import FactContext
from src.schemas.parse_artifact import FiscalFact
from src.services.analysis_fact_snapshot import AnalysisFactSnapshot,persist_fact_snapshot
from src.services.analysis_result_store import _upsert_analysis_job,_upsert_analysis_result
from src.services.fiscal_input_builder import VERSIONS

async def main():
    dsn=os.environ['GOVBUDGET_TEST_DATABASE_URL']
    if not dsn.startswith('postgresql://postgres:isolated_validation_only@127.0.0.1:19018/pdf_input_v2'):
        raise RuntimeError('only the task-specific disposable database is permitted')
    await DatabaseConnection.initialize(dsn);await run_migrations();await run_migrations()
    conn=await DatabaseConnection.acquire()
    try:
        migration=next(m for m in MIGRATIONS if m['id']=='2026-10-17_0032_analysis_fact_snapshots')
        async with conn.transaction():
            for _ in range(2):
                for statement in migration['sql']:await conn.execute(statement)
        raw=FactContext([FiscalFact('r1c1','actual','12.34','万元',Decimal('12.34'))],file_hash='a'*64,parse_run_uid='isolated-run')
        effective=copy.deepcopy(raw);effective.facts[0].normalized_value=Decimal('56.78');effective.overlay_records=[{'amount':'56.78'}]
        effective.version_token=effective.compute_version_token()
        snapshot=AnalysisFactSnapshot.create(revision_id='rev-pg-test',raw=raw,effective=effective,versions=VERSIONS)
        payload={'job_id':'pdf-v2-pg-test','filename':'isolated.pdf','status':'done',
                 'analysis_revision':'rev-pg-test','result':{'issues':{},'meta':{'fact_snapshot':snapshot.summary()}}}
        await conn.execute("INSERT INTO parse_runs(parse_run_uid,document_version_id,source_file_hash) VALUES($1,1,$2) ON CONFLICT DO NOTHING",raw.parse_run_uid,raw.file_hash)
        async with conn.transaction():
            job_id=await _upsert_analysis_job(conn,payload,result_fingerprint='first')
            await _upsert_analysis_result(conn,job_id,payload)
            await persist_fact_snapshot(conn,job_id,snapshot)
        row=await conn.fetchrow('SELECT * FROM analysis_fact_snapshots WHERE job_id=$1',job_id)
        raw_restored=FactContext.from_dict(json.loads(row['raw_context']))
        effective_restored=FactContext.from_dict(json.loads(row['effective_context']))
        assert raw_restored.version_token==raw.version_token and effective_restored.version_token==effective.version_token
        assert isinstance(row['analysis_generation'],int) and row['revision_id']=='rev-pg-test'
        assert row['parse_run_id'] is not None
        async with conn.transaction(): await persist_fact_snapshot(conn,job_id,snapshot)
        assert await conn.fetchval('SELECT count(*) FROM analysis_fact_snapshots WHERE job_id=$1',job_id)==1
        rejected=[]
        for operation in ['UPDATE analysis_fact_snapshots SET versions=versions WHERE job_id=$1',
                          'DELETE FROM analysis_fact_snapshots WHERE job_id=$1']:
            try:
                async with conn.transaction():await conn.execute(operation,job_id)
            except asyncpg.PostgresError:rejected.append(operation.split()[0])
        assert rejected==['UPDATE','DELETE']
        before=await conn.fetchval('SELECT analysis_revision FROM analysis_jobs WHERE id=$1',job_id)
        try:
            async with conn.transaction():
                await _upsert_analysis_job(conn,payload,result_fingerprint='second')
                await _upsert_analysis_result(conn,job_id,payload)
                changed=copy.deepcopy(snapshot.to_dict());changed['effective_context']['facts'][0]['normalized_value']='999'
                AnalysisFactSnapshot.from_dict(changed)
        except ValueError:pass
        assert await conn.fetchval('SELECT analysis_revision FROM analysis_jobs WHERE id=$1',job_id)==before
        receipt={'migration_applied_twice':True,'raw_digest':raw.version_token,'effective_digest':effective.version_token,
                 'generation_type':'int','revision_id':row['revision_id'],'raw_amount':'12.34','effective_amount':'56.78',
                 'idempotent_same_snapshot':True,'append_only_rejections':rejected,'transaction_rollback':True,
                 'database':'dedicated_disposable_localhost_19018','api_worker_chain_verified':False}
        Path('../snapshot-pg-receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
    finally:
        await DatabaseConnection.release(conn);await DatabaseConnection.close()

asyncio.run(main())
