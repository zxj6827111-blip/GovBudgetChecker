import sys,copy,hashlib
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from src.services.pdf_parser import PDFParser
from scripts import d1_table_reconciliation as d1
pdf=Path(__file__).resolve().parents[1]/'fixtures/no_code_multilane_source.pdf'
digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
a=PDFParser(None).build_parse_artifact(str(pdf))
with patch.object(d1.PDFParser,'build_parse_artifact',return_value=a):normal=d1.reconcile_auto(str(pdf))
bad=copy.deepcopy(a);positions=set()
for f in list(bad.table_fragments)+list(bad.physical_fragments):
 for ri,row in enumerate(f.rows):
  if len(row)<4 or row[2] not in ['本年支出A','本年支出B']:continue
  value='200' if row[2]=='本年支出A' else '100';row[3]=value;positions.add((f.fragment_uid,ri,3))
  for c in f.cells:
   if c.row_idx==ri and c.col_idx==3:c.raw_text=value;c.numeric_value=Decimal(value)
assert len(positions)==2
with patch.object(d1.PDFParser,'build_parse_artifact',return_value=bad):corrupted=d1.reconcile_auto(str(pdf))
assert hashlib.sha256(pdf.read_bytes()).hexdigest()==digest
print('normal',normal['pass'],normal['gap'],'corrupted',corrupted['pass'],corrupted['gap'])
assert normal['instances']==1 and normal['pass']==1 and normal['gap']==0
assert corrupted['instances']==1 and corrupted['gap']==1,'无编码双栏表的错位金额必须GAP，不能误学金额为编码后零比较PASS'
