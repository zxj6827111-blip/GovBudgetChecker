"""真实人工记录在隔离重跑后的可见性校验；生产文件只读。"""
import os,sys,json,gzip,hashlib,shutil
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];CODE=ROOT/'artifacts/comprehensive-review-closure-20261109'
api_env=json.loads((OUT/'private/effective_environments.json').read_text())['api'];os.environ['GOVBUDGET_API_KEY']=api_env['GOVBUDGET_API_KEY'];os.environ.update(DATABASE_URL='',PYTHON_DOTENV_DISABLED='1',ORG_DATA_DIR=str(ROOT/'data'));sys.path.insert(0,str(CODE))
from api import runtime
from src.services import issue_workflow_store as workflow
state=json.loads(gzip.decompress((OUT/'private/workflow_before.json.gz').read_bytes()));before=hashlib.sha256((ROOT/'uploads/.issue_workflow.json').read_bytes()).hexdigest()
folder=OUT/'private/real_decision_history_final14';folder.mkdir(exist_ok=True);runtime.UPLOAD_ROOT=folder
(folder/'.issue_workflow.json').write_text(json.dumps(state,ensure_ascii=False));(folder/'.issue_workflow.json').chmod(0o600)
rows=[];jobs=sorted({r['job_id'] for r in state['issues'].values()})
for job in jobs:
 target=folder/job;target.mkdir(exist_ok=True)
 fresh=OUT/'release_final14'/job/'uploads'/job/'status.json'
 (target/'status.json').write_bytes(gzip.decompress((OUT/'frozen'/job/'status.json.gz').read_bytes()))
 context=OUT/'frozen'/job/'fact_context.json.gz'
 if context.exists():(target/'fact_context.json').write_bytes(gzip.decompress(context.read_bytes()))
 from src.services.legacy_history_guard import archive_before_reanalysis
 archive_before_reanalysis(target,runtime.REANALYZE_EPHEMERAL_FILES)
 shutil.copyfile(fresh,target/'status.json')
 if (fresh.parent/'fact_context.json').exists():shutil.copyfile(fresh.parent/'fact_context.json',target/'fact_context.json')
 original=ROOT/'uploads'/job/'preserved_analyses'
 if original.exists():
  for manifest in original.glob('*/manifest.json'):
   dest=target/'preserved_analyses'/manifest.parent.name;dest.mkdir(parents=True,exist_ok=True);shutil.copyfile(manifest,dest/'manifest.json')
   if (manifest.parent/'status.json').exists():shutil.copyfile(manifest.parent/'status.json',dest/'status.json')
 view=workflow.get_visible_state({'is_admin':True},job_id=job);prior=view.get('prior_decisions') or [];visible=list(view['issues'].values());old=[r for r in state['issues'].values() if r['job_id']==job]
 missing=[]
 for record in old:
  candidates=prior+visible
  if record.get('note') and not any(r.get('note')==record['note'] for r in candidates):missing.append(record['issue_id'])
  if not any(r.get('issue_id')==record['issue_id'] or r.get('resolved_from_issue_id')==record['issue_id'] for r in candidates):missing.append(record['issue_id']+':decision_identity_lost')
 assert not missing,(job,missing)
 rows.append(dict(job=job,old_records=len(old),current_records=len(visible),prior_records=len(prior),inherited=sum(bool(r.get('inherited')) for r in visible),notes_preserved=True))
assert hashlib.sha256((ROOT/'uploads/.issue_workflow.json').read_bytes()).hexdigest()==before
receipt=dict(jobs=len(rows),records=sum(r['old_records'] for r in rows),all_notes_and_decisions_visible=True,production_workflow_unchanged=True,rows=rows)
(OUT/'real_decision_history_final14_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in receipt.items() if k!='rows'},ensure_ascii=False))
