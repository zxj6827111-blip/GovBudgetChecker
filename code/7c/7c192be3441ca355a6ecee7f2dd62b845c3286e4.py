import json,sys
from pathlib import Path
OUT=Path(__file__).resolve().parent;label=sys.argv[1];entries=json.loads((OUT/(label+'_summary.json')).read_text())
collisions=[];unknown_refs=[];total=0
for e in entries:
 s=json.loads((OUT/label/e['job']/'uploads'/e['job']/'status.json').read_text());r=s['review_report']
 items=[*r['issues'],*r['tips'],*r.get('candidate_findings',[]),
        *[i for i in r['manual_review'] if i.get('record_type')=='finding_review_candidate']];ids={i['id'] for i in items};total+=len(ids)
 for item in items:
  aliases=set(item.get('merged_issue_ids') or [])|set((item.get('location') or {}).get('merged_issue_ids') or [])
  cross=aliases&(ids-{item['id']})
  if cross:collisions.append(dict(job=e['job'],issue=item['id'],other_current_ids=sorted(cross)))
 for receipt in r['check_receipts']:
  refs=set(receipt.get('issue_refs') or [])-ids
  if refs:unknown_refs.append(dict(job=e['job'],rule=receipt['rule_id'],check=receipt['sub_check_id'],unknown_refs=sorted(refs)))
result=dict(materials=len(entries),canonical_findings=total,cross_group_identity_collisions=collisions,unresolved_receipt_refs=unknown_refs)
(OUT/(label+'_identity_verification.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({k:len(v) if isinstance(v,list) else v for k,v in result.items()},ensure_ascii=False));assert not collisions
