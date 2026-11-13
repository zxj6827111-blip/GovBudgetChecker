import json,collections,hashlib
from pathlib import Path
OUT=Path(__file__).resolve().parent
base={e['job']:e for e in json.loads((OUT/'baseline_fresh_summary.json').read_text())}
current={e['job']:e for e in json.loads((OUT/'candidate_final_summary.json').read_text())}
changes=[];errors=[];fact_changes=[];formals=[]
def context(label,job):
 p=OUT/label/job/'uploads'/job/'fact_context.json'
 data=json.loads(p.read_text()) if p.exists() else {}
 fields=('table_code','instance_uid','subject_code','subject_name','indicator','raw_value','normalized_value','unit','org_name','fiscal_year','fund_scope','budget_stage','recognition_status')
 return sorted(json.dumps({k:f.get(k) for k in fields},ensure_ascii=False,sort_keys=True) for f in data.get('facts',[]))
for job,e in current.items():
 old=base[job];b=set(old['todo'].get('blocking_ids') or []);c=set(e['todo'].get('blocking_ids') or [])
 if b!=c:changes.append(dict(job=job,filename=e['filename'],removed=sorted(b-c),added=sorted(c-b)))
 s=json.loads((OUT/'candidate_final'/job/'uploads'/job/'status.json').read_text());summary=s['result']['meta']['rule_execution_summary']
 if not e['ok'] or e['error'] or summary.get('execution_error') or summary.get('parse_error'):errors.append(job)
 if context('baseline_fresh',job)!=context('candidate_final',job):fact_changes.append(job)
 formals.append(dict(job=job,old=old['todo'].get('queue_counts',{}).get('queue_required_total'),new=e['todo'].get('queue_counts',{}).get('queue_required_total')))
result=dict(materials=len(current),expected=len(base),errors=errors,persisted_financial_fact_changes=fact_changes,baseline_blockers=sum(len(e['todo'].get('blocking_ids') or []) for j,e in base.items() if j in current),candidate_blockers=sum(len(e['todo'].get('blocking_ids') or []) for e in current.values()),changes=changes,formal_counts=formals)
(OUT/'candidate_comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in result.items() if k not in ['changes','formal_counts']},ensure_ascii=False))
print('added blockers',[(r['job'],r['added']) for r in changes if r['added']])
