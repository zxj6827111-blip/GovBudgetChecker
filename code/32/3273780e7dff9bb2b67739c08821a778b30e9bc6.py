"""最终安全候选验证及中间111批次结束后，保全并切换；不在此脚本重分析。"""
import collections,hashlib,json,os,shutil,signal,subprocess,time,urllib.request
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];MAIN=ROOT/'artifacts/daily-main';WORK=ROOT/'artifacts/comprehensive-review-closure-20261109'
read=lambda name:json.loads((OUT/name).read_text())
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def git(directory,*args):return subprocess.check_output(['git',*args],cwd=directory,text=True).strip()
def ids(port):return {int(v) for v in subprocess.run(['lsof','-t',f'-iTCP:{port}','-sTCP:LISTEN'],capture_output=True,text=True).stdout.split()}
def write(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))
OLD=read('release_candidate.json')['candidate'];NEW=git(WORK,'rev-parse','HEAD');assert OLD.startswith('01bfb5c5') and NEW!=OLD
expected={'src/engine/native_hierarchy_checks.py','src/engine/native_issue_dedup.py','tests/remediation/test_orphan_functional_roots.py'}
assert set(git(WORK,'diff','--name-only',OLD,NEW).splitlines())==expected
assert git(MAIN,'rev-parse','HEAD')==OLD and not git(MAIN,'status','--porcelain','--untracked-files=no') and not git(WORK,'status','--porcelain','--untracked-files=no')
snapshot=read('release_final10_code_manifest.json');assert all(h(WORK/n)==sha for n,sha in snapshot.items())
assert 'passed, 197 skipped' in (OUT/'backend-final10-release.log').read_text() and 'FAILED ' not in (OUT/'backend-final10-release.log').read_text()
assert read('postgres-final10_test_receipt.json')['exit_code']==0
entries=read('release_final10_summary.json');assert len(entries)==345 and all(e['ok'] and not e['error'] for e in entries)
comparison=read('release_final10_comparison.json');assert not any(comparison[k] for k in ['errors','monetary_cell_changes','unproven_fact_changes','qualification_changes'])
impact=read('final10_orphan_impact_verified.json');assert impact['verified'] and impact['materials']==345
identity=read('release_final10_identity_verification.json');assert not identity['cross_group_identity_collisions'] and not identity['unresolved_receipt_refs']
assert read('daily_final_finished.json')['finished']==111 and not read('daily_final_finished.json')['errors']
assert read('database_final_verified.json')['verified']==111 and not read('database_final_verified.json')['pending']
assert 'PASS verify_served_build.py' in (OUT/'final9_orchestration.log').read_text()
assert read('runtime_recovery_health.json')['rows'][-1]['finished']==111
statuses={p.parent.name:json.loads(p.read_text()) for p in (ROOT/'uploads').glob('*/status.json')}
assert not any(s.get('status') in {'queued','processing','running','analyzing'} for s in statuses.values())
prior=read('deployment_final9_receipt.json');assert prior['after']==OLD
before=read('preservation_before.json');assert all(h(ROOT/n)==sha for n,sha in before['root_modified_files'].items()) and all(h(ROOT/n)==sha for n,sha in before['pdfs'].items()) and h(ROOT/'.env')==before['root_env_sha256']
assert h(ROOT/'data/ai-model-settings.json')==read('deployment_preflight.json')['ai_settings_sha256']
build=read('frontend_final_test_receipt.json')['build_id'];assert (MAIN/'app/.next/BUILD_ID').read_text().strip()==build
phase=OUT/'phasefinal9_runtime_acceptance';phase.mkdir(exist_ok=False)
for name in ['release_candidate.json','implementation_manifest.json','deployment_final_receipt.json','deployment_final_preflight.json','daily_final_accepted.json','daily_final_release.json','daily_final_progress.json','daily_final_progress.log','daily_final_finished.json','database_final_verified.json','preservation_final.json','database_history_verified.json','production_history_verified.json','shared_scope_final_verified.json','served_build_verification.json','验收报告.md','runtime_recovery_health.json','real_decision_history_final_receipt.json']:
 if (OUT/name).exists():shutil.copy2(OUT/name,phase/name)
preflight=dict(statuses={j:h(ROOT/'uploads'/j/'status.json') for j in statuses},revisions={j:s.get('analysis_revision') for j,s in statuses.items()},workflow_sha256=h(ROOT/'uploads/.issue_workflow.json'),ai_settings_sha256=h(ROOT/'data/ai-model-settings.json'))
write('deployment_final10_preflight.json',preflight)
worker_pid=prior['processes']['worker'];deadline=time.monotonic()+45
while True:
 beats=[json.loads(p.read_text()) for p in (ROOT/'uploads/.worker-heartbeats').glob(f'{worker_pid}-*.json')]
 if any(b.get('active_jobs')==0 and b.get('queued_jobs')==0 and time.time()-b['ts']<15 for b in beats):break
 if time.monotonic()>deadline:raise RuntimeError('Worker not observably idle; no shutdown')
 time.sleep(.5)
groups=set(prior['processes'].values());listener_groups={os.getpgid(pid) for pid in ids(18743)|ids(3000)};owned_groups=groups|listener_groups
for pid in groups:
 assert os.getpgid(pid)==pid
 os.killpg(pid,signal.SIGTERM)
deadline=time.monotonic()+30
while (ids(18743) or ids(3000)) and time.monotonic()<deadline:time.sleep(.25)
for pid in ids(18743)|ids(3000):
 assert os.getpgid(pid) in owned_groups
 os.killpg(os.getpgid(pid),signal.SIGKILL)
time.sleep(.5);assert not ids(18743) and not ids(3000)
envs=read('private/effective_environments.json')
def launch(kind):
 if kind=='api':args=['/opt/homebrew/bin/node',str(MAIN/'scripts/dev-backend.cjs')];cwd=MAIN;env=dict(envs['api'],JOB_QUEUE_ROLE='api',JOB_QUEUE_WORKERS='2')
 elif kind=='worker':args=[str(MAIN/'.venv/bin/python'),'-m','api.worker'];cwd=MAIN;env=dict(envs['api'],JOB_QUEUE_ROLE='worker',JOB_QUEUE_WORKERS='2',JOB_LIST_CACHE_WARMUP='false')
 else:args=['/opt/homebrew/bin/npm','run','start'];cwd=MAIN/'app';env=dict(envs['frontend'],NODE_ENV='production',PORT='3000')
 with (OUT/('daily-'+kind+'-final10.log')).open('ab') as log:return subprocess.Popen(args,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True).pid
def ready():
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}));deadline=time.monotonic()+90
 while True:
  try:
   for port,path in [(18743,'/health'),(18743,'/ready'),(3000,'/login')]:
    with op.open(f'http://127.0.0.1:{port}{path}',timeout=4) as response:assert response.status==200
   return
  except Exception:
   if time.monotonic()>deadline:raise RuntimeError('Final9 readiness failed')
   time.sleep(.5)
subprocess.run(['git','merge','--ff-only',NEW],cwd=MAIN,check=True,capture_output=True)
processes={}
try:
 for kind in ['api','frontend','worker']:processes[kind]=launch(kind)
 ready()
except BaseException as error:
 for pid in processes.values():
  try:os.killpg(pid,signal.SIGTERM)
  except ProcessLookupError:pass
 deadline=time.monotonic()+30
 while (ids(18743) or ids(3000)) and time.monotonic()<deadline:time.sleep(.25)
 assert not ids(18743) and not ids(3000),'Rollback requires unrelated-port inspection'
 assert not git(MAIN,'status','--porcelain','--untracked-files=no')
 subprocess.run(['git','switch','--detach',OLD],cwd=MAIN,check=True,capture_output=True)
 restored={kind:launch(kind) for kind in ['api','frontend','worker']};ready()
 write('deployment_final10_rollback.json',dict(failed=NEW,restored=OLD,processes=restored,error_type=type(error).__name__))
 raise
assert h(ROOT/'uploads/.issue_workflow.json')==preflight['workflow_sha256'] and h(ROOT/'data/ai-model-settings.json')==preflight['ai_settings_sha256']
code={n:h(WORK/n) for n in git(WORK,'diff','--name-only','6e63bdde9292dfb3bb6b649e3f10093a41b6f54c',NEW).splitlines()};assert all(h(MAIN/n)==sha for n,sha in code.items())
release=dict(read('release_candidate.json'),candidate=NEW,engine_candidate=NEW,ui_candidate=read('release_candidate.json')['ui_candidate'],safety_final=True,previous_deployed=OLD,final_replay='release_final10',backend_test_log='backend-final10-release.log',postgres_receipt='postgres-final10_test_receipt.json')
write('implementation_manifest.json',code);write('release_candidate.json',release);write('deployment_final_preflight.json',preflight)
receipt=dict(before=OLD,after=NEW,processes=processes,backend_listener_pids=sorted(ids(18743)),frontend_listener_pids=sorted(ids(3000)),build_id=build,health_ready_login_200=True,configuration_and_decisions_preserved=True,runtime_queue=dict(api_role='api',worker_role='worker',workers=2,persistent_env_files_unchanged=True))
write('deployment_final_receipt.json',receipt);write('deployment_final10_receipt.json',receipt);print(json.dumps(receipt,ensure_ascii=False),flush=True)
