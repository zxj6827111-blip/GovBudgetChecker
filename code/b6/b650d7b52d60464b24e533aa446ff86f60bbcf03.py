from pathlib import Path
import json,hashlib,collections
B=Path('/Volumes/workSSD/software development/GovBudgetChecker');R=Path(__file__).parent;D=B/'outputs/unified_closeout_round6_20261104'
scan=json.loads((D/'painpoint_scan.json').read_text());audit=json.loads((R/'painpoint_inventory_audit.json').read_text());proof={p['job_id']:p for p in audit['candidate_proofs']}
# Two pilots cover OCR recovery flags and the previously identified native column case.
selected=[1,7,3,4,5,6,8,10,11,14];rows=[]
for rank,idx in enumerate(selected,1):
 c=scan['candidates'][idx-1];p=Path(c['status_path']);st=json.loads(p.read_text());pr=proof[c['job_id']];checks=[]
 for item in (st.get('review_report') or {}).get('manual_review') or []:
  if isinstance(item,dict) and item.get('record_type')=='unfinished_check':checks.append({k:item.get(k) for k in ('obligation_id','title','status','reason','check_relation_id','next_action')})
 assert len(checks)==c['unfinished_total'];assert pr['material_pdf_matches']
 rows.append({'order':rank,'pilot':rank<=2,'from_candidate_number':idx,'job_id':c['job_id'],'filename':c['filename'],'pdf_path':pr['material_pdf_matches'][0],'sha256':c['checksum'],'year':c['fiscal_year'],'historical_doc_type':c['doc_type'],'historical_mode':c['mode'],'historical_use_ai_assist':pr['use_ai_assist'],'historical_ai_state':(pr.get('ai_execution') or {}).get('state'),'historical_revision':c['analysis_revision'],'historical_status_path':str(p),'historical_status_sha256':pr['status_sha256'],'technical_reasons':c['technical_reasons'],'unfinished_total':len(checks),'unfinished_checks_full':checks,'scope_note':'Historical doc_type/archive organization are not independent truth; verify actual cover and requested scope before running.'})
report={'kind':'diagnostic_input_manifest_draft','not_blind_test':True,'not_run':True,'candidate_business_head':'4cda0edd361c81530654da8915306a400bd7f100','count':len(rows),'years':dict(collections.Counter(str(r['year']) for r in rows)),'types':dict(collections.Counter(r['historical_doc_type'] for r in rows)),'pilot_orders':[1,2],'phase1':'Local rules plus existing local OCR, external AI disabled; compare local applicable checks, keep unexecuted AI checks visible','baseline_limit':'Historical outputs mix modes/configs; they show user experience, not a controlled code-only baseline','items':rows}
(R/'diagnostic_manifest_10.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'count':len(rows),'complete_unfinished_records':sum(r['unfinished_total'] for r in rows),'years':report['years'],'types':report['types'],'pilot_jobs':[r['job_id'] for r in rows if r['pilot']]},ensure_ascii=False,indent=2))
