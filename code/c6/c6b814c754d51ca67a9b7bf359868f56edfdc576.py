"""一次性本任务 PostgreSQL：事务回滚、真实停库恢复、精确候选复用。"""
from pathlib import Path
import asyncio,hashlib,json,os,secrets,subprocess,sys,tempfile,time,uuid,socket
BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]
sys.path.insert(0,str(BASE/'validation-runtime-v2'))
OUT=BASE/'projection-outage-pg-v2';OUT.mkdir(exist_ok=False)
NAME='gbc-pdf-projection-'+uuid.uuid4().hex[:10]
LABEL='pdf-input-v2-orientation-outage-validation'
def command(args):return subprocess.run(args,capture_output=True,text=True,check=True).stdout.strip()
def docker(*args):return command(['docker',*args])
def owned():
    data=json.loads(docker('inspect',NAME))[0]
    assert data['Config']['Labels'].get('codex.task')==LABEL
    return data

def write(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str))
async def main(work,dsn):
    os.environ.update(DATABASE_URL=dsn,UPLOAD_DIR=str(work/'uploads'),DATA_DIR=str(work/'data'),TESTING='true',OCR_ENABLED='false')
    from src.db.connection import DatabaseConnection
    from src.db.migrations import run_migrations
    from src.services.fiscal_input_builder import FiscalInputBuilder,VERSIONS
    from src.services.document_profile_resolver import resolve_document_profile
    from src.services.structured_ingest_runner import run_structured_ingest
    from src.services.analysis_fact_snapshot import AnalysisFactSnapshot,stage_candidate,read_candidate,read_bound_snapshot
    from src.services import analysis_result_store as store
    from src.services import effective_fact_projection as projection
    from src.services.ps_shared_query_service import PSSharedQueryService
    from copy import deepcopy
    from decimal import Decimal
    await DatabaseConnection.initialize();await run_migrations()
    job_id=str(uuid.uuid4());job=work/'uploads'/job_id;job.mkdir(parents=True)
    original=ROOT/'samples/bad/中共上海市普陀区委社会工作部 2024 年度部门决算.pdf'
    source=job/'original.pdf';source.write_bytes(original.read_bytes())
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    builder=FiscalInputBuilder();artifact=builder.read_native(source)
    profile=resolve_document_profile(filename=original.name,page_texts=[b.text for b in artifact.text_blocks])
    raw=builder.facts(artifact,org_name=profile.organization_name.value,fiscal_year=profile.year,report_kind=profile.kind)
    assert raw.facts and raw.definite_facts()
    ingest=await run_structured_ingest(job_id=job_id,pdf_path=source,metadata={'checksum':digest,'organization_name':raw.org_name,'report_year':raw.fiscal_year,'report_kind':raw.report_kind},parse_artifact=artifact,effective_fact_context=raw)
    assert ingest['status']=='done',ingest
    raw=builder.facts(artifact,org_name=raw.org_name,fiscal_year=raw.fiscal_year,report_kind=raw.report_kind)
    async def table_hash():
        conn=await DatabaseConnection.acquire()
        try:
            rows=await conn.fetch('SELECT * FROM parse_facts WHERE parse_run_id=$1 ORDER BY id',ingest['parse_run_id'])
            return hashlib.sha256(json.dumps([dict(r) for r in rows],sort_keys=True,default=str).encode()).hexdigest()
        finally:await DatabaseConnection.release(conn)
    raw_db_hash=await table_hash()
    def candidate(revision,effective):
        sealed=AnalysisFactSnapshot.create(revision_id=revision,raw=raw,effective=effective,versions=VERSIONS)
        payload={'job_id':job_id,'filename':'original.pdf','file_hash':digest,'status':'review_required','mode':'dual','analysis_revision':revision,'structured_ingest':ingest,'result':{'meta':{'fact_snapshot':sealed.summary(),'effective_projection':projection.projection_identity(sealed,job_id)}}}
        path=stage_candidate(job,payload,sealed)
        return payload,sealed,path
    first,sealed1,path=candidate('rev-before',raw)
    assert await store.persist_analysis_job_snapshot(first,include_results=True)
    write(job/'status.json',first)
    corrected=deepcopy(raw);target=corrected.definite_facts()[0]
    original_value=str(target.normalized_value)
    target.normalized_value=Decimal('12345.67')
    corrected.overlay_records=[{'record_id':'synthetic-controlled-human-correction','fact_uid':target.fact_uid,'field':'normalized_value','value':'12345.67','scope':'mechanism_control_not_original_issue_truth'}]
    corrected.version_token=corrected.compute_version_token()
    second,sealed2,path=candidate('rev-after',corrected)
    frozen_candidate=path.read_bytes()
    (OUT/'immutable-candidate.json').write_bytes(frozen_candidate)
    write(OUT/'first-published.json',first)
    original_writer=projection.publish_effective_projection
    attempts=[]
    async def fail_after_write(conn,snapshot,payload):
        result=await original_writer(conn,snapshot,payload)
        attempts.append(result['report_id'])
        raise RuntimeError('controlled_projection_failure_after_all_rows')
    projection.publish_effective_projection=fail_after_write
    assert not await store.persist_analysis_job_snapshot(second,include_results=True)
    projection.publish_effective_projection=original_writer
    assert len(attempts)==3 and path.read_bytes()==frozen_candidate
    conn=await DatabaseConnection.acquire()
    try:
        job_db_id=await conn.fetchval('SELECT id FROM analysis_jobs WHERE job_uuid=$1',job_id)
        revision=await conn.fetchval("SELECT raw_response->>'analysis_revision' FROM analysis_results WHERE job_id=$1",job_db_id)
        assert revision=='rev-before'
        assert await conn.fetchval('SELECT count(*) FROM analysis_fact_snapshots WHERE job_id=$1',job_db_id)==1
        assert not await conn.fetchval('SELECT EXISTS(SELECT 1 FROM org_dept_annual_report WHERE id=$1::uuid)',attempts[0])
        old_view=await PSSharedQueryService(conn).list_report_line_items(projection.projection_identity(sealed1,job_id)['report_id'],limit=5000)
        assert old_view['fact_snapshot']==sealed1.summary()
    finally:await DatabaseConnection.release(conn)
    assert await table_hash()==raw_db_hash
    await DatabaseConnection.close()
    owned();docker('stop','--time','10',NAME)
    assert not await store.persist_analysis_job_snapshot(second,include_results=True)
    assert path.read_bytes()==frozen_candidate and json.loads((job/'status.json').read_text())['analysis_revision']=='rev-before'
    owned();docker('start',NAME)
    assert int(docker('port',NAME,'5432/tcp').rsplit(':',1)[1])==int(dsn.rsplit(':',1)[1].split('/')[0]),'published PG port changed'
    for _ in range(40):
        ready=subprocess.run(['docker','exec',NAME,'pg_isready','-U','validator','-d','validation'],capture_output=True).returncode==0
        if ready:break
        await asyncio.sleep(.3)
    assert ready
    saved_facts=FiscalInputBuilder.facts
    def forbidden(*args,**kwargs):raise AssertionError('outage recovery must never rematerialize')
    FiscalInputBuilder.facts=forbidden
    synced=await store.sync_pending_analysis_snapshots(work/'uploads')
    FiscalInputBuilder.facts=saved_facts
    assert synced['synced']==1 and not synced['failed'],synced
    assert path.read_bytes()==frozen_candidate
    conn=await DatabaseConnection.acquire()
    try:
        restored=await read_bound_snapshot(conn,job_db_id=job_db_id,revision_id='rev-after',expected=sealed2.summary())
        assert restored.payload_json==sealed2.payload_json
        assert await conn.fetchval('SELECT analysis_revision FROM analysis_jobs WHERE id=$1',job_db_id)==2
        view=await PSSharedQueryService(conn).list_report_line_items(projection.projection_identity(sealed2,job_id)['report_id'],limit=5000)
        values=[i['values_json']['_meta']['fact'] for i in view['items'] if i['values_json']['_meta']['fact']['fact_uid']==target.fact_uid]
        assert len(values)==1 and values[0]['normalized_value']=='12345.67'
        history=await PSSharedQueryService(conn).list_report_line_items(projection.projection_identity(sealed1,job_id)['report_id'],limit=5000)
        assert history==old_view
    finally:await DatabaseConnection.release(conn)
    assert await table_hash()==raw_db_hash
    assert hashlib.sha256(original.read_bytes()).hexdigest()==digest
    write(OUT/'receipt.json',{'passed':True,'scope':'disposable_pg_mechanism_control_not_business_acceptance','container':NAME,'projection_write_failures_rolled_back':len(attempts),'real_database_stop_start':True,'candidate_bytes_identical':True,'recovery_rematerialization_forbidden':True,'source_original_unchanged':True,'raw_database_facts_sha256_unchanged':raw_db_hash,'old_revision':'rev-before','new_revision':'rev-after','database_generation':2,'old_effective_digest':sealed1.summary()['effective_digest'],'new_effective_digest':sealed2.summary()['effective_digest'],'raw_digest':sealed2.summary()['raw_digest'],'old_and_new_shared_views_match_their_own_snapshot':True,'controlled_original_value':original_value,'controlled_effective_value':'12345.67','facts':len(raw.facts)})
    await DatabaseConnection.close()

try:
    with tempfile.TemporaryDirectory(prefix='gbc-projection-outage-') as tmp:
        work=Path(tmp);env=work/'pg.env';password=secrets.token_hex(24)
        env.write_text('POSTGRES_USER=validator\nPOSTGRES_DB=validation\nPOSTGRES_PASSWORD='+password+'\n');env.chmod(0o600)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));bound_port=sock.getsockname()[1]
        docker('run','-d','--name',NAME,'--label','codex.task='+LABEL,'--cpus','1','--memory','512m','--env-file',str(env),'-p',f'127.0.0.1:{bound_port}:5432','postgres:16')
        port=int(docker('port',NAME,'5432/tcp').rsplit(':',1)[1]);assert port==bound_port
        for _ in range(50):
            if subprocess.run(['docker','exec',NAME,'pg_isready','-U','validator','-d','validation'],capture_output=True).returncode==0:break
            time.sleep(.3)
        asyncio.run(main(work,f'postgresql://validator:{password}@127.0.0.1:{port}/validation'))
finally:
    try:
        owned();docker('stop','--time','10',NAME);docker('rm','-v',NAME)
        write(OUT/'cleanup.json',{'owned_container_removed':True,'container':NAME})
    except Exception as exc:
        write(OUT/'cleanup.json',{'owned_container_removed':False,'container':NAME,'error_type':type(exc).__name__})
