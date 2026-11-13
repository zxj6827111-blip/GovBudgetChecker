import copy,json,importlib.util
from pathlib import Path
R=Path(__file__).parent;D=R.parents[1]/'outputs/unified_closeout_round4_20261104/B1_review_package'
s=importlib.util.spec_from_file_location('dprobe',R/'delivery_tools/decimal_checks_v2.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
anns={}
for p in (D/'annotations_revised').glob('*/annotation.json'):
 a=json.loads(p.read_text());anns[a['sample_id']]=a
spec=json.loads((D/'decimal_spec_v4.json').read_text())
base=next(x for x in spec['rows'] if x['item']=='2140101行政运行')
results=[]
for name,field,value in [('wrong_subject_code','row','2140106 财政拨款收入'),('wrong_income_column','column','事业收入')]:
 row=copy.deepcopy(base);row['operands'][0][field]=value
 results.append({'name':name,'before':base['operands'][0],'after':row['operands'][0], 'baseline':m.evaluate(base,anns),'mutant':m.evaluate(row,anns)})
(R/'real_decimal_source_probes.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print(json.dumps(results,ensure_ascii=False,indent=2))
