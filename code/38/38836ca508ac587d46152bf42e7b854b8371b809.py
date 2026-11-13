from pathlib import Path
import collections, hashlib, json, sys
ROOT=Path(__file__).resolve().parents[1]
W=ROOT.parents[1]; R3=W/'outputs/unified_review_remediation_r3_20261106'
sys.path.insert(0,str(W))
from scripts import effect_acceptance_v3 as h
def dump(name,v): (ROOT/name).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
identity=h.read(R3/'source_identity.json')
dump('source_identity_check.json',{'head':identity['head'],'files':{k:{'expected':v,'actual':sha(W/k),'matches':sha(W/k)==v} for k,v in identity['files'].items()}})
snapshot={str(p.relative_to(R3)):sha(p) for p in R3.rglob('*') if p.is_file()}
dump('r3_material_seal_before.json',snapshot)
contexts=[]
for name in ['final_candidate','repeat4_final']:
    root=R3/name;meta=h.read(root/'meta.json')
    files=meta['code']['files']
    code_mismatch=[k for k,v in files.items() if not (W/k).is_file() or sha(W/k)!=v]
    rows=[]
    for s in meta['samples']:
        sid=s['sample_id'];p=root/'work'/sid/'uploads'/sid/'fact_context.json'
        fc=h.read(p)
        rows.append({'sample_id':sid,'pdf_sha256':s['sha256'],'path':str(p),'sha256':sha(p),'fact_count':len(fc['facts']),'top_keys':list(fc)})
    contexts.append({'collection':name,'sample_count':len(rows),'code_digest':meta['code']['digest'],'code_after':meta['code_after'],'code_mismatch':code_mismatch,'failed':meta['failed'],'code_root_writes':meta['code_root_writes'],'dotenv_isolation_ok':meta['dotenv_isolation_ok'],'profile':meta.get('profile'), 'rows':rows})
dump('collection_context_audit.json',contexts)
old=W/'outputs/unified_remediation_20261106';r2=W/'outputs/unified_review_remediation_20261106'
ind=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/unified_independent_review_r2_20261106')
evp=ind/'r05_independent_evidence_v2.json';ev=h.load_evidence(evp,'e9fda775588d43e256e0e7beea9220c8ec60c363f27b36c53fdc400ab3a76092')
summaries=[]
for name,base,cand,evidence in [('old_boundary_r05_proof',old/'final_d1',r2/'final_collection',ev),('r2_to_r3_regression',r2/'final_collection',R3/'final_candidate',{}),('current_from_original_unadjudicated',old/'final_d1',R3/'final_candidate',{})]:
    report=h.compare_collections(base,cand,R3/'kit_final',evidence);dump(name+'.json',report)
    summaries.append({'name':name,'overall':report['overall'],'problems':len(report['problems']),'pending':len(report['pending'])})
dump('effect_gate_summary.json',summaries)

import pymupdf as fitz
all_objects=[]
for name in ['original9_objects.json','r2_current35_objects.json']:
    dataset=h.read(R3/name);rows=[]
    for n,obj in enumerate(dataset['objects']):
        sid=obj.get('origin_group',obj.get('sample_id'));old_obj=obj.get('old');new=obj.get('new')
        rows.append({'index':n,'sample_id':sid,'key':obj['key'],'old_digest_verified':h.digest(old_obj)==obj['old_digest'],'new_digest_verified':h.digest(new)==obj['new_digest'],'direction':'add' if old_obj is None else 'remove' if new is None else 'change','title':(new or old_obj or {}).get('title'),'old':old_obj,'new':new})
    dump(name.replace('.json','_identity_audit.json'), [{k:v for k,v in r.items() if k not in ['old','new']} for r in rows]);all_objects+=rows
sids=sorted({o['sample_id'] for o in all_objects})
(ROOT/'source_pages').mkdir(exist_ok=True)
pdf_index=[]
for sid in sids:
    p=next((R3/'kit_final/pdfs'/sid).glob('*.pdf'))
    with fitz.open(p) as d:
        pages=[page.get_text() for page in d]
        (ROOT/'source_pages'/f'{sid}.txt').write_text('\n\n'.join(f'=== 物理页 {n} ===\n{t}' for n,t in enumerate(pages,1)))
        pdf_index.append({'sample_id':sid,'pdf':str(p),'sha256':sha(p),'pages':len(pages)})
dump('pdf_index.json',pdf_index)

def compact(v):
    if isinstance(v,dict):
        return {k:compact(x) for k,x in v.items() if k not in ['candidates','evidence','bbox','cell_observations']}
    if isinstance(v,list):return [compact(x) for x in v]
    return v
# 只做阅读索引，不据此生成事实池或批准。
with (ROOT/'object_reading_index.txt').open('w') as f:
    for o in all_objects:
        f.write(f"\n{o['sample_id']} {o['index']} {o['direction']} {o['title']}\n")
        x=o['new'] or o['old'] or {}
        f.write(json.dumps({k:x[k] for k in ['obligation_id','status','reason','detail','message'] if k in x},ensure_ascii=False)+'\n')
        missing=x.get('missing_operands') or x.get('unresolved_rules') or []
        f.write(json.dumps(compact(missing),ensure_ascii=False)+'\n')
print(json.dumps({'contexts':[(r['collection'],r['sample_count'],len(r['code_mismatch'])) for r in contexts],'gates':summaries,'objects':len(all_objects),'pdfs':len(sids)},ensure_ascii=False))
