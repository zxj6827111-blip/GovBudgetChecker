from pathlib import Path
import sys,json,hashlib,subprocess,types,shutil,datetime,importlib.metadata,re
w=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106');sys.path.insert(0,str(w))
o=w/'outputs/ir3_gap02_fix_20261106';main=Path('/Volumes/workSSD/software development/GovBudgetChecker');src=main/'outputs/independent_ir3_d71c9ad7_20261106';oldsrc=main/'outputs/independent_ir3_9631ea55_20261106'
from scripts import effect_acceptance_v3 as h
from scripts import effect_fact_sources as current
from scripts.verify_pending_review import code_fingerprint
base='d71c9ad7eb671ff956b2fcf21d814a5e9c0cc640';head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=w,text=True).strip()
def dump(n,v):(o/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def tree(p):return {str(x.relative_to(p)):h.sha(x) for x in p.rglob('*') if x.is_file()}
mods={};identities={}
for label,rev in [('963_module','9631ea55decc8e5b94531e483a5d907842be292a'),('d71_module',base)]:
 code=subprocess.check_output(['git','show',rev+':scripts/effect_fact_sources.py'],cwd=w,text=True);m=types.ModuleType('scripts.effect_fact_sources');exec(compile(code,rev+':effect_fact_sources.py','exec'),m.__dict__);mods[label]=m;identities[label]={'sha':rev,'module_sha256':hashlib.sha256(code.encode()).hexdigest()}
mods['candidate_module']=current;identities['candidate_module']={'sha':head,'module_sha256':h.sha(w/'scripts/effect_fact_sources.py')}
inputs=[]
for receipt in ['effect_boundary_controls','extra_effect','neighbor_effect']:
 for r in h.read(oldsrc/f'receipts/{receipt}.json'):inputs.append((r['case'],oldsrc/'control_cases'/r['case'],r['expected'],'sealed_inputs64'))
assert len(inputs)==64
for r in h.read(src/'receipts/masking_boundary.json'):inputs.append((r['case'],src/'masking_cases'/r['case'],r['expected'],'sealed_masking_inputs'))
inputs.append(('legitimate_hyphen_row_label_positive',src/'control_cases/legitimate_hyphen_row_label_positive','PASS','sealed_masking_inputs'))
inputs.append(('exact_same_pdf_row_mask',src/'masking_cases/exact_same_pdf_row_mask','FAIL','sealed_masking_inputs'))
rows=[]
for name,folder,want,group in inputs:
 target=o/group/name;assert not target.exists();shutil.copytree(folder,target)
 files=tree(folder);assert tree(target)==files
 path=target/'external-evidence.json';ev=h.load_evidence(path,h.sha(path));versions={}
 for label,m in mods.items():
  sys.modules['scripts.effect_fact_sources']=m;r=h.compare_collections(target/'before',target/'after',target/'kit',ev)
  versions[label]=r
 rows.append({'case':name,'expected':want,'expectation_met':versions['candidate_module']['overall']==want,'source':str(folder),'copy':str(target),'file_hashes':files,'external_input_sha256':h.sha(path),'pdf_sha256':h.sha(target/'kit/pdfs/I/original.pdf'),'versions':versions})
sys.modules['scripts.effect_fact_sources']=current
assert all(r['expectation_met'] for r in rows)
dump('same_original_inputs64.json',{'candidate_sha':head,'module_identity':identities,'scope':'identical sealed PDFs and before/after/external inputs; in-process fact_source module differential only, not historical service replay','cases':rows[:64]})
dump('same_original_masking_inputs.json',{'candidate_sha':head,'module_identity':identities,'scope':'7 original controls + exact same-PDF pair; observed old reports in copied cases remain historical observations, never approvals','cases':rows[64:]})
# 精确对照：PDF/前后集合完全不变，仅原独立已封存 row 与其摘要有差异。
a=o/'sealed_masking_inputs/cropped_negative_exact_row';b=o/'sealed_masking_inputs/exact_same_pdf_row_mask'
unchanged={name:tree(a/name)==tree(b/name) for name in ['kit','before','after']};assert all(unchanged.values())
def differences(a,b,path=''):
 if type(a)!=type(b):return [path]
 if isinstance(a,dict):return [p for k in a.keys()|b.keys() for p in differences(a.get(k),b.get(k),path+'/'+str(k))]
 if isinstance(a,list):
  assert len(a)==len(b)
  return [p for i,(x,y) in enumerate(zip(a,b,strict=True)) for p in differences(x,y,path+'/'+str(i))]
 return [] if a==b else [path]
delta=differences(h.read(a/'external-evidence.json'),h.read(b/'external-evidence.json'))
allowed=[r'/samples/0/facts/\d+/source_cell/row/(quote|bbox/\d+)',r'/samples/0/relation_contracts/0/operand_identities/[^/]+/source_cell_digest']
assert delta and all(any(re.fullmatch(p,d) for p in allowed) for d in delta),delta
pair_rows={r['case']:r for r in rows};pair={label:{'normal_All':pair_rows['cropped_negative_exact_row']['versions'][label]['overall'],'expanded_All_minus':pair_rows['exact_same_pdf_row_mask']['versions'][label]['overall']} for label in mods}
assert pair['d71_module']=={'normal_All':'FAIL','expanded_All_minus':'PASS'}
assert pair['candidate_module']=={'normal_All':'FAIL','expanded_All_minus':'FAIL'}
dump('exact_same_pdf_masking_pair_replay.json',{'candidate_sha':head,'module_identity':identities,'same_pdf_and_before_after':unchanged,'pdf_sha256':h.sha(a/'kit/pdfs/I/original.pdf'),'original_external_differences_only':delta,'normal_external_sha256':h.sha(a/'external-evidence.json'),'expanded_external_sha256':h.sha(b/'external-evidence.json'),'outcomes':pair,'original_independent_pair':{'path':str(src/'exact_same_pdf_masking_pair.json'),'sha256':h.sha(src/'exact_same_pdf_masking_pair.json')},'sources':'same_original_masking_inputs.json','status':'CANDIDATE_FIX_PENDING_INDEPENDENT_REVIEW'})
# 当前离线门禁只读取原代际工件，绝不重新绑定授权或采集 SHA。
prior=w/'outputs/ir3_residual_fix_20261106';r3=w/'outputs/unified_review_remediation_r3_20261106';r2=w/'outputs/unified_review_remediation_20261106';old=w/'outputs/unified_remediation_20261106'
evp=main/'outputs/unified_independent_review_r2_20261106/r05_independent_evidence_v2.json';ev=h.load_evidence(evp,'e9fda775588d43e256e0e7beea9220c8ec60c363f27b36c53fdc400ab3a76092');gates={}
for name,b,c,e in [('original_to_963_collection_unapproved',old/'final_d1',prior/'final_candidate',{}),('old_binding_R05_only',old/'final_d1',r2/'final_collection',ev),('regression_342_to_963_collection',r3/'final_candidate',prior/'final_candidate',{}),('regression_R2_to_963_collection',r2/'final_collection',prior/'final_candidate',{})]:
 r=h.compare_collections(b,c,prior/'kit_candidate',e);r['execution_identity']={'gate_code_sha':head,'analysis_recollected':False,'before':str(b),'after':str(c),'after_meta_sha256':h.sha(c/'meta.json')};dump(name+'.json',r);gates[name]={'overall':r['overall'],'refused':len(r['problems']),'pending':len(r['pending'])}
assert gates['original_to_963_collection_unapproved']=={'overall':'FAIL','refused':127,'pending':35}
assert gates['old_binding_R05_only']=={'overall':'FAIL','refused':119,'pending':35}
dump('gate_summary.json',{'candidate_sha':head,'gates':gates,'overall_acceptance':'NOT_SIGNED','new_analysis':False,'new_real_approval':False})
refs=o/'pending_references';refs.mkdir();registry={}
for name,p,key,count in [('original9_147',r3/'original9_objects.json','objects',147),('R2_current35',r3/'r2_current35_objects.json','objects',35),('scope31_UNAPPROVED',r3/'final_analysis/scope31_v2_requests_UNAPPROVED.json','requests',31)]:
 shutil.copyfile(p,refs/(name+'.json'));assert h.sha(p)==h.sha(refs/(name+'.json'));assert len(h.read(p)[key])==count
 registry[name]={'source':str(p),'sha256':h.sha(p),'copy':str(refs/(name+'.json')),'count':count,'status':'UNAPPROVED_RETAINED'}
dump('pending_registries.json',registry)
changes=subprocess.check_output(['git','diff','--name-only',base,head],cwd=w,text=True).splitlines();assert set(changes)=={'scripts/effect_fact_sources.py','tests/test_effect_row_label_boundary.py'}
unchanged={}
for p in ['src/engine/narrative_zero_evidence.py','scripts/effect_acceptance_v3.py','scripts/verify_pending_review.py','docs/unified_remediation_20261106/scope_review_v2.schema.json','tests/test_effect_fact_source_geometry.py','tests/test_effect_acceptance_v3.py','tests/remediation/test_narrative_zero_evidence.py','tests/test_independent_scope_supplement.py','tests/test_verify_pending_review.py','tests/test_unified_receipt_repairs.py']:
 assert (w/p).read_bytes()==subprocess.check_output(['git','show',base+':'+p],cwd=w);unchanged[p]=h.sha(w/p)
meta=h.read(prior/'final_candidate/meta.json');oldfiles=meta['code']['files'];newfiles=code_fingerprint(w)['files'];fpdelta={p:{'old':oldfiles.get(p),'current':newfiles.get(p)} for p in oldfiles.keys()|newfiles.keys() if oldfiles.get(p)!=newfiles.get(p)}
assert set(fpdelta)=={'scripts/effect_fact_sources.py','tests/test_effect_fact_source_geometry.py','tests/test_effect_row_label_boundary.py'}
contexts=[]
for group in ['final_candidate','repeat4_final']:
 for p in sorted((prior/group/'work').glob('*/uploads/*/fact_context.json')):
  data=h.read(p);contexts.append({'source':str(p),'sha256':h.sha(p),'facts':len(data['facts']),'collection':group})
assert len(contexts)==31
dump('analysis_unchanged.json',{'candidate_sha':head,'analysis_generation_retained':'9631ea55decc8e5b94531e483a5d907842be292a','analysis_new_capture':False,'fingerprint_delta_vs_retained_generation':fpdelta,'retained_native_profile':meta['profile'],'meta_source':str(prior/'final_candidate/meta.json'),'meta_sha256':h.sha(prior/'final_candidate/meta.json'),'contexts31':contexts,'scope':'retained 9631 collections, not a new SHA collection; no full independent factual-truth or live acceptance claimed'})
copied={p.name:h.sha(p) for p in (o/'scripts').glob('*.py')};assert all(h.sha(src/'scripts'/p)==v for p,v in copied.items())
dump('source_and_test_identity.json',{'candidate_sha':head,'base_sha':base,'changed_files':{p:h.sha(w/p) for p in changes},'unchanged_files':unchanged,'copied_independent_scripts':copied,'test_versions':{p:importlib.metadata.version(p) for p in ['pytest','PyMuPDF','mypy','ruff']},'python_version':sys.version,'commands':'check_commands.json','legacy_control_module_label_note':'unmodified masking/exact scripts call the current imported module d71_module; new command SHA identifies the candidate; saved-input differential names all modules by exact SHA'})
(o/'source_changes.patch').write_bytes(subprocess.check_output(['git','diff','--binary',base,head],cwd=w))
protected=h.read(o/'protected_input_hashes.json');integrity={}
for name,record in protected.items():
 now=tree(Path(record['root']));assert now==record['files'],name
 integrity[name]={'root':record['root'],'files':len(now),'all_unchanged':True}
seal=h.read(src/'independent_manifest.json');assert all(h.sha(src/e['path'])==e['sha256'] for e in seal['files'])
for line in (src/'SHA256SUMS').read_text().splitlines():
 value,p=line.split('  ',1);assert h.sha(src/p)==value
dump('protected_input_integrity.json',{'roots':integrity,'current_independent_manifest_and_SHA256SUMS':'PASS'})
ports={}
for port in [3000,18743]:ports[str(port)]=bool(subprocess.run(['lsof','-nP',f'-iTCP:{port}','-sTCP:LISTEN','-t'],capture_output=True,text=True).stdout.strip())
dump('runtime_boundary.json',{'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'main_sha':subprocess.check_output(['git','rev-parse','main'],cwd=w,text=True).strip(),'listening_ports':ports,'web_or_db_started':False,'merge_performed':False,'push_performed':False,'daily_entry_switch_performed':False,'production_accepted':False})
print(json.dumps({'sha':head,'same_original_inputs':len(rows),'expected_met':True,'exact_pair':pair,'gates':gates,'protected':{k:v['files'] for k,v in integrity.items()},'retained_contexts':len(contexts)},ensure_ascii=False),flush=True)
