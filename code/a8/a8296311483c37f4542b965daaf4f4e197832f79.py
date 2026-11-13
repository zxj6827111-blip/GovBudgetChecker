from pathlib import Path
import json,hashlib
from scripts import effect_acceptance_v3 as h
w=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106');out=w/'outputs/unified_review_remediation_r3_20261106';old=w/'outputs/unified_remediation_20261106';r2=w/'outputs/unified_review_remediation_20261106';ind=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/unified_independent_review_20261106')
def dump(name,value):(out/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
prior=json.loads((ind/'manual16_independent_dispositions.json').read_text());sids={r['sample_id'] for r in prior if r['disposition']=='NEEDS_FURTHER_INDEPENDENT_ORIGINAL_ADJUDICATION'};assert len(sids)==9
base=h.load_collection(old/'baseline_main');after=h.load_collection(old/'final_d1')
original=[]
for sid in sorted(sids):
 changes,invalid=h.layer_changes(base[sid]['finding_layers']['manual_review'],after[sid]['finding_layers']['manual_review'])
 for key,b,c in changes:
  original.append({'origin_group':sid,'layer':'manual_review','key':key,'classification':'缺独立证据','status':'PENDING_INDEPENDENT_ORIGINAL_SEMANTIC_REVIEW','before_binding':base[sid]['source_binding'],'after_binding':after[sid]['source_binding'],'old_digest':h.digest(b),'new_digest':h.digest(c),'old':b,'new':c,'source_hint':str(ind/'manual16_independent_dispositions.json'),'approval_created':False})
dump('original9_objects.json',{'groups':sorted(sids),'objects':original,'note':'逐对象登记并保留前后内容；登记不等于修好，不生成批准。'})
base=h.load_collection(old/'final_d1');after=h.load_collection(r2/'final_collection');current=[]
for sid in sorted(base.keys()&after.keys()):
 for layer in ['candidate','tips','manual_review']:
  changes,invalid=h.layer_changes(base[sid]['finding_layers'][layer],after[sid]['finding_layers'][layer])
  for key,b,c in changes:
   current.append({'sample_id':sid,'layer':layer,'key':key,'classification':'缺独立证据','status':'PENDING_EXACT_OBJECT_ADJUDICATION','before_binding':base[sid]['source_binding'],'after_binding':after[sid]['source_binding'],'old_digest':h.digest(b),'new_digest':h.digest(c),'old':b,'new':c,'approval_created':False})
assert len(current)==35,len(current)
dump('r2_current35_objects.json',{'object_count':35,'objects':current,'note':'R-05仅签收8条关系N/A，不提升为整个待核对象批准。此清单不与原9组抵账。'})
print('registered original9 groups and',len(original),'objects; current35 objects; no approvals')
