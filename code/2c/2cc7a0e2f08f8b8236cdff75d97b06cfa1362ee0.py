from pathlib import Path
import datetime,hashlib,json,subprocess
OUT=Path(__file__).resolve().parents[1];C=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106');D=C/'outputs/ir3_gap01_fix_20261106'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(n,v):(OUT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
old=OUT.parent/'independent_ir3_9631ea55_20261106'
protections=read(D/'protected_input_hashes.json');protected={}
for name in ['independent','prior_delivery']:
 root=Path(protections[name+'_root']);now={str(p.relative_to(root)):sha(p) for p in root.rglob('*') if p.is_file()}
 protected[name]={'files':len(now),'changed':[k for k,v in protections[name].items() if now.get(k)!=v],'added':sorted(now.keys()-protections[name].keys())}
 assert not protected[name]['changed'] and not protected[name]['added']
snapshot=read(OUT/'delivery_input_snapshot.json');actual={str(p.relative_to(D)):sha(p) for p in D.rglob('*') if p.is_file()};assert snapshot==actual
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=C,text=True).strip();status=subprocess.check_output(['git','status','--porcelain=v1'],cwd=C,text=True)
assert head=='d71c9ad7eb671ff956b2fcf21d814a5e9c0cc640' and not status
scripts=read(OUT/'execution_setup.json')['scripts'];assert all(sha(OUT/'scripts'/n)==s for n,s in scripts.items())
control_files=['effect_boundary_controls','narrative_boundary_controls','extra_effect','extra_narrative','scope_v2_controls','neighbor_effect','neighbor_narrative']
controls=[]
for name in control_files:
 rows=read(OUT/f'receipts/{name}.json');controls.append({'name':name,'count':len(rows),'met':sum(x['expectation_met'] for x in rows)})
assert sum(r['count'] for r in controls)==95 and all(r['count']==r['met'] for r in controls)
extra=read(OUT/'receipts/masking_boundary.json')+read(OUT/'receipts/legal_row_label.json')
pair=read(OUT/'exact_same_pdf_masking_pair.json');assert pair['all_pdf_and_before_after_bytes_equal'] and pair['actual']=='PASS'
oldreplay=read(OUT/'same_original_inputs64.json')['cases'];assert len(oldreplay)==64 and all(r['expectation_met'] for r in oldreplay)
identity=read(OUT/'identity_and_integrity.json');generation=read(OUT/'analysis_generation_check.json')
assert all(r['hash_matches'] and r['fact_count_matches'] for r in generation['contexts']) and len(generation['contexts'])==31
assert all(r['matches'] for r in identity['test_logs']) and all(r['matches'] for r in identity['pending_registries'])
counts={}
for name,key in [('original9_147','objects'),('R2_current35','objects'),('scope31_UNAPPROVED','requests')]:counts[name]={'count':len(read(D/f'pending_references/{name}.json')[key]),'status':'UNAPPROVED'}
review={'candidate_sha':head,'base_sha':'9631ea55decc8e5b94531e483a5d907842be292a','verdict':'RETURN_FOR_REWORK','finding_count':1,'findings':[{'id':'IR3-GAP-02','priority':'P1','title':'Row-label masking can delete a numeric-column sign before full-token verification','file':str(C/'scripts/effect_fact_sources.py'),'lines':[64,70],'expected':'FAIL','actual':'PASS','mechanism':'A literal, source-valid All - row anchor reaches into the Total numeric column. Removing those glyphs strips the true negative sign, then falsely accepts +10 = 6 + 4.','evidence':'exact_same_pdf_masking_pair.json','same_input_old_module':'FAIL','same_input_candidate_module':'PASS','synthetic_boundary_control':True,'real_corpus_defect_not_claimed':True}],'old_IR3_GAP_01':{'same_original_inputs':64,'met':64,'corrected_old_mismatches':11,'false_pass_corrected':10,'false_fail_corrected':1},'unchanged_controls':controls,'new_masking_controls':{'count':len(extra),'met':sum(r['expectation_met'] for r in extra),'false_passes':sum(r['expected']=='FAIL' and r['actual']=='PASS' for r in extra),'exact_same_pdf_pair_is_repeat_evidence_not_an_extra_defect':True},'independent_pytest':{'passed':296,'skipped':0,'log':'focused296.log'},'executor_full_suite_audit_only':identity['executor_full_suite_audited_not_rerun'],'executor_mypy_ruff':'logs verified; not independently rerun','gate_summary':read(OUT/'gate_summary.json'),'pending_registries':counts,'analysis':{'new_capture':False,'retained_generation':generation['retained_generation'],'retained_contexts':31,'fingerprint_changed_files':sorted(generation['fingerprint_delta'])},'source_and_seal_integrity':{'candidate_status_clean':not status,'delivery_files_unchanged':len(actual),'protected':protected,'original_script_hashes_preserved':True},'new_real_approvals':0,'merged':False,'pushed':False,'services_started_or_switched':False,'excluded':['B1','original7 disputes','full budget acceptance','OCR','AI','real template chain','real Word','production readiness'],'runtime_evidence_reuse':'R2 native configuration and SHA 69884e9 only','helper_errors_documented':'execution_note.json'}
dump('independent_review.json',review)
dump('final_integrity_check.json',{'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head':head,'candidate_status':status,'current_delivery_snapshot_matches':True,'protected':protected,'contexts31_hashes_match':True,'old_control_scripts_unchanged':True})
entries=[{'path':str(p.relative_to(OUT)),'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(OUT.rglob('*')) if p.is_file() and p.name not in ['independent_manifest.json','SHA256SUMS']]
dump('independent_manifest.json',{'candidate_sha':head,'verdict':review['verdict'],'files':entries})
(OUT/'SHA256SUMS').write_text(''.join(r['sha256']+'  '+r['path']+'\n' for r in entries)+sha(OUT/'independent_manifest.json')+'  independent_manifest.json\n')
assert all(sha(OUT/r['path'])==r['sha256'] for r in entries)
print(json.dumps({'verdict':review['verdict'],'findings':review['finding_count'],'original_controls':95,'additional_controls':len(extra),'additional_met':sum(r['expectation_met'] for r in extra),'old_same_input':64,'tests':296,'seal_files':len(entries),'protected':protected,'pending':counts},ensure_ascii=False))
