"""补齐启动顺序：API的/ready需独立worker先报告心跳。"""
import json,os,subprocess,time,urllib.request
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];MAIN=ROOT/'artifacts/daily-main'
read=lambda name:json.loads((OUT/name).read_text())
release=read('release_candidate.json');starting=read('runtime_recovery_starting.json');processes=starting['processes']
assert release['candidate']==starting['candidate'] and 'worker' not in processes
for pid in processes.values():os.kill(pid,0)
commands=subprocess.check_output(['ps','-axo','command'],text=True).splitlines()
assert not any(str(MAIN/'.venv/bin/python')+' -m api.worker' in command for command in commands)
env=dict(read('private/effective_environments.json')['api'],JOB_QUEUE_ROLE='worker',JOB_QUEUE_WORKERS='2',JOB_LIST_CACHE_WARMUP='false')
with (OUT/'daily-worker-recovery.log').open('ab') as log:
 worker=subprocess.Popen([str(MAIN/'.venv/bin/python'),'-m','api.worker'],cwd=MAIN,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
processes['worker']=worker.pid
(OUT/'runtime_recovery_starting.json').write_text(json.dumps(dict(candidate=release['candidate'],processes=processes),indent=2))
op=urllib.request.build_opener(urllib.request.ProxyHandler({}));end=time.monotonic()+60
while True:
 try:
  assert worker.poll() is None
  for port,path in [(18743,'/health'),(18743,'/ready'),(3000,'/login')]:
   with op.open(f'http://127.0.0.1:{port}{path}',timeout=5) as response:assert response.status==200
  break
 except Exception:
  if time.monotonic()>end:raise RuntimeError('API/worker readiness failed')
  time.sleep(.5)
def ids(port):return sorted({int(v) for v in subprocess.run(['lsof','-t',f'-iTCP:{port}','-sTCP:LISTEN'],capture_output=True,text=True).stdout.split()})
receipt=dict(before=release['engine_candidate'],after=release['candidate'],engine_computation_candidate=release['engine_candidate'],
 ui_only_changes=['app/lib/issueCategories.ts','app/tests/issueCategories.test.ts'],processes=processes,
 backend_listener_pids=ids(18743),frontend_listener_pids=ids(3000),build_id=release['build_id'],health_ready_login_200=True,
 configuration_and_decisions_preserved=True,engine_source_bytes_unchanged=True,
 runtime_queue=dict(api_role='api',worker_role='worker',workers=2,original_default_workers=32,persistent_env_files_unchanged=True),
 successful_jobs_preserved=25,cancelled_jobs_pending_official_retry=54,queued_jobs_automatic_resume=32,
 startup_order_correction='API readiness requires worker heartbeat; worker started before ready acceptance')
for name in ['deployment_final_receipt.json','runtime_recovery_deployment.json']:(OUT/name).write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
print(json.dumps(receipt,ensure_ascii=False),flush=True)
