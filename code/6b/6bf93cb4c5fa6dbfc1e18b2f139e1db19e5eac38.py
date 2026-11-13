import sys,json
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from scripts.d1_table_reconciliation import _reconcile_row_alignment
p=Path(__file__).resolve().parents[1]/'evidence/d1_split_code_normal_control.json'
f=json.loads(p.read_text());r=_reconcile_row_alignment(f['rows'],[f['original']],[1]);print(r)
assert not r['row_mismatches'],'D1误拒绝：原件与逻辑行完全一致的拆码对照'
