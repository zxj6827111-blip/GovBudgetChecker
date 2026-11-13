"""合法行标自身带横线时仍可通过；该横线不属于金额列。"""
from pathlib import Path
import json
OUT=Path(__file__).resolve().parents[1];p=OUT/'scripts/independent_boundary_controls.py'
source=p.read_text().split('\nresults=[test(')[0]
for old,new in [("p.insert_text((45,100),'All')","p.insert_text((45,100),'All -')"),("'row':anchor('All')","'row':anchor('All -')")]:
 assert source.count(old)==1;source=source.replace(old,new)
ns={'__file__':str(p),'__name__':'legal_row_label_fixture'};exec(compile(source,str(p),'exec'),ns)
r=ns['test']('legitimate_hyphen_row_label_positive',want='PASS')
(OUT/'receipts/legal_row_label.json').write_text(json.dumps([r],ensure_ascii=False,indent=2)+'\n')
print(json.dumps(r,ensure_ascii=False))
