from pathlib import Path
import collections, hashlib, json, subprocess,sys
ROOT=Path(__file__).resolve().parents[1];W=ROOT.parents[1];R3=W/'outputs/unified_review_remediation_r3_20261106'
sys.path.insert(0,str(W))
from scripts import effect_acceptance_v3 as h
def dump(n,v):(ROOT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
base=W/'outputs/unified_remediation_20261106';r2=W/'outputs/unified_review_remediation_20261106'
registries=[]
for fn,before,after,layer_set in [('original9_objects.json',base/'baseline_main',base/'final_d1',['manual_review']),('r2_current35_objects.json',base/'final_d1',r2/'final_collection',['candidate','tips','manual_review'])]:
    registered=h.read(R3/fn)['objects'];a=h.load_collection(before);b=h.load_collection(after)
    sids={o.get('sample_id',o.get('origin_group')) for o in registered};actual=[]
    for sid in sorted(sids):
        for layer in layer_set:
            entries,invalid=h.layer_changes(a[sid]['finding_layers'][layer],b[sid]['finding_layers'][layer])
            assert not invalid
            for k,old,new in entries:actual.append((sid,layer,tuple(k),h.digest(old),h.digest(new)))
    expected=[(o.get('sample_id',o.get('origin_group')),o['layer'],tuple(o['key']),o['old_digest'],o['new_digest']) for o in registered]
    registries.append({'registry':fn,'objects':len(expected),'exact_source_registry_matches':collections.Counter(actual)==collections.Counter(expected),'independently_approved_objects':0})
dump('original_registry_fidelity.json',registries)

all_final=h.load_collection(R3/'final_candidate');repeat=h.load_collection(R3/'repeat4_final');repeat_checks=[]
money_keys=['org_name','org_scope','fiscal_year','table_code','indicator','fund_scope','period','budget_stage','subject_code','subject_name','row_order','raw_value','normalized_value','unit','recognition_status']
for sid,r in repeat.items():
    a=all_final[sid]
    receipts=lambda v:collections.Counter(h.canonical(h.stable(x)) for x in v['receipts'])
    fc=lambda root:h.read(R3/root/'work'/sid/'uploads'/sid/'fact_context.json')['facts']
    monetary=lambda root:collections.Counter(h.canonical({k:r.get(k) for k in money_keys}) for r in fc(root))
    repeat_checks.append({'sample_id':sid,'receipts_unchanged':receipts(a)==receipts(r),'layers_unchanged':all(collections.Counter(h.issue_digest(x) for x in a['finding_layers'][layer])==collections.Counter(h.issue_digest(x) for x in r['finding_layers'][layer]) for layer in ['formal','manual_review','tips','candidate']),'monetary_projection_unchanged':monetary('final_candidate')==monetary('repeat4_final')})
dump('repeat4_independent.json',repeat_checks)
projection=[]
for sid in all_final:
    origin=base/('cover_r2_d1' if sid=='SP-ORIG-07' else 'final_d1')/'work'/sid/'uploads'/sid/'fact_context.json'
    current=R3/'final_candidate/work'/sid/'uploads'/sid/'fact_context.json'
    values=lambda p:collections.Counter(h.canonical({k:r.get(k) for k in money_keys}) for r in h.read(p)['facts'])
    projection.append({'sample_id':sid,'original':str(origin),'original_sha256':sha(origin),'candidate_sha256':sha(current),'same_projection':values(origin)==values(current),'limit':'固定投影回归，不是独立PDF全量真值，也不补写R2上下文'})
dump('monetary_projection27_independent.json',projection)

record=h.read(ROOT/'r3_material_seal_before.json')
changed=[k for k,v in record.items() if not (R3/k).is_file() or sha(R3/k)!=v]
added=[str(p.relative_to(R3)) for p in R3.rglob('*') if p.is_file() and str(p.relative_to(R3)) not in record]
old_sources=h.read(R3/'review_source_hashes.json')
old_source_bad=[k for k,v in old_sources.items() if not Path(k).is_file() or sha(Path(k))!=v]
manifest=h.read(R3/'review_manifest.json')
manifest_bad=[x['path'] for x in manifest['files'] if not Path(x['path']).is_file() or sha(Path(x['path']))!=x['sha256']]
patch=subprocess.check_output(['git','diff','69884e9c1d7fee91e530b952b7e5b1935802e987','3424596ec9f3dc8d807c199cacd999c77b45cd29','--'],cwd=W)
dump('seal_integrity.json',{'r3_files_hashed':len(record),'r3_changed':changed,'r3_added':added,'previous_review_files':len(old_sources),'previous_review_hash_mismatches':old_source_bad,'delivery_manifest_entries':len(manifest['files']),'delivery_manifest_mismatches':manifest_bad,'source_patch_exact_git_diff':patch==(R3/'source_changes.patch').read_bytes(),'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=W,text=True).strip(),'tracked_worktree_status':subprocess.check_output(['git','status','--porcelain=v1'],cwd=W,text=True)})
print(json.dumps({'registries':registries,'repeat':repeat_checks,'projection27':all(p['same_projection'] for p in projection),'r3_changed':changed,'previous_review_mismatches':old_source_bad,'manifest_bad':manifest_bad,'patch_exact':patch==(R3/'source_changes.patch').read_bytes()},ensure_ascii=False))
