import importlib.util,copy,json,os
from pathlib import Path
R=Path(__file__).parent;D=Path(os.environ.get('B1_PACKAGE', str(R.parent/'B1_review_package')))
def load(n,p):
 s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
dec=load('dec_real_r4',R/'delivery_tools/decimal_checks_v2.py')
ledger=load('ledger_roles_r4',R/'delivery_tools/build_final_ledger.py')
ann={}
for p in (D/'annotations_revised').glob('*/annotation.json'):
 a=json.loads(p.read_text());ann[a['sample_id']]=a
rows=json.loads((D/'decimal_spec_v4.json').read_text())['rows']
base=next(x for x in rows if x['item']=='2140101行政运行')

def test_actual_b1_wrong_subject_code_rejected():
 row=copy.deepcopy(base);row['operands'][0]['row']='2140106 财政拨款收入'
 assert not dec.evaluate(row,ann)['source_ok']

def test_actual_b1_wrong_income_column_rejected():
 row=copy.deepcopy(base);row['operands'][0]['column']='事业收入'
 assert not dec.evaluate(row,ann)['source_ok']

def test_role_targets_not_fixed_without_required_evidence():
 rows=json.loads((R/'control_eval/target_ledger.json').read_text())['rows']
 ids={'MISS|GS-2024-06|C|GS-W01|1','MISS|GS-2024-05|C|GS-S03|0','MISS|GS-2023-05|C|GS-N03|0'}
 selected=[x for x in rows if x['target_id'] in ids];assert len(selected)==3
 for row in selected:
  assert ledger.decide(row)[0]=='fixed'
  for a in row['anchor_entries']:
   if a.get('role')!='correct_contrast':a['covered']=False;a['f_items']=[]
  assert ledger.decide(row)[0]=='strict_partial'
