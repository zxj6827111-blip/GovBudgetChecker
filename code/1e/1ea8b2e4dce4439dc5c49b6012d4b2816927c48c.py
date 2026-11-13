"""更新已验证的日常后端；配置、前端及历史结果保持原位。"""
from pathlib import Path
import json,subprocess,hashlib,os,signal,time,urllib.request,re
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker');OUT=ROOT/'outputs/native_review_coverage_20261108';MAIN=ROOT/'artifacts/daily-main';WORK=ROOT/'artifacts/native-review-coverage-20261108'
OLD='eae15090c23107d14cfd67ca576a7434c2505197'

def git(folder,*args):return subprocess.check_output(['git','-C',str(folder),*args],text=True).strip()
def listeners(port):return {int(x) for x in subprocess.run(['lsof','-t',f'-iTCP:{port}','-sTCP:LISTEN'],capture_output=True,text=True).stdout.split()}
def cwd(pid):return next(x[1:] for x in subprocess.check_output(['lsof','-a','-p',str(pid),'-d','cwd','-Fn'],text=True).splitlines() if x.startswith('n'))
def stop(pid):
 assert cwd(pid)==str(MAIN)
 os.kill(pid,signal.SIGTERM)
 end=time.monotonic()+25
 while time.monotonic()<end:
  if not listeners(18743):return
  time.sleep(.2)
 raise RuntimeError('old backend not released; no code update performed')
def launch():
 env=json.loads((ROOT/'outputs/claude_closeout_20261108/private/effective_environments.json').read_text())['api']
 with (OUT/'daily-backend.log').open('ab') as log:
  p=subprocess.Popen(['/opt/homebrew/bin/node',str(MAIN/'scripts/dev-backend.cjs')],cwd=MAIN,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
 return p.pid
def ready():
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));end=time.monotonic()+100
 while time.monotonic()<end:
  try:
   for port,path in [(18743,'/health'),(18743,'/ready'),(3000,'/login')]:
    with opener.open(f'http://127.0.0.1:{port}{path}',timeout=4) as r:assert r.status==200
   return
  except Exception:time.sleep(.5)
 raise RuntimeError('service readiness failed')
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
before=json.loads((OUT/'preservation_before.json').read_text());code=json.loads((OUT/'implementation_manifest.json').read_text());new=git(WORK,'rev-parse','HEAD')
assert git(MAIN,'rev-parse','HEAD')==OLD and git(MAIN,'branch','--show-current')=='main'
assert not git(MAIN,'status','--porcelain','--untracked-files=no')
assert all(h(WORK/p)==sha for p,sha in code.items())
log=(OUT/'pytest-exact-final.log').read_text();assert re.search(r'5437 passed, 196 skipped',log),log[-1200:]
replay=json.loads((OUT/'replay_summary.json').read_text());assert len(replay)==7 and all(x['ok'] and x['status']=='done' and not x['todo']['blocking_ids'] for x in replay)
assert json.loads((OUT/'backup_receipt.json').read_text())['database_dump_bytes']>0
assert all(h(ROOT/p)==sha for p,sha in before['root_modified_files'].items()) and h(ROOT/'.env')==before['root_env_sha256']
assert all(h(ROOT/'uploads'/job/'status.json')==sha for job,sha in before['statuses'].items())
assert not [p.parent.name for p in (ROOT/'uploads').glob('*/status.json') if json.loads(p.read_text()).get('status') in {'queued','running','processing','analyzing'}]
front=listeners(3000);assert front
backend=67428;assert cwd(backend)==str(MAIN)
stop(backend);new_pid=None
try:
 subprocess.run(['git','merge','--ff-only',new],cwd=MAIN,check=True,capture_output=True)
 new_pid=launch();ready();assert listeners(3000)==front
 assert all(h(MAIN/p)==sha for p,sha in code.items())
 receipt={'before':OLD,'after':new,'backend_supervisor_pid':new_pid,'backend_listener_pids':sorted(listeners(18743)),'frontend_pids_unchanged':sorted(front),'health_ready_login_200':True,'configuration_unchanged':h(ROOT/'.env')==before['root_env_sha256'],'preserved_old_statuses':len(before['statuses'])}
 (OUT/'deployment_receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt),flush=True)
except Exception:
 if new_pid:stop(new_pid)
 if git(MAIN,'rev-parse','HEAD')!=OLD:subprocess.run(['git','switch','--detach',OLD],cwd=MAIN,check=True,capture_output=True)
 launch();ready();raise
