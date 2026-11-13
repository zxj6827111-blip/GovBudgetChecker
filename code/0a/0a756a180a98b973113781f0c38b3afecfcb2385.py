"""Independent negative controls; pure calls and original status reads only."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
CODE=Path('/Volumes/workSSD/software development/gbc-worktrees/auto-check-remediation-r22')
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(CODE))
from src.engine.narrative_zero_evidence import narrative_zero_sources
from src.engine.native_final_disclosure import _bind_klass_via_independent_chain
from src.services.fiscal_code_hierarchy import hierarchy_paths
from src.services.review_lifecycle_service import _obligation_evidence_error
from src.engine.diagnostic_fields import _section_page
from src.services.remediation_statistics import derive_attribution

results={}
zero_text='财政拨款公务接待费支出决算0万元。'
prefixes=['','以下表述不成立：','以下为假设情形。','以下为模板示例。','不得认定']
results['zero_scope']=[]
for lead in prefixes:
    text='甲局2024年度部门决算。'+lead+zero_text
    doc=SimpleNamespace(page_texts=[text],fact_context=SimpleNamespace(org_name='甲局',fiscal_year=2024),report_kind='final')
    refs=narrative_zero_sources(doc,table_code='FIN_07_three_public',indicator='reception',stage='决算')
    results['zero_scope'].append({'text':text,'expected_authorized':not lead,'actual_authorized':bool(refs),'refs':refs})

facts=[SimpleNamespace(subject_code=c,subject_name=n,indicator='total_actual') for c,n in [('213','农林水支出'),('21303','水利'),('2130314','防汛')]]
paths=hierarchy_paths({f.subject_code for f in facts})
item=re.search(r'(?P<klass>.+?)\(类\)(?P<kuan>.+?)\(款\)(?P<xiang>.+?)\(项\)','农林支出(类)水利(款)防汛(项)')
original='一般公共预算财政拨款支出决算表\n213 农林水支出\n21303 水利\n2130314 防汛\n'
results['original_uniqueness']=[]
for extra in ['', '214 交通运输支出\n21403 水利\n2140314 防汛\n']:
    doc=SimpleNamespace(page_texts=[original+extra])
    aliases=[]
    bound=_bind_klass_via_independent_chain(doc,facts,item,paths,aliases)
    results['original_uniqueness'].append({'original_text':original+extra,'parsed_codes':[f.subject_code for f in facts],'expected_binding':None if extra else '213','actual_binding':bound,'aliases':aliases})

results['manual_evidence']=[]
for decision,note,evidence in [
    ('verified_ok',None,'第8页 收入决算表（按系统定位核对）'),
    ('verified_ok','〔局部确认草稿〕第8页经营收入列空白，本人确认为无此项','第8页 收入决算表（按系统定位核对）'),
    ('not_applicable','〔局部确认草稿〕第8页经营收入列空白，本人确认为无此项',None),
    ('not_applicable','已有空表或无此项说明：〔局部确认草稿〕第8页经营收入列空白，本人确认为无此项',None),
    ('not_applicable','第8页经营收入列空白，本人确认为无此项',None),
]:
    err=_obligation_evidence_error(decision,note,evidence)
    results['manual_evidence'].append({'decision':decision,'note':note,'evidence_reference':evidence,'accepted':err is None,'error':err})

spec=importlib.util.spec_from_file_location('reviewed_three_carriers',CODE/'scripts/verify_three_carriers.py')
carrier=importlib.util.module_from_spec(spec)
spec.loader.exec_module(carrier)
sample=json.loads((ROOT/'uploads/02e4fb64ab6bbf325117d325a2dcbfa4/status.json').read_text())
result=sample.get('result') or {}
report=sample.get('review_report') or {}
coverage=(result.get('meta') or {}).get('obligation_coverage') or {}
results['actual_carriers']={'job':'02e4fb64ab6bbf325117d325a2dcbfa4','formal_counts':{k:len(v) if isinstance(v,list) else None for k,v in [('result.rule_findings',result.get('rule_findings')),('result.issues',result.get('issues')),('review_report.issues',report.get('issues'))]},'tool_snapshot':carrier.three_carrier_snapshot(result,report,coverage)}
results['single_formal_carrier_controls']=[]
for where in ['result.issues','review_report.issues']:
    r={'rule_findings':[]};q={'check_receipts':[]}
    issue={'id':'F-1','rule':'V33-X','message':'金额不一致','page':8}
    (r if where.startswith('result') else q)['issues']=[issue]
    results['single_formal_carrier_controls'].append({'where':where,'expected_formal_count':1,'actual_snapshot':carrier.three_carrier_snapshot(r,q,{'instances':[]})})

results['section_anchor']=[]
for first in ['2025年度单位决算','2024年度部门决算']:
    doc=SimpleNamespace(page_texts=[first,'目录\n三、支出决算情况说明……18','三、支出决算情况说明\n本年支出合计10万元'])
    results['section_anchor'].append({'cover':first,'expected_page':3,'actual_page':_section_page(doc,'说明文本中未提取到项目支出')})

results['phase0_receipt_capture']=[]
delivery=json.loads((ROOT/'outputs/r22_rework_20261107/relations_after_candidate.json').read_text())
for t in delivery['targets']:
    actual=json.loads((OUT/'candidate_replay'/f"{t['job_uuid']}.json").read_text())
    rs=actual['receipts']
    delivered=sum(rel.get('status')=='insufficient_data' for info in t['rules'].values() for rel in info.get('relations',[]))
    results['phase0_receipt_capture'].append({'job':t['job_uuid'],'delivered_insufficient_receipts_count':delivered,'actual_dict_insufficient_receipts_count':sum(r.get('status')=='insufficient_data' for r in rs),'same_records_read_via_getattr':sum(getattr(r,'status','')=='insufficient_data' for r in rs)})
results['statistics_missing_record']={'before':{'status':'insufficient_data'},'after':{},'actual_attribution':derive_attribution({'status':'insufficient_data'},{}),'limitation':'Missing replay record does not establish physical source material absence.'}
(OUT/'adversarial_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
for k,v in results.items():
    if k=='actual_carriers':print(k,json.dumps({'formal_counts':v['formal_counts'],'tool_main':v['tool_snapshot']['main_carrier'],'conflicts':v['tool_snapshot']['conflicts']},ensure_ascii=False))
    else:print(k,json.dumps(v,ensure_ascii=False))
