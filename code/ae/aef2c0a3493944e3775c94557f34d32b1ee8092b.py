"""恢复后只等待和验收，不部署、不再入队；任一检查失败立即停止。"""
import json,subprocess,time
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];PYTHON=ROOT/'artifacts/daily-main/.venv/bin/python'
read=lambda name:json.loads((OUT/name).read_text())
assert len(read('recovery_retry_accepted.json')['created'])==54 and not read('recovery_retry_accepted.json')['failed']
assert read('runtime_recovery_deployment.json')['runtime_queue']['workers']==2
def run(script,*args,output=None):
 print('START',script,*args,flush=True)
 with (OUT/(output or script.replace('.py','.log'))).open('w') as log:
  subprocess.run([str(PYTHON),str(OUT/script),*args],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
 print('PASS',script,flush=True)
run('watch_daily_final.py',output='daily_final_progress.log')
assert read('daily_final_finished.json')['finished']==111 and not read('daily_final_finished.json')['errors']
deadline=time.monotonic()+180
while True:
 run('verify_database_final.py','--partial',output='database_final_partial.log')
 partial=read('database_final_partial.json')
 if partial['verified']==111 and not partial['pending']:break
 if time.monotonic()>deadline:raise RuntimeError('Current database/file publication did not settle')
 time.sleep(5)
run('verify_database_final.py',output='database_final_verified.log')
run('verify_preservation.py',output='preservation_final.log')
run('database_audit.py','after_final',output='database_after_final_summary.json')
run('verify_database_history.py',output='database_history_final.log')
run('verify_production_history.py',output='production_history_final.log')
run('verify_shared_scope_final.py',output='shared_scope_final.log')
run('verify_served_build.py',output='served_build_final.log')
run('write_acceptance_report.py',output='acceptance_report_generation.log')
print('BACKEND_RECOVERY_CLOSURE_COMPLETE; actual UI verification remains separate',flush=True)
