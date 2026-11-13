"""恢复经验证的发布目录，保留被外部启动的根工作区全部文件。"""
from pathlib import Path
import subprocess,os,signal,json,time,urllib.request
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker');OUT=ROOT/'outputs/native_review_coverage_20261108';MAIN=ROOT/'artifacts/daily-main'
def listeners(port):return {int(x) for x in subprocess.run(['lsof','-t',f'-iTCP:{port}','-sTCP:LISTEN'],capture_output=True,text=True).stdout.split()}
def cwd(pid):return next(x[1:] for x in subprocess.check_output(['lsof','-a','-p',str(pid),'-d','cwd','-Fn'],text=True).splitlines() if x.startswith('n'))
assert not [p for p in (ROOT/'uploads').glob('*/status.json') if json.loads(p.read_text()).get('status') in {'queued','processing','running','analyzing'}]
assert subprocess.check_output(['git','-C',str(MAIN),'rev-parse','HEAD'],text=True).strip()=='b656b0e134798fe360e413381b068b3ce2bb9abd'
assert (MAIN/'app/.next/BUILD_ID').is_file()
assert cwd(23830)==str(ROOT)
os.kill(23830,signal.SIGTERM)
end=time.monotonic()+20
while time.monotonic()<end:
 if not listeners(18743) and not listeners(3000):break
 time.sleep(.2)
else:
 for port in (18743,3000):
  for pid in listeners(port):
   assert cwd(pid) in {str(ROOT),str(ROOT/'app')}
   os.kill(pid,signal.SIGTERM)
 end=time.monotonic()+15
 while time.monotonic()<end:
  if not listeners(18743) and not listeners(3000):break
  time.sleep(.2)
assert not listeners(18743) and not listeners(3000),'root service still holds a port'
saved=json.loads((ROOT/'outputs/claude_closeout_20261108/private/effective_environments.json').read_text());records={}
for name,args,folder,env in [
 ('backend',['/opt/homebrew/bin/node',str(MAIN/'scripts/dev-backend.cjs')],MAIN,saved['api']),
 ('frontend',['/opt/homebrew/bin/node',str(MAIN/'app/node_modules/next/dist/bin/next'),'start','-p','3000'],MAIN/'app',dict(saved['frontend'],NODE_ENV='production'))]:
 with (OUT/f'restored-{name}.log').open('ab') as log:
  p=subprocess.Popen(args,cwd=folder,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
 records[name]=p.pid
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));end=time.monotonic()+100
while time.monotonic()<end:
 try:
  for port,path in [(18743,'/health'),(18743,'/ready'),(3000,'/login')]:
   with opener.open(f'http://127.0.0.1:{port}{path}',timeout=3) as r:assert r.status==200
  break
 except Exception:time.sleep(.5)
else:raise RuntimeError('verified runtime did not become ready')
assert all(cwd(p)==str(MAIN) for p in listeners(18743))
assert all(cwd(p)==str(MAIN/'app') for p in listeners(3000))
receipt={'processes':records,'backend_listeners':sorted(listeners(18743)),'frontend_listeners':sorted(listeners(3000)),'verified_release_restored':True,'health_ready_login_200':True,'original_worktree_untouched':True}
(OUT/'runtime_restored_receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt),flush=True)
