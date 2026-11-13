"""Second independent review: mechanism controls beyond previously disclosed examples."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from collections import Counter
import re

ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
CODE=ROOT.parent/'gbc-worktrees/auto-check-remediation-r22'
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(CODE))
from src.engine.narrative_zero_evidence import narrative_zero_sources
from src.engine.native_final_disclosure import _bind_klass_via_independent_chain, GENERAL
from src.services.fiscal_code_hierarchy import hierarchy_paths
from src.services.review_lifecycle_service import _obligation_evidence_error
from src.engine.pipeline import run_rules_with_outcomes
from src.engine.rules_v33 import R33240_Table2_IncomeAdvancedCheck

results={}
def load(name,p):
    spec=importlib.util.spec_from_file_location(name,p)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
tool=load('new_three_carriers',CODE/'scripts/verify_three_carriers.py')
collect=load('new_r22_collect',CODE/'scripts/phase0_r22_target_relations.py')

base='甲局2024年度部门决算。'
texts=[
    base+'财政拨款公务接待费支出决算0万元。',
    base+'以下为模板示例。财政拨款公务接待费支出决算0万元。因公出国（境）费支出决算0万元。',
    base+'以下为假设情形。财政拨款公务接待费支出决算0万元。因公出国（境）费支出决算0万元。',
    base+'以下为模板示例。'+('用于演示该指标的填报方式和文字格式，'*4)+'财政拨款公务接待费支出决算0万元。',
]
results['frame_scope']=[]
for text in texts:
    doc=SimpleNamespace(page_texts=[text],fact_context=SimpleNamespace(org_name='甲局',fiscal_year=2024),report_kind='final')
    results['frame_scope'].append({'text':text,'authorized':{i:bool(narrative_zero_sources(doc,table_code='FIN_07_three_public',indicator=i,stage='决算')) for i in ['reception','overseas']}})

facts=[SimpleNamespace(subject_code=c,subject_name=n,indicator='total_actual',table_code=GENERAL,extra_dims={'evidence_refs':[{'physical_page':1}]}) for c,n in [('213','农林水支出'),('21303','水利'),('2130314','防汛')]]
paths=hierarchy_paths({f.subject_code for f in facts})
item=re.search(r'(?P<klass>.+?)\(类\)(?P<kuan>.+?)\(款\)(?P<xiang>.+?)\(项\)','农林支出(类)水利(款)防汛(项)')
first='一般公共预算财政拨款支出决算表\n213 农林水支出\n21303 水利\n2130314 防汛\n'
second='一般公共预算财政拨款支出决算表（续）\n214 交通运输支出\n21403 水利\n2140314 防汛\n'
results['original_page_coverage']=[]
for pages in [[first],[first+second],[first,second]]:
    doc=SimpleNamespace(page_texts=pages,parse_artifact=SimpleNamespace(logical_tables=[SimpleNamespace(table_code=GENERAL,page_start=1,page_end=len(pages))]))
    aliases=[]
    results['original_page_coverage'].append({'original_pages':pages,'parsed_fact_evidence_pages':[1],'expected_binding':'213' if len(pages)==1 and pages[0]==first else None,'actual_binding':_bind_klass_via_independent_chain(doc,facts,item,paths,aliases),'aliases':aliases})

results['scope_protocol']=[]
for decision,note,evidence,scope in [
    ('verified_ok',None,'第8页 收入决算表',True),
    ('not_applicable','已有空表或无此项说明：〔局部确认草稿〕第8页经营收入为空',None,True),
    ('not_applicable','第8页经营收入列空白，本人确认为无此项',None,True),
    ('verified_ok','合计10万元等于基本8万元加项目2万元，全部关系已核对',None,True),
    ('verified_ok','合计10万元等于基本8万元加项目2万元，全部关系已核对',None,False),
]:
    error=_obligation_evidence_error(decision,note,evidence,scope_confirmed=scope)
    results['scope_protocol'].append({'decision':decision,'note':note,'evidence':evidence,'scope_confirmed':scope,'accepted':error is None})

issue={'id':'F1','rule':'V33-X','message':'合计10万元与分项8万元不一致','page':8}
snap1=tool.three_carrier_snapshot({'rule_findings':[issue]}, {}, {})
snap2=tool.three_carrier_snapshot({'rule_findings':[{**issue,'message':'合计100万元与分项8万元不一致'}]}, {}, {})
results['carrier_content_change']={'snapshots_identical':tool._canonical(snap1)==tool._canonical(snap2),'before':issue,'after':{**issue,'message':'合计100万元与分项8万元不一致'}}
same={'id':'uuid:one','rule':'V33-X','message':'同一问题','page':8}
dedup=tool.three_carrier_snapshot({'rule_findings':[same]}, {'issues':[{**same,'id':'uuid:two'}]}, {})
results['carrier_volatile_id']={'same_content_except_id':True,'formal_total':dedup['formal_total_deduped'],'expected_total':1}

before={'head_commit':'before','targets':[{'job_uuid':'same-job','rules':{'V33-X':{'status':'pass','relations':[{'relation_id':'r1','status':'pass'}]}}}]}
after={'head_commit':'after','targets':[{'job_uuid':'same-job','rules':{'V33-X':{'status':'insufficient_data','relations':[]}}}]}
(OUT/'missing_before.json').write_text(json.dumps(before))
(OUT/'missing_after.json').write_text(json.dumps(after))
collect._run_relations_stats(str(OUT/'missing_before.json'),str(OUT/'missing_after.json'),str(OUT/'missing_stats.json'))
results['missing_after_engine']=json.loads((OUT/'missing_stats.json').read_text())

# A controlled addition to the original income explanation is a synthetic positive,
# not a claim that the original PDF contains zero evidence.
path=ROOT/'uploads/b589d6bd4436edaa140a224135bf10d0/上海市知识产权局.pdf'
built=collect.build_offline_doc(path,{'name':None,'level':None})
doc=built['doc']
income=[]
for add in ['', '\n上海市知识产权局2023年度部门决算。上级补助收入为0万元。经营收入为0万元。附属单位上缴收入为0万元。', '\n上海市知识产权局2023年度部门决算。上级补助收入决算为0万元。经营收入决算为0万元。附属单位上缴收入决算为0万元。']:
    d=copy.deepcopy(doc)
    if add:d.page_texts[-1]+=add
    issues,outcomes=run_rules_with_outcomes(d,use_ai_assist=False,report_kind='final',rules=[R33240_Table2_IncomeAdvancedCheck])
    receipts=d.fact_receipts
    income.append({'synthetic_zero_explanation_added':bool(add),'added_text':add,'recognized_zero_sources':{i:bool(narrative_zero_sources(d,table_code='FIN_02_income',indicator=i,stage='决算')) for i in ['superior_subsidy_income','operational_income','subsidiary_income']},'outcome':outcomes[0].status,'receipt_statuses':dict(Counter(r.get('status') for r in receipts)),
        'total_income_relation':[r for r in receipts if r.get('sub_check_id')=='FIN_02_income:total:income_sources']})
results['full_income_rule_control']=income

(OUT/'remaining_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str)+'\n')
for key,value in results.items():
    if key=='missing_after_engine':print(key,json.dumps({'engine':value['engine'],'attribution':value['attribution']},ensure_ascii=False))
    elif key=='full_income_rule_control':print(key,json.dumps([{k:v for k,v in x.items() if k!='total_income_relation'} for x in value],ensure_ascii=False))
    else:print(key,json.dumps(value,ensure_ascii=False))
