import sys,json,collections
from pathlib import Path
OUT=Path(__file__).resolve().parent;label=sys.argv[1];entries=json.loads((OUT/(label+'_summary.json')).read_text());cases=[];rule_counts=collections.Counter();ids=collections.Counter();errors=[];status_counts=collections.Counter()
for e in entries:
 status=json.loads((OUT/label/e['job']/'uploads'/e['job']/'status.json').read_text());status_counts[status.get('status')]+=1;meta=(status.get('result') or {}).get('meta') or {};summary=meta.get('rule_execution_summary') or {};rules=summary.get('unresolved_rules') or [];rule_counts.update(x['rule_id'] for x in rules);blocked=set((e.get('todo') or {}).get('blocking_ids') or [])
 if summary.get('execution_error') or summary.get('parse_error') or not e.get('ok') or status.get('status') in {'error','failed'}:errors.append(dict(job=e['job'],filename=e['filename'],summary=summary,error=e.get('error'),status=status.get('status')))
 for item in (status.get('review_report') or {}).get('manual_review',[]):
  if item.get('record_type')=='unfinished_check' and item.get('obligation_id') in blocked:
   ids[item['obligation_id']]+=1;cases.append(dict(job=e['job'],filename=e['filename'],unresolved_rules=rules,**item))
(OUT/(label+'_blocked_cases.json')).write_text(json.dumps(cases,ensure_ascii=False,indent=2));(OUT/(label+'_errors.json')).write_text(json.dumps(errors,ensure_ascii=False,indent=2));print(json.dumps(dict(materials=len(entries),statuses=dict(status_counts),blocked_checks=len(cases),blocked_materials=len(set(x['job'] for x in cases)),by_check=dict(ids),unresolved_rules=dict(rule_counts),errors=len(errors)),ensure_ascii=False))
