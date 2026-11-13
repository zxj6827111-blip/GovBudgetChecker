"""Local daily cutover/rollback. Keeps the old checkout and persistent paths intact."""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
MAIN=ROOT/'artifacts/daily-main'
OUT=Path(__file__).resolve().parent
NODE='/opt/homebrew/bin/node'
def request(path):
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 with op.open('http://127.0.0.1:18743'+path,timeout=4) as res:return json.loads(res.read())
def listeners(port):
 return {int(x) for x in subprocess.run(['lsof','-t',f'-iTCP:{port}','-sTCP:LISTEN'],capture_output=True,text=True).stdout.split()}
def process_cwd(pid):
 v=subprocess.run(['lsof','-a','-p',str(pid),'-d','cwd','-Fn'],capture_output=True,text=True).stdout
 return next((x[1:] for x in v.splitlines() if x.startswith('n')),None)
def stop_daily(expected_root,top_pids):
 for pid in top_pids:
  if process_cwd(pid) in [str(expected_root),str(expected_root/'app')]:
   try:os.kill(pid,signal.SIGTERM)
   except ProcessLookupError:pass
 deadline=time.monotonic()+20
 while time.monotonic()<deadline:
  if not listeners(3000) and not listeners(18743):return
  time.sleep(.2)
 # npm进程退出后可能留next子进程；只处理端口及cwd均属于目标实例的进程。
 for pid in listeners(3000)|listeners(18743):
  assert process_cwd(pid) in [str(expected_root),str(expected_root/'app')], 'unexpected process on daily ports'
  os.kill(pid,signal.SIGTERM)
 deadline=time.monotonic()+15
 while time.monotonic()<deadline:
  if not listeners(3000) and not listeners(18743):return
  time.sleep(.2)
 raise RuntimeError('daily ports not released; no unscoped kill performed')
def launch(args,cwd,env,name):
 with (OUT/(name+'.log')).open('ab') as log:
  proc=subprocess.Popen(args,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
 return {'pid':proc.pid,'cwd':str(cwd),'args':args}
def wait_ready(records,new):
 deadline=time.monotonic()+100
 while time.monotonic()<deadline:
  try:
   schema=request('/openapi.json')['components']['schemas']
   ok='scope_confirmed' in schema['ObligationDecisionRequest']['properties']
   if new:ok=ok and 'card_summary' in schema['CoverageObligationItem']['properties']
   else:ok=True
   op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
   with op.open('http://127.0.0.1:3000/login',timeout=4) as r:front=r.status==200
   if ok and front:return
  except Exception:pass
  time.sleep(.5)
 raise RuntimeError('service readiness timeout; see release logs')
def rollback():
 current=OUT/'processes_main.json'
 if current.exists():
  records=json.loads(current.read_text());stop_daily(MAIN,[p['pid'] for p in records.values()])
 else:
  assert not listeners(3000) and not listeners(18743)
 saved=json.loads((OUT/'private/live_processes.json').read_text())
 top=next(x for x in saved['processes'] if x['argv'][:2]==['node','scripts/dev.cjs'])
 record=launch([NODE,str(ROOT/'scripts/dev.cjs')],ROOT,top['environment'],'rollback_old_services')
 (OUT/'processes_rollback.json').write_text(json.dumps(record,indent=2));wait_ready({},False)
 print('rollback ready: old workspace service restored',flush=True)
def upgrade():
 assert subprocess.check_output(['git','-C',str(MAIN),'rev-parse','HEAD'],text=True).strip()=='ed9478655bc38266cbea0d2f5e23928249435e28'
 assert json.loads((OUT/'build_result.json').read_text())['exit_code']==0
 assert (OUT/'backup_receipt.json').exists()
 before=json.loads((OUT/'runtime_before.json').read_text())
 assert hashlib.sha256((ROOT/'.env').read_bytes()).hexdigest()==before['root_env_sha256']
 active=[]
 for f in Path(before['paths']['uploads']).glob('*/status.json'):
  s=json.loads(f.read_text()).get('status')
  if s in ['running','processing','analyzing','queued']:active.append(f.parent.name)
 assert not active,'active jobs appeared; leave current services running'
 envs=json.loads((OUT/'private/release_environments.json').read_text())
 saved=json.loads((OUT/'private/live_processes.json').read_text())
 top=[p['pid'] for p in saved['processes'] if p['argv'][:2]==['node','scripts/dev.cjs']]
 assert len(top)==1
 stop_daily(ROOT,top);print('old daily service ports released',flush=True)
 records={}
 try:
  records['backend_supervisor']=launch([NODE,str(MAIN/'scripts/dev-backend.cjs')],MAIN,envs['api'],'main_backend')
  frontend=dict(envs['frontend']);frontend['NODE_ENV']='production'
  records['frontend']=launch([NODE,str(MAIN/'app/node_modules/next/dist/bin/next'),'start','--hostname','::','--port','3000'],MAIN/'app',frontend,'main_frontend')
  (OUT/'processes_main.json').write_text(json.dumps(records,indent=2))
  wait_ready(records,True)
  result={'main_sha':'ed9478655bc38266cbea0d2f5e23928249435e28','processes':records,
          'backend_listener_pids':sorted(listeners(18743)),'frontend_listener_pids':sorted(listeners(3000)),
          'schema_scope_confirmed':True,'schema_card_summary':True,'frontend_login_200':True,
          'queue_role':envs['api'].get('JOB_QUEUE_ROLE','all')}
  (OUT/'cutover_receipt.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
 except Exception:
  print('new service readiness failed; restoring old service',flush=True)
  rollback();raise

parser=argparse.ArgumentParser();parser.add_argument('action',choices=['upgrade','rollback']);args=parser.parse_args()
if args.action=='upgrade':upgrade()
else:rollback()
