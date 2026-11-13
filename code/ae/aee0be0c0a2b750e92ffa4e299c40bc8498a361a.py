import os
import sys,json,tempfile,copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from src.engine.native_final_disclosure import check_performance_counts
from src.engine.rules_v33 import Rule
from src.services import report_confirmation_gate as gate
from api import runtime
OUT=Path(os.environ.get('CODEX_REVIEW_OUT', str(Path(__file__).resolve().parents[1] / 'replay')))
OUT.mkdir(parents=True,exist_ok=True)
class R(Rule):code='V33-250';severity='error'
complete='绩效自评的2025年度项目53个，全部项目评级结果如下：绩效评级为“优”的项目50个，其余评级均为0个。'
bodies=[('partial','绩效自评的2025年度项目53个，其中部分项目评级结果如下：绩效评级为“优”的项目50个。'),('complete',complete),('complete_plus_archive',complete+'部分项目评级资料已归档。'),('complete_plus_archive_before','绩效自评的2025年度项目53个，部分项目评级资料已归档。全部项目评级结果如下：绩效评级为“优”的项目50个，其余评级均为0个。')]
res={'performance':[]}
for name,body in bodies:
 d=SimpleNamespace(page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n'+body],report_kind='final',fiscal_year=2025,organization_name='甲局')
 try:iss=check_performance_counts(R(),d);err=None
 except Exception as e:iss=getattr(e,'partial_issues',[]);err=str(e)
 res['performance'].append({'case':name,'text':body,'issues':[i.model_dump() if hasattr(i,'model_dump') else i.__dict__ for i in iss],'receipts':getattr(d,'fact_receipts',[]),'error':err})
# Re-use Codex's old publication-before-append injection, now reading authoritative status.
with tempfile.TemporaryDirectory(prefix='gbc-r3-confirm-control-') as td:
 root=Path(td);j='control';p=root/j/'status.json';p.parent.mkdir()
 def data(rev='R1',decision=False):return {'job_id':j,'analysis_revision':rev,'result':{'rule_findings':[{'id':'A','rule_id':'R','severity':'high','message':'m','location':{'page':1},'evidence_verification':{'status':'verified'}}]},'review_reasons':[],'human_decisions':[{'issue_id':'A','status':'needs_review'}] if decision else []}
 orig=gate.append_confirm_record
 for mode in ['revision','decision']:
  p.write_text(json.dumps(data()))
  (p.parent/'confirm_reports.jsonl').unlink(missing_ok=True)
  def injected(job,record):
   p.write_text(json.dumps(data(rev='R2') if mode=='revision' else data(decision=True)))
   return orig(job,record)
  with patch.object(runtime,'UPLOAD_ROOT',root),patch.object(gate,'append_confirm_record',side_effect=injected):
   a=gate.confirm_report_atomic(j,expected_analysis_revision='R1',idempotency_key='independent-race-'+mode,confirm_report_enabled=True,actor='Codex')
   payload=runtime.get_job_status_payload(j,apply_issue_filters=False)
   rb=gate.confirm_readback(gate.build_report_confirmation(payload,confirm_report_enabled=True),gate.read_confirm_records(j),payload)
   res['confirm_'+mode+'_interleave']={'response':a,'readback':rb,'records':gate.read_confirm_records(j)}
(OUT/'additional_controls.json').write_text(json.dumps(res,ensure_ascii=False,indent=2,default=str))
for p in res['performance']:print(p['case'],len(p['issues']),[(r.get('status'),r.get('formula')) for r in p['receipts']],p['error'])
for k,v in res.items():
 if k.startswith('confirm_'):print(k,v['response']['http'],len(v['records']),v['readback']['counts'],v['readback']['current_confirm_decision_changed'])
