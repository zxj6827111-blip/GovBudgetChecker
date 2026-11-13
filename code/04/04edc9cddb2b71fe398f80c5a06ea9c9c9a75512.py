import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from scripts import d1_table_reconciliation as d1
pdf=Path(__file__).resolve().parents[1]/'fixtures/integer_table_embedded_original.pdf'
results={}
for key,value in [('normal','100'),('corrupted','200')]:
 f=SimpleNamespace(fragment_uid='p1',page_number=1,rows=[['合计',value]],source_fragment_uids=[])
 i=SimpleNamespace(table_code='FIN_03_expenditure',title='支出决算表',page_start=1,page_end=1,fragment_uids=['p1'])
 a=SimpleNamespace(table_fragments=[f],physical_fragments=[f],logical_tables=[i])
 with patch.object(d1.PDFParser,'build_parse_artifact',return_value=a):results[key]=d1.reconcile_auto(str(pdf))
print({k:(r['pass'],r['gap']) for k,r in results.items()})
assert results['normal']['gap']==0
assert results['corrupted']['gap']==1,'清晰原格100与错误有效片段200必须GAP，不能零比较PASS'
