"""Deploy the verified frontend only; preserve data and retain the previous build."""
from pathlib import Path
import os,subprocess,shutil,json,hashlib,signal,time,socket,urllib.request
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
OUT=ROOT/'outputs/issue_category_display_20261108'
MAIN=ROOT/'artifacts/daily-main'; CAND=ROOT/'artifacts/issue-category-display-20261108'
PRIVATE=OUT/'private';PRIVATE.mkdir(mode=0o700,exist_ok=True);PRIVATE.chmod(0o700)
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
before=json.loads((OUT/'preservation_before.json').read_text())
assert h(ROOT/'.env')==before['root_env_hash']
assert not subprocess.check_output(['git','-C',str(MAIN),'diff','--name-only'],text=True).strip()
sha=subprocess.check_output(['git','-C',str(MAIN),'rev-parse','HEAD'],text=True).strip()
assert sha==subprocess.check_output(['git','-C',str(CAND),'rev-parse','HEAD'],text=True).strip()
old_config=json.loads((ROOT/'outputs/claude_closeout_20261108/private/effective_environments.json').read_text())['frontend']
old_state=json.loads((ROOT/'outputs/claude_closeout_20261108/runtime_before.json').read_text())
assert h(ROOT/'.env')==old_state['root_env_sha256']
prefixes=('GOVBUDGET_','BACKEND_','LOCAL_DATA_','LOCAL_UPLOADS_','USER_SESSION_')
exact={'ORG_DATA_DIR','USER_DATA_DIR','AI_MODEL_SETTINGS_PATH','DISCLOSURE_CALENDAR_PATH','AUDIT_LOG_PATH'}
env=os.environ.copy();env.update({k:v for k,v in old_config.items() if k.startswith(prefixes) or k in exact});env['NODE_ENV']='production'
assert env.get('BACKEND_URL')=='http://127.0.0.1:18743'
node=shutil.which('node'); assert node
source=CAND/'app/.next'; cache=MAIN/'app/.next'; stage=MAIN/'app/.next.category-staged';backup=PRIVATE/'frontend_before'
assert source.exists() and not stage.exists() and not backup.exists()
shutil.copytree(source,stage,ignore=lambda directory,names: ['cache'] if Path(directory)==source and 'cache' in names else [])
assert (stage/'BUILD_ID').read_text()==(source/'BUILD_ID').read_text()
pids=[int(x) for x in subprocess.check_output(['lsof','-t','-iTCP:3000','-sTCP:LISTEN'],text=True).split()]
assert len(pids)==1,pids
cwd=subprocess.check_output(['lsof','-a','-p',str(pids[0]),'-d','cwd','-Fn'],text=True)
assert str(MAIN/'app') in cwd
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def ready():
 try:
  with opener.open('http://127.0.0.1:3000/login',timeout=2) as r:return r.status==200
 except Exception:return False
def start():
 log=(OUT/'daily-frontend.log').open('ab')
 return subprocess.Popen([node,str(MAIN/'app/node_modules/next/dist/bin/next'),'start','-p','3000'],cwd=MAIN/'app',env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
os.kill(pids[0],signal.SIGTERM)
for _ in range(50):
 with socket.socket() as s:
  s.settimeout(.1)
  if s.connect_ex(('127.0.0.1',3000))!=0:break
 time.sleep(.1)
else:raise RuntimeError('old frontend still listening; staged build retained')
cache.rename(backup);stage.rename(cache)
new=start()
for _ in range(100):
 if ready():break
 if new.poll() is not None:break
 time.sleep(.1)
else:pass
if not ready():
 if new.poll() is None:os.kill(new.pid,signal.SIGTERM)
 cache.rename(PRIVATE/'frontend_failed');backup.rename(cache);restored=start()
 raise RuntimeError(f'new frontend failed; restored previous build with pid {restored.pid}')
checks={}
for port,path in [(3000,'/login'),(18743,'/health'),(18743,'/ready')]:
 with opener.open(f'http://127.0.0.1:{port}{path}',timeout=10) as r:checks[f'{port}{path}']=r.status
unchanged={k:h(ROOT/k)==v for k,v in before['root_modified_files'].items()}; assert all(unchanged.values())
status_changed=[k for k,v in before['statuses'].items() if h(ROOT/'uploads'/k/'status.json')!=v];assert not status_changed
assert all(h(Path(v['path']))==v['sha256'] for v in before['originals'].values())
receipt={'commit':sha,'frontend_pid':new.pid,'previous_pid':pids[0],'frontend_build_id':(cache/'BUILD_ID').read_text(),'health':checks,'existing_dirty_files_preserved':len(unchanged),'existing_analysis_snapshots_preserved':len(before['statuses']),'changed_historical_analyses':status_changed,'original_pdfs_unchanged':2,'config_unchanged':True,'backend_restarted':False,'backup_build':str(backup)}
(OUT/'deployment_verification.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
print(json.dumps(receipt,ensure_ascii=False))
