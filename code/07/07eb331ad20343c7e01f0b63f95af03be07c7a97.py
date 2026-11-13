import sys, json, asyncio, os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from src.engine.pipeline import build_document, build_issues_payload
from src.engine.rules_v33 import Rule
from src.services.report_confirmation_gate import build_report_confirmation
OUT=Path('/private/tmp/gbc-independent-20261110')
class NoiseRule(Rule):
 code='INDEPENDENT-NOISE'; severity='error';desc='控制试验'
 def __init__(self,message):self.message=message
 def apply(self,doc):return [self._issue(self.message,{'page':1},evidence_text='金额100万元')]
results={}
for label,msg in [('plain','金额100万元与分项之和90万元不一致'),('keyword','金额格式错误：金额100万元与分项之和90万元不一致')]:
 doc=build_document('control.pdf',['甲局2025年度部门决算\n金额100万元'],[],0)
 result=build_issues_payload(doc,report_kind='final',rules=[NoiseRule(msg)])
 results['noise_'+label]={'delivered':len(result['issues']['all']),'summary':result['rule_execution_summary'],'doc_ledger':getattr(doc,'delivery_noise_ledger',None),'payload_has_ledger':'delivery_noise_ledger' in result}
# 产品实际持久化形状与读取门禁直接比对
saved=[]
for prefix in ['6000a11c','0a8a27','0b507c']:
 p=next(Path('uploads').glob(prefix+'*/status.json'))
 status=json.loads(p.read_text());result=status.get('result') or {}
 gate=build_report_confirmation(status)
 all_items=(result.get('issues') or {}).get('all',[]) if isinstance(result.get('issues'),dict) else []
 saved.append({'task':prefix,'result_keys':list(result),'issues_all':len(all_items),'issues_evidence_verified':sum((f.get('evidence_verification') or {}).get('status')=='verified' for f in all_items),'rule_findings':len(result.get('rule_findings') or []),'confirmation_counts':gate['counts']})
results['saved_schema']=saved
# 技术未完成的明确结构字段不能因提示措辞含人工/歧义而放行
status={'analysis_revision':'r2','result':{'rule_findings':[]},'review_reasons':[{'unresolved_rules':[{'rule_id':'V33-222','detail':'原件清晰但字段映射失败，需人工复核','diagnostics':[{'next_action':'tech_fix','reason_codes':['operand_missing']}]}]}]}
results['technical_reclassified_as_ambiguity']=build_report_confirmation(status,confirm_report_enabled=True)
# verified 表示引文可定位，不代表材料缺陷已确定
status={'analysis_revision':'r2','result':{'rule_findings':[{'id':'ambiguous','rule_id':'X','severity':'manual_review','message':'真实口径歧义','location':{'page':1},'evidence_verification':{'status':'verified'}}]},'review_reasons':[]}
results['verified_manual_review']=build_report_confirmation(status,confirm_report_enabled=True)
# API 对过期请求与显式忽略的处理
os.environ['TESTING']='true';os.environ['GOVBUDGET_CONFIRM_REPORT_ENABLED']='1'
from api.routes import jobs
status={'analysis_revision':'rev-current','result':{'rule_findings':[
 {'id':'finding-ignored','rule_id':'V33-243','severity':'high','message':'甲','location':{'page':1},'evidence_verification':{'status':'verified'}},
 {'id':'finding-question','rule_id':'V33-243','severity':'manual_review','message':'乙','location':{'page':1},'evidence_verification':{'status':'verified'}}]},'human_decisions':[{'issue_id':'finding-ignored','status':'ignored'},{'issue_id':'finding-question','status':'questioned'}]}
audit=[]
async def probe_api():
 body=jobs.ConfirmReportRequest.model_validate({'expected_analysis_revision':'rev-old'})
 with patch.object(jobs,'require_job_access',return_value=(None,None,{'username':'reviewer'},status)),patch.object(jobs,'append_audit_event',side_effect=lambda **kw:audit.append(kw)):
  answer=await jobs.confirm_report('synthetic-control',body,SimpleNamespace())
 return {'accepted_request_body':body.model_dump(),'response':answer,'audit_calls':len(audit),'human_decisions_after':status['human_decisions']}
results['api_stale_ignored_questioned']=asyncio.run(probe_api())
(OUT/'controls.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str))
print(json.dumps(results,ensure_ascii=False,indent=2,default=str))
