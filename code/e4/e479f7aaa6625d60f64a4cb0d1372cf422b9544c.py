import copy,json,sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path.cwd()))
from src.engine.native_final_disclosure import check_performance_counts
from src.engine.rules_v33 import Rule
from src.engine.rule_outcome import RuleOutcomeSignal
from scripts import d1_table_reconciliation as d1
OUT=Path('/private/tmp/gbc-r7-results-20261111')
class R(Rule):code='V33-250';severity='error'
base='绩效自评的2025年度项目53个，'
complete=base+'评级结果如下。绩效评级为“优”的项目50个，其余评级均为0个。'
partial=base+'其中，绩效评级为“优”的项目50个。'
cases=[('complete',complete,1),
 ('complete_materials',complete+'相关评级材料如下：其他材料中的示例，绩效评级为“良”的项目3个。',1),
 ('partial',partial,0),
 ('partial_materials',partial+'相关评级材料如下：其他材料中的示例，绩效评级为“良”的项目10个。',0)]
results={'performance':[],'d1':[]}
for name,text,expected in cases:
 doc=SimpleNamespace(page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n'+text],report_kind='final',fiscal_year=2025,organization_name='甲局')
 try: issues=check_performance_counts(R(),doc); signal=None
 except RuleOutcomeSignal as exc: issues=exc.partial_issues; signal=str(exc)
 row={'case':name,'text':text,'expected_count':expected,'actual_count':len(issues),'signal':signal,'receipts':getattr(doc,'fact_receipts',[]),'locations':[i.location for i in issues],'messages':[i.message for i in issues]};results['performance'].append(row)
 print(name,expected,len(issues),[(r['status'],r['formula']) for r in row['receipts']])

d1_cases={
 'coded_int_amounts':[['科目编码','科目名称','预算数','决算数'],['201','一般公共服务支出','100','100.50'],['202','外交支出','200','200.50'],['203','国防支出','300','300.50']],
 'two_amounts':[['项目','预算数','决算数'],['甲','100','100.50'],['乙','200','200.50'],['合计','300','301.00']],
 'three_amounts':[['项目','合计','基本支出','项目支出'],['甲','100','20.50','79.50'],['乙','200','80.50','119.50'],['合计','300','101.00','199.00']],
}
for name,cells in d1_cases.items():
 rows=[{'page':1,'cells':c} for c in cells];text='\n'.join(' '.join(c) for c in cells)
 normal=d1._reconcile_row_alignment(rows,[text],[1]);roles,codes,year=d1._learn_column_roles(rows)
 row={'case':name,'text':text,'rows':rows,'roles':roles,'codes':sorted(codes),'year_flag':year,'normal':normal,'original_records':d1._parse_original_page(text,1,codes,year)}
 results['d1'].append(row);print('D1',name,roles,normal)
(OUT/'bounded_controls.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str))
