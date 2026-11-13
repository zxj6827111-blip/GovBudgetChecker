import os
import sys,json,copy
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path.cwd()))
from src.engine.native_final_disclosure import check_performance_counts
from src.engine.rules_v33 import Rule
from src.engine.rule_outcome import RuleOutcomeSignal
from scripts import d1_table_reconciliation as d1
out=Path(os.environ.get('CODEX_REVIEW_OUT',str(Path(__file__).resolve().parents[1]/'replay')));out.mkdir(parents=True,exist_ok=True)
class R(Rule):code='V33-250';severity='error'
partial='绩效自评的2025年度项目53个，其中，绩效评级为“优”的项目50个。'
complete='绩效自评的2025年度项目53个，全部项目评级结果如下：绩效评级为“优”的项目50个，其余评级均为0个。'
res={'performance':[]}
for name,s,expected in [('partial',partial,0),('partial_with_materials',partial+'部分项目评级资料如下：项目自评表、评审意见。',0),('partial_with_related_materials',partial+'相关评级材料如下：项目自评表、整改资料。',0),('complete',complete,1),('complete_archive_between','绩效自评的2025年度项目53个，全部项目评级结果如下，部分项目评级资料已归档，绩效评级为“优”的项目50个，其余评级均为0个。',1),('explicit_partial','绩效自评的2025年度项目53个，其中部分项目评级结果如下：绩效评级为“优”的项目50个。',0)]:
 d=SimpleNamespace(page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n'+s],report_kind='final',fiscal_year=2025,organization_name='甲局')
 try:issues=check_performance_counts(R(),d);signal=None
 except RuleOutcomeSignal as e:issues=e.partial_issues;signal=str(e)
 r={'case':name,'text':s,'expected_count':expected,'actual_count':len(issues),'signal':signal,'messages':[i.message for i in issues],'receipts':getattr(d,'fact_receipts',[])};res['performance'].append(r);print(name,expected,len(issues),[(x.get('status'),x.get('formula')) for x in r['receipts']])
for decimal in [False,True]:
 fmt=lambda n:str(n)+('.00' if decimal else '')
 original=[{'page':1,'cells':['合计',fmt(301)]},{'page':1,'cells':['301','工资福利支出',fmt(100)]},{'page':1,'cells':['302','商品和服务支出',fmt(201)]}]
 bad=copy.deepcopy(original);bad[0]['cells'][1]=fmt(302)
 text='\n'.join(' '.join(r['cells']) for r in original)
 for name,rows in [('normal',original),('corrupted',bad)]:
  key='d1_'+('decimal' if decimal else 'integer')+'_'+name
  res[key]={'original_text':text,'merged_rows':rows,'learned_codes':sorted(d1._codes_from_rows(rows)),'actual':d1._reconcile_row_alignment(rows,[text],[1]),'original_inventory':d1._parse_original_page(text,1,d1._codes_from_rows(rows))}
  print(key,res[key]['learned_codes'],res[key]['actual'])
(out/'bounded_controls.json').write_text(json.dumps(res,ensure_ascii=False,indent=2))
