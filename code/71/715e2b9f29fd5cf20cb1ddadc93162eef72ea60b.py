import json,collections,re,unicodedata
from pathlib import Path
OUT=Path(__file__).resolve().parent
base={e['job']:e for e in json.loads((OUT/'baseline_fresh_summary.json').read_text())}
current={e['job']:e for e in json.loads((OUT/'release_final5_summary.json').read_text())}
def clean(v):return re.sub(r'\s+','',unicodedata.normalize('NFKC',str(v or '')))
def facts(label,j):
 p=OUT/label/j/'uploads'/j/'fact_context.json'
 return json.loads(p.read_text()).get('facts',[]) if p.exists() else []
def anchor(f):
 refs=f.get('extra_dims',{}).get('evidence_refs',[])
 return (f.get('table_code'),f.get('indicator'),f.get('budget_stage'),tuple(sorted(ref.get('source_cell_id') or '' for ref in refs)))
money_fields=('table_code','subject_code','indicator','raw_value','normalized_value','unit','org_name','fiscal_year','fund_scope','budget_stage','period')
def monetary(items):
 return sorted(json.dumps(dict(values={k:f.get(k) for k in money_fields},anchor=anchor(f)),sort_keys=True,ensure_ascii=False) for f in items)
changes=[];errors=[];money_changes=[];unproven=[];label_repairs=[];qualification=[]
for job,e in current.items():
 old=base[job];b=set(old['todo'].get('blocking_ids') or []);c=set(e['todo'].get('blocking_ids') or [])
 if b!=c:changes.append(dict(job=job,filename=e['filename'],removed=sorted(b-c),added=sorted(c-b)))
 s=json.loads((OUT/'release_final5'/job/'uploads'/job/'status.json').read_text());meta=s['result']['meta'];summary=meta['rule_execution_summary']
 if not e['ok'] or e['error'] or summary.get('execution_error') or summary.get('parse_error'):errors.append(job)
 old_facts,new_facts=facts('baseline_fresh',job),facts('release_final5',job)
 if monetary(old_facts)!=monetary(new_facts):money_changes.append(job);continue
 records=[r for t in meta.get('page_extraction',{}).get('table_reading',{}).get('label_visibility',{}).get('tables',[]) for r in t.get('records',[]) if r.get('status')=='recovered' and r.get('role_basis')=='native_merged_total_label' and clean(r.get('recovered_text'))=='合计' and not r.get('unsafe_hidden_characters')]
 a=collections.defaultdict(list);bmap=collections.defaultdict(list)
 for f in old_facts:a[anchor(f)].append(f)
 for f in new_facts:bmap[anchor(f)].append(f)
 for key,old_items in a.items():
  new_items=bmap[key]
  if len(old_items)!=1 or len(new_items)!=1:
   if sorted((str(f.get('subject_name')),str(f.get('recognition_status'))) for f in old_items)!=sorted((str(f.get('subject_name')),str(f.get('recognition_status'))) for f in new_items):unproven.append(dict(job=job,anchor=key,reason='ambiguous_source_fact_identity'))
   continue
  x,y=old_items[0],new_items[0];name_changed=x.get('subject_name')!=y.get('subject_name');status_changed=x.get('recognition_status')!=y.get('recognition_status')
  if not name_changed and not status_changed:continue
  money_sources=[re.match(r'^(.*):r(\d+)c\d+$',sid) for sid in key[3]]
  proof=[r for r in records if any(m and r.get('fragment_uid')==m[1] and r.get('row')==int(m[2]) for m in money_sources) and clean(x.get('subject_name'))==clean(r.get('original_text')) and clean(y.get('subject_name'))==clean(r.get('recovered_text'))]
  if name_changed:
   if len(proof)!=1:unproven.append(dict(job=job,anchor=key,old=x.get('subject_name'),new=y.get('subject_name'),reason='label_change_without_unique_visible_source_proof'))
   else:label_repairs.append(dict(job=job,table=key[0],source=proof[0]['source_cell_id'],old=x.get('subject_name'),new=y.get('subject_name')))
  if status_changed:qualification.append(dict(job=job,table=key[0],old=x.get('recognition_status'),new=y.get('recognition_status'),source_proven=bool(proof)))
  if status_changed and not proof:unproven.append(dict(job=job,anchor=key,reason='qualification_change_without_source_label_proof'))
result=dict(materials=len(current),expected=len(base),errors=errors,monetary_cell_changes=money_changes,unproven_fact_changes=unproven,source_label_repairs=label_repairs,qualification_changes=qualification,baseline_blockers=sum(len(e['todo'].get('blocking_ids') or []) for j,e in base.items() if j in current),candidate_blockers=sum(len(e['todo'].get('blocking_ids') or []) for e in current.values()),changes=changes)
(OUT/'release_final5_comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:(len(v) if isinstance(v,list) else v) for k,v in result.items() if k!='changes'},ensure_ascii=False));print('added_blockers',[(r['job'],r['added']) for r in changes if r['added']])
