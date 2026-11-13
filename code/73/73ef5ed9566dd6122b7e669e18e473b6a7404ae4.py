import copy,importlib.util,json,os
from pathlib import Path
import pytest
R=Path(__file__).parent;P=Path(os.environ.get('B1_PACKAGE',str(R.parent/'B1_review_package')))
s=importlib.util.spec_from_file_location('decimal_col_boundary',R/'delivery_tools/decimal_checks_v2.py');d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
anns={}
for p in (P/'annotations_revised').glob('*/annotation.json'):
 a=json.loads(p.read_text());anns[a['sample_id']]=a
spec=json.loads((P/'decimal_spec_v4.json').read_text())['rows']
@pytest.mark.parametrize('item,column',[
 ('2210201合计=基本+项目','基本支出预算数'),
 ('2140101行政运行','非财政拨款收入'),
])
def test_contradictory_column_is_rejected(item,column):
 row=copy.deepcopy(next(r for r in spec if r['item']==item))
 row['operands'][0]['column']=column
 verdict=d.evaluate(row,anns)
 assert verdict['source_ok'] is False
 assert verdict['result']=='PENDING'
@pytest.mark.parametrize('item',['2210201合计=基本+项目','2140101行政运行'])
def test_original_column_remains_valid(item):
 row=next(r for r in spec if r['item']==item)
 verdict=d.evaluate(row,anns)
 assert verdict['source_ok'] is True
 assert verdict['result']=='FAIL'
