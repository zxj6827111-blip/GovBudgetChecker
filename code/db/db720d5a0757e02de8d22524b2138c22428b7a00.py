import sys,json
from pathlib import Path
from unittest.mock import patch
from copy import deepcopy
from types import SimpleNamespace
sys.path.insert(0,str(Path.cwd()))
from src.schemas.parse_artifact import ParseArtifact
from scripts import d1_table_reconciliation as d1
from src.engine.native_final_disclosure import check_performance_counts
from src.engine.rules_v33 import Rule
OUT=Path('/private/tmp/gbc-independent-20261110')
artifact=ParseArtifact.from_dict(json.loads((OUT/'6000a11c_artifact.json').read_text()))
p=str(next(Path('uploads').glob('6000a11c*/*.pdf')))
with patch.object(d1.PDFParser,'build_parse_artifact',return_value=artifact):base=d1.reconcile_auto(p)
mut=deepcopy(artifact);table=next(t for t in mut.table_fragments if t.table_code=='FIN_02_income')
r1,r2,col=2,3,2
changed={'row_a':r1,'row_b':r2,'column':col,'before':[table.rows[r1][col],table.rows[r2][col]]}
table.rows[r1][col],table.rows[r2][col]=table.rows[r2][col],table.rows[r1][col]
changed['after']=[table.rows[r1][col],table.rows[r2][col]]
for c in table.cells:
 if c.row_idx in (r1,r2) and c.col_idx==col:
  c.raw_text=table.rows[c.row_idx][col]
  c.numeric_value=__import__('decimal').Decimal(c.raw_text)
assert changed
with patch.object(d1.PDFParser,'build_parse_artifact',return_value=mut):bad=d1.reconcile_auto(p)
class R(Rule):code='INDEPENDENT-PERFORMANCE';severity='error'
results={'d1_original':{k:base[k] for k in ['instances','pass','gap']},'d1_wrong_row':{k:bad[k] for k in ['instances','pass','gap']},'d1_injected_change':changed,'performance':[]}
for body in ['绩效自评的2025年度项目53个，其中，绩效评级为“优”的项目50个。','绩效自评的2025年度项目53个，全部项目评级结果如下：绩效评级为“优”的项目50个，其余评级均为0个。']:
 doc=SimpleNamespace(page_texts=['甲局\n2025年度部门决算\n十、预算绩效管理情况\n'+body],report_kind='final',fiscal_year=2025,organization_name='甲局')
 issues=check_performance_counts(R(),doc)
 results['performance'].append({'source':body,'issues':len(issues),'receipts':getattr(doc,'fact_receipts',[])})
(OUT/'additional_controls.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str))
print(json.dumps(results,ensure_ascii=False,indent=2,default=str))
