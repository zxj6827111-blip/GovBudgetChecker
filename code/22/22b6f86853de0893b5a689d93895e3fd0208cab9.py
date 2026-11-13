import dotenv
dotenv.load_dotenv=lambda *a,**k:False
dotenv.dotenv_values=lambda *a,**k:{}
from pathlib import Path
from copy import deepcopy
from src.engine.rules_v33 import R33103_Income_vs_Text
from src.engine.rule_registry import _registrations
from src.engine.pipeline import build_issues_payload
from src.engine.check_obligations import build_check_receipts,build_obligation_ledger
from src.services.document_profile_resolver import resolve_document_profile
from src.schemas.review_report import build_review_report
import json,io,contextlib
fixture_source=json.loads(Path('outputs/reacceptance_20261004/rules_audit.json').read_text())['reproduction']['code']
space={}
with contextlib.redirect_stdout(io.StringIO()):exec(fixture_source,space)
checks=[]
cases={
 'same_clause_other_indicator_share':'本年收入合计1000.00万元，其中财政拨款收入1000.00万元，事业收入0.00万元，占0.00%。',
 'semicolon_other_indicator_share':'本年收入合计1000.00万元，其中财政拨款收入1000.00万元；事业收入0.00万元，占0.00%。',
 'fiscal_own_share_then_other_share':'本年收入合计1000.00万元，其中财政拨款收入1000.00万元，占100.00%，事业收入0.00万元，占0.00%。',
}
for name,body in cases.items():
 doc,items,kind=space['doc_for']('V33-103');doc=deepcopy(doc);doc.fact_context=space['fctx'](items,kind)
 doc.page_texts[1]='二、收入决算情况说明\n'+body+'\n三、支出决算情况说明'
 doc.page_tables[0]=[[['科目编码','科目名称','本年收入合计','财政拨款收入','上级补助收入','事业收入','经营收入','附属单位上缴收入','其他收入'],['','合计','1000.00','1000.00','0.00','0.00','0.00','0.00','0.00']]]
 companion=next(r.implementation for r in _registrations() if r.rule_id=='V33-226')
 payload=build_issues_payload(doc,use_ai_assist=False,report_kind=kind,rules=[R33103_Income_vs_Text(),companion])
 profile=resolve_document_profile(explicit_report_kind='final',filename='unit-2025-final.pdf',page_texts=doc.page_texts,cover_org_name='测试单位',cover_org_label='单位')
 ledger=build_obligation_ledger(profile,report_kind=kind,rule_execution_summary=payload['rule_execution_summary'],ai_required=False)
 final=build_check_receipts(payload,report_kind=kind,rule_execution_summary=payload['rule_execution_summary'],obligation_coverage=ledger,fact_receipts=doc.fact_receipts)
 canonical=build_review_report({'job_id':'round6-synthetic','analysis_revision':'round6','status':'done','report_kind':kind,'result':{**payload,'meta':{'check_receipts':final}}})
 checks.append({'case_id':name,'expected_finding_count':0,'body':body,'payload':payload,'final_check_receipts':final,'document_profile':profile.model_dump(),'actual_obligation_ledger':ledger,'review_report':canonical,'passed':not bool(payload['issues']['all'])})
print(json.dumps(checks,ensure_ascii=False,default=str))
