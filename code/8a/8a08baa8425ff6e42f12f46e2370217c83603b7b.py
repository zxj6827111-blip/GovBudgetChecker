"""保全32并发失败现场，部署已验证UI，在现成API/worker分离模式下续跑。"""
import gzip,hashlib,json,os,shutil,subprocess,time,urllib.request
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];MAIN=ROOT/'artifacts/daily-main';WORK=ROOT/'artifacts/comprehensive-review-closure-20261109'
def read(name):return json.loads((OUT/name).read_text())
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def git(directory,*args):return subprocess.check_output(['git',*args],cwd=directory,text=True).strip()
def write(name,data):(OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2))
def ids(port):return {int(x) for x in subprocess.run(['lsof','-t',f'-iTCP:{port}','-sTCP:LISTEN'],capture_output=True,text=True).stdout.split()}
old=read('release_candidate.json');OLD=old['candidate'];NEW=git(WORK,'rev-parse','HEAD');proof=read('frontend_final_test_receipt.json')
assert OLD=='cdbf028ebb7b93bd3c7784c7ad5c6faea1f2e253' and NEW.startswith('5eedf5d')
assert set(git(WORK,'diff','--name-only',OLD,NEW).splitlines())=={'app/lib/issueCategories.ts','app/tests/issueCategories.test.ts'}
assert git(MAIN,'rev-parse','HEAD')==OLD and not git(MAIN,'status','--porcelain','--untracked-files=no')
assert not git(WORK,'status','--porcelain','--untracked-files=no') and not ids(18743) and not ids(3000)
assert proof['all_checks_passed'] and all(h(WORK/n)==sha for n,sha in proof['source_hashes'].items())
assert (WORK/'app/.next/BUILD_ID').read_text().strip()==proof['build_id']
code=read('implementation_manifest.json');assert all(h(WORK/n)==sha for n,sha in code.items() if n.startswith(('src/','api/')))
assert '5717 passed, 197 skipped' in (OUT/'backend-final7-release.log').read_text()
assert read('postgres-final7_test_receipt.json')['exit_code']==0
assert len(read('release_final7_summary.json'))==345 and not read('release_final7_comparison.json')['errors']
before=read('preservation_before.json');assert all(h(ROOT/n)==sha for n,sha in before['root_modified_files'].items())
assert all(h(ROOT/n)==sha for n,sha in before['pdfs'].items()) and h(ROOT/'.env')==before['root_env_sha256']
selected=[r['job'] for r in read('daily_latest_preview.json')['selected']]
statuses={j:json.loads((ROOT/'uploads'/j/'status.json').read_text()) for j in selected}
assert sum(s.get('status')=='error' and s.get('error')=='pipeline_cancelled' for s in statuses.values())==54
assert sum(s.get('status')=='queued' for s in statuses.values())==32
assert sum(s.get('status') in {'done','review_required'} for s in statuses.values())==25
frozen=OUT/'engine_cdbf_acceptance';frozen.mkdir(exist_ok=False)
for name in ['release_candidate.json','implementation_manifest.json','deployment_final_receipt.json','daily_final_progress.json','daily_final_progress.log','final_orchestration.log']:
 shutil.copy2(OUT/name,frozen/name)
private=OUT/'private/cancelled_attempt_statuses.json.gz';private.write_bytes(gzip.compress(json.dumps(statuses,ensure_ascii=False).encode()));private.chmod(0o600)
staged=MAIN/'app'/('.next-ui-candidate-'+NEW[:8]);backup=MAIN/'app'/('.next-before-ui-'+NEW[:8]);assert not staged.exists() and not backup.exists()
shutil.copytree(WORK/'app/.next',staged,symlinks=True)
subprocess.run(['git','merge','--ff-only',NEW],cwd=MAIN,check=True,capture_output=True)
(MAIN/'app/.next').rename(backup);staged.rename(MAIN/'app/.next')
new_code={n:h(WORK/n) for n in git(WORK,'diff','--name-only',old['base'],NEW).splitlines()}
assert all(h(MAIN/n)==sha for n,sha in new_code.items())
write('implementation_manifest.json',new_code)
release=dict(old,candidate=NEW,engine_candidate=OLD,ui_candidate=NEW,build_id=proof['build_id'])
write('release_candidate.json',release)
envs=read('private/effective_environments.json');api_env=dict(envs['api'],JOB_QUEUE_ROLE='api',JOB_QUEUE_WORKERS='2');worker_env=dict(envs['api'],JOB_QUEUE_ROLE='worker',JOB_QUEUE_WORKERS='2',JOB_LIST_CACHE_WARMUP='false')
def launch(kind):
 if kind=='api':args=['/opt/homebrew/bin/node',str(MAIN/'scripts/dev-backend.cjs')];cwd=MAIN;env=api_env
 elif kind=='worker':args=[str(MAIN/'.venv/bin/python'),'-m','api.worker'];cwd=MAIN;env=worker_env
 else:args=['/opt/homebrew/bin/npm','run','start'];cwd=MAIN/'app';env=dict(envs['frontend'],NODE_ENV='production',PORT='3000')
 with (OUT/('daily-'+kind+'-recovery.log')).open('ab') as log:
  return subprocess.Popen(args,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True).pid
processes={kind:launch(kind) for kind in ['api','frontend','worker']}
write('runtime_recovery_starting.json',dict(candidate=NEW,processes=processes))
op=urllib.request.build_opener(urllib.request.ProxyHandler({}));deadline=time.monotonic()+90
while True:
 try:
  for port,path in [(18743,'/health'),(18743,'/ready'),(3000,'/login')]:
   with op.open(f'http://127.0.0.1:{port}{path}',timeout=4) as r:assert r.status==200
  break
 except Exception:
  if time.monotonic()>deadline:raise RuntimeError('Recovery API/frontend readiness failed; preserve runtime_recovery_starting.json')
  time.sleep(.5)
os.kill(processes['worker'],0)
receipt=dict(before=OLD,after=NEW,engine_computation_candidate=OLD,ui_only_changes=['app/lib/issueCategories.ts','app/tests/issueCategories.test.ts'],processes=processes,
 backend_listener_pids=sorted(ids(18743)),frontend_listener_pids=sorted(ids(3000)),build_id=proof['build_id'],health_ready_login_200=True,
 configuration_and_decisions_preserved=True,engine_source_bytes_unchanged=True,runtime_queue=dict(api_role='api',worker_role='worker',workers=2,original_default_workers=32,persistent_env_files_unchanged=True),
 previous_frontend_build_preserved=str(backup),successful_jobs_preserved=25,cancelled_jobs_pending_official_retry=54,queued_jobs_automatic_resume=32)
write('deployment_final_receipt.json',receipt);write('runtime_recovery_deployment.json',receipt)
print(json.dumps(receipt,ensure_ascii=False),flush=True)
