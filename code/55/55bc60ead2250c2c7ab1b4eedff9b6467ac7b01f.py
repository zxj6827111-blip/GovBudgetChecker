"""只读检验真实历史备注在当前工作流接口模型中可见，不写人工决定。"""
import gzip,json,os,sys
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];MAIN=ROOT/'artifacts/daily-main'
os.environ.update(json.loads((OUT/'private/effective_environments.json').read_text())['api'])
os.environ['JOB_QUEUE_ROLE']='api';sys.path.insert(0,str(MAIN))
from src.services.issue_workflow_store import get_visible_state
original=json.loads(gzip.decompress((OUT/'private/workflow_before.json.gz').read_bytes()))
rows=[]
for job in sorted({r['job_id'] for r in original['issues'].values()}):
 view=get_visible_state({'is_admin':True},job_id=job)
 visible=list(view['issues'].values());prior=view.get('prior_decisions') or [];before=[r for r in original['issues'].values() if r['job_id']==job]
 for record in before:
  assert any(r.get('issue_id')==record['issue_id'] or r.get('resolved_from_issue_id')==record['issue_id'] for r in [*visible,*prior]),(job,record['issue_id'])
  if record.get('note'):assert any(r.get('note')==record['note'] for r in [*visible,*prior]),(job,'note')
 rows.append(dict(job=job,original_records=len(before),visible_current_records=len(visible),visible_history_records=len(prior),inherited=sum(bool(r.get('inherited')) for r in visible),all_original_notes_visible=True))
result=dict(jobs=len(rows),original_records=sum(r['original_records'] for r in rows),all_notes_visible=True,rows=rows)
(OUT/'production_history_verified.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False))
