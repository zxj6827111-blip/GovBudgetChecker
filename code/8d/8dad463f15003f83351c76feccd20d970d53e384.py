from pathlib import Path
import collections,hashlib,json,subprocess,sys,types,xml.etree.ElementTree as ET
OUT=Path(__file__).resolve().parents[1];C=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
OLD64=OUT.parent/'independent_ir3_9631ea55_20261106';OLD8=OUT.parent/'independent_ir3_d71c9ad7_20261106';D=C/'outputs/ir3_gap02_fix_20261106';CAP=C/'outputs/ir3_residual_fix_20261106'
sys.path.insert(0,str(C))
from scripts import effect_acceptance_v3 as h
from scripts import effect_fact_sources as current
from scripts.verify_pending_review import code_fingerprint
def dump(n,v):(OUT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def git(*args):return subprocess.check_output(['git',*args],cwd=C)
def check_hashes(root,files):return [p for p,s in files.items() if not (root/p).is_file() or h.sha(root/p)!=s]
identity=h.read(D/'source_and_test_identity.json');head=git('rev-parse','HEAD').decode().strip();assert head==identity['candidate_sha']=='9e66caae33edadd7a59e0b651f3be10b31fc3029'
manifest=h.read(D/'review_manifest.json')['files'];protected=h.read(D/'protected_input_hashes.json');integrity={}
for name,r in protected.items():
 root=Path(r['root']);actual={str(p.relative_to(root)):h.sha(p) for p in root.rglob('*') if p.is_file()}
 integrity[name]={'files':len(actual),'changed':check_hashes(root,r['files']),'added':sorted(actual.keys()-r['files'].keys())}
testlogcheck=[{'log':r['log'],'matches':h.sha(D/r['log'])==r['log_sha256'],'exit_code':r['exit_code']} for r in h.read(D/'check_commands.json')]
xml=ET.parse(D/'full_suite.xml').getroot();cases=xml.findall('.//testcase');skips=[{'test':e.get('classname')+'::'+e.get('name'),'message':e.find('skipped').get('message'),'detail':e.find('skipped').text} for e in cases if e.find('skipped') is not None]
pending=[]
for name,r in h.read(D/'pending_registries.json').items():pending.append({'name':name,'source':r['source'],'sha256':r['sha256'],'matches':h.sha(Path(r['source']))==h.sha(Path(r['copy']))==r['sha256'],'count':r['count'],'approval':'UNAPPROVED'})
summary={'head':head,'working_tree':git('status','--porcelain=v1').decode(),'main_sha':git('rev-parse','main').decode().strip(),'changed_files':git('diff','--name-only',identity['base_sha'],head).decode().splitlines(),'patch_matches':git('diff','--binary',identity['base_sha'],head)==(D/'source_changes.patch').read_bytes(),'source_hash_mismatches':check_hashes(C,{**identity['changed_files'],**identity['unchanged_files']}),'delivery_manifest_entries':len(manifest),'delivery_manifest_mismatches':check_hashes(D,manifest),'protected':integrity,'test_logs':testlogcheck,'executor_full_suite_audited_not_rerun':{'cases':len(cases),'passed':sum(not any(e.find(t) is not None for t in ['skipped','failure','error']) for e in cases),'skipped':len(skips),'failures':len(xml.findall('.//failure')),'errors':len(xml.findall('.//error')),'skip_list_matches':skips==h.read(D/'skipped196.json')['cases']},'pending_registries':pending}
dump('identity_and_integrity.json',summary);dump('delivery_input_snapshot.json',{str(p.relative_to(D)):h.sha(p) for p in D.rglob('*') if p.is_file()})
modules={};sources={}
for label,rev in [('963_module','9631ea55decc8e5b94531e483a5d907842be292a'),('d71_module',identity['base_sha'])]:
 source=git('show',rev+':scripts/effect_fact_sources.py');mod=types.ModuleType('scripts.effect_fact_sources');exec(compile(source,rev,'exec'),mod.__dict__);modules[label]=mod;sources[label]={'commit':rev,'file_sha256':hashlib.sha256(source).hexdigest()}
modules['9e66_module']=current;sources['9e66_module']={'commit':head,'file_sha256':h.sha(C/'scripts/effect_fact_sources.py')}
def replay(rows):
 results=[]
 try:
  for name,folder,want in rows:
   evp=folder/'external-evidence.json';ev=h.load_evidence(evp,h.sha(evp));versions={}
   for label,module in modules.items():
    sys.modules['scripts.effect_fact_sources']=module;r=h.compare_collections(folder/'before',folder/'after',folder/'kit',ev);versions[label]={'overall':r['overall'],'problems':r['problems'],'pending':r['pending']}
   results.append({'case':name,'expected':want,'expectation_met':versions['9e66_module']['overall']==want,'unchanged_original_directory':str(folder),'input_hashes':{str(p.relative_to(folder)):h.sha(p) for p in folder.rglob('*') if p.is_file()},'versions':versions})
 finally:sys.modules['scripts.effect_fact_sources']=current
 return results
old64=[]
for n in ['effect_boundary_controls','extra_effect','neighbor_effect']:
 old64.extend((r['case'],OLD64/'control_cases'/r['case'],r['expected']) for r in h.read(OLD64/f'receipts/{n}.json'))
a=replay(old64)
b=replay([(r['case'],OLD8/'masking_cases'/r['case'],r['expected']) for r in h.read(OLD8/'receipts/masking_boundary.json')]+[('legitimate_hyphen_row_label_positive',OLD8/'control_cases/legitimate_hyphen_row_label_positive','PASS'),('exact_same_pdf_row_mask',OLD8/'masking_cases/exact_same_pdf_row_mask','FAIL')])
for name,rows in [('same_original_inputs64.json',a),('same_original_masking_inputs8.json',b)]:dump(name,{'scope':'Read-only replay of original sealed inputs; current offline verifier with only fact_source module exchanged; no historical full-service run.','sources':sources,'cases':rows})
contexts=[];meta=h.read(CAP/'final_candidate/meta.json');previous=meta['code']['files'];now=code_fingerprint(C)['files'];delta={p:{'previous':previous.get(p),'current':now.get(p)} for p in now.keys()|previous.keys() if now.get(p)!=previous.get(p)}
for r in h.read(D/'analysis_unchanged.json')['contexts31']:
 p=Path(r['source']);fc=h.read(p);contexts.append({**r,'hash_matches':h.sha(p)==r['sha256'],'fact_count_matches':len(fc['facts'])==r['facts'],'context_keys':list(fc)})
dump('analysis_generation_check.json',{'new_analysis_run':False,'retained_generation':'9631ea55decc8e5b94531e483a5d907842be292a','fingerprint_delta':delta,'profile':meta['profile'],'contexts':contexts})
original=C/'outputs/unified_remediation_20261106/final_d1';r2=C/'outputs/unified_review_remediation_20261106/final_collection';r3=C/'outputs/unified_review_remediation_r3_20261106/final_candidate';proof=OUT.parent/'unified_independent_review_r2_20261106/r05_independent_evidence_v2.json'
ev=h.load_evidence(proof,'e9fda775588d43e256e0e7beea9220c8ec60c363f27b36c53fdc400ab3a76092');gates=[]
for name,before,after,evidence in [('original_to_963_unapproved',original,CAP/'final_candidate',{}),('R05_original_binding_only',original,r2,ev),('regression_342_to_963',r3,CAP/'final_candidate',{}),('regression_R2_to_963',r2,CAP/'final_candidate',{})]:
 r=h.compare_collections(before,after,CAP/'kit_candidate',evidence);r['identity']={'gate_code':head,'before':str(before),'after':str(after),'after_meta_sha256':h.sha(after/'meta.json'),'new_analysis':False};dump(name+'.json',r);gates.append({'name':name,'overall':r['overall'],'refused':len(r['problems']),'pending':len(r['pending'])})
dump('gate_summary.json',gates)
print(json.dumps({'code':{k:summary[k] for k in ['head','changed_files','patch_matches','source_hash_mismatches']},'delivery_bad':summary['delivery_manifest_mismatches'],'protected':integrity,'full_suite_audited':summary['executor_full_suite_audited_not_rerun'],'same_original64':[len(a),sum(r['expectation_met'] for r in a)],'same_original_masking8':[len(b),sum(r['expectation_met'] for r in b)],'fingerprint_delta':list(delta),'contexts':len(contexts),'gates':gates},ensure_ascii=False))
