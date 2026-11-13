import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path.cwd()))
from src.engine.native_final_disclosure import check_performance_counts
from src.engine.rule_outcome import RuleOutcomeSignal
from src.engine.rules_v33 import Rule
class R(Rule):code='V33-250';severity='error'
partial='绩效自评的2025年度项目53个，其中，绩效评级为“优”的项目50个。'
results=[]
for name,body,count,status,op in [('partial',partial,0,'pass','>='),('materials',partial+'部分项目评级资料如下：项目自评表、评审意见。',0,'pass','>='),('complete','绩效自评的2025年度项目53个，全部项目评级结果如下：绩效评级为“优”的项目50个，其余评级均为0个。',1,'fail',' = ')]:
 d=SimpleNamespace(page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n'+body],report_kind='final',fiscal_year=2025,organization_name='甲局')
 try:issues=check_performance_counts(R(),d);signal=None
 except RuleOutcomeSignal as e:issues=e.partial_issues;signal=str(e)
 receipts=getattr(d,'fact_receipts',[])
 ok=(signal is None and len(issues)==count and len(receipts)==1 and receipts[0]['status']==status and op in receipts[0]['formula'])
 results.append(ok);print(name,len(issues),[(r['status'],r['formula']) for r in receipts],ok)
assert all(results),'资料标题不得把合法部分披露改为完整构成；必须保留已计算pass回执，不能跳过'
