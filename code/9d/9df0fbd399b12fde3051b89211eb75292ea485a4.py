from pathlib import Path
import json,hashlib,collections
BASE=Path(__file__).resolve().parent
OLD=BASE.parent/'pdf-input-v2-20261018-stitch/frozen34'
NEW=BASE/'frozen34-v25'
FIELDS=['raw_value','normalized_value','unit','org_name','org_scope','fiscal_year','period','fund_scope','budget_stage','subject_code','subject_name','parent_subject_code','hierarchy_path','indicator','table_code','instance_uid','row_order','recognition_status']
EXTRA=['report_year','amount_year','as_of','row_role','row_identity_sources','evidence_refs','native_text_sources']
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def key(f):
    refs=f['extra_dims'].get('evidence_refs',[])
    return canonical([sorted([[r.get('file_hash'),r.get('physical_page'),r.get('source_cell_id'),r.get('bbox')] for r in refs],key=canonical),f['indicator']])
def projection(f):return {**{k:f.get(k) for k in FIELDS},'extra':{k:f['extra_dims'].get(k) for k in EXTRA}}
records=[]
for p in sorted(OLD.glob('*/documents/*/fact_context.json')):
    q=NEW/p.relative_to(OLD)
    a,b=json.loads(p.read_text()),json.loads(q.read_text())
    before,after=collections.defaultdict(list),collections.defaultdict(list)
    for f in a['facts']:before[key(f)].append(projection(f))
    for f in b['facts']:after[key(f)].append(projection(f))
    changes=[]
    for k in sorted(set(before)|set(after)):
        aa,bb=sorted(before[k],key=canonical),sorted(after[k],key=canonical)
        if aa!=bb:changes.append({'source_key':k,'before':aa,'after':bb})
    context_changes={k:[a.get(k),b.get(k)] for k in ['org_name','fiscal_year','report_kind','budget_stage'] if a.get(k)!=b.get(k)}
    records.append({'document':p.parent.name,'before_facts':len(a['facts']),'after_facts':len(b['facts']),'changed_cells':len(changes),'changes':changes,'context_changes':context_changes})
result={'documents':len(records),'before_facts':sum(r['before_facts'] for r in records),'after_facts':sum(r['after_facts'] for r in records),'changed_cells':sum(r['changed_cells'] for r in records),'changed_contexts':sum(bool(r['context_changes']) for r in records),'records':records,'scope':'fixed_development_regression_not_independent_new_original_acceptance'}
assert len(records)==34
Path(BASE/'fixed34-v25-comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print({k:v for k,v in result.items() if k!='records'})
