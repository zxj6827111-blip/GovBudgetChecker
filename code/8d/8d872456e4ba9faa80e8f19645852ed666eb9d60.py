"""固定旧独立封印，仅改变当前回执理由，验证范围批准不能自动改绑。"""
import copy
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
CAND=Path('/Volumes/workSSD/software development/gbc-worktrees/unified-remediation-20261106')
sys.path.insert(0,str(CAND))
from scripts import effect_acceptance_v3 as effect,verify_pending_review as governance
ev=CAND/'outputs/unified_review_remediation_20261106'
review=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/unified_independent_review_20261106/independent_scope31_v1.json')
row=copy.deepcopy(effect.load_collection(ev/'final_collection')['SP-ORIG-01'])
key=('OBL-TXT-T2','V33-103','T2:fiscal_allocation:share_of_income_total')
row['receipts']=[r for r in row['receipts']if governance._receipt_key(r)==key]
assert len(row['receipts'])==1
row['obligations']={key[0]:{'status':'insufficient_data'}}
results=[]
for name in ['unchanged_scope_positive','changed_applicability_reason']:
    current=copy.deepcopy(row)
    if name!='unchanged_scope_positive':
        current['receipts'][0]['incomplete_reason']='收入说明已经披露财政拨款收入占比，但本次提取失败；不能沿用未披露比例的适用性结论'
    applied=governance.apply_scope_review({'entries':[]},{'SP-ORIG-01':current},review,governance.sha256_file(review))
    _,unknown=governance.diagnostic_scope(current,applied)
    results.append({'case':name,'original_receipt_reason':row['receipts'][0]['incomplete_reason'],
        'current_receipt_reason':current['receipts'][0]['incomplete_reason'],'review_file_sha256':governance.sha256_file(review),
        'original_case_id':governance.receipt_scope_id(row,row['receipts'][0]),
        'current_case_id':governance.receipt_scope_id(current,current['receipts'][0]),
        'applied_scope_entries':applied['entries'],'unknown_after_apply':unknown,
        'expected_unknown':name!='unchanged_scope_positive','actual_unknown':bool(unknown)})
(ROOT/'receipts/scope_semantic_rebase_control.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print(json.dumps([{k:r[k]for k in ['case','expected_unknown','actual_unknown']}for r in results],ensure_ascii=False))
