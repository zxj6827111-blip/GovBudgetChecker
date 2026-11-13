import os
import sys,copy,json
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from src.services.pdf_parser import PDFParser
from scripts import d1_table_reconciliation as d1
out=Path('/private/tmp/gbc-r8-results-20261111')
out.mkdir(parents=True,exist_ok=True)
pdf=next(Path('/Volumes/workSSD/software development/GovBudgetChecker/uploads').glob('6000a11c*/*.pdf'))
a=PDFParser(None).build_parse_artifact(str(pdf))
results={}
with patch.object(d1.PDFParser,'build_parse_artifact',return_value=a):results['normal']=d1.reconcile_auto(str(pdf))
logical=copy.deepcopy(a)
t=next(t for t in logical.table_fragments if t.table_code=='FIN_02_income')
physical_before=json.dumps([f.to_dict() for f in logical.physical_fragments],sort_keys=True,default=str)
t.rows=copy.deepcopy(t.rows);t.cells=copy.deepcopy(t.cells)
for r1,r2,col in [(2,3,2)]:
 before=[t.rows[r1][col],t.rows[r2][col]];t.rows[r1][col],t.rows[r2][col]=t.rows[r2][col],t.rows[r1][col]
 for c in t.cells:
  if c.row_idx in [r1,r2] and c.col_idx==col:c.raw_text=t.rows[c.row_idx][col];c.numeric_value=Decimal(c.raw_text)
with patch.object(d1.PDFParser,'build_parse_artifact',return_value=logical):results['logical_total_class_swap']=d1.reconcile_auto(str(pdf))
results['logical_change']={'before':before,'after':[t.rows[r1][col],t.rows[r2][col]],'physical_fragments_unchanged':physical_before==json.dumps([f.to_dict() for f in logical.physical_fragments],sort_keys=True,default=str)}
physical=copy.deepcopy(a);changes=[]
for t in physical.physical_fragments:
 for ri,row in enumerate(t.rows):
  norms=[''.join(str(v or '').split()).replace(',','') for v in row]
  if '14107.46' in norms and '6215.76' in norms:
   ca,cb=norms.index('14107.46'),norms.index('6215.76')
   before=[row[ca],row[cb]];row[ca],row[cb]=row[cb],row[ca]
   for c in t.cells:
    if c.row_idx==ri and c.col_idx in [ca,cb]:
     c.raw_text=row[c.col_idx];c.numeric_value=Decimal(c.raw_text.replace(',',''))
   changes.append({'page':t.page_number,'columns':[ca,cb],'before':before,'after':[row[ca],row[cb]]})
assert changes,'needs genuine FIN03 total amount pair'
with patch.object(d1.PDFParser,'build_parse_artifact',return_value=physical):results['physical_basic_project_swap']=d1.reconcile_auto(str(pdf))
results['physical_changes']=changes
(out/'d1_actual_injections.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str))
for k,v in results.items():print(k,{x:v[x] for x in ['instances','pass','gap']} if isinstance(v,dict) and 'instances' in v else v)
(out/'current_artifact.json').write_text(json.dumps(a.to_dict(),ensure_ascii=False,default=str))
