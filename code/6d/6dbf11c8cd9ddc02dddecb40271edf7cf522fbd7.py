from pathlib import Path
import json,time,hashlib,io,csv,zipfile,re
import httpx,pymupdf as fitz
RUN=Path(__file__).resolve().parents[1];OUT=RUN/'evidence/user_flow';OUT.mkdir(parents=True,exist_ok=True)
state=json.loads((RUN/'run_state.json').read_text());job=state['items']['7']['job_id'];base=f"http://127.0.0.1:{state['api_port']}"
login=httpx.post(base+'/api/auth/login',headers={'X-API-Key':state['api_key']},json={'username':'admin','password':state['admin_password']});login.raise_for_status()
auth={'X-API-Key':state['api_key'],'X-Session-Token':login.json()['token']};client=httpx.Client(headers=auth,timeout=180)
report={'job_id':job,'checks':{}};checks=report['checks'];old_note='SP-20261106-原件预算空白待补充';new_note='SP-20261106-新代际仍待补充预算原件'
def get(path):
 r=client.get(base+path);r.raise_for_status();return r.json()
def exports(label,revision=None,expect=None,forbid=None):
 data={}
 for fmt in ['json','csv','pdf','docx']:
  params={'job_id':job,'format':fmt}
  if revision:params['analysis_revision']=revision
  r=client.get(base+'/api/reports/download',params=params);r.raise_for_status();(OUT/f'{label}.{fmt}').write_bytes(r.content)
  if fmt=='json':value=json.dumps(r.json(),ensure_ascii=False)
  elif fmt=='csv':value=r.content.decode('utf-8-sig');assert len(list(csv.reader(io.StringIO(value))))>1
  elif fmt=='pdf':
   with fitz.open(stream=r.content,filetype='pdf')as pdf:value=''.join(p.get_text()for p in pdf);assert len(pdf)>0
  else:
   with zipfile.ZipFile(io.BytesIO(r.content))as z:value=z.read('word/document.xml').decode()
  compact=re.sub(r'\s+','',value)
  data[fmt]={'http':r.status_code,'sha256':hashlib.sha256(r.content).hexdigest(),'content_nonempty':len(value)>100,
             'expected_note':expect is None or expect in compact,'forbidden_note_absent':forbid is None or forbid not in compact}
  assert data[fmt]['content_nonempty']and data[fmt]['expected_note']and data[fmt]['forbidden_note_absent'],(label,fmt,data[fmt])
 return data
status=get(f'/api/jobs/{job}/status');rev=status['analysis_revision'];immutable=RUN/'db_env/uploads'/job/'analysis_versions'/rev/'status.json';archive_sha=hashlib.sha256(immutable.read_bytes()).hexdigest()
review=get(f'/api/reviews?job_uuid={job}')['data']['review'];slot=review['slot_id'];start=client.post(base+f'/api/reviews/{slot}/start',json={'job_uuid':job});start.raise_for_status()
review=get(f'/api/reviews?job_uuid={job}')['data']['review'];basis=review['current_analysis']['analysis_basis_token'];checks['start_session']=bool(review['current_session'])
oid='OBL-SG-TOTAL';body={'decision':'pending','job_uuid':job,'analysis_basis_token':basis,'note':old_note,'evidence_reference':'原件第12页预算空格；第18页决算零不能填补预算'}
r=client.put(base+f'/api/reviews/{slot}/obligations/{oid}',json=body);r.raise_for_status();checks['pending_decision_persisted']=old_note in json.dumps(get(f'/api/reviews?job_uuid={job}'),ensure_ascii=False)
issue=status['review_report']['issues'][0]['id'];r=client.post(base+'/api/workflow',json={'action':'update_issue','job_id':job,'issue_id':issue,'status':'needs_review','note':'专项隔离流程验证：保留待复核，不改金额'});r.raise_for_status();checks['issue_workflow_persisted']=issue in json.dumps(get('/api/workflow?job_id='+job),ensure_ascii=False)
checks['exports_current']=exports('current_before',expect=old_note)
r=client.post(base+f'/api/reviews/{slot}/complete',json={'job_uuid':job});checks['unresolved_complete_rejected']=r.status_code==409;assert checks['unresolved_complete_rejected']
coverage_before=status['check_coverage'];status_after=get(f'/api/jobs/{job}/status');checks['manual_record_did_not_complete_engine']=coverage_before==status_after['check_coverage'];assert checks['manual_record_did_not_complete_engine']
r=client.post(base+f'/api/jobs/{job}/reanalyze',json={'mode':'dual','use_local_rules':True,'use_ai_assist':False});r.raise_for_status()
for _ in range(300):
 time.sleep(1);fresh=get(f'/api/jobs/{job}/status')
 if fresh['status']in['review_required','completed','error','failed']and fresh.get('analysis_revision')!=rev:break
else:raise RuntimeError('reanalysis timeout')
assert fresh['status']not in['error','failed'];checks['new_revision']=fresh['analysis_revision']!=rev;checks['archive_unchanged']=hashlib.sha256(immutable.read_bytes()).hexdigest()==archive_sha;assert checks['archive_unchanged']
checks['new_export_no_old_session']=exports('after_reanalysis',forbid=old_note)
new_review=get(f'/api/reviews?job_uuid={job}')['data']['review'];checks['session_invalidated']=not new_review.get('current_session');assert checks['session_invalidated']
r=client.post(base+f'/api/reviews/{slot}/start',json={'job_uuid':job});r.raise_for_status();new_review=get(f'/api/reviews?job_uuid={job}')['data']['review'];new_basis=new_review['current_analysis']['analysis_basis_token'];assert new_basis!=basis
r=client.put(base+f'/api/reviews/{slot}/obligations/{oid}',json=body);checks['stale_basis_rejected']=r.status_code==409;assert checks['stale_basis_rejected']
r=client.put(base+f'/api/reviews/{slot}/obligations/{oid}',json={**body,'analysis_basis_token':new_basis,'note':new_note});r.raise_for_status()
checks['new_session_exports']=exports('new_session',expect=new_note,forbid=old_note)
checks['historical_export_no_new_session']=exports('historical',revision=rev,forbid=new_note)
checks['current_fact_snapshot']=get(f'/api/jobs/{job}/fact-snapshot?limit=1')['revision_id']==fresh['analysis_revision'];assert checks['current_fact_snapshot']
report.update(overall='PASS',old_analysis_revision=rev,new_analysis_revision=fresh['analysis_revision'],scope='isolated native PDF + database/API lifecycle; not independent accuracy acceptance')
(OUT/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'overall':report['overall'],'checks':list(checks)},ensure_ascii=False))
