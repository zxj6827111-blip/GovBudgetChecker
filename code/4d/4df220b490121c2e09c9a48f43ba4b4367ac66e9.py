from pathlib import Path
import json,copy,importlib.util
R=Path(__file__).parent;P=R.parents[1]/'outputs/unified_closeout_round5_20261104/B1_review_package'
s=importlib.util.spec_from_file_location('dec_r5_dim',R/'delivery_tools/decimal_checks_v2.py');d=importlib.util.module_from_spec(s);s.loader.exec_module(d)
anns={}
for p in (P/'annotations_revised').glob('*/annotation.json'):
 a=json.loads(p.read_text());anns[a['sample_id']]=a
rows=json.loads((P/'decimal_spec_v4.json').read_text())['rows'];results=[]
for title,column in [('2210201合计=基本+项目','基本支出预算数'),('2140101行政运行','非财政拨款收入')]:
 base=next(x for x in rows if x['item']==title);row=copy.deepcopy(base);row['operands'][0]['column']=column
 results.append({'item':title,'before_source':base['operands'][0],'after_source':row['operands'][0],'before':d.evaluate(base,anns),'after':d.evaluate(row,anns)})
(R/'column_dimension_probes.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps(results,ensure_ascii=False,indent=2))
