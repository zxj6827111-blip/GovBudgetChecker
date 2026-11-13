from pathlib import Path
import json,subprocess,urllib.request,time
root=Path('/Volumes/workSSD/software development/GovBudgetChecker');out=root/'outputs/native_review_coverage_20261108';main=root/'artifacts/daily-main'
existing=subprocess.run(['lsof','-t','-iTCP:18743','-sTCP:LISTEN'],capture_output=True,text=True).stdout.strip()
assert not existing,'backend already running; do not duplicate'
assert subprocess.check_output(['git','-C',str(main),'rev-parse','HEAD'],text=True).strip()=='b656b0e134798fe360e413381b068b3ce2bb9abd'
env=json.loads((root/'outputs/claude_closeout_20261108/private/effective_environments.json').read_text())['api']
with (out/'daily-backend-recovered.log').open('ab') as log:
 p=subprocess.Popen(['/opt/homebrew/bin/node',str(main/'scripts/dev-backend.cjs')],cwd=main,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));deadline=time.monotonic()+60
while time.monotonic()<deadline:
 try:
  for path in ('/health','/ready'):
   with opener.open('http://127.0.0.1:18743'+path,timeout=3) as r:assert r.status==200
  receipt={'backend_supervisor_pid':p.pid,'recovered':True,'health_ready_200':True}
  (out/'recovery_receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt),flush=True);break
 except Exception:time.sleep(.5)
else:raise RuntimeError('backend recovery failed; inspect recovery log')
