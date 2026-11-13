import asyncio,concurrent.futures,importlib.util,json,os,pathlib,secrets,subprocess,sys,tempfile,time,uuid
import httpx
ROOT=pathlib.Path.cwd(); OUT=pathlib.Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/pdf_review_batch2_20261027/online-final'); OUT.mkdir(parents=True,exist_ok=True)
spec=importlib.util.spec_from_file_location('previous_validation','/Volumes/workSSD/software development/GovBudgetChecker/outputs/pdf_review_remediation_merge_20261027T154139Z/validate_batch1_final_isolated_v2.py'); old=importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
checks={}; processes=[]; handles=[]; name='gbc-batch2-'+secrets.token_hex(5)
def check(name,value):
 checks[name]=bool(value)
 if not value: raise AssertionError(name)
def wait(c,job):
 status=old.wait_analysis(c,job)
 if status.get('status') in {'error','cancelled'}: raise AssertionError(str({k:status.get(k) for k in ['status','error','message']}))
 return status
def block(s): return s['result']['meta']['obligation_coverage']['blocking_total']
def analyze(c,pdf,org,year):
 with pdf.open('rb') as f:
  r=c.post('/api/documents/upload',files={'file':(pdf.name,f,'application/pdf')},data={'org_unit_id':org,'fiscal_year':str(year),'doc_type':'dept_final'})
 r.raise_for_status(); job=r.json()['job_id']; c.post('/api/analyze/'+job,json={'mode':'legacy','use_ai_assist':False,'use_local_rules':True}).raise_for_status(); return job,wait(c,job)
def info(c,job):
 r=c.get('/api/jobs/'+job+'/document-scope'); r.raise_for_status(); return r.json()
def body(state,scope): return dict(request_id=str(uuid.uuid4()),document_version_id=state['document_version_id'],file_hash=state['file_hash'],analysis_basis_token=state['analysis_basis_token'],expected_decision_revision=(state['decision'] or {}).get('revision',0),material_scope=scope,note='已核对测试原件机构及编报范围',evidence_reference='原始报送说明与第3页机构设置',acknowledge_conflicts=False)
try:
 with tempfile.TemporaryDirectory(prefix='gbc-batch2-online-') as workstr:
  work=pathlib.Path(workstr); password=secrets.token_hex(24); envfile=work/'postgres.env'; envfile.write_text(f'POSTGRES_PASSWORD={password}\nPOSTGRES_USER=validator\nPOSTGRES_DB=validation\n'); envfile.chmod(0o600)
  old.docker('run','-d','--rm','--name',name,'--env-file',str(envfile),'-p','127.0.0.1::5432','postgres:16')
  port=int(old.docker('port',name,'5432/tcp').rsplit(':',1)[1]); dsn=f'postgresql://validator:{password}@127.0.0.1:{port}/validation'
  for _ in range(80):
   if subprocess.run(['docker','exec',name,'pg_isready','-U','validator','-d','validation'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0: break
   time.sleep(.3)
  asyncio.run(old.prepare_db(dsn))
  env=os.environ.copy()
  for key in list(env):
   if key.startswith(('AI_','OPENAI_','GEMINI_','ZHIPU_','DEEPSEEK_','CODEX_API_')) or key in {'PG_SCHEMA','DATABASE_URL'}: env.pop(key)
  api_key,admin_password,session_secret=[secrets.token_hex(24) for _ in range(3)]; ap=old.free_port(); uploads=work/'uploads'
  env.update(PYTHON_DOTENV_DISABLED='1',TESTING='false',DATABASE_URL=dsn,GOVBUDGET_AUTH_ENABLED='true',GOVBUDGET_API_KEY=api_key,USER_SESSION_SECRET=session_secret,DEFAULT_ADMIN_PASSWORD=admin_password,USER_DATA_DIR=str(work/'users'),ORG_DATA_DIR=str(work/'orgs'),UPLOAD_DIR=str(uploads),AUDIT_LOG_PATH=str(OUT/'audit.jsonl'),AI_ASSIST_ENABLED='false',AI_ASSIST_REQUIRED='false',AI_ENABLED='false',OCR_ENABLED='false',JOB_QUEUE_ENABLED='true',JOB_QUEUE_ROLE='all',JOB_QUEUE_WORKERS='2',JOB_QUEUE_POLL_INTERVAL_SECONDS='0.2',JOB_QUEUE_INLINE_FALLBACK='false',READY_REQUIRE_DATABASE='true',READY_REQUIRE_AI_EXTRACTOR='false')
  log=(OUT/'api.log').open('w'); handles.append(log); proc=subprocess.Popen([sys.executable,'-m','uvicorn','api.main:app','--host','127.0.0.1','--port',str(ap)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT); processes.append(proc)
  with httpx.Client(base_url=f'http://127.0.0.1:{ap}',timeout=180,headers={'X-API-Key':api_key}) as c:
   for _ in range(120):
    if proc.poll() is not None: raise RuntimeError('API exited; see log')
    try:
     if c.get('/health').status_code==200: break
    except httpx.TransportError: pass
    time.sleep(.5)
   r=c.post('/api/auth/login',json={'username':'admin','password':admin_password}); r.raise_for_status(); c.headers['X-Session-Token']=r.json()['token']; changed=secrets.token_hex(24)
   c.post('/api/auth/change-password',json={'old_password':admin_password,'new_password':changed}).raise_for_status(); r=c.post('/api/auth/login',json={'username':'admin','password':changed}); r.raise_for_status(); c.headers['X-Session-Token']=r.json()['token']
   check('ready',c.get('/ready').status_code==200)
   r=c.post('/api/organizations',json={'name':'上海市信访办公室','level':'department'}); r.raise_for_status(); dept=r.json()['id']
   r=c.post('/api/organizations',json={'name':'上海市信访办公室单位','level':'unit','parent_id':dept}); r.raise_for_status(); unit=r.json()['id']
   pdf=pathlib.Path('/Volumes/workSSD/software development/GovBudgetChecker/uploads/67217a651ab2f9e7f64dba9d85c7742d/上海市信访办公室2024年决算.pdf')
   job,first=analyze(c,pdf,unit,2024); (OUT/'petition-before-confirmation.json').write_text(json.dumps(first,ensure_ascii=False,indent=2))
   check('petition_7_to_1',block(first)==1)
   check('subject_alias_verified',first['result']['meta']['check_subject']['source']=='verified_cover_alias')
   state=info(c,job); request=body(state,'unit_self'); endpoint='/api/jobs/'+job+'/document-scope'
   check('no_default_confirmation',state['decision'] is None and state['suggestion_is_confirmation'] is False)
   check('bad_basis_rejected',c.post(endpoint,json={**request,'analysis_basis_token':'stale'}).status_code==409)
   check('bad_version_rejected',c.post(endpoint,json={**request,'document_version_id':state['document_version_id']+1}).status_code==409)
   check('missing_reason_rejected',c.post(endpoint,json={**request,'note':' '}).status_code==422)
   review=c.get('/api/reviews',params={'job_uuid':job}); review.raise_for_status(); slot=review.json()['data']['review_context']['slot_id']; c.post('/api/reviews/'+slot+'/start',json={'job_uuid':job}).raise_for_status()
   r=c.post(endpoint,json=request); check('confirm_accepted',r.status_code==202)
   check('old_review_cannot_complete',c.post('/api/reviews/'+slot+'/complete',json={'job_uuid':job}).status_code==409)
   second=wait(c,job); (OUT/'petition-after-confirmation.json').write_text(json.dumps(second,ensure_ascii=False,indent=2))
   check('petition_1_to_0',block(second)==0)
   check('new_analysis_revision',second['analysis_revision']!=first['analysis_revision'])
   check('real_terminology_finding_retained',any(x.get('rule_id')=='CMM-018' for x in second['result'].get('issues',{}).get('all',[])))
   revision=second['analysis_revision']; check('idempotent_retry',c.post(endpoint,json=request).status_code==202); time.sleep(.5); check('idempotent_no_rerun',c.get('/api/jobs/'+job+'/status').json()['analysis_revision']==revision)
   current=info(c,job); check('confirmation_published',current['decision']['dispatch_status']=='applied')
   reset=body(current,'unknown'); reset2={**reset,'request_id':str(uuid.uuid4())}
   def send(b): return httpx.post(f'http://127.0.0.1:{ap}'+endpoint,headers=dict(c.headers),cookies=c.cookies,json=b,timeout=120).status_code
   with concurrent.futures.ThreadPoolExecutor(2) as pool: codes=list(pool.map(send,[reset,reset2]))
   check('concurrent_first_writer_only',sorted(codes)==[202,409]); resetstatus=wait(c,job); check('unknown_restores_blocker',block(resetstatus)==1)
   # Simulate a crash after the confirmation transaction but before file/queue dispatch.
   proc.terminate(); proc.wait(timeout=10)
   proc=subprocess.Popen([sys.executable,'-m','uvicorn','api.main:app','--host','127.0.0.1','--port',str(ap)],cwd=ROOT,env={**env,'JOB_QUEUE_ROLE':'api'},stdout=log,stderr=subprocess.STDOUT); processes.append(proc)
   for _ in range(80):
    try:
     if c.get('/health').status_code==200: break
    except httpx.TransportError: pass
    time.sleep(.2)
   persisted_request=body(info(c,job),'unit_self'); reqfile=work/'scope-request.json'; reqfile.write_text(json.dumps(persisted_request))
   persist_code="""import asyncio,json,sys,hashlib
from pathlib import Path
from src.db.connection import DatabaseConnection
from src.services.review_lifecycle_service import confirm_document_scope
async def main():
 await DatabaseConnection.initialize()
 conn=await DatabaseConnection.acquire()
 try:
  request=json.loads(Path(sys.argv[2]).read_text())
  row=await confirm_document_scope(conn,sys.argv[1],actor='admin',request=request,actual_file_hash=request['file_hash'])
  print(row['dispatch_status'])
 finally:
  await DatabaseConnection.release(conn); await DatabaseConnection.close()
asyncio.run(main())
"""
   prepared=subprocess.run([sys.executable,'-c',persist_code,job,str(reqfile)],env=env,cwd=ROOT,capture_output=True,text=True)
   if prepared.returncode: raise RuntimeError(prepared.stderr[-1800:])
   pending=info(c,job); check('transaction_committed_outbox_pending',pending['decision']['dispatch_status']=='pending' and pending['analysis_completed'] is False)
   check('old_file_did_not_fake_reanalysis',json.loads((uploads/job/'status.json').read_text())['status']=='review_required')
   pending_view=c.get('/api/jobs/'+job+'/status').json()
   check('pending_db_state_visible_before_dispatch',pending_view['status']=='queued' and pending_view['scope_reanalysis']['dispatch_status']=='pending')
   original_bytes=(uploads/job/pdf.name).read_bytes(); (uploads/job/pdf.name).write_bytes(original_bytes+b'\n% isolated hash conflict probe\n')
   retry_code="import asyncio; from src.services.document_scope import dispatch_pending; asyncio.run(dispatch_pending())"
   attempted=subprocess.run([sys.executable,'-c',retry_code],env=env,cwd=ROOT,capture_output=True,text=True)
   if attempted.returncode: raise RuntimeError(attempted.stderr[-1200:])
   failed_dispatch=info(c,job)['decision']
   check('source_hash_conflict_keeps_pending_with_backoff',failed_dispatch['dispatch_status']=='pending' and failed_dispatch['dispatch_attempts']==1 and bool(failed_dispatch['retry_not_before']))
   (uploads/job/pdf.name).write_bytes(original_bytes)

   proc.terminate(); proc.wait(timeout=10)
   proc=subprocess.Popen([sys.executable,'-m','uvicorn','api.main:app','--host','127.0.0.1','--port',str(ap)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT); processes.append(proc)
   for _ in range(80):
    try:
     if c.get('/health').status_code==200: break
    except httpx.TransportError: pass
    time.sleep(.2)
   recovered=wait(c,job); check('cold_start_recovers_undispatched_confirmation',block(recovered)==0 and info(c,job)['decision']['dispatch_status']=='applied')
   (OUT/'petition-cold-recovery.json').write_text(json.dumps(recovered,ensure_ascii=False,indent=2))
   r=c.post('/api/organizations',json={'name':'上海市公安局','level':'department'}); r.raise_for_status(); police=r.json()['id']
   pj,ps=analyze(c,pathlib.Path('/Volumes/workSSD/software development/GovBudgetChecker/uploads/8a455c636430a5484e5375a1ca1bcba6/上海市公安局2023年度部门决算.pdf'),police,2023)
   (OUT/'police-analysis.json').write_text(json.dumps(ps,ensure_ascii=False,indent=2)); check('police_genuine_gap_retained',block(ps)==1)
   gap=next(x for x in ps['result']['meta']['obligation_coverage']['instances'] if x['obligation_id']=='OBL-TXT-T5')['detail']
   check('police_source_difference_visible',all(word in gap for word in ['第23页','第13页','其他行政单位养老支出','其他行政事业单位养老支出','尚未绑定金额']))
   pi=info(c,pj); conflict=body(pi,'unit_self'); check('source_conflict_requires_acknowledgement',c.post('/api/jobs/'+pj+'/document-scope',json=conflict).status_code==409)
   with httpx.Client(base_url=f'http://127.0.0.1:{ap}') as anonymous:
    check('anonymous_get_rejected',anonymous.get(endpoint).status_code==401); check('anonymous_post_rejected',anonymous.post(endpoint,json=request).status_code==401)
   # Keep source-bound contexts for independent numeric comparison after disposable uploads are removed.
   for label,j in [('petition',job),('police',pj)]:
    for filename in ['fact_context.json','structured_ingest.json']:
     target=uploads/j/filename
     if target.exists(): (OUT/(label+'-'+filename)).write_bytes(target.read_bytes())
   checks['petition_job']=job; checks['police_job']=pj
except Exception as exc:
 checks['error']=repr(exc); raise
finally:
 for p in processes:
  p.terminate()
  try:p.wait(timeout=10)
  except subprocess.TimeoutExpired:p.kill();p.wait()
 for h in handles:h.close()
 try:old.docker('rm','-f',name,timeout=30)
 except Exception:pass
 (OUT/'validation-receipt.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)); print(json.dumps(checks,ensure_ascii=False,indent=2))
