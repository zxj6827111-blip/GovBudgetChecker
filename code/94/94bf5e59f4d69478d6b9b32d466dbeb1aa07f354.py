"""仅重试已保全、确认由旧看门狗取消的54份，使用官方业务入口。"""
import asyncio,json,os,sys
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];MAIN=ROOT/'artifacts/daily-main'
env=json.loads((OUT/'private/effective_environments.json').read_text())['api'];os.environ.update(env);os.environ.update(JOB_QUEUE_ROLE='api',JOB_QUEUE_WORKERS='2',JOB_LIST_CACHE_WARMUP='false');sys.path.insert(0,str(MAIN));os.chdir(MAIN)
from api import main as api_main,runtime
from api.queue_runtime import has_recent_worker_heartbeat
from src.db.connection import DatabaseConnection
from src.services.audit_log import append_audit_event
rows=json.loads((OUT/'daily_final_interrupted_status_inventory.json').read_text());targets=[r['job'] for r in rows if r['status']=='error' and r['error']=='pipeline_cancelled']
assert len(targets)==54 and runtime.get_job_queue() is None and has_recent_worker_heartbeat(runtime.UPLOAD_ROOT)
path=OUT/'recovery_retry_accepted.json';assert not path.exists(),'Do not resubmit a recorded recovery batch'
receipt=dict(requested=len(targets),created=[],failed=[],mode='legacy',use_ai_assist=False)
path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
async def run():
 try:
  for job in targets:
   s=runtime.read_json_file(runtime.UPLOAD_ROOT/job/'status.json',default={})
   assert s.get('status')=='error' and s.get('error')=='pipeline_cancelled',(job,s.get('status'))
   try:
    result=await runtime.reanalyze_job(job,dict(use_local_rules=True,use_ai_assist=False,mode='legacy'))
    receipt['created'].append(result)
   except Exception as error:
    receipt['failed'].append(dict(job=job,error_type=type(error).__name__))
    raise
   finally:path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
  append_audit_event(action='jobs.reanalyze_cancelled',actor='codex:user-authorized-maintenance',result='success',resource_type='job_batch',details=dict(count=len(targets),reason='watchdog_restart_pipeline_cancelled',channel='local_application_service',use_ai_assist=False))
  print('Official retry accepted:',len(receipt['created']),'cancelled only; 25 prior successes unchanged',flush=True)
 finally:
  if DatabaseConnection.is_initialized():await DatabaseConnection.close()
asyncio.run(run())
