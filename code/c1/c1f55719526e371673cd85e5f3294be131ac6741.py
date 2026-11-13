from pathlib import Path
import subprocess,os,signal,time,json,shutil,urllib.request
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker');OUT=ROOT/'outputs/shiquan_reconciliation_merge_20261109'
MAIN=ROOT/'artifacts/daily-main';WORK=ROOT/'artifacts/shiquan-reconciliation-merge-20261109'
assert 'Compiled successfully' in (OUT/'build-title-accepted.log').read_text()
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=MAIN,text=True).strip()=='5686421da53d64c6435ae792ee1d0548fae45b83'
assert not subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=MAIN,text=True).strip()
new=subprocess.check_output(['git','rev-parse','HEAD'],cwd=WORK,text=True).strip()
changes=subprocess.check_output(['git','diff','--name-only','HEAD',new],cwd=MAIN,text=True).splitlines()
assert changes==['app/lib/uiAdapters.ts', 'app/tests/reviewReading.test.ts']
def ids(port):return set(subprocess.check_output(['lsof','-t',f'-iTCP:{port}','-sTCP:LISTEN'],text=True).split())
front=ids(3000);back=ids(18743);assert front=={'37348'}
candidate=MAIN/'app/.next-merge-title-candidate';backup=OUT/'private/frontend_before_title';assert not candidate.exists() and not backup.exists()
shutil.copytree(WORK/'app/.next',candidate,ignore=shutil.ignore_patterns('cache'))
subprocess.run(['git','merge','--ff-only',new],cwd=MAIN,check=True,capture_output=True)
os.kill(37310,signal.SIGTERM);time.sleep(.3)
for pid in front:
 try:os.kill(int(pid),signal.SIGTERM)
 except ProcessLookupError:pass
end=time.monotonic()+15
while time.monotonic()<end:
 r=subprocess.run(['lsof','-t','-iTCP:3000','-sTCP:LISTEN'],capture_output=True,text=True)
 if not r.stdout.strip():break
 time.sleep(.2)
assert not r.stdout.strip()
(MAIN/'app/.next').rename(backup);candidate.rename(MAIN/'app/.next')
env=json.loads((ROOT/'outputs/claude_closeout_20261108/private/effective_environments.json').read_text())['frontend'];env.update(NODE_ENV='production',PORT='3000')
with (OUT/'daily-frontend-title.log').open('ab') as log:
 p=subprocess.Popen(['/opt/homebrew/bin/npm','run','start'],cwd=MAIN/'app',env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));end=time.monotonic()+30
while True:
 try:
  with opener.open('http://127.0.0.1:3000/login',timeout=3) as response:assert response.status==200
  break
 except Exception:
  if time.monotonic()>end:raise
  time.sleep(.3)
assert ids(18743)==back
receipt={'commit':new,'frontend_supervisor_pid':p.pid,'frontend_listener_pids':sorted(ids(3000)),'backend_listener_pids_unchanged':sorted(back),'build_id':(MAIN/'app/.next/BUILD_ID').read_text().strip()}
(OUT/'frontend_control_deployment.json').write_text(json.dumps(receipt,indent=2));print(receipt)
