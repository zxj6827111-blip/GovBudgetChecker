"""用户授权的本机维护入队；不启动新worker，不绑定端口，沿用应用业务入口。"""
import asyncio,json,os,sys,subprocess,hashlib,time
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];MAIN=ROOT/'artifacts/daily-main'
env=json.loads((OUT/'private/effective_environments.json').read_text())['api'];os.environ.update(env);os.environ.update(JOB_QUEUE_ROLE='api',JOB_LIST_CACHE_WARMUP='false');sys.path.insert(0,str(MAIN));os.chdir(MAIN)
from api import main as api_main,runtime
from api.queue_runtime import has_recent_worker_heartbeat
from src.services.audit_log import append_audit_event
from src.db.connection import DatabaseConnection
expected={r['job'] for r in json.loads((OUT/'daily_latest_preview.json').read_text())['selected']}
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=MAIN,text=True).strip()==json.loads((OUT/'release_candidate.json').read_text())['candidate']
assert runtime.get_job_queue() is None and has_recent_worker_heartbeat(runtime.UPLOAD_ROOT)
assert all(json.loads((runtime.UPLOAD_ROOT/j/'status.json').read_text()).get('status') not in runtime.ACTIVE_ANALYSIS_STATUSES for j in expected)
async def run():
 try:
  body=dict(use_local_rules=True,use_ai_assist=False,mode='legacy',latest_per_department=True,direct_department_only=False)
  response=await runtime.reanalyze_all_jobs(body)
  (OUT/'daily_reanalysis_accepted.json').write_text(json.dumps(response,ensure_ascii=False,indent=2))
  append_audit_event(action='jobs.reanalyze_all',actor='codex:user-authorized-maintenance',result='success' if not response['failed_count'] else 'partial',resource_type='job_batch',details={**{k:response.get(k) for k in ['requested_count','selected_count','created_count','skipped_count','failed_count']},'channel':'local_application_service','use_ai_assist':False,'release':subprocess.check_output(['git','rev-parse','HEAD'],cwd=MAIN,text=True).strip()})
  actual={r['job_id'] for r in response['created']}
  print(json.dumps({k:response[k] for k in ['requested_count','selected_count','created_count','skipped_count','failed_count']},ensure_ascii=False),flush=True)
  assert response['failed_count']==0 and actual==expected,(response['failed'],sorted(expected-actual),sorted(actual-expected))
 finally:
  if DatabaseConnection.is_initialized():await DatabaseConnection.close()
asyncio.run(run())
