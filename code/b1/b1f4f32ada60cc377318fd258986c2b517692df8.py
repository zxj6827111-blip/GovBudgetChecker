"""原件隔离重放：只写本报告目录，AI/OCR/数据库禁用，保留原件和生产结果。"""
import os,json,subprocess,shutil,sys,time
from pathlib import Path
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
OUT=ROOT/'outputs/shiquan_source_coverage_20261109';CODE=ROOT/'artifacts/shiquan-source-coverage-20261109'
PYTHON=ROOT/'artifacts/daily-main/.venv/bin/python'
manifest=json.loads((OUT/'manifest_before.json').read_text())
profile=json.loads((ROOT/'outputs/claude_closeout_20261108/gold33_final2/meta.json').read_text())['profile']
profile['GBC_TEMPLATE_SCOPE']='shanghai';profile['GBC_BUDGET_TEMPLATE_SCOPE']='shanghai'
profile['GOVBUDGET_REPORTING_TASKS_ENABLED']='false'
entries=[]
for job,item in manifest.items():
 base=OUT/'replay'/job;upload=base/'uploads';target=upload/job;target.mkdir(parents=True,exist_ok=True)
 org=base/'org';org.mkdir(exist_ok=True)
 for name in ['organizations.json','job_org_links.json']:
  src=ROOT/'data'/name
  if src.exists():shutil.copyfile(src,org/name)
 pdf=Path(item['pdf']);shutil.copyfile(pdf,target/pdf.name)
 meta=json.loads((ROOT/'uploads'/job/'job_meta.json').read_text())
 initial={**meta,'job_id':job,'checksum':item['sha256'],'status':'queued','use_local_rules':True,'use_ai_assist':False,'mode':'legacy'}
 (target/'status.json').write_text(json.dumps(initial,ensure_ascii=False));(target/'job_meta.json').write_text(json.dumps(meta,ensure_ascii=False))
 env={k:os.environ[k] for k in ['PATH','HOME','LANG','TMPDIR'] if k in os.environ};env.update(profile)
 env.update(ORG_DATA_DIR=str(org),UPLOAD_DIR=str(upload),AUDIT_LOG_PATH=str(base/'audit.jsonl'),
  AI_MODEL_SETTINGS_PATH=str(base/'absent_ai.json'),DISCLOSURE_CALENDAR_PATH=str(base/'absent_calendar.json'))
 task={'kind':'pipeline','job_dir':str(target),'dotenv_names':[],'pinned_names':sorted(profile)}
 (base/'task.json').write_text(json.dumps(task));result=base/'worker_result.json'
 cmd=[str(PYTHON),'-s',str(ROOT/'scripts/gold_baseline_run.py'),'_worker','--code-root',str(CODE),'--task',str(base/'task.json'),'--result',str(result)]
 t=time.time()
 with (base/'worker.log').open('w') as log:
  try:r=subprocess.run(cmd,cwd=CODE,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240)
  except subprocess.TimeoutExpired:raise SystemExit('原件超时 '+job)
 record=json.loads(result.read_text()) if result.exists() else {'ok':False,'error':'no worker result'}
 status=json.loads((target/'status.json').read_text())
 entry={'job':job,'ok':record.get('ok'),'status':status.get('status'),'seconds':round(time.time()-t,2),'error':record.get('error'),'todo':record.get('todo')}
 entries.append(entry);(OUT/'replay_summary.json').write_text(json.dumps(entries,ensure_ascii=False,indent=2))
 print(job,entry['status'],entry['seconds'],'blocking',(record.get('todo') or {}).get('blocking_ids'),'error',entry['error'],flush=True)
 if not entry['ok']:raise SystemExit(1)
