import json,sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path.cwd()))
from src.engine.native_final_disclosure import check_performance_counts
from src.engine.rules_v33 import Rule
from src.engine.rule_outcome import RuleOutcomeSignal
from scripts import d1_table_reconciliation as d1
OUT=Path('/private/tmp/gbc-r8-results-20261111')
class R(Rule):code='V33-250';severity='error'
base='绩效自评的2025年度项目53个，'
counts='绩效评级为“优”的项目50个，其余评级均为0个。'
cases=[('complete',base+'评级结果如下。'+counts,1),
 ('archive_comma',base+'相关材料已归档，评级结果如下。'+counts,1),
 ('archive_period',base+'相关材料已归档。评级结果如下。'+counts,1),
 ('archive_parentheses',base+'（相关材料已归档）评级结果如下。'+counts,1),
 ('archive_semicolon',base+'相关材料已归档；评级结果如下。'+counts,1),
 ('partial',base+'其中，绩效评级为“优”的项目50个。',0)]
results={'performance':[],'d1':[]}
for name,text,expected in cases:
 doc=SimpleNamespace(page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n'+text],report_kind='final',fiscal_year=2025,organization_name='甲局')
 try:issues=check_performance_counts(R(),doc);signal=None
 except RuleOutcomeSignal as exc:issues=exc.partial_issues;signal=str(exc)
 row={'case':name,'text':text,'expected_count':expected,'actual_count':len(issues),'signal':signal,'receipts':getattr(doc,'fact_receipts',[]),'messages':[i.message for i in issues]};results['performance'].append(row)
 print(name,expected,len(issues),[(r['status'],r['formula']) for r in row['receipts']])
for name,header in [('expenditure_headers',['项目','本年支出合计','基本支出','项目支出']),('amount_headers',['项目','金额','金额','金额'])]:
 cells=[header,['甲','100','20.50','79.50'],['乙','200','80.50','119.50'],['合计','300','101.00','199.00']]
 rows=[{'page':1,'cells':c} for c in cells];text='\n'.join(' '.join(c) for c in cells);roles,codes=d1._learn_column_roles(rows)
 result=d1._reconcile_row_alignment(rows,[text],[1]);results['d1'].append({'case':name,'rows':rows,'source_text':text,'roles':roles,'codes':sorted(codes),'alignment':result})
 print('D1',name,roles,result)
(OUT/'bounded_controls.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str))
