from pathlib import Path
import sys,json,collections
BASE=Path(__file__).resolve().parent;sys.path.insert(0,str(BASE/'validation-runtime-v28'))
from src.engine.fact_context import FactContext
p=BASE/'complete-income-replay-v28/fact_context.lossless.json'
c=FactContext.from_dict(json.loads(p.read_text()))
reasons=collections.Counter();rows={}
for f in c.facts:
 qual=f.extra_dims.get('ocr_provenance',{}).get('qualification',{})
 if qual.get('status')=='qualified':continue
 ref=f.extra_dims['evidence_refs'][0];page=ref['physical_page']
 if not 7<=page<=14:continue
 reasons.update(qual.get('reasons',[]))
 key=(page,f.row_order)
 row=rows.setdefault(key,{'page':page,'row':f.row_order,'subject_code':f.subject_code,'subject_name':f.subject_name,'indicators':[],'qualification_reasons':qual.get('reasons',[]),'source_identity_fragments':f.extra_dims.get('ocr_provenance',{}).get('identity_fragments',[]),'sources':[]})
 row['indicators'].append(f.indicator);row['sources'].append(ref['source_cell_id'])
summary={'reason_counts':dict(reasons),'rows':list(rows.values()),'not_a_human_truth_label':True}
(BASE/'complete-income-replay-v28/unqualified-business-rows.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(dict(reasons))
for row in rows.values():print({k:v for k,v in row.items() if k not in {'source_identity_fragments','sources'}})
