"""本轮独立临时实例：新数据库/代码快照/端口/上传，不重用执行者state或凭据。"""
import asyncio
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import zipfile

import httpx
import pymupdf

ROOT=Path(__file__).resolve().parents[1]
CAND=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
MAIN=Path('/Volumes/workSSD/software development/GovBudgetChecker')
DELIVERY=CAND/'outputs/unified_review_remediation_20261106'
COMMIT='69884e9c1d7fee91e530b952b7e5b1935802e987'
ATTEMPT=os.environ.get('GBC_REVIEW_ATTEMPT','1')
assert ATTEMPT in {'1','2'}
PRIVATE=ROOT/('runtime_private' if ATTEMPT=='1'else'runtime_private_2')
OUT=ROOT/('live_receipts' if ATTEMPT=='1'else'live_receipts_2')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2))
def free_port():
    with socket.socket()as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def run(cmd,**kw):return subprocess.run(cmd,check=True,capture_output=True,**kw)
def docker(*args):return run(['docker',*args],text=True).stdout.strip()

def main():
    PRIVATE.mkdir(exist_ok=False);PRIVATE.chmod(0o700);OUT.mkdir()
    code=PRIVATE/'code';code.mkdir()
    archive=PRIVATE/'candidate.tar'
    with archive.open('wb')as f:subprocess.run(['git','archive',COMMIT],cwd=CAND,stdout=f,check=True)
    with tarfile.open(archive)as tf:tf.extractall(code,filter='data')
    for name in ['uploads','org','users','tmp','logs','ai','responses']:(PRIVATE/name).mkdir()
    for n in ['organizations.json','job_org_links.json']:
        shutil.copyfile(MAIN/'data'/n,PRIVATE/'org'/n)
    save(PRIVATE/'ai/settings.json',{'revision':1,'enabled':False,'default_use_ai':False,'active_profile_id':None,'profiles':[]})
    save(PRIVATE/'calendar.json',{})
    password=secrets.token_hex(24);key=secrets.token_hex(24);secret=secrets.token_hex(32);admin=secrets.token_hex(18)
    dbport=free_port();apiport=free_port()
    container='codex-ind-r2-'+str(os.getpid())+'-'+secrets.token_hex(3)
    (PRIVATE/'postgres.env').write_text(f'POSTGRES_USER=validator\nPOSTGRES_DB=validation\nPOSTGRES_PASSWORD={password}\n')
    (PRIVATE/'postgres.env').chmod(0o600)
    dsn=f'postgresql://validator:{password}@127.0.0.1:{dbport}/validation'
    # 保留宿主HOME原值；不继承业务密钥、数据库地址或任何AI配置。
    env={k:v for k,v in os.environ.items()if k in {'PATH','HOME','LANG','LC_ALL','SHELL','TZ'}}
    env.update({'PYTHONPATH':str(code),'PYTHON_DOTENV_DISABLED':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1',
        'DATABASE_URL':dsn,'GOVBUDGET_AUTH_ENABLED':'true','GOVBUDGET_API_KEY':key,'USER_SESSION_SECRET':secret,
        'DEFAULT_ADMIN_PASSWORD':admin,'REQUIRE_FIRST_LOGIN_PASSWORD_CHANGE':'false','ALLOW_INSECURE_DEFAULT_ADMIN':'1',
        'USER_DATA_DIR':str(PRIVATE/'users'),'ORG_DATA_DIR':str(PRIVATE/'org'),'UPLOAD_DIR':str(PRIVATE/'uploads'),
        'AUDIT_LOG_PATH':str(PRIVATE/'logs/audit.jsonl'),'TMPDIR':str(PRIVATE/'tmp'),'AI_ASSIST_ENABLED':'false',
        'AI_ASSIST_REQUIRED':'false','AI_ENABLED':'false','AI_EXTRACTOR_DIRECT_FALLBACK':'false',
        'AI_EXTRACTOR_URL':'http://127.0.0.1:9/ai/extract/v1','AI_MODEL_SETTINGS_PATH':str(PRIVATE/'ai/settings.json'),
        'OCR_ENABLED':'false','JOB_QUEUE_ENABLED':'true','JOB_QUEUE_INLINE_FALLBACK':'false','JOB_QUEUE_WORKERS':'1',
        'JOB_QUEUE_POLL_INTERVAL_SECONDS':'0.5','JOB_QUEUE_RESUME_ON_START':'true','READY_REQUIRE_DATABASE':'true',
        'READY_REQUIRE_AI_EXTRACTOR':'false','DISCLOSURE_CALENDAR_PATH':str(PRIVATE/'calendar.json'),
        'GBC_TEMPLATE_STORAGE':str(PRIVATE/'uploads/.templates'),'GBC_TEMPLATE_SCOPE':'0','GBC_BUDGET_TEMPLATE_SCOPE':'0',
        'JOB_LIST_CACHE_WARMUP':'false'})
    processes=[];created=False;http=[];report={'overall':'IN_PROGRESS','source_commit':COMMIT,
       'source_archive_sha256':sha(archive),'profile':'isolated Shanghai native PDFs; AI/OCR/template gates disabled',
       'independent_state':True,'executor_state_read':False,'daily_entry_touched':False,'uploads':[],'exports':[]}
    phase='create_database'
    try:
        docker('run','--pull=never','-d','--name',container,'--env-file',str(PRIVATE/'postgres.env'),'-p',f'127.0.0.1:{dbport}:5432','postgres:16');created=True
        for _ in range(120):
            r=subprocess.run(['docker','exec',container,'pg_isready','-U','validator','-d','validation'],capture_output=True)
            if r.returncode==0:break
            time.sleep(.5)
        else:raise RuntimeError('database startup timeout')
        phase='migrations'
        migration=('import asyncio\nfrom src.db.connection import DatabaseConnection\nfrom src.db.migrations import run_migrations\n'
          'async def go():\n await DatabaseConnection.initialize(__import__("os").environ["DATABASE_URL"])\n await run_migrations()\n await DatabaseConnection.close()\nasyncio.run(go())\n')
        r=subprocess.run([sys.executable,'-c',migration],cwd=code,env=env,capture_output=True,timeout=600)
        (PRIVATE/'logs/migrations.log').write_bytes(r.stdout+r.stderr)
        if r.returncode:raise RuntimeError('migration failed')
        for name,args,role in [('api',['-m','uvicorn','api.main:app','--host','127.0.0.1','--port',str(apiport)],'api'),
                               ('worker',['-m','api.worker'],'worker')]:
            with(PRIVATE/'logs'/f'{name}.log').open('wb')as log:
                processes.append(subprocess.Popen([sys.executable,*args],cwd=code,env={**env,'JOB_QUEUE_ROLE':role},stdout=log,stderr=subprocess.STDOUT,start_new_session=True))
        base=f'http://127.0.0.1:{apiport}';phase='api_startup'
        for _ in range(90):
            try:
                r=httpx.get(base+'/api/health',timeout=2)
                if r.status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(1)
        else:raise RuntimeError('api startup timeout')
        client=httpx.Client(base_url=base,headers={'X-API-Key':key},timeout=180)
        r=client.post('/api/auth/login',json={'username':'admin','password':admin});r.raise_for_status()
        client.headers['X-Session-Token']=r.json()['token']
        def request(method,path,**kw):
            response=client.request(method,path,**kw)
            item={'method':method,'path':path.split('?')[0],'http':response.status_code,
                  'response_sha256':hashlib.sha256(response.content).hexdigest()}
            if response.status_code==409:item['response']=response.json()
            http.append(item)
            # 登录除外的原响应只存私有本地目录，不进入脱敏索引。
            (PRIVATE/'responses'/f'{len(http):03d}.body').write_bytes(response.content)
            return response
        def get(path):
            r=request('GET',path);r.raise_for_status();return r.json()
        selections={r['order']:r for r in json.loads((DELIVERY/'flow/org_selection.json').read_text())['items']}
        manifest=json.loads((DELIVERY/'flow/manifest.json').read_text())['items']
        jobs={};phase='fresh_uploads'
        for item in manifest:
            order=item['order'];pdf=Path(item['pdf_path']);selection=selections[order]
            with pdf.open('rb')as f:
                r=request('POST','/api/documents/upload',data={'mode':'dual','use_local_rules':'true','use_ai_assist':'false',
                     'doc_type':'unit_final','enqueue':'true','org_unit_id':selection['org_id'],
                     'fiscal_year':str(item['year']),'report_year':str(item['year'])},files={'file':(pdf.name,f,'application/pdf')})
            r.raise_for_status();upload=r.json();job=upload.get('job_id')or upload['id']
            for _ in range(180):
                status=get(f'/api/jobs/{job}/status')
                if status['status']in {'review_required','completed','error','failed'}:break
                time.sleep(2)
            else:raise RuntimeError('analysis timeout')
            assert status['status']not in {'error','failed'}
            assert status['checksum']==sha(pdf)==upload['checksum']
            assert status['organization_name']==selection['cover_subject']
            jobs[order]=(job,status)
            report['uploads'].append({'order':order,'pdf_sha256':sha(pdf),'job_id':job,'analysis_revision':status['analysis_revision'],
               'organization':status['organization_name'],'year':status['fiscal_year'],'status':status['status']})
        phase='review_lifecycle';job,status=jobs[7];rev=status['analysis_revision'];coverage=status['check_coverage']
        archive_status=PRIVATE/'uploads'/job/'analysis_versions'/rev/'status.json';original_sha=sha(archive_status)
        review=get('/api/reviews?job_uuid='+job)['data']['review'];slot=review['slot_id']
        request('POST',f'/api/reviews/{slot}/start',json={'job_uuid':job}).raise_for_status()
        review=get('/api/reviews?job_uuid='+job)['data']['review'];basis=review['current_analysis']['analysis_basis_token']
        old_note='IND-R2-独立原件预算空白待核';new_note='IND-R2-独立新代际预算仍待核'
        body={'decision':'pending','job_uuid':job,'analysis_basis_token':basis,'note':old_note,
              'evidence_reference':'独立核读原件p12预算格；p18决算零不填预算'}
        request('PUT',f'/api/reviews/{slot}/obligations/OBL-SG-TOTAL',json=body).raise_for_status()
        issue=status['review_report']['issues'][0]['id']
        request('POST','/api/workflow',json={'action':'update_issue','job_id':job,'issue_id':issue,
             'status':'needs_review','note':'独立流程验证；保留待核，不修改引擎金额'}).raise_for_status()
        def exports(label,revision=None,want=None,forbid=None):
            for fmt in ['json','csv','pdf','docx']:
                params={'job_id':job,'format':fmt}
                if revision:params['analysis_revision']=revision
                r=request('GET','/api/reports/download',params=params);r.raise_for_status()
                target=PRIVATE/f'{label}.{fmt}';target.write_bytes(r.content)
                if fmt=='pdf':
                    with pymupdf.open(target)as d:text=''.join(p.get_text()for p in d)
                elif fmt=='docx':
                    with zipfile.ZipFile(target)as z:text=z.read('word/document.xml').decode()
                else:text=r.content.decode('utf-8-sig')
                text=''.join(text.split())
                assert want is None or want in text
                assert forbid is None or forbid not in text
                report['exports'].append({'label':label,'format':fmt,'sha256':sha(target),'expected_note':True,'forbidden_note_absent':True})
        exports('current_before',want=old_note)
        rejected=request('POST',f'/api/reviews/{slot}/complete',json={'job_uuid':job});assert rejected.status_code==409
        assert get(f'/api/jobs/{job}/status')['check_coverage']==coverage
        request('POST',f'/api/jobs/{job}/reanalyze',json={'mode':'dual','use_local_rules':True,'use_ai_assist':False}).raise_for_status()
        for _ in range(180):
            fresh=get(f'/api/jobs/{job}/status')
            if fresh['status']in {'review_required','completed','error','failed'}and fresh.get('analysis_revision')!=rev:break
            time.sleep(2)
        else:raise RuntimeError('reanalysis timeout')
        assert fresh['status']not in {'error','failed'}and sha(archive_status)==original_sha
        exports('after_reanalysis',forbid=old_note)
        assert not get('/api/reviews?job_uuid='+job)['data']['review'].get('current_session')
        request('POST',f'/api/reviews/{slot}/start',json={'job_uuid':job}).raise_for_status()
        current=get('/api/reviews?job_uuid='+job)['data']['review'];new_basis=current['current_analysis']['analysis_basis_token'];assert new_basis!=basis
        stale=request('PUT',f'/api/reviews/{slot}/obligations/OBL-SG-TOTAL',json=body);assert stale.status_code==409
        request('PUT',f'/api/reviews/{slot}/obligations/OBL-SG-TOTAL',json={**body,'analysis_basis_token':new_basis,'note':new_note}).raise_for_status()
        exports('new_session',want=new_note,forbid=old_note);exports('historical',revision=rev,forbid=new_note)
        assert get(f'/api/jobs/{job}/fact-snapshot?limit=1')['revision_id']==fresh['analysis_revision']
        phase='database_readback'
        sql="SELECT json_build_object('decisions',(SELECT json_agg(json_build_object('obligation_id',obligation_id,'decision',decision,'note',note,'revision',revision)) FROM review_obligation_decisions),'sessions',(SELECT json_agg(json_build_object('status',status)) FROM review_sessions));"
        db=json.loads(docker('exec',container,'psql','-U','validator','-d','validation','-Atc',sql))
        assert len(db['decisions'])==2 and {r['decision']for r in db['decisions']}=={'pending'}
        assert {r['status']for r in db['sessions']}=={'invalidated','in_progress'}
        save(OUT/'independent_sql_readback.json',db)
        report.update(overall='PASS',old_analysis_revision=rev,new_analysis_revision=fresh['analysis_revision'],
           incomplete_completion_409=rejected.json(),stale_basis_409=stale.json(),archive_unchanged=True,
           manual_note_did_not_complete_engine=True,independent_live_http_count=len(http),browser_reexecuted=False)
        # 新建独立浏览器实例所需凭据只留在私有目录，不回显，也不进入索引。
        save(PRIVATE/'browser_access.json',{'api_port':apiport,'api_key':key,'admin_password':admin,'session_secret':secret,'job_id':job})
        if os.environ.get('GBC_REVIEW_KEEP_STACK')=='1':
            report['temporarily_running_for_browser']=True
            save(PRIVATE/'owned_processes.json',{'pids':[p.pid for p in processes],'container':container})
            processes=[];created=False
    except Exception as exc:
        report.update(overall='FAIL',failed_phase=phase,error_type=type(exc).__name__)
    finally:
        save(OUT/'independent_http_receipts.json',http)
        for process in processes:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        if created:
            stopped=docker('stop',container)
            report['private_database_stopped']=stopped==container
        save(OUT/'independent_live_report.json',report)
    print(json.dumps({'overall':report['overall'],'failed_phase':report.get('failed_phase'),
        'uploads':len(report['uploads']),'exports':len(report['exports']),'http_receipts':len(http),'daily_entry_touched':False},ensure_ascii=False))
    return 0 if report['overall']=='PASS'else 1

if __name__=='__main__':raise SystemExit(main())
