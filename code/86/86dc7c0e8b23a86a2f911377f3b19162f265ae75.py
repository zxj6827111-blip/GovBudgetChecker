from pathlib import Path
import collections,hashlib,json,subprocess,sys
OUT=Path(__file__).resolve().parents[1]
CAND=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
E=CAND/'outputs/ir3_residual_fix_20261106';PRIOR=CAND/'outputs/unified_review_remediation_r3_20261106'
sys.path.insert(0,str(CAND))
from scripts import effect_acceptance_v3 as h
def dump(n,v):(OUT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
identity=h.read(E/'source_and_test_identity.json')
files={**identity['changed_files'],**identity['R02_scope_files_unchanged']}
diff=subprocess.check_output(['git','diff',identity['base'],identity['head'],'--'],cwd=CAND)
code={'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=CAND,text=True).strip(),'changed_files':subprocess.check_output(['git','diff','--name-only',identity['base'],identity['head']],cwd=CAND,text=True).splitlines(),'sha_mismatches':[p for p,s in files.items() if sha(CAND/p)!=s],'source_patch_matches':diff==(E/'source_changes.patch').read_bytes(),'status':subprocess.check_output(['git','status','--porcelain=v1'],cwd=CAND,text=True)}
manifest=h.read(E/'review_manifest.json')
manifest_bad=[x['path'] for x in manifest['files'] if not Path(x['path']).is_file() or sha(Path(x['path']))!=x['sha256']]
oldreview=CAND/'outputs/independent_r3_review_20261106';oldmanifest=h.read(oldreview/'independent_manifest.json')
old_bad=[x['path'] for x in oldmanifest['files'] if not (oldreview/x['path']).is_file() or sha(oldreview/x['path'])!=x['sha256']]
test_logs=[{'log':x['log'],'sha_matches':sha(E/x['log'])==x['sha256'],'code_commit':x['code_commit'],'executor_exit_code':x['exit_code']} for x in h.read(E/'check_commands.json')]
pending=[]
for name,x in h.read(E/'pending_registries.json').items():
    pending.append({'name':name,'copy_matches_source':sha(Path(x['source']))==sha(Path(x['copy']))==x['sha256'],'status':x['status']})
dump('identity_and_delivery_integrity.json',{'code':code,'manifest_entries':len(manifest['files']),'manifest_mismatches':manifest_bad,'old_independent_manifest_entries':len(oldmanifest['files']),'old_independent_manifest_mismatches':old_bad,'test_logs':test_logs,'pending_registries':pending})
snapshot={str(p.relative_to(E)):sha(p) for p in E.rglob('*') if p.is_file()};dump('candidate_delivery_hashes.json',snapshot)
keys=['org_name','org_scope','fiscal_year','table_code','indicator','fund_scope','period','budget_stage','subject_code','subject_name','row_order','raw_value','normalized_value','unit','recognition_status']
contexts=[];projections=[]
for name in ['final_candidate','repeat4_final']:
    root=E/name;meta=h.read(root/'meta.json');rows=[]
    for s in meta['samples']:
        sid=s['sample_id'];p=root/'work'/sid/'uploads'/sid/'fact_context.json';fc=h.read(p)
        pdf=next((E/'kit_candidate/pdfs'/sid).glob('*.pdf'))
        rows.append({'sample_id':sid,'fact_context':str(p),'context_sha256':sha(p),'fact_count':len(fc['facts']),'top_level_keys':list(fc),'original_pdf_sha_matches':sha(pdf)==s['sha256']})
        if name=='final_candidate':
            old=PRIOR/'final_candidate/work'/sid/'uploads'/sid/'fact_context.json'
            project=lambda path:collections.Counter(h.canonical({k:f.get(k) for k in keys}) for f in h.read(path)['facts'])
            projections.append({'sample_id':sid,'baseline_context':str(old),'candidate_context':str(p),'baseline_sha256':sha(old),'candidate_sha256':sha(p),'projection_equal':project(old)==project(p)})
    contexts.append({'collection':name,'samples':rows,'sample_count':len(rows),'failed':meta['failed'],'code_root_writes':meta['code_root_writes'],'dotenv_isolation_ok':meta['dotenv_isolation_ok'],'code_digest':meta['code']['digest'],'code_after':meta['code_after'],'code_file_mismatches':[k for k,v in meta['code']['files'].items() if not (CAND/k).is_file() or sha(CAND/k)!=v],'profile':meta.get('profile')})
dump('complete_contexts31.json',contexts);dump('money_projection27_independent.json',projections)
a=h.load_collection(E/'final_candidate');b=h.load_collection(E/'repeat4_final');repeat=[]
for sid,row in b.items():
    receipts=lambda x:collections.Counter(h.canonical(h.stable(v)) for v in x['receipts'])
    repeat.append({'sample_id':sid,'receipt_identity_and_value_equal':receipts(a[sid])==receipts(row),'four_layers_equal':all(collections.Counter(h.issue_digest(x) for x in a[sid]['finding_layers'][layer])==collections.Counter(h.issue_digest(x) for x in row['finding_layers'][layer]) for layer in ['formal','manual_review','tips','candidate'])})
dump('repeat4_independent.json',repeat)
original=CAND/'outputs/unified_remediation_20261106/final_d1';r2=CAND/'outputs/unified_review_remediation_20261106/final_collection'
proof=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/unified_independent_review_r2_20261106/r05_independent_evidence_v2.json');ev=h.load_evidence(proof,'e9fda775588d43e256e0e7beea9220c8ec60c363f27b36c53fdc400ab3a76092')
gates=[]
for name,before,after,evidence in [('original_to_candidate_unapproved',original,E/'final_candidate',{}),('old_binding_R05_only',original,r2,ev),('regression_342_to_candidate',PRIOR/'final_candidate',E/'final_candidate',{}),('regression_R2_to_candidate',r2,E/'final_candidate',{})]:
    r=h.compare_collections(before,after,E/'kit_candidate',evidence);dump(name+'.json',r)
    gates.append({'name':name,'overall':r['overall'],'refused_conditions':len(r['problems']),'pending_objects':len(r['pending'])})
dump('gate_summary.json',gates)
print(json.dumps({'identity':code,'manifest_mismatches':manifest_bad,'old_manifest_mismatches':old_bad,'pending':pending,'contexts':[(x['collection'],x['sample_count'],x['code_file_mismatches']) for x in contexts],'projection27':all(r['projection_equal'] for r in projections),'repeat4':repeat,'gates':gates},ensure_ascii=False))
