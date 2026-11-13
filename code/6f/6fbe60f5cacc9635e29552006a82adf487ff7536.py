import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import dotenv
dotenv.load_dotenv=lambda *a,**k:False
dotenv.dotenv_values=lambda *a,**k:{}
from dataclasses import asdict
from decimal import Decimal
from src.engine.fact_context import FactContext
from src.engine.pipeline import build_issues_payload
from src.engine.check_obligations import build_check_receipts,build_obligation_ledger
from src.engine.rule_registry import select_rules
from src.engine.rules_v33 import R33103_Income_vs_Text,R33104_Expense_vs_Text,build_document,_resolve_indicator_percentage
from src.schemas.parse_artifact import FiscalFact
from src.services.document_profile_resolver import resolve_document_profile
import json

def fact(uid,indicator,value,table):
 return FiscalFact(fact_uid=uid,indicator=indicator,raw_value=str(value),unit='万元',normalized_value=Decimal(str(value)),subject_name='合计',table_code=table,recognition_status='verified',confidence=.99,evidence_ids=['synthetic-ev-'+uid])

def run(kind,case_id,body,total,fiscal=None,basic=None,project=None):
 if kind=='income':
  heading,table_name='二、收入决算情况说明','收入决算表'
  table=[[['科目编码','科目名称','本年收入合计','财政拨款收入','上级补助收入','事业收入','经营收入','附属单位上缴收入','其他收入'],['','合计',str(total),str(fiscal),'0','0','0','0',str(total-fiscal)]]]
  facts=[fact('income-total','total_actual',total,'FIN_02_income'),fact('income-fiscal','fiscal_allocation',fiscal,'FIN_02_income')]
  rule=R33103_Income_vs_Text();companion_id='V33-226';obligation_id='OBL-TXT-T2'
  claim=_resolve_indicator_percentage(body,('一般公共预算财政拨款收入','财政拨款收入','财政拨款'),('本年收入合计','收入总额','总收入','总收入合计','收入合计'))
 else:
  heading,table_name='三、支出决算情况说明','支出决算表'
  table=[[['科目编码','科目名称','本年支出合计','基本支出','项目支出','上缴上级支出','经营支出','对附属单位补助支出'],['','合计',str(total),str(basic),str(project),'0','0','0']]]
  facts=[fact('expense-total','total_actual',total,'FIN_03_expenditure'),fact('expense-basic','basic_actual',basic,'FIN_03_expenditure'),fact('expense-project','project_actual',project,'FIN_03_expenditure')]
  rule=R33104_Expense_vs_Text();companion_id='V33-220';obligation_id='OBL-TXT-T3'
  claim={nm:asdict(_resolve_indicator_percentage(body,(nm,),('本年支出合计','支出合计','支出总额','总支出'))) for nm in ('基本支出','项目支出')}
 text=f'2025年测试单位决算公开\n{heading}\n{body}\n{table_name}\n单位：万元'
 doc=build_document('测试单位2025年决算.pdf',[text],[table],100)
 doc.fact_context=FactContext(facts=facts,fiscal_year=2025,report_kind='final')
 companion=next(r for r in select_rules('final') if r.code==companion_id)
 payload=build_issues_payload(doc,use_ai_assist=False,report_kind='final',rules=[rule,companion])
 profile=resolve_document_profile(explicit_report_kind='final',filename=doc.path,page_texts=doc.page_texts,cover_org_name='测试单位',cover_org_label='单位')
 ledger=build_obligation_ledger(profile,report_kind='final',rule_execution_summary=payload['rule_execution_summary'])
 receipts=build_check_receipts(payload,report_kind='final',rule_execution_summary=payload['rule_execution_summary'],obligation_coverage=ledger,fact_receipts=doc.fact_receipts)
 return {'case_id':case_id,'body':body,'claim':asdict(claim) if kind=='income' else claim,'payload':payload,'document_profile':profile.model_dump(),'obligation':next(i for i in ledger['instances'] if i['obligation_id']==obligation_id),'ledger':ledger,'fact_receipts':doc.fact_receipts,'final_check_receipts':receipts}

cases=[]
incomes=[
 ('qualified_fiscal_denominator','本年收入合计1000万元，财政拨款收入600万元，占财政拨款收入合计100%。',600),
 ('legitimate_total_denominator','本年收入合计1000万元，财政拨款收入600万元，占本年收入合计60%。',600),
 ('wrong_total_percentage_positive','本年收入合计1000万元，财政拨款收入600万元，占本年收入合计59.89%。',600),
 ('foreign_subject_with_repeated_indicator','本年收入合计1000万元，财政拨款收入1000万元，教育局财政拨款收入50万元，占50%。',1000),
 ('foreign_subject_without_repeated_indicator_control','本年收入合计1000万元，财政拨款收入1000万元，教育局50万元，占50%。',1000),
]
for cid,body,fiscal in incomes:cases.append(run('income',cid,body,1000,fiscal=fiscal))
expenses=[
 ('expense_both_legal','本年支出合计100万元，基本支出60万元，占60%，项目支出40万元，占40%。'),
 ('expense_both_wrong_positive','本年支出合计100万元，基本支出60万元，占55%，项目支出40万元，占45%。'),
 ('expense_pass_plus_na','本年支出合计100万元，基本支出60万元，占60%，项目支出40万元。'),
 ('expense_qualified_basic_denominator','本年支出合计100万元，基本支出60万元，占基本支出合计100%，项目支出40万元，占40%。'),
]
for cid,body in expenses:cases.append(run('expense',cid,body,100,basic=60,project=40))
print(json.dumps(cases,ensure_ascii=False,default=str))
