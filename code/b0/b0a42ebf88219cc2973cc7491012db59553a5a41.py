"""按物理来源及指标逐格比较；金额不参与匹配身份。"""
from pathlib import Path
import argparse,json
ap=argparse.ArgumentParser();ap.add_argument('before',type=Path);ap.add_argument('after',type=Path);ap.add_argument('output',type=Path);args=ap.parse_args()
fields=['raw_value','normalized_value','unit','org_name','org_scope','fiscal_year','period','fund_scope','budget_stage','subject_code','subject_name','indicator','table_code','instance_uid','row_order','recognition_status','classification_level','parent_subject_code','hierarchy_path']
ref_fields=['file_hash','physical_page','source_cell_id','source_block_id','quote','bbox','source_method']
def key(f):
 refs=f['extra_dims']['evidence_refs']
 return (tuple(sorted((r['file_hash'],r['physical_page'],r['source_cell_id']) for r in refs)),f['indicator'])
def project(f):
 return {**{k:f.get(k) for k in fields},'evidence_refs':[{k:r.get(k) for k in ref_fields} for r in f['extra_dims']['evidence_refs']]}
records=[]
for p in sorted(args.before.glob('*/documents/*/facts.json')):
 rel=p.relative_to(args.before);q=args.after/rel
 if not q.exists(): raise RuntimeError(f'incomplete replay: {rel}')
 before=json.loads(p.read_text());after=json.loads(q.read_text())
 a={key(f):project(f) for f in before};b={key(f):project(f) for f in after}
 assert len(a)==len(before) and len(b)==len(after),'ambiguous physical projection'
 diffs=[]
 for k in sorted(set(a)|set(b),key=str):
  if a.get(k)!=b.get(k):diffs.append({'identity':k,'before':a.get(k),'after':b.get(k)})
 records.append({'document':str(rel.parent),'before_count':len(before),'after_count':len(after),'differences':diffs})
result={'scope':'fixed_originals_regression_not_new_blind_acceptance','documents':len(records),'before_facts':sum(r['before_count'] for r in records),'after_facts':sum(r['after_count'] for r in records),'changed_cells':sum(len(r['differences']) for r in records),'comparison_fields':fields+['physical_evidence'],'records':records}
args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='records'},ensure_ascii=False))
assert result['documents']==34 and result['before_facts']==result['after_facts']==24726 and result['changed_cells']==0
