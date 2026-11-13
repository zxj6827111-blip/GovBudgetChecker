import os
import sys,json,copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from src.engine.native_final_disclosure import check_performance_counts
from src.engine.rules_v33 import Rule
from src.engine.rule_outcome import RuleOutcomeSignal
from scripts import d1_table_reconciliation as d1
from scripts.d4_real_link_acceptance import _verify_text_issue_block
out=Path(os.environ.get('CODEX_REVIEW_OUT',str(Path(__file__).resolve().parents[1]/'replay')));out.mkdir(parents=True,exist_ok=True)
class R(Rule):code='V33-250';severity='error'
base='绩效自评的2025年度项目53个，全部项目评级结果如下：绩效评级为“优”的项目50个，其余评级均为0个。'
rows=[]
for name,body in [
 ('base',base),
 ('archive_after',base+'部分项目评级资料已归档。'),
 ('archive_between_comma','绩效自评的2025年度项目53个，全部项目评级结果如下，部分项目评级资料已归档，绩效评级为“优”的项目50个，其余评级均为0个。'),
 ('archive_between_parentheses','绩效自评的2025年度项目53个，全部项目评级结果如下（部分项目评级资料已归档）：绩效评级为“优”的项目50个，其余评级均为0个。'),
 ('complete_declaration_sentence','绩效自评的2025年度项目53个，全部项目评级结果如下。绩效评级为“优”的项目50个，其余评级均为0个。')]:
 d=SimpleNamespace(page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n'+body],report_kind='final',fiscal_year=2025,organization_name='甲局')
 try:issues=check_performance_counts(R(),d);err=None
 except RuleOutcomeSignal as e:issues=getattr(e,'partial_issues',[]);err=str(e)
 except Exception as e:raise
 rows.append({'case':name,'text':body,'expected_count':1,'actual_count':len(issues),'error':err,'issues':[i.model_dump() if hasattr(i,'model_dump') else i.__dict__ for i in issues],'receipts':getattr(d,'fact_receipts',[])})
res={'performance':rows}
for name,original,merged in [('decimal','合计 100.00',[{'page':1,'cells':['合计','200.00']}]),('integer','合计 100',[{'page':1,'cells':['合计','200']}]),('coded_integer','201 一般公共服务支出 100',[{'page':1,'cells':['201','一般公共服务支出','200']}])]:
 res['d1_'+name]={'original':original,'merged':merged,'expected':'reject incorrect amount','actual':d1._reconcile_row_alignment(merged,[original],[1])}
# Real PDF original, mutated effective physical view: 100 -> 200. Only the parser return is injected.
p=Path(__file__).resolve().parents[1]/'fixtures/integer_table_embedded_original.pdf'
frag=SimpleNamespace(fragment_uid='p1',page_number=1,rows=[['合计','200']],source_fragment_uids=[])
inst=SimpleNamespace(table_code='FIN_03_expenditure',title='支出决算表',page_start=1,page_end=1,fragment_uids=['p1'])
a=SimpleNamespace(table_fragments=[frag],physical_fragments=[frag],logical_tables=[inst])
with patch.object(d1.PDFParser,'build_parse_artifact',return_value=a):res['d1_integer_auto']=d1.reconcile_auto(str(p))
# A separate exactness control: 22 is not page 2; an extended revision is not the expected revision.
member={'id':'A','page':2,'evidence':[{'page':2,'quote':'13.24'}],'label':'原文摘录'}
rev='rev-20261111T010000-12345678'
for name,block in [('normal',f'ISSUE_ID|A|\n页码：2\n分析版本：{rev}\n原文摘录（第2页）：13.24'),('wrong_page',f'ISSUE_ID|A|\n页码：22\n分析版本：{rev}\n原文摘录（第2页）：13.24'),('wrong_revision',f'ISSUE_ID|A|\n页码：2\n分析版本：{rev}-old\n原文摘录（第2页）：13.24')]:res['export_'+name]={'text':block,'actual':_verify_text_issue_block(block,member,rev)}
(out/'new_controls.json').write_text(json.dumps(res,ensure_ascii=False,indent=2,default=str))
for r in rows:print(r['case'],r['actual_count'],[(x.get('status'),x.get('formula')) for x in r['receipts']],r['error'])
for k,v in res.items():
 if k.startswith('d1_'):print(k,{x:v[x] for x in ['pass','gap']} if 'pass' in v else v['actual'])
 if k.startswith('export_'):print(k,v['actual'])
