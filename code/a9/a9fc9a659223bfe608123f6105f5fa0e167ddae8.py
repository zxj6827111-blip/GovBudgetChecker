"""日常副本已退出时快进最终补丁；UI 构建不变，失败回到已验证097f。"""
import hashlib,json,os,re,signal,subprocess,time,urllib.request
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];MAIN=ROOT/'artifacts/daily-main';WORK=ROOT/'artifacts/comprehensive-review-closure-20261109'
release=json.loads((OUT/'release_candidate.json').read_text());OLD='097f9fec7a7332efc921bbc1e867a23044e81870';NEW=release['candidate'];envs=json.loads((OUT/'private/effective_environments.json').read_text())
def h(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def git(*args):return subprocess.check_output(['git',*args],cwd=MAIN,text=True).strip()
def ids(port):return {int(x) for x in subprocess.run(['lsof','-t',f'-iTCP:{port}','-sTCP:LISTEN'],capture_output=True,text=True).stdout.split()}
def launch(kind):
 env=dict(envs[kind]);args=['/opt/homebrew/bin/node',str(MAIN/'scripts/dev-backend.cjs')];directory=MAIN
 if kind=='frontend':env.update(NODE_ENV='production',PORT='3000');args=['/opt/homebrew/bin/npm','run','start'];directory=MAIN/'app'
 with (OUT/('daily-'+kind+'-final.log')).open('ab') as log:
  return subprocess.Popen(args,cwd=directory,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True).pid
def ready():
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}));end=time.monotonic()+90
 while time.monotonic()<end:
  try:
   for port,path in [(18743,'/health'),(18743,'/ready'),(3000,'/login')]:
    with op.open(f'http://127.0.0.1:{port}{path}',timeout=4) as r:assert r.status==200
   return
  except Exception:time.sleep(.5)
 raise RuntimeError('Final readiness failed')
assert OLD!=NEW and git('rev-parse','HEAD')==OLD and git('branch','--show-current')=='main'
assert not git('status','--porcelain','--untracked-files=no')
assert not ids(18743) and not ids(3000),'Services changed since inspection; recapture before update'
code=json.loads((OUT/'implementation_manifest.json').read_text());assert all(h(WORK/n)==sha for n,sha in code.items())
assert '5717 passed, 197 skipped' in (OUT/'backend-final7-release.log').read_text()
assert json.loads((OUT/'postgres-final7_test_receipt.json').read_text())['exit_code']==0
entries=json.loads((OUT/'release_final7_summary.json').read_text());assert len(entries)==345 and all(e['ok'] and not e['error'] for e in entries)
comparison=json.loads((OUT/'release_final7_comparison.json').read_text());assert not comparison['errors'] and not comparison['monetary_cell_changes'] and not comparison['unproven_fact_changes']
assert not json.loads((OUT/'release_final7_identity_verification.json').read_text())['cross_group_identity_collisions']
assert (MAIN/'app/.next/BUILD_ID').read_text().strip()==release['build_id']
preserve=json.loads((OUT/'preservation_before.json').read_text());assert all(h(ROOT/n)==sha for n,sha in preserve['root_modified_files'].items());assert all(h(ROOT/n)==sha for n,sha in preserve['pdfs'].items())
assert h(ROOT/'.env')==preserve['root_env_sha256']
assert h(ROOT/'data/ai-model-settings.json')==json.loads((OUT/'deployment_preflight.json').read_text())['ai_settings_sha256']
statuses={p.parent.name:json.loads(p.read_text()) for p in (ROOT/'uploads').glob('*/status.json')};assert not any(s.get('status') in {'queued','processing','running','analyzing'} for s in statuses.values())
preflight=dict(statuses={j:h(ROOT/'uploads'/j/'status.json') for j in statuses},revisions={j:s.get('analysis_revision') for j,s in statuses.items()},workflow_sha256=h(ROOT/'uploads/.issue_workflow.json'),ai_settings_sha256=h(ROOT/'data/ai-model-settings.json'))
(OUT/'deployment_final_preflight.json').write_text(json.dumps(preflight,ensure_ascii=False,indent=2))
subprocess.run(['git','merge','--ff-only',NEW],cwd=MAIN,check=True,capture_output=True)
processes={}
try:
 for kind in ('api','frontend'):processes[kind]=launch(kind)
 ready()
except BaseException as error:
 for pid in processes.values():
  try:
   if os.getpgid(pid)==pid:os.killpg(pid,signal.SIGTERM)
  except ProcessLookupError:pass
 end=time.monotonic()+25
 while (ids(18743) or ids(3000)) and time.monotonic()<end:time.sleep(.25)
 assert not ids(18743) and not ids(3000),'Rollback ports not released; no unrelated kill permitted'
 assert not git('status','--porcelain','--untracked-files=no')
 subprocess.run(['git','switch','--detach',OLD],cwd=MAIN,check=True,capture_output=True)
 rollback_processes={kind:launch(kind) for kind in ('api','frontend')};ready()
 (OUT/'deployment_final_rollback.json').write_text(json.dumps(dict(candidate=NEW,restored=OLD,error_type=type(error).__name__,processes=rollback_processes),indent=2))
 raise
assert git('rev-parse','HEAD')==NEW and all(h(MAIN/n)==sha for n,sha in code.items())
assert h(ROOT/'uploads/.issue_workflow.json')==preflight['workflow_sha256'] and h(ROOT/'data/ai-model-settings.json')==preflight['ai_settings_sha256']
receipt=dict(before=OLD,after=NEW,processes=processes,backend_listener_pids=sorted(ids(18743)),frontend_listener_pids=sorted(ids(3000)),build_id=release['build_id'],health_ready_login_200=True,configuration_and_decisions_preserved=True)
(OUT/'deployment_final_receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt),flush=True)
