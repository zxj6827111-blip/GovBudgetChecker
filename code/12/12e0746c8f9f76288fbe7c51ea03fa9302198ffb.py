import sys,copy,json,asyncio,tempfile,os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from scripts.d1_table_reconciliation import _reconcile_row_alignment
from src.engine.native_final_disclosure import check_performance_counts
from src.engine.rules_v33 import Rule
from src.services.report_confirmation_gate import build_report_confirmation
OUT=Path('/private/tmp/gbc-r2-results-20261111')
results={}
page='支出决算表\n合计 100.00 70.00 30.00\n201 一般公共服务支出 100.00 70.00 30.00'
normal=[{'page':1,'cells':['合计','100.00','70.00','30.00']},{'page':1,'cells':['201','一般公共服务支出','100.00','70.00','30.00']}]
wrong=copy.deepcopy(normal)
for row in wrong:row['cells'][-2:]=row['cells'][-2:][::-1]
results['d1_columns']={'normal':_reconcile_row_alignment(normal,[page],[1]),'wrong_basic_project_swapped':_reconcile_row_alignment(wrong,[page],[1])}
# 同页多证据不等于每个必需侧都交付：仅给错误侧一个真实目录/标题引文
from scripts.d4_real_link_acceptance import verify_all_evidence
f={'id':'I','source':'rule','rule_id':'R','severity':'high','location':{'page':1,'table_refs':[{'page':1,'span':'本年支出100万元','role':'合计'},{'page':1,'span':'基本70万元 项目40万元','role':'分项'}]},'evidence':[{'evidence_id':'e1','page':1,'quote':'支出决算表'}],'evidence_verification':{'status':'verified'}}
class Client:
 def get(self,*a,**kw):return SimpleNamespace(status_code=200,json=lambda:{'physical_page':1,'quote':'支出决算表','file_hash':'a'*64})
results['d4_same_page_missing_operands']=verify_all_evidence(Client(),'control',{'rule_findings':[f]},['支出决算表\n本年支出100万元\n基本70万元 项目40万元'],'a'*64,1)
# 写侧读到R1后，新版本发布，确认记录实际落盘前并未重新核对当前状态。
os.environ['TESTING']='true';os.environ['GOVBUDGET_CONFIRM_REPORT_ENABLED']='1'
from api.routes import jobs
from api import runtime
from src.services import report_confirmation_gate as gate
with tempfile.TemporaryDirectory(prefix='independent-confirm-race-') as td:
 root=Path(td);job='control';(root/job).mkdir()
 status_path=root/job/'status.json'
 def status(rev):return {'analysis_revision':rev,'result':{'rule_findings':[{'id':'finding-'+rev,'rule_id':'R','severity':'high','message':'m','location':{'page':1},'evidence_verification':{'status':'verified'}}]},'review_reasons':[]}
 status_path.write_text(json.dumps(status('R1')))
 def require(req,jid):return None,None,{'username':'independent'},json.loads(status_path.read_text())
 original_append=gate.append_confirm_record
 def append_after_publication(jid,record):
  status_path.write_text(json.dumps(status('R2')))
  return original_append(jid,record)
 async def invoke():
  body=jobs.ConfirmReportRequest(expected_analysis_revision='R1',idempotency_key='race-key-independent')
  with patch.object(runtime,'UPLOAD_ROOT',root),patch.object(jobs,'require_job_access',side_effect=require),patch.object(gate,'append_confirm_record',side_effect=append_after_publication),patch.object(jobs,'append_audit_event'):
   answer=await jobs.confirm_report(job,body,SimpleNamespace())
   readback=await jobs.get_report_confirmation(job,SimpleNamespace())
   return {'response':answer,'current_revision':json.loads(status_path.read_text())['analysis_revision'],'readback_revision':readback['analysis_revision'],'readback_latest_record_revision':readback['latest_confirm_record']['analysis_revision'],'records':gate.read_confirm_records(job)}
 results['confirm_revision_race']=asyncio.run(invoke())
# 完整构成提示不应把“下列为部分”强制改为等式
class R(Rule):code='V33-250';severity='error'
results['performance_variants']=[]
for body in ['绩效自评的2025年度项目53个，其中，绩效评级为“优”的项目50个。','绩效自评的2025年度项目53个，全部项目评级结果如下：绩效评级为“优”的项目50个，其余评级均为0个。','绩效自评的2025年度项目53个，其中部分项目评级结果如下：绩效评级为“优”的项目50个。']:
 doc=SimpleNamespace(page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n'+body],report_kind='final',fiscal_year=2025,organization_name='甲局')
 try:iss=check_performance_counts(R(),doc);err=None
 except Exception as e:iss=getattr(e,'partial_issues',[]);err=str(e)
 results['performance_variants'].append({'original':body,'issues':[i.message for i in iss],'receipts':[r.get('status') for r in getattr(doc,'fact_receipts',[])],'error':err})
(OUT/'independent_controls.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str))
print(json.dumps(results,ensure_ascii=False,indent=2,default=str))
