from pathlib import Path
import json,hashlib,shutil
root=Path('/Volumes/workSSD/software development/GovBudgetChecker')
out=root/'outputs/pdf_review_remediation_merge_20261027T154139Z'
original=root/'uploads/67217a651ab2f9e7f64dba9d85c7742d'
current=out/'target-final-online/main-sample-job'
a=json.loads((original/'fact_context.json').read_text())['facts']
b=json.loads((current/'fact_context.json').read_text())['facts']
def ref(f):
 refs=f['extra_dims']['evidence_refs']
 assert len(refs)==1
 return refs[0]
def key(f):
 e=ref(f)
 return e['file_hash'],e['physical_page'],e['source_cell_id'],f['indicator']
def protected(f):
 e=ref(f)
 return (f['raw_value'],f['unit'],f['normalized_value'],f['display_precision'],e.get('bbox'),e.get('quote'),f['row_order'],f['table_code'],f['budget_stage'],f['extra_dims'].get('source_header'))
old={key(f):f for f in a};new={key(f):f for f in b}
common=set(old)&set(new)
status=json.loads((current/'status.json').read_text())
summary={'baseline_facts':len(a),'current_facts':len(b),'added_keys':len(set(new)-set(old)),'lost_keys':len(set(old)-set(new)),'monetary_and_source_changes':sum(protected(old[k])!=protected(new[k]) for k in common),'subject_identity_changes':sum((old[k]['subject_code'],old[k]['subject_name'])!=(new[k]['subject_code'],new[k]['subject_name']) for k in common),'fact_uid_changes':sum(old[k]['fact_uid']!=new[k]['fact_uid'] for k in common),'blocking_total':status['review_report']['obligation_coverage']['blocking_total'],'formal_problem_groups':len(status['review_report']['issues']),'baseline_context_sha256':hashlib.sha256((original/'fact_context.json').read_bytes()).hexdigest(),'current_context_sha256':hashlib.sha256((current/'fact_context.json').read_bytes()).hexdigest()}
assert summary['baseline_facts']==summary['current_facts']==620,summary
assert summary['added_keys']==summary['lost_keys']==summary['monetary_and_source_changes']==summary['fact_uid_changes']==0,summary
assert summary['subject_identity_changes']==32,summary
assert summary['blocking_total']==7 and summary['formal_problem_groups']==5,summary
shutil.copy2(original/'fact_context.json',out/'baseline-original-fact_context.json')
(out/'target-main-sample-comparison.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False,indent=2))
