from pathlib import Path
import collections,hashlib,json,subprocess
OUT=Path(__file__).resolve().parents[1];CAND=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106');E=CAND/'outputs/ir3_residual_fix_20261106'
def read(n):return json.loads((OUT/n).read_text())
def dump(n,x):(OUT/n).write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
snapshot=read('candidate_delivery_hashes.json');changed=[p for p,s in snapshot.items() if not (E/p).is_file() or sha(E/p)!=s];added=[str(p.relative_to(E)) for p in E.rglob('*') if p.is_file() and str(p.relative_to(E)) not in snapshot]
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=CAND,text=True).strip();assert head=='9631ea55decc8e5b94531e483a5d907842be292a'
status=subprocess.check_output(['git','status','--porcelain=v1'],cwd=CAND,text=True)
seal={'candidate_head':head,'tracked_worktree_status':status,'delivery_snapshot_files':len(snapshot),'changed':changed,'added':added}
dump('final_seal_check.json',seal)
fields=['org_name','org_scope','fiscal_year','table_code','indicator','fund_scope','period','budget_stage','subject_code','subject_name','row_order','raw_value','normalized_value','unit','recognition_status']
repeat=[]
for row in read('repeat4_independent.json'):
 sid=row['sample_id'];p=E/'repeat4_final/work'/sid/'uploads'/sid/'fact_context.json';q=E/'final_candidate/work'/sid/'uploads'/sid/'fact_context.json'
 project=lambda p:collections.Counter(json.dumps({k:f.get(k) for k in fields},sort_keys=True,ensure_ascii=False) for f in json.loads(p.read_text())['facts'])
 repeat.append({**row,'monetary_projection_equal':project(p)==project(q),'repeat_fact_context_sha256':sha(p),'final_fact_context_sha256':sha(q)})
dump('repeat4_independent_complete.json',repeat)
controls={}
for name in ['effect_boundary_controls','narrative_boundary_controls','extra_effect','extra_narrative','scope_v2_controls','neighbor_effect','neighbor_narrative']:
 rows=read('receipts/'+name+'.json');controls[name]={'cases':len(rows),'expectations_met':sum(r['expectation_met'] for r in rows),'mismatches':[r['case'] for r in rows if not r['expectation_met']]}
report={'version':'independent-9631ea55-20261106-v1','candidate_head':head,'base':'3424596ec9f3dc8d807c199cacd999c77b45cd29','verdict':'RETURN_FOR_REWORK','findings':[{'id':'IR3-GAP-01','priority':'P1','file':'scripts/effect_fact_sources.py','line':35,'end_line':39,'title':'字形间距被错误当作完整金额/单位的词元边界','affected_residuals':['IR3-F02','IR3-F03'],'false_pass_examples':['space_extended_unit_1','space_negative_3','space_parentheses_3','space_exponent_3'],'false_reject_positive':'spaced_signed_complete_positive','new_regression_confirmed_by_same_input_module_differential':True,'differential_limit':'同一当前核验器/输入，仅替换fact_source模块；不是完整旧版本链路','evidence':['gap_geometry.json','fact_source_differential.json','receipts/neighbor_effect.json','renders/gap_counterexamples.png']}],'residuals':{'IR3-F01':'PASS_REQUESTED_BOUNDARIES_AND_KNOWN_NATIVE_CORPUS_ONLY','IR3-F02':'FAIL_SPACED_MAGNITUDE_UNIT','IR3-F03':'FAIL_SPACED_NOTATION_AND_SIGN_REGRESSION','R-02_and_scope_v2':'UNCHANGED_AND_CONTROL_EXPECTATIONS_MET'},'independent_controls':controls,'independent_focused_tests':{'passed':258,'log':'focused258.log'},'full_suite':{'executor_passed':5333,'executor_skipped':196,'independent_rerun':False,'executor_log_digest_verified':True},'complete_contexts':{'final_candidate':27,'repeat4_final':4,'own_fresh_collection_created':False},'gates':read('gate_summary.json'),'repeat4':repeat,'monetary_projection27_equal':all(x['projection_equal'] for x in read('money_projection27_independent.json')),'pending':{'original9_objects':147,'r2_objects':35,'scope31_requests':'UNAPPROVED','new_approvals':0,'copies_hash_match_originals':True},'runtime':{'web_or_db_started':False,'new_live_acceptance':False,'prior_live_scope':'69884e9 native only'},'excluded':['B1','original7 disputed','all budgets','OCR','AI','real template chain','real Word','production readiness'],'merge_push_or_entry_switch':False,'integrity':seal}
dump('independent_review.json',report)
manifest=[]
for p in sorted(OUT.rglob('*')):
 if p.is_file() and p.name not in ['independent_manifest.json','SHA256SUMS']:
  manifest.append({'path':str(p.relative_to(OUT)),'sha256':sha(p),'bytes':p.stat().st_size})
dump('independent_manifest.json',{'candidate_head':head,'files':manifest})
(OUT/'SHA256SUMS').write_text(''.join(r['sha256']+'  '+r['path']+'\n' for r in manifest))
bad=[r['path'] for r in manifest if sha(OUT/r['path'])!=r['sha256']]
assert not bad and not changed and not added and not status
assert all(r['receipt_identity_and_value_equal'] and r['four_layers_equal'] and r['monetary_projection_equal'] for r in repeat)
print(json.dumps({'verdict':report['verdict'],'findings':len(report['findings']),'controls':controls,'delivery_snapshot_files':len(snapshot),'candidate_delivery_unchanged':not changed and not added,'output_manifest_files':len(manifest),'output_manifest_mismatches':bad},ensure_ascii=False))
