import sys
from types import SimpleNamespace
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from src.engine.native_final_disclosure import check_performance_counts
from src.engine.rules_v33 import Rule
class R(Rule):code='V33-250';severity='error'
s='绩效自评的2025年度项目53个，全部项目评级结果如下：绩效评级为“优”的项目50个，其余评级均为0个。部分项目评级资料已归档。'
d=SimpleNamespace(page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n'+s],report_kind='final',fiscal_year=2025,organization_name='甲局')
issues=check_performance_counts(R(),d);print('issues=',len(issues),'receipts=',d.fact_receipts)
assert len(issues)==1,'完整构成53≠50应报错，无关归档句不改变关系'
