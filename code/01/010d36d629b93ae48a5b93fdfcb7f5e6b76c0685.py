from pathlib import Path
import os,sys,json,hashlib,subprocess,types,shutil,datetime,importlib.metadata
w=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106');sys.path.insert(0,str(w))
out=w/'outputs/ir3_gap01_fix_20261106';src=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/independent_ir3_9631ea55_20261106')
from scripts import effect_acceptance_v3 as h
from scripts import effect_fact_sources as current
from scripts.verify_pending_review import code_fingerprint
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=w,text=True).strip();base='9631ea55decc8e5b94531e483a5d907842be292a'
def dump(name,v):(out/name).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
mods={};source_ids={}
for label,rev in [('342_module','3424596ec9f3dc8d807c199cacd999c77b45cd29'),('963_module',base)]:
 code=subprocess.check_output(['git','show',rev+':scripts/effect_fact_sources.py'],cwd=w,text=True)
 mod=types.ModuleType('scripts.effect_fact_sources');exec(compile(code,rev+':scripts/effect_fact_sources.py','exec'),mod.__dict__);mods[label]=mod
 source_ids[label]={'sha':rev,'file_sha256':hashlib.sha256(code.encode()).hexdigest()}
mods['candidate_module']=current;source_ids['candidate_module']={'sha':head,'file_sha256':h.sha(w/'scripts/effect_fact_sources.py')}
expected={}
for name in ['effect_boundary_controls','extra_effect','neighbor_effect']:
 for r in h.read(src/f'receipts/{name}.json'):expected[r['case']]=r['expected']
replayed=[]
for name,want in expected.items():
 folder=src/'control_cases'/name;evfile=folder/'external-evidence.json';ev=h.load_evidence(evfile,h.sha(evfile));versions={}
 for label,mod in mods.items():
  sys.modules['scripts.effect_fact_sources']=mod
  result=h.compare_collections(folder/'before',folder/'after',folder/'kit',ev)
  versions[label]={'overall':result['overall'],'problems':result['problems'],'pending':result['pending']}
 replayed.append({'case':name,'expected':want,'candidate_expectation_met':versions['candidate_module']['overall']==want,'original_case':str(folder),'external_input_sha256':h.sha(evfile),'pdf_sha256':h.sha(folder/'kit/pdfs/I/original.pdf'),'before_meta_sha256':h.sha(folder/'before/meta.json'),'after_meta_sha256':h.sha(folder/'after/meta.json'),'versions':versions})
sys.modules['scripts.effect_fact_sources']=current
assert all(r['candidate_expectation_met'] for r in replayed)
dump('same_input_module_differential.json',{'candidate_sha':head,'sources':source_ids,'scope':'same independent PDFs/before/after/external input; only fact_source module switched in-process; not a historical full-service rerun','cases':replayed})
# 当前门禁读取旧采集；不改 analysis binding，不冒充新代际采集，也不迁移批准。
prior=w/'outputs/ir3_residual_fix_20261106';r3=w/'outputs/unified_review_remediation_r3_20261106';r2=w/'outputs/unified_review_remediation_20261106';old=w/'outputs/unified_remediation_20261106'
evpath=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/unified_independent_review_r2_20261106/r05_independent_evidence_v2.json')
ev=h.load_evidence(evpath,'e9fda775588d43e256e0e7beea9220c8ec60c363f27b36c53fdc400ab3a76092')
gates={}
for name,b,c,e in [('original_to_963_collection_unapproved',old/'final_d1',prior/'final_candidate',{}),('old_binding_R05_only',old/'final_d1',r2/'final_collection',ev),('regression_342_to_963_collection',r3/'final_candidate',prior/'final_candidate',{}),('regression_R2_to_963_collection',r2/'final_collection',prior/'final_candidate',{})]:
 result=h.compare_collections(b,c,prior/'kit_candidate',e)
 result['execution_identity']={'gate_code_sha':head,'analysis_recollected':False,'before':str(b),'after':str(c),'after_meta_sha256':h.sha(c/'meta.json'),'note':'historical collection identities retained; current offline gate evaluation only'}
 dump(name+'.json',result)
 gates[name]={'overall':result['overall'],'refused':len(result['problems']),'pending':len(result['pending'])}
assert gates['original_to_963_collection_unapproved']=={'overall':'FAIL','refused':127,'pending':35}
assert gates['old_binding_R05_only']=={'overall':'FAIL','refused':119,'pending':35}
dump('gate_summary.json',{'candidate_sha':head,'gates':gates,'overall_acceptance':'NOT_SIGNED','new_analysis_created':False,'new_approval_created':False})
protected=h.read(out/'protected_input_hashes.json');integrity={}
for name in ['independent','prior_delivery']:
 root=Path(protected[name+'_root']);hashes={str(p.relative_to(root)):h.sha(p) for p in root.rglob('*') if p.is_file()}
 changed=[p for p,sha in protected[name].items() if hashes.get(p)!=sha];added=sorted(hashes.keys()-protected[name].keys())
 assert not changed and not added,(name,changed,added)
 integrity[name]={'files':len(hashes),'changed':changed,'added':added,'root':str(root)}
dump('protected_input_integrity.json',integrity)
refs=out/'pending_references';refs.mkdir(exist_ok=True);pending={}
for name,path in [('original9_147',r3/'original9_objects.json'),('R2_current35',r3/'r2_current35_objects.json'),('scope31_UNAPPROVED',r3/'final_analysis/scope31_v2_requests_UNAPPROVED.json')]:
 shutil.copyfile(path,refs/(name+'.json'));assert h.sha(path)==h.sha(refs/(name+'.json'))
 pending[name]={'source':str(path),'copy':str(refs/(name+'.json')),'sha256':h.sha(path),'status':'UNAPPROVED_RETAINED'}
dump('pending_registries.json',pending)
changes=subprocess.check_output(['git','diff','--name-only',base,head],cwd=w,text=True).splitlines()
assert set(changes)=={'scripts/effect_fact_sources.py','tests/test_effect_fact_source_geometry.py'}
unchanged={}
for path in ['src/engine/narrative_zero_evidence.py','scripts/effect_acceptance_v3.py','scripts/verify_pending_review.py','docs/unified_remediation_20261106/scope_review_v2.schema.json','tests/test_effect_acceptance_v3.py','tests/remediation/test_narrative_zero_evidence.py','tests/test_independent_scope_supplement.py','tests/test_verify_pending_review.py','tests/test_unified_receipt_repairs.py']:
 assert (w/path).read_bytes()==subprocess.check_output(['git','show',base+':'+path],cwd=w)
 unchanged[path]=h.sha(w/path)
meta=h.read(prior/'final_candidate/meta.json');current_files=code_fingerprint(w)['files'];previous_files=meta['code']['files']
fingerprint_delta={p:{'previous':previous_files.get(p),'current':current_files.get(p)} for p in current_files.keys()|previous_files.keys() if current_files.get(p)!=previous_files.get(p)}
assert set(fingerprint_delta)=={'scripts/effect_fact_sources.py','tests/test_effect_fact_source_geometry.py'},fingerprint_delta
contexts=[]
for group in ['final_candidate','repeat4_final']:
 for p in sorted((prior/group/'work').glob('*/uploads/*/fact_context.json')):
  data=h.read(p);assert 'facts' in data
  contexts.append({'source':str(p),'sha256':h.sha(p),'facts':len(data['facts']),'collection':group})
assert len(contexts)==31
sources={n:h.sha(src/'scripts'/n) for n in ['independent_boundary_controls.py','extra_controls.py','neighbor_controls.py']}
assert all(h.sha(out/'scripts'/n)==v for n,v in sources.items())
dump('analysis_unchanged.json',{'candidate_sha':head,'analysis_generation_retained':base,'new_analysis_run':False,'reason':'only offline effect_fact_sources changed; all analysis sources and config unchanged','fingerprint_file_delta':fingerprint_delta,'runtime_profile_retained':meta['profile'],'recorded_profile_source':str(prior/'final_candidate/meta.json'),'recorded_profile_source_sha256':h.sha(prior/'final_candidate/meta.json'),'contexts31':contexts,'scope':'these are retained captures, not freshly rerun pipeline or independently proved full fact truth'})
dump('source_and_test_identity.json',{'candidate_sha':head,'base_sha':base,'changes':{p:h.sha(w/p) for p in changes},'unchanged_files':unchanged,'test_packages':{p:importlib.metadata.version(p) for p in ['pytest','PyMuPDF','mypy','ruff']},'copied_control_scripts':sources,'commands':'check_commands.json'})
(out/'source_changes.patch').write_bytes(subprocess.check_output(['git','diff','--binary',base,head],cwd=w))
# 真实入口仅只读确认；不启动任何服务。
ports={}
for port in [3000,18743]:
 p=subprocess.run(['lsof','-nP',f'-iTCP:{port}','-sTCP:LISTEN','-t'],capture_output=True,text=True)
 ports[str(port)]=bool(p.stdout.strip())
dump('runtime_boundary.json',{'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'main_sha':subprocess.check_output(['git','rev-parse','main'],cwd=w,text=True).strip(),'listening_ports':ports,'web_or_db_started':False,'merge_performed':False,'push_performed':False,'daily_entry_switch_performed':False,'production_accepted':False})
print(json.dumps({'same_input_cases':len(replayed),'all_expected':True,'gate_summary':gates,'protected':{k:v['files'] for k,v in integrity.items()},'analysis_fingerprint_changed_files':list(fingerprint_delta),'retained_contexts':len(contexts)},ensure_ascii=False),flush=True)
