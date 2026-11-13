from pathlib import Path
import json,collections,hashlib,subprocess
from scripts import effect_acceptance_v3 as h
w=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106');out=w/'outputs/unified_review_remediation_r3_20261106';old=w/'outputs/unified_remediation_20261106';r2=w/'outputs/unified_review_remediation_20261106';ind=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/unified_independent_review_r2_20261106');final=out/'final_collection'
def dump(name,v):(out/name).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
m=h.read(final/'meta.json');assert not m['failed'] and not m['code_root_writes'] and m['dotenv_isolation_ok'] and m['code']['digest']==m['code_after'];print('final27 immutable-source and isolation checks PASS')
keys=['org_name','org_scope','fiscal_year','table_code','indicator','fund_scope','period','budget_stage','subject_code','subject_name','row_order','raw_value','normalized_value','unit','recognition_status'];facts=[]
for sample in m['samples']:
 sid=sample['sample_id'];base=old/('cover_r2_d1' if sid=='SP-ORIG-07' else 'final_d1');paths=[base/'work'/sid/'uploads'/sid/'fact_context.json',final/'work'/sid/'uploads'/sid/'fact_context.json'];sets=[]
 for p in paths:
  rows=h.read(p)['facts'];sets.append(collections.Counter(h.canonical({k:r.get(k) for k in keys}) for r in rows))
 facts.append({'sample_id':sid,'before_path':str(paths[0]),'after_path':str(paths[1]),'before_sha256':h.sha(paths[0]),'after_sha256':h.sha(paths[1]),'before_count':sum(sets[0].values()),'after_count':sum(sets[1].values()),'identity_amount_projection_unchanged':sets[0]==sets[1]})
assert all(r['identity_amount_projection_unchanged'] for r in facts),[r for r in facts if not r['identity_amount_projection_unchanged']]
dump('monetary_projection27.json',{'keys':keys,'rows':facts,'limit':'当前27的完整上下文已保存；比较基线为首轮明确路径，非缺失上下文的R2离线27，不补写旧meta。不等于27全部事实已独立核原件。'})
a,b=h.load_collection(r2/'final_collection'),h.load_collection(final);changes=[]
for sid in sorted(a):
 bm={h.receipt_key(r):r for r in a[sid]['receipts']};cm={h.receipt_key(r):r for r in b[sid]['receipts']}
 for key in sorted(bm.keys()|cm.keys()):
  before,after=bm.get(key),cm.get(key)
  if h.stable(before)!=h.stable(after):
   changes.append({'sample_id':sid,'key':key,'before_status':before.get('status') if before else None,'after_status':after.get('status') if after else None,'evaluation_changed':h.receipt_evaluation(before or {})!=h.receipt_evaluation(after or {}),'old_digest':h.digest(before),'new_digest':h.digest(after),'old':before,'new':after,'independent_acceptance':'NOT_CREATED'})
dump('r2_to_r3_receipt_changes.json',changes)
current=[]
for sid in sorted(a):
 for layer in ['candidate','tips','manual_review']:
  entries,invalid=h.layer_changes(a[sid]['finding_layers'][layer],b[sid]['finding_layers'][layer])
  for key,before,after in entries:current.append({'sample_id':sid,'layer':layer,'key':key,'old':before,'new':after,'old_digest':h.digest(before),'new_digest':h.digest(after),'classification':'缺独立证据','status':'PENDING_EXACT_REVIEW'})
dump('r3_new_layer_changes.json',current)
# 旧R-05证明只能在它已封印的R2代际中复演，不迁移到当前代际。
evidence=ind/'r05_independent_evidence_v2.json';sealed=h.load_evidence(evidence,'e9fda775588d43e256e0e7beea9220c8ec60c363f27b36c53fdc400ab3a76092')
for name,base,candidate,ev in [('old_boundary_r05_proof',old/'final_d1',r2/'final_collection',sealed),('r2_to_r3_regression',r2/'final_collection',final,{}),('current_from_original_unadjudicated',old/'final_d1',final,{})]:
 report=h.compare_collections(base,candidate,out/'kit_candidate',ev);report['tool_sha256']=h.sha(w/'scripts/effect_acceptance_v3.py');dump(name+'.json',report);print(name,report['overall'],len(report['problems']),len(report['pending']))
# 只生成待复核请求；不生成批准字段或可被工具消费的证据包。
review_path=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/unified_independent_review_20261106/independent_scope31_v1.json');review=h.read(review_path);requests=[]
from scripts import verify_pending_review as gov
for d in review['decisions']:
 row=b.get(d['sample_id']);key=tuple(d['key']);receipt=next((r for r in row['receipts'] if h.receipt_key(r)==key),None) if row else None
 requests.append({'sample_id':d['sample_id'],'pdf_sha256':d['pdf_sha256'],'key':d['key'],'prior_decision':d['diagnostic_scope_decision'],'prior_review_sha256':h.sha(review_path),'observed_case_id':gov.receipt_scope_id(row,receipt) if receipt else None,'observed_semantic_digest':gov.scope_semantic_digest(receipt) if receipt else None,'observed_status':receipt.get('status') if receipt else None,'observed_reason':receipt.get('incomplete_reason') if receipt else None,'status':'UNAPPROVED_REVIEW_REQUEST','approval_created':False})
dump('scope31_v2_requests_UNAPPROVED.json',{'version':'scope-v2-review-requests-not-an-approval','requests':requests})
dump('scope_consumption_receipt.json',gov.apply_scope_review({'entries':[]},b,review_path,h.sha(review_path)))
print('facts27 unchanged; R2-to-R3 changed receipts',len(changes),'layer objects',len(current))
