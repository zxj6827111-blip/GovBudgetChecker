"""Restore only this run's acceptance DB; continue independent controls after first failure."""
import os,sys,json,hashlib,shutil,subprocess,tempfile,time,secrets,asyncio,uuid,importlib.util
from pathlib import Path
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker');sys.path.insert(0,str(ROOT))
from scripts.run_isolated_service_validation import docker,port,service_lifetime,request_with_backoff,get_report_with_backoff
import httpx
BASE=ROOT/'outputs/pdf_acceptance_shanghai2022_20261021';OUT=BASE/'continued_controls_v2';OUT.mkdir(exist_ok=True)
def write(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
receipt={'scope':'restored_disposable_acceptance_database_controls_only','production_logic_changed':False,'steps':{},'errors':[]}
processes=[];handles=[];name='gbc-sh2022-controls-'+uuid.uuid4().hex[:8]
first=json.loads((BASE/'runtime_native/service_receipt.json').read_text());control=first['scenarios'][-1];job=control['job_id']
try:
 with tempfile.TemporaryDirectory(prefix='gbc-sh2022-controls-')as temp,service_lifetime(processes,handles):
  work=Path(temp);password=secrets.token_hex(24);secretfile=work/'postgres.env';secretfile.write_text(f'POSTGRES_PASSWORD={password}\nPOSTGRES_USER=validator\nPOSTGRES_DB=validation\n');secretfile.chmod(0o600)
  docker('run','-d','--rm','--name',name,'--env-file',str(secretfile),'-p','127.0.0.1::5432','postgres:16')
  pg_port=int(docker('port',name,'5432/tcp').rsplit(':',1)[1]);dsn=f'postgresql://validator:{password}@127.0.0.1:{pg_port}/validation'
  for _ in range(90):
   r=subprocess.run(['docker','exec',name,'pg_isready','-U','validator','-d','validation'],capture_output=True)
   if r.returncode==0:break
   time.sleep(.5)
  with (BASE/'runtime_native/acceptance_database.dump').open('rb')as data:
   r=subprocess.run(['docker','exec','-i',name,'pg_restore','-U','validator','-d','validation','--no-owner','--no-acl'],stdin=data,capture_output=True)
  if r.returncode:raise RuntimeError('disposable restore failed: '+r.stderr.decode()[-1000:])
  shutil.copytree(BASE/'runtime_native/runtime_uploads',work/'uploads')
  env=os.environ.copy()
  for k in list(env):
   if k.startswith(('AI_','OPENAI_','GEMINI_','ZHIPU_','DEEPSEEK_','CODEX_API_')):env.pop(k)
  api_key=secrets.token_hex(24);admin=secrets.token_hex(24)
  env.update(GOVBUDGET_RATE_LIMIT='10000',PYTHON_DOTENV_DISABLED='1',TESTING='false',DATABASE_URL=dsn,GOVBUDGET_AUTH_ENABLED='true',GOVBUDGET_API_KEY=api_key,USER_SESSION_SECRET=secrets.token_hex(24),DEFAULT_ADMIN_PASSWORD=admin,USER_DATA_DIR=str(work/'users'),ORG_DATA_DIR=str(work/'orgs'),UPLOAD_DIR=str(work/'uploads'),AUDIT_LOG_PATH=str(OUT/'audit.jsonl'),AI_ASSIST_ENABLED='false',AI_ASSIST_REQUIRED='false',AI_ENABLED='false',OCR_ENABLED='false',OCR_MODEL_FINGERPRINT='',JOB_QUEUE_ENABLED='true',JOB_QUEUE_INLINE_FALLBACK='false',JOB_QUEUE_WORKERS='1',JOB_QUEUE_POLL_INTERVAL_SECONDS='.5',READY_REQUIRE_DATABASE='true',READY_REQUIRE_AI_EXTRACTOR='false')
  api_port=port();receipt.update(container=name,api_port=api_port,database_restore_source_sha256=sha(BASE/'runtime_native/acceptance_database.dump'))
  for role,cmd in [('api',[sys.executable,'-m','uvicorn','api.main:app','--host','127.0.0.1','--port',str(api_port)]),('worker',[sys.executable,'-m','api.worker'])]:
   log=(OUT/(role+'.log')).open('w');handles.append(log);p=subprocess.Popen(cmd,cwd=ROOT,env={**env,'JOB_QUEUE_ROLE':role},stdout=log,stderr=subprocess.STDOUT);processes.append(p)
  try:
   with httpx.Client(base_url=f'http://127.0.0.1:{api_port}',headers={'X-API-Key':api_key},timeout=180)as client:
    for _ in range(120):
     try:
      if client.get('/health').status_code==200:break
     except httpx.TransportError:pass
     time.sleep(.5)
    response=request_with_backoff(client,'POST','/api/auth/login',json={'username':'admin','password':admin});response.raise_for_status();client.headers['X-Session-Token']=response.json()['token'];changed=secrets.token_hex(24)
    response=request_with_backoff(client,'POST','/api/auth/change-password',json={'old_password':admin,'new_password':changed});response.raise_for_status()
    response=request_with_backoff(client,'POST','/api/auth/login',json={'username':'admin','password':changed});response.raise_for_status();client.headers['X-Session-Token']=response.json()['token']
    # The older source job rejection was a harness ordering collision with a newer upload.
    # Run mapping on the latest source job, preserving the failed first attempt unchanged.
    receipt['prior_passed_controls_reference']='continued_controls/progress.json; not rerun in this profile'
    receipt['isolated_environment_override']={'GOVBUDGET_RATE_LIMIT':10000,'default_limit':100,'purpose':'functional page suite only; default rate-limit failure remains open'}
    # Full batch browser targets immutable first revisions, including legacy historical views.
    cases=[]
    for s in first['scenarios']:
     if s['scenario']=='native_correction' or not s.get('service_validation_passed'):continue
     d=BASE/'runtime_native'/s['scenario'];snap=json.loads((d/'immutable_snapshot.json').read_text());src=next(x for x in json.loads((BASE/'source_manifest.json').read_text())if x['sha256']==s['source_sha256'])
     cases.append({'doc_id':src['doc_id'],'source_sha256':src['sha256'],'job_id':s['job_id'],'revision_id':snap['revision_id'],'snapshot_hash':snap['snapshot_hash'],'raw_count':len(snap['raw_context']['facts']),'effective_count':len(snap['effective_context']['facts']),'json_sha256':sha(d/'report.json')})
    write(OUT/'all_page_cases.json',cases);env['UAT_ALL_CASES']=str(OUT/'all_page_cases.json')
    # Helper's native_final label is a real saved first revision of the latest correction-control job.
    shutil.copytree(BASE/'runtime_native/native_correction',OUT/'native_final',ignore=shutil.ignore_patterns('corrected'),dirs_exist_ok=True)
    current=client.get(f'/api/jobs/{job}/status');current.raise_for_status();write(OUT/'latest_control_status_before_browser.json',current.json());new_revision=current.json()['analysis_revision']
    download=get_report_with_backoff(client,params={'job_id':job,'format':'json','analysis_revision':new_revision});download.raise_for_status();(OUT/'latest_control_report.json').write_bytes(download.content)
    fact_case={'job_id':job,'old_revision':control['correction']['old_revision'],'new_revision':new_revision,'old_json_sha256':control['exports']['report.json'],'new_json_sha256':hashlib.sha256(download.content).hexdigest()}
    spec=importlib.util.spec_from_file_location('extended_all_pages',BASE/'harness/extended_all_pages.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    try:receipt['steps']['extended']=module.run_extended_validation(client=client,env=env,work=work,out=OUT,container=name,pg_port=pg_port,password=changed,processes=processes,handles=handles,fact_case=fact_case)
    except Exception as e:receipt['steps']['extended']={'passed':False,'error':str(e)}
  finally:
   for p in reversed(processes):
    if p.poll()is None:
     p.terminate()
     try:p.wait(timeout=20)
     except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
   shutil.copytree(work/'uploads',OUT/'runtime_uploads',dirs_exist_ok=True)
   dump=subprocess.run(['docker','exec',name,'pg_dump','-U','validator','-d','validation','-Fc'],capture_output=True)
   if dump.returncode==0:(OUT/'acceptance_database.dump').write_bytes(dump.stdout)
except Exception as e:receipt['errors'].append(type(e).__name__+': '+str(e))
finally:
 r=subprocess.run(['docker','rm','-f',name],capture_output=True);receipt['container_removed']=r.returncode==0;
 p=OUT/'extended/all_material_pages/receipt.json'
 if p.exists():receipt['steps']['all_material_pages']=json.loads(p.read_text())
 receipt['processes_stopped']=all(p.poll()is not None for p in processes);receipt['passed']=bool(receipt['steps'])and all(s.get('passed')for s in receipt['steps'].values())and not receipt['errors'];write(OUT/'receipt.json',receipt);print(json.dumps(receipt,ensure_ascii=False,default=str)[:2000])
