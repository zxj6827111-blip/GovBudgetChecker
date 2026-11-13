import sys,json,copy
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path.cwd()))
from src.engine.native_final_disclosure import check_performance_counts
from src.engine.rules_v33 import Rule
from src.engine.rule_outcome import RuleOutcomeSignal
from scripts import d1_table_reconciliation as d1
out=Path('/private/tmp/gbc-r6-results-20261111')
class R(Rule):code='V33-250';severity='error'
base='绩效自评的2025年度项目53个，'
parts='绩效评级为“优”的项目50个，其余评级均为0个。'
cases=[('bare_colon',base+'评级结果如下：'+parts,1),('bare_sentence',base+'评级结果如下。'+parts,1),('bare_semicolon',base+'评级结果如下；'+parts,1),('explicit_sentence',base+'全部项目评级结果如下。'+parts,1),('partial',base+'其中，绩效评级为“优”的项目50个。',0),('materials',base+'其中，绩效评级为“优”的项目50个。相关评级材料如下：项目自评表、整改资料。',0)]
res={'performance':[]}
for name,text,expected in cases:
 d=SimpleNamespace(page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n'+text],report_kind='final',fiscal_year=2025,organization_name='甲局')
 try:issues=check_performance_counts(R(),d);signal=None
 except RuleOutcomeSignal as e:issues=e.partial_issues;signal=str(e)
 item={'case':name,'text':text,'expected_count':expected,'actual_count':len(issues),'signal':signal,'receipts':getattr(d,'fact_receipts',[]),'messages':[i.message for i in issues]};res['performance'].append(item);print(name,expected,len(issues),[(r['status'],r['formula']) for r in item['receipts']])
for label in ['本年支出合计','年末结转和结余','年度结余']:
 original=[{'page':1,'cells':['年初结转和结余','2025',label,'100']}];bad=copy.deepcopy(original);bad[0]['cells'][1]='2026';text=' '.join(original[0]['cells'])
 res['d1_'+label]={'original':text,'bad_rows':bad,'known_codes':list(d1._codes_from_rows(bad)),'original_records':d1._parse_original_page(text,1,d1._codes_from_rows(bad)),'actual':d1._reconcile_row_alignment(bad,[text],[1])};print('D1',label,res['d1_'+label]['actual'])
(out/'bounded_controls.json').write_text(json.dumps(res,ensure_ascii=False,indent=2))
