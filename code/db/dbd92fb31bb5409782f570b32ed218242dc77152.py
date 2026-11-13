"""全部111份发布并验收后，切换仅含分类改动的最终前端；不再提交重跑。"""
import hashlib,json,os,shutil,signal,subprocess,time,urllib.request
from pathlib import Path

OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1]
MAIN=ROOT/'artifacts/daily-main';WORK=ROOT/'artifacts/comprehensive-review-closure-20261109'
def read(name):return json.loads((OUT/name).read_text())
def h(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def git(directory,*args):return subprocess.check_output(['git',*args],cwd=directory,text=True).strip()
def listeners(port):return {int(v) for v in subprocess.run(['lsof','-t',f'-iTCP:{port}','-sTCP:LISTEN'],capture_output=True,text=True).stdout.split()}
def write(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2))

old_release=read('release_candidate.json');OLD=old_release['candidate'];NEW=git(WORK,'rev-parse','HEAD')
expected_paths={'app/lib/issueCategories.ts','app/tests/issueCategories.test.ts'}
assert OLD!=NEW and set(git(WORK,'diff','--name-only',OLD,NEW).splitlines())==expected_paths
assert not git(WORK,'status','--porcelain','--untracked-files=no')
assert git(MAIN,'rev-parse','HEAD')==OLD and git(MAIN,'branch','--show-current')=='main'
assert not git(MAIN,'status','--porcelain','--untracked-files=no')
assert read('daily_final_finished.json')['finished']==111 and not read('daily_final_finished.json')['errors']
assert read('database_final_verified.json')['verified']==111 and not read('database_final_verified.json')['pending']
assert (OUT/'验收报告.md').exists() and 'BACKEND_CLOSURE_COMPLETE' in (OUT/'final_orchestration.log').read_text()
assert not any(json.loads(p.read_text()).get('status') in {'queued','processing','running','analyzing'} for p in (ROOT/'uploads').glob('*/status.json'))
build_proof=read('frontend_final_test_receipt.json');BUILD=build_proof['build_id']
assert build_proof['all_checks_passed'] and (WORK/'app/.next/BUILD_ID').read_text().strip()==BUILD
assert all(h(WORK/name)==sha for name,sha in build_proof['source_hashes'].items())
old_code=read('implementation_manifest.json')
assert all(h(WORK/name)==sha for name,sha in old_code.items() if name.startswith(('src/','api/')))
before=read('preservation_before.json');assert all(h(ROOT/name)==sha for name,sha in before['root_modified_files'].items())
assert all(h(ROOT/name)==sha for name,sha in before['pdfs'].items()) and h(ROOT/'.env')==before['root_env_sha256']
old_receipt=read('deployment_final_receipt.json');assert old_receipt['after']==OLD
workflow_hash=h(ROOT/'uploads/.issue_workflow.json');ai_hash=h(ROOT/'data/ai-model-settings.json')

# 后端计算版本及首次部署验收单独冻结，避免把UI提交冒充为111份的执行提交。
frozen=OUT/'engine_cdbf_acceptance';frozen.mkdir(exist_ok=False)
for name in ['release_candidate.json','implementation_manifest.json','deployment_final_receipt.json',
             'database_final_verified.json','preservation_final.json','served_build_verification.json',
             'database_history_verified.json','production_history_verified.json','shared_scope_final_verified.json','验收报告.md']:
 shutil.copy2(OUT/name,frozen/name)
staged=MAIN/'app'/('.next-ui-candidate-'+NEW[:8]);backup=MAIN/'app'/('.next-before-ui-'+NEW[:8])
assert not staged.exists() and not backup.exists();shutil.copytree(WORK/'app/.next',staged,symlinks=True)
assert (staged/'BUILD_ID').read_text().strip()==BUILD
envs=read('private/effective_environments.json')
def launch(kind):
 env=dict(envs[kind]);args=['/opt/homebrew/bin/node',str(MAIN/'scripts/dev-backend.cjs')];cwd=MAIN
 if kind=='frontend':env.update(NODE_ENV='production',PORT='3000');args=['/opt/homebrew/bin/npm','run','start'];cwd=MAIN/'app'
 with (OUT/('daily-'+kind+'-ui-final.log')).open('ab') as log:
  return subprocess.Popen(args,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True).pid
def stop_owned(groups):
 for pid in groups:
  try:
   assert os.getpgid(pid)==pid
   os.killpg(pid,signal.SIGTERM)
  except ProcessLookupError:pass
 end=time.monotonic()+30
 while (listeners(18743) or listeners(3000)) and time.monotonic()<end:time.sleep(.25)
 remaining=listeners(18743)|listeners(3000)
 for pid in remaining:
  assert os.getpgid(pid) in groups,'Refuse to stop an unrelated listener'
 for pid in groups:
  try:os.killpg(pid,signal.SIGKILL)
  except ProcessLookupError:pass
 end=time.monotonic()+10
 while (listeners(18743) or listeners(3000)) and time.monotonic()<end:time.sleep(.25)
 assert not listeners(18743) and not listeners(3000)
def ready():
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}));end=time.monotonic()+90
 while time.monotonic()<end:
  try:
   for port,path in [(18743,'/health'),(18743,'/ready'),(3000,'/login')]:
    with op.open(f'http://127.0.0.1:{port}{path}',timeout=4) as response:assert response.status==200
   return
  except Exception:time.sleep(.5)
 raise RuntimeError('UI closeout readiness failed')
groups=set(old_receipt['processes'].values())
for pid in listeners(18743)|listeners(3000):assert os.getpgid(pid) in groups
stop_owned(groups)
new_processes={};swapped=False
try:
 subprocess.run(['git','merge','--ff-only',NEW],cwd=MAIN,check=True,capture_output=True)
 (MAIN/'app/.next').rename(backup);staged.rename(MAIN/'app/.next');swapped=True
 for kind in ['api','frontend']:new_processes[kind]=launch(kind)
 ready()
except BaseException as error:
 stop_owned(set(new_processes.values()))
 assert not git(MAIN,'status','--porcelain','--untracked-files=no')
 subprocess.run(['git','switch','--detach',OLD],cwd=MAIN,check=True,capture_output=True)
 if backup.exists():
  if (MAIN/'app/.next').exists():(MAIN/'app/.next').rename(MAIN/'app'/('.next-ui-failed-'+NEW[:8]))
  backup.rename(MAIN/'app/.next')
 restored={kind:launch(kind) for kind in ['api','frontend']};ready()
 write('deployment_ui_rollback.json',dict(failed=NEW,restored=OLD,error_type=type(error).__name__,processes=restored))
 raise
assert h(ROOT/'uploads/.issue_workflow.json')==workflow_hash and h(ROOT/'data/ai-model-settings.json')==ai_hash
paths=git(WORK,'diff','--name-only',old_release['base'],NEW).splitlines()
code={name:h(WORK/name) for name in paths};assert all(h(MAIN/name)==sha for name,sha in code.items())
assert all(code[name]==sha for name,sha in old_code.items() if name.startswith(('src/','api/')))
release=dict(old_release,candidate=NEW,engine_candidate=OLD,ui_candidate=NEW,build_id=BUILD)
write('implementation_manifest.json',code);write('release_candidate.json',release)
receipt=dict(before=OLD,after=NEW,engine_computation_candidate=OLD,ui_only_changes=sorted(expected_paths),
    processes=new_processes,backend_listener_pids=sorted(listeners(18743)),frontend_listener_pids=sorted(listeners(3000)),
    build_id=BUILD,health_ready_login_200=True,configuration_and_decisions_preserved=True,
    engine_source_bytes_unchanged=True,no_additional_reanalysis=True,previous_frontend_build_preserved=str(backup))
write('deployment_final_receipt.json',receipt);write('deployment_ui_final_receipt.json',receipt)
print(json.dumps(receipt,ensure_ascii=False),flush=True)
