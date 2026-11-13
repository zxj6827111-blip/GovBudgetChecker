from pathlib import Path
import datetime,hashlib,json,subprocess
OUT=Path(__file__).resolve().parents[1];C=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106');D=C/'outputs/ir3_gap02_fix_20261106'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(n,v):(OUT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
identity=read(OUT/'identity_and_integrity.json');setup=read(OUT/'execution_setup.json');head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=C,text=True).strip();status=subprocess.check_output(['git','status','--porcelain=v1'],cwd=C,text=True)
assert head==setup['sha']==identity['head']=='9e66caae33edadd7a59e0b651f3be10b31fc3029' and not status
protected={}
for name,r in read(D/'protected_input_hashes.json').items():
 root=Path(r['root']);now={str(p.relative_to(root)):sha(p) for p in root.rglob('*') if p.is_file()};bad=[p for p,s in r['files'].items() if now.get(p)!=s];added=sorted(now.keys()-r['files'].keys());assert not bad and not added
 protected[name]={'files':len(now),'changed':bad,'added':added}
snapshot=read(OUT/'delivery_input_snapshot.json');actual={str(p.relative_to(D)):sha(p) for p in D.rglob('*') if p.is_file()};assert snapshot==actual
assert all(sha(OUT/'scripts'/n)==s for n,s in setup['scripts'].items())
assert not identity['source_hash_mismatches'] and not identity['delivery_manifest_mismatches'] and identity['patch_matches']
assert all(r['matches'] for r in identity['test_logs']) and all(r['matches'] for r in identity['pending_registries'])
controls=[]
for name in ['effect_boundary_controls','narrative_boundary_controls','extra_effect','extra_narrative','scope_v2_controls','neighbor_effect','neighbor_narrative','masking_boundary','legal_row_label']:
 rows=read(OUT/f'receipts/{name}.json');controls.append({'name':name,'count':len(rows),'met':sum(x['expectation_met'] for x in rows)})
assert sum(r['count'] for r in controls)==102 and all(r['count']==r['met'] for r in controls)
old_pair=read(OUT/'exact_same_pdf_masking_pair.json');assert old_pair['all_pdf_and_before_after_bytes_equal'] and old_pair['actual']=='FAIL'
replays={}
for name,count in [('same_original_inputs64.json',64),('same_original_masking_inputs8.json',8)]:
 rows=read(OUT/name)['cases'];assert len(rows)==count and all(r['expectation_met'] for r in rows);replays[name]={'cases':count,'met':count}
extra=read(OUT/'receipts/native_line_controls.json');pair=read(OUT/'native_line_exact_pair.json')
assert len(extra)==5 and sum(r['expectation_met'] for r in extra)==4
assert pair['same_pdf_and_before_after_bytes'] and pair['joined_and_split_page_pixels_identical'] and pair['mask_actual']=='PASS' and pair['true_row_actual']=='FAIL'
assert extra[0]['versions']['9e66_module']['problems']==[] and extra[0]['versions']['9e66_module']['pending']==[]
assert all(not errors for f in extra[0]['source_checks'].values() for errors in f.values())
generation=read(OUT/'analysis_generation_check.json');assert len(generation['contexts'])==31 and all(r['hash_matches'] and r['fact_count_matches'] for r in generation['contexts'])
assert '317 passed' in (OUT/'focused317.log').read_text()
review={'candidate_sha':head,'base_sha':'d71c9ad7eb671ff956b2fcf21d814a5e9c0cc640','verdict':'RETURN_FOR_REWORK','open_defects':1,'new_defects_added':0,'findings':[{'id':'IR3-GAP-02','status':'PARTIALLY_FIXED_NOT_CLOSED','priority':'P1','file':str(C/'scripts/effect_fact_sources.py'),'primary_lines':[119,123],'related_lines':[103,104],'title':'Complete native PDF line exception still allows a numeric-cell sign to be masked as a row label','mechanism':'Header text x0=150 is treated as column boundary although the visible table boundary is x=130. Minus at x=141..147 is inside the numeric cell but a complete native text line All ... - is accepted as a row label, overriding the full-amount conflict and turning -10.00 into +10.00.','expected':'FAIL','actual':'PASS','synthetic_boundary_control':True,'real_corpus_new_defect_not_claimed':True,'evidence':['receipts/native_line_controls.json','native_line_exact_pair.json','renders/joined_line_cropped.png'],'equivalent_render_test':{'joined_line':'PASS','split_lines':'FAIL','full_page_pixels_identical':True,'pdf_file_bytes_identical':False},'identical_original_inputs_test':{'normal_row':'FAIL','expanded_row':'PASS','pdf_kit_before_after_bytes_identical':True}}],'unchanged_controls':controls,'old_exact_masking_pair_correctly_rejected':True,'sealed_input_replay':replays,'new_controls':{'count':5,'met':4,'false_pass':1,'exact_pair_is_additional_evidence_not_another_defect':True},'independent_tests':{'passed':317,'skipped':0,'log':'focused317.log'},'executor_full_suite_audit_only':identity['executor_full_suite_audited_not_rerun'],'executor_mypy_ruff':'log hashes checked; not independently rerun','gates':read(OUT/'gate_summary.json'),'pending_registries':identity['pending_registries'],'analysis':{'new_capture':False,'generation':generation['retained_generation'],'contexts':31,'fingerprint_changed_files':sorted(generation['fingerprint_delta'])},'integrity':{'protected':protected,'current_delivery_unchanged':len(actual),'candidate_clean':True,'copied_control_script_bytes_unchanged':True},'real_approvals_created':0,'merged':False,'pushed':False,'web_db_started_or_switched':False,'excluded':['B1','original7 disputes','full budget acceptance','OCR','AI','real template chain','real Word','production readiness'],'R2_live_evidence_boundary':'69884e9 explicit native config only'}
dump('independent_review.json',review)
dump('final_integrity_check.json',{'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head':head,'candidate_status':status,'current_delivery_snapshot_matches':True,'protected':protected,'contexts31_hashes_match':True,'original_control_scripts_unchanged':True})
entries=[{'path':str(p.relative_to(OUT)),'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(OUT.rglob('*')) if p.is_file() and p.name not in ['independent_manifest.json','SHA256SUMS']]
dump('independent_manifest.json',{'candidate_sha':head,'verdict':review['verdict'],'files':entries});(OUT/'SHA256SUMS').write_text(''.join(r['sha256']+'  '+r['path']+'\n' for r in entries)+sha(OUT/'independent_manifest.json')+'  independent_manifest.json\n')
assert all(sha(OUT/r['path'])==r['sha256'] for r in entries)
print(json.dumps({'verdict':review['verdict'],'IR3_GAP_02':'NOT_CLOSED','open_defects':1,'new_defects_added':0,'old_controls':102,'old_inputs':[64,8],'independent_tests':317,'new_controls':[5,4],'sealed_files':len(entries),'protected':protected},ensure_ascii=False))
