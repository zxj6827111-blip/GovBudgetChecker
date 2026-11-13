from pathlib import Path
import collections,hashlib,json,sys
ROOT=Path(__file__).resolve().parents[1]
def read(n):return json.loads((ROOT/n).read_text())
def dump(n,v):(ROOT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
findings=[
 {'id':'IR3-F01','priority':'P1','residual':'R-01','title':'跨页/分号仍丢失未完成限定','file':'src/engine/narrative_zero_evidence.py','line':53,'cases':['qualifier_page_no_punctuation','qualifier_semicolon'],'expected':'unknown/no zero source','actual':'definite zero source'},
 {'id':'IR3-F02','priority':'P1','residual':'R-03/F04','title':'扩展中文数量级单位被裁成支持子串','file':'scripts/effect_fact_sources.py','line':14,'cases':['extra_unit_suffix_in_million','extra_unit_suffix_in_thousand_wan'],'expected':'FAIL','actual':'PASS'},
 {'id':'IR3-F03','priority':'P1','residual':'R-03/F03','title':'括号/指数金额可裁成普通十进制数通过','file':'scripts/effect_fact_sources.py','line':13,'cases':['extra_accounting_parentheses_crop','extra_scientific_mantissa_crop'],'expected':'FAIL','actual':'PASS'},
]
def control_summary(path):
 rows=read(path);return {'cases':len(rows),'met':sum(r['expectation_met'] for r in rows),'failed':[r['case'] for r in rows if not r['expectation_met']]}
groups=[]
for name in ['original9_objects','r2_current35_objects']:
 anchors=read(name+'_original_anchor_audit.json');missing=read(name+'_missing_claim_review.json');ident=read(name+'_identity_audit.json');dispositions=[]
 for a,m,i in zip(anchors,missing,ident,strict=True):
  assert a['index']==m['index']==i['index'] and i['old_digest_verified'] and i['new_digest_verified']
  x={'index':a['index'],'sample_id':a['sample_id'],'key':a['key'],'title':a['title'],'source_pdf_sha256':a['source']['sha256'],'source_pdf':a['source']['pdf'],'direction':i['direction'],'independent_original_anchor_checks':a['anchor_count'],'original_anchor_failures':len(a['anchor_failures']),'missing_claim_observations':m['summary'],'disposition':'NOT_APPROVED_PENDING_EXACT_SEMANTIC_ADJUDICATION','reason':'原件空白/引文观察与整体语义、全部聚合组成、具体变更授权分开。无锚点或未能唯一定位的主张不视为通过。','approval_created':False}
  if a['sample_id'] in ['RB-2023-U04','RB-2025-U02']:
   x['narrow_original_observation']='p14购置预算/决算格空，p21明确购置决算为0；不证明预算/其它分项或整个对象；未迁移旧R05授权。'
  elif a['sample_id']=='SP-ORIG-07':
   x['narrow_original_observation']='p12三公表空白、p18同年度主体无三公支出、p1封面年度单位文种可见；不由此批准完整聚合变更。'
  dispositions.append(x)
 dump(name+'_dispositions.json',dispositions)
 groups.append({'name':name,'objects':len(dispositions),'with_exact_anchors':sum(bool(x['independent_original_anchor_checks']) for x in dispositions),'exact_anchor_checks':sum(x['independent_original_anchor_checks'] for x in dispositions),'approved_objects':0,'full_object_semantic_acceptance':'PENDING','not_offset_against_other_registry':True})
report={'version':'independent-r3-review-20261106-v1','candidate_head':'3424596ec9f3dc8d807c199cacd999c77b45cd29','verdict':'RETURN_FOR_REWORK','findings':findings,'residuals':{'R-01':'FAIL_BOUNDARY_COUNTEREXAMPLES','R-02':'PASS_REQUESTED_BOUNDARIES_ONLY','R-03':'FAIL_WHOLE_TOKEN_COUNTEREXAMPLES','R-04':'PASS_V2_SEMANTIC_CONTROLS; REAL_SCOPE31_PENDING'},'controls':{n:control_summary(p) for n,p in [('original27','receipts/effect_boundary_controls.json'),('original9','receipts/narrative_boundary_controls.json'),('extra_effect13','receipts/extra_effect.json'),('extra_narrative8','receipts/extra_narrative.json'),('scope_v2_9','receipts/scope_v2_controls.json')]},'current_focused_tests':{'passed':144,'log':'focused.log','full_suite_rerun':False},'scope31':{'requests':31,'status':'UNAPPROVED','new_real_v2_approval_issued':False,'old_v1_anchor_invented':False,'at_least_8_old_diagnose_status_changed_to_na':True,'synthetic_positive_is_not_business_approval':True},'original_object_groups':groups,'gates':read('effect_gate_summary.json'),'source_and_seal_integrity':read('seal_integrity.json'),'repeat4':read('repeat4_independent.json'),'runtime':{'web_db_created_or_restarted':False,'live_evidence_reused_only_as_historical_69884e9_native':True,'R3_live_chain_accepted':False},'exclusions':['B1','original7_disputed','all_budgets','OCR','AI','real_template_chain','real_Word','production_readiness'],'merge_push_or_daily_entry_switch':False,'note':'逐对象原件观察审查不等于完整语义批准。原147与R2新35全部仍待逐对象语义裁决。'}
dump('independent_review.json',report)
manifest=[]
for p in sorted(ROOT.rglob('*')):
 if p.is_file() and 'deps' not in p.relative_to(ROOT).parts and p.name not in ['SHA256SUMS','independent_manifest.json']:
  manifest.append({'path':str(p.relative_to(ROOT)),'sha256':sha(p),'bytes':p.stat().st_size})
dump('independent_manifest.json',{'candidate_head':report['candidate_head'],'files':manifest})
(ROOT/'SHA256SUMS').write_text(''.join(f"{r['sha256']}  {r['path']}\n" for r in manifest))
print(json.dumps({'verdict':report['verdict'],'findings':len(findings),'controls':report['controls'],'groups':groups,'manifest_files':len(manifest)},ensure_ascii=False))
