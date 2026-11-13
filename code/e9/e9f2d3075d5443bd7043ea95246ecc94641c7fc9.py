"""固定候选切换；配置、原件、历史结果不迁移，旧构建保留可回退。"""
import hashlib,json,os,re,shutil,signal,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent;MAIN=ROOT/'artifacts/daily-main';WORK=ROOT/'artifacts/comprehensive-review-closure-20261109'
release=json.loads((OUT/'release_candidate.json').read_text());OLD=release['base'];NEW=release['candidate']
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def git(*args):return subprocess.check_output(['git',*args],cwd=MAIN,text=True).strip()
def ids(port):return {int(x) for x in subprocess.run(['lsof','-t',f'-iTCP:{port}','-sTCP:LISTEN'],capture_output=True,text=True).stdout.split()}
def cwd(pid):
 r=subprocess.run(['lsof','-a','-p',str(pid),'-d','cwd','-Fn'],capture_output=True,text=True)
 return next((x[1:] for x in r.stdout.splitlines() if x.startswith('n')),None)
def wait_ports_empty(port,timeout):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  if not ids(port):return
  time.sleep(.2)
 raise RuntimeError('Daily port '+str(port)+' not released; no unscoped kill permitted')
def launch(kind):
 env=dict(envs[kind]);args=['/opt/homebrew/bin/node',str(MAIN/'scripts/dev-backend.cjs')];directory=MAIN
 if kind=='frontend':env.update(NODE_ENV='production',PORT='3000');args=['/opt/homebrew/bin/npm','run','start'];directory=MAIN/'app'
 with (OUT/('daily-'+kind+'.log')).open('ab') as log:
  return subprocess.Popen(args,cwd=directory,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True).pid
def ready():
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}));end=time.monotonic()+100
 while time.monotonic()<end:
  try:
   for port,path in [(18743,'/health'),(18743,'/ready'),(3000,'/login')]:
    with op.open(f'http://127.0.0.1:{port}{path}',timeout=4) as r:assert r.status==200
   return
  except Exception:time.sleep(.5)
 raise RuntimeError('Readiness failed')
assert git('rev-parse','HEAD')==OLD and git('branch','--show-current')=='main'
assert not git('status','--porcelain','--untracked-files=no')
code=json.loads((OUT/'implementation_manifest.json').read_text());assert all(h(WORK/name)==sha for name,sha in code.items())
log=(OUT/'backend-release.log').read_text();assert re.search(r'\d+ passed, 196 skipped',log) and 'FAILED ' not in log
assert json.loads((OUT/'postgres-v4_test_receipt.json').read_text())['exit_code']==0
assert 'Compiled successfully' in (OUT/'frontend-build.log').read_text() and not (OUT/'frontend-tsc.log').read_text().strip()
replays=json.loads((OUT/'release_final_summary.json').read_text());assert len(replays)==345 and all(e['ok'] and not e['error'] for e in replays)
comparison=json.loads((OUT/'release_comparison.json').read_text());assert not comparison['errors'] and not comparison['monetary_cell_changes'] and not comparison['unproven_fact_changes']
before=json.loads((OUT/'preservation_before.json').read_text());assert all(h(ROOT/name)==sha for name,sha in before['root_modified_files'].items())
assert h(ROOT/'.env')==before['root_env_sha256'] and all(h(ROOT/name)==sha for name,sha in before['pdfs'].items())
assert json.loads((OUT/'backup_receipt.json').read_text())['database_dump_bytes']>0
statuses={p.parent.name:json.loads(p.read_text()) for p in (ROOT/'uploads').glob('*/status.json')}
assert not [j for j,s in statuses.items() if s.get('status') in {'queued','processing','running','analyzing'}]
preflight=dict(statuses={j:h(ROOT/'uploads'/j/'status.json') for j in statuses},revisions={j:s.get('analysis_revision') for j,s in statuses.items()},workflow_sha256=h(ROOT/'uploads/.issue_workflow.json'),ai_settings_sha256=h(ROOT/'data/ai-model-settings.json'))
(OUT/'deployment_preflight.json').write_text(json.dumps(preflight,ensure_ascii=False,indent=2))
saved=json.loads((OUT/'private/live_processes.json').read_text());envs=json.loads((OUT/'private/effective_environments.json').read_text())
backend=next(p['pid'] for p in saved['processes'] if p['argv'][-1].endswith('/scripts/dev-backend.cjs'));front=next(p for p in saved['processes'] if p['pid'] in saved['listeners']['3000'])
assert ids(18743)==set(saved['listeners']['18743']) and ids(3000)==set(saved['listeners']['3000'])
assert cwd(backend)==str(MAIN) and cwd(front['pid'])==str(MAIN/'app') and cwd(front['ppid'])==str(MAIN/'app')
candidate=MAIN/'app/.next-comprehensive-candidate';backup=OUT/'private/frontend_before';assert candidate.is_dir() and not backup.exists()
assert (candidate/'BUILD_ID').read_text().strip()==release['build_id']
processes={}
try:
 os.kill(backend,signal.SIGTERM);wait_ports_empty(18743,30)
 os.kill(front['ppid'],signal.SIGTERM)
 time.sleep(.5)
 for pid in ids(3000):
  assert cwd(pid)==str(MAIN/'app');os.kill(pid,signal.SIGTERM)
 wait_ports_empty(3000,20)
 subprocess.run(['git','merge','--ff-only',NEW],cwd=MAIN,check=True,capture_output=True)
 (MAIN/'app/.next').rename(backup);candidate.rename(MAIN/'app/.next')
 processes['api']=launch('api');processes['frontend']=launch('frontend');ready()
 assert git('rev-parse','HEAD')==NEW and all(h(MAIN/name)==sha for name,sha in code.items())
 assert h(ROOT/'uploads/.issue_workflow.json')==preflight['workflow_sha256']
 assert h(ROOT/'data/ai-model-settings.json')==preflight['ai_settings_sha256']
 receipt=dict(before=OLD,after=NEW,processes=processes,backend_listener_pids=sorted(ids(18743)),frontend_listener_pids=sorted(ids(3000)),build_id=release['build_id'],health_ready_login_200=True,configuration_and_decisions_preserved=True)
 (OUT/'deployment_receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt),flush=True)
except BaseException:
 for pid in processes.values():
  if cwd(pid) in {str(MAIN),str(MAIN/'app')}:
   try:os.kill(pid,signal.SIGTERM)
   except ProcessLookupError:pass
 for port in [18743,3000]:
  for pid in ids(port):
   if cwd(pid) in {str(MAIN),str(MAIN/'app')}:os.kill(pid,signal.SIGTERM)
  wait_ports_empty(port,25)
 if backup.exists():
  if (MAIN/'app/.next').exists():(MAIN/'app/.next').rename(OUT/'private/frontend_failed_candidate')
  backup.rename(MAIN/'app/.next')
 if git('rev-parse','HEAD')!=OLD:subprocess.run(['git','switch','--detach',OLD],cwd=MAIN,check=True,capture_output=True)
 launch('api');launch('frontend');ready();raise
