from pathlib import Path
import collections,hashlib,json,subprocess,sys,types,xml.etree.ElementTree as ET
OUT=Path(__file__).resolve().parents[1]
C=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
OLD=OUT.parent/'independent_ir3_9631ea55_20261106';D=C/'outputs/ir3_gap01_fix_20261106';CAP=C/'outputs/ir3_residual_fix_20261106'
sys.path.insert(0,str(C))
from scripts import effect_acceptance_v3 as h
from scripts import effect_fact_sources as current
from scripts.verify_pending_review import code_fingerprint
def dump(n,v):(OUT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def git(*args):return subprocess.check_output(['git',*args],cwd=C)
def check_hashes(root,files):return [p for p,s in files.items() if not (root/p).is_file() or h.sha(root/p)!=s]
identity=h.read(D/'source_and_test_identity.json');head=git('rev-parse','HEAD').decode().strip()
manifest=h.read(D/'review_manifest.json')['files']
protected=h.read(D/'protected_input_hashes.json');integrity={}
for group in ['independent','prior_delivery']:
 root=Path(protected[group+'_root']);hashes={str(p.relative_to(root)):h.sha(p) for p in root.rglob('*') if p.is_file()}
 integrity[group]={'files':len(hashes),'changed':check_hashes(root,protected[group]),'added':sorted(hashes.keys()-protected[group].keys())}
oldman=h.read(OLD/'independent_manifest.json')['files']
commands=h.read(D/'check_commands.json')
testlogcheck=[{'log':r['log'],'matches':h.sha(D/r['log'])==r['log_sha256'],'exit_code':r['exit_code']} for r in commands]
xml=ET.parse(D/'full_suite.xml').getroot();cases=xml.findall('.//testcase');skips=[{'test':e.get('classname')+'::'+e.get('name'),'message':e.find('skipped').get('message'),'detail':e.find('skipped').text} for e in cases if e.find('skipped') is not None]
pending=[]
for name,r in h.read(D/'pending_registries.json').items():
 pending.append({'name':name,'source':r['source'],'sha256':r['sha256'],'matches':h.sha(Path(r['source']))==h.sha(Path(r['copy']))==r['sha256'],'approval':'UNAPPROVED'})
summary={'head':head,'working_tree':git('status','--porcelain=v1').decode(),'main_sha':git('rev-parse','main').decode().strip(),'changed_files':git('diff','--name-only',identity['base_sha'],head).decode().splitlines(),'patch_matches':git('diff','--binary',identity['base_sha'],head)==(D/'source_changes.patch').read_bytes(),'source_hash_mismatches':check_hashes(C,{**identity['changes'],**identity['unchanged_files']}),'delivery_manifest_entries':len(manifest),'delivery_manifest_mismatches':check_hashes(D,manifest),'protected':integrity,'previous_seal_mismatches':check_hashes(OLD,{r['path']:r['sha256'] for r in oldman}),'test_logs':testlogcheck,'executor_full_suite_audited_not_rerun':{'cases':len(cases),'passed':sum(not any(e.find(t) is not None for t in ['skipped','failure','error']) for e in cases),'skipped':len(skips),'failures':len(xml.findall('.//failure')),'errors':len(xml.findall('.//error')),'skip_list_matches':skips==h.read(D/'skipped196.json')['cases']},'pending_registries':pending}
dump('identity_and_integrity.json',summary)
dump('delivery_input_snapshot.json',{str(p.relative_to(D)):h.sha(p) for p in D.rglob('*') if p.is_file()})
expect={}
for n in ['effect_boundary_controls','extra_effect','neighbor_effect']:
 for r in h.read(OLD/f'receipts/{n}.json'):expect[r['case']]=r['expected']
modules={};sources={}
for label,rev in [('342_module','3424596ec9f3dc8d807c199cacd999c77b45cd29'),('963_module',identity['base_sha'])]:
 source=git('show',rev+':scripts/effect_fact_sources.py');mod=types.ModuleType('scripts.effect_fact_sources');exec(compile(source,rev,'exec'),mod.__dict__);modules[label]=mod;sources[label]={'commit':rev,'file_sha256':hashlib.sha256(source).hexdigest()}
modules['d71_module']=current;sources['d71_module']={'commit':head,'file_sha256':h.sha(C/'scripts/effect_fact_sources.py')}
replay=[]
try:
 for name,want in expect.items():
  folder=OLD/'control_cases'/name;evp=folder/'external-evidence.json';ev=h.load_evidence(evp,h.sha(evp));versions={}
  for label,module in modules.items():
   sys.modules['scripts.effect_fact_sources']=module
   report=h.compare_collections(folder/'before',folder/'after',folder/'kit',ev)
   versions[label]={'overall':report['overall'],'problems':report['problems'],'pending':report['pending']}
  replay.append({'case':name,'expected':want,'expectation_met':versions['d71_module']['overall']==want,'unchanged_original_directory':str(folder),'input_hashes':{str(p.relative_to(folder)):h.sha(p) for p in folder.rglob('*') if p.is_file()},'versions':versions})
finally:sys.modules['scripts.effect_fact_sources']=current
dump('same_original_inputs64.json',{'scope':'Same original files; only fact_source module exchanged in current offline verifier. Not historical full-service executions.','sources':sources,'cases':replay})
contexts=[];meta=h.read(CAP/'final_candidate/meta.json');previous=meta['code']['files'];now=code_fingerprint(C)['files']
delta={p:{'previous':previous.get(p),'current':now.get(p)} for p in now.keys()|previous.keys() if now.get(p)!=previous.get(p)}
for r in h.read(D/'analysis_unchanged.json')['contexts31']:
 p=Path(r['source']);fc=h.read(p);contexts.append({**r,'hash_matches':h.sha(p)==r['sha256'],'fact_count_matches':len(fc['facts'])==r['facts'],'context_keys':list(fc)})
dump('analysis_generation_check.json',{'new_analysis_run':False,'retained_generation':identity['base_sha'],'fingerprint_delta':delta,'profile':meta['profile'],'contexts':contexts})
original=C/'outputs/unified_remediation_20261106/final_d1';r2=C/'outputs/unified_review_remediation_20261106/final_collection';r3=C/'outputs/unified_review_remediation_r3_20261106/final_candidate'
proof=OUT.parent/'unified_independent_review_r2_20261106/r05_independent_evidence_v2.json';ev=h.load_evidence(proof,'e9fda775588d43e256e0e7beea9220c8ec60c363f27b36c53fdc400ab3a76092');gates=[]
for name,before,after,evidence in [('original_to_963_unapproved',original,CAP/'final_candidate',{}),('R05_original_binding_only',original,r2,ev),('regression_342_to_963',r3,CAP/'final_candidate',{}),('regression_R2_to_963',r2,CAP/'final_candidate',{})]:
 r=h.compare_collections(before,after,CAP/'kit_candidate',evidence);r['identity']={'gate_code':head,'before':str(before),'after':str(after),'after_meta_sha256':h.sha(after/'meta.json'),'new_analysis':False};dump(name+'.json',r);gates.append({'name':name,'overall':r['overall'],'refused':len(r['problems']),'pending':len(r['pending'])})
dump('gate_summary.json',gates)
dump('execution_note.json',{'initial_summary_helper_error':'AttributeError at final print loop: synthetic_scope_v2.json is a dict, not list. Four subprocesses completed successfully, independent_commands.json and all receipts saved before error; no original controls changed.','unexpected_candidate_execution_errors':[]})
print(json.dumps({'code':{k:summary[k] for k in ['head','changed_files','patch_matches','source_hash_mismatches']},'delivery_bad':summary['delivery_manifest_mismatches'],'protected':integrity,'full_suite_audited':summary['executor_full_suite_audited_not_rerun'],'replayed':len(replay),'replay_expectations_met':sum(r['expectation_met'] for r in replay),'fingerprint_delta':list(delta),'contexts':len(contexts),'gates':gates},ensure_ascii=False))
