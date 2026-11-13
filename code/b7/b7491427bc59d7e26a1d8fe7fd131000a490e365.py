import asyncio,hashlib,json,os,tempfile
from pathlib import Path
from unittest.mock import patch
from starlette.requests import Request
with tempfile.TemporaryDirectory(prefix="govbudget-current-production-synthetic-") as temp:
 os.environ.update(TESTING="true",GOVBUDGET_AUTH_ENABLED="false",DATABASE_URL="",AI_ENABLED="false",OCR_ENABLED="false",PDF_PARSE_ISOLATION_ENABLED="false",RULE_EXECUTION_ISOLATION_ENABLED="false",PIPELINE_TIMEOUT_SEC="30",UPLOAD_DIR=temp,AUDIT_LOG_PATH=temp+"/audit.jsonl")
 from api import main,runtime
 from api.routes import corrections,reports,workflow as workflow_route
 from src.services import issue_workflow_store as workflow
 from src.services.pdf_parser import PDFParser
 from src.engine.fact_context import build_fact_context_from_artifact
 from src.services.correction_overlay_store import current_analysis_basis_token,read_jsonl_records
 import fitz,pdfplumber
 root=Path(temp);jd=root/"native-budget-synthetic";jd.mkdir();pdf=jd/"source.pdf"
 with fitz.open() as d:
  p=d.new_page(width=650,height=700)
  for point,text in [((40,40),"2026年测试局部门预算公开"),((40,70),"部门预算收支总表"),((40,95),"单位：万元"),((40,280),"2026年收入预算90万元，支出预算100万元。"),((40,305),"预算编制说明：本年度预算按实际工作需要安排。")]:p.insert_text(point,text,fontname="china-s",fontsize=11)
  xs=[40,200,320,480,610];ys=[115,155,195,235]
  for x in xs:p.draw_line((x,115),(x,235))
  for y in ys:p.draw_line((40,y),(610,y))
  rows=[["收入项目","本年收入","支出项目","本年支出"],["一般公共预算","100.00","基本支出","100.00"],["收入总计","100.00","支出总计","100.00"]]
  for r,row in enumerate(rows):
   for c,v in enumerate(row):p.insert_text((xs[c]+6,ys[r]+25),v,fontname="china-s",fontsize=10)
  d.save(pdf)
 digest=hashlib.sha256(pdf.read_bytes()).hexdigest()
 def parse_context():
  with pdfplumber.open(pdf) as h:a=PDFParser(None).build_parse_artifact_from_handle(h,file_hash=digest)
  c=build_fact_context_from_artifact(a,org_name="测试局",fiscal_year=2026,report_kind="budget",doc_type="budget")
  return a,c
 a1,c1=parse_context();a2,c2=parse_context()
 out={"repeat_parse_same_pdf":{"same_file_hash":a1.source_file_hash==a2.source_file_hash,"fragment_ids_a":[x.fragment_uid for x in a1.table_fragments],"fragment_ids_b":[x.fragment_uid for x in a2.table_fragments],"fact_ids_a":[x.fact_uid for x in c1.facts] if c1 else [],"fact_ids_b":[x.fact_uid for x in c2.facts] if c2 else [],"table_codes":[x.table_code for x in a1.table_fragments],"fact_values":[{"indicator":x.indicator,"raw":x.raw_value,"normalized":str(x.normalized_value)} for x in c1.facts] if c1 else []}}
 initial={"job_id":jd.name,"filename":"source.pdf","status":"queued","mode":"legacy","use_local_rules":True,"use_ai_assist":False,"fiscal_year":2026,"report_year":2026,"report_kind":"budget","doc_type":"budget","organization_name":"测试局","created_by":"test-admin"}
 (jd/"status.json").write_text(json.dumps(initial),encoding="utf-8")
 def snapshot():
  p=json.loads((jd/"status.json").read_text());m=p.get("result",{}).get("meta",{});r=m.get("fact_receipts",[])
  iss=p.get("review_report",{}).get("issues",[])
  return {"status":p.get("status"),"error":p.get("error"),"analysis_conclusion":p.get("analysis_conclusion"),"basis":current_analysis_basis_token(jd.name,jd),"analysis_revision":p.get("analysis_revision"),"canonical_revision":p.get("review_report",{}).get("analysis_revision"),"fact_context":m.get("fact_context"),"fact_overlay_applied":m.get("fact_overlay_applied"),"fact_receipts":r,"t1_issue_messages":[i.get("message") for i in iss if i.get("rule_id")=="BUD-107"],"t1_issue_identities":[{"id":i.get("id"),"fact_ids":i.get("fact_ids"),"check_relation_id":i.get("check_relation_id"),"evidence_ids":i.get("evidence_ids")} for i in iss if i.get("rule_id")=="BUD-107"],"report_evidence_ids":sorted(corrections.report_evidence_ids(p)),"human_decisions":p.get("review_report",{}).get("human_decisions",[]),"review_reasons":p.get("review_reasons",[])}

 export_save_root=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/remediation_20261005_round7/export_probe');export_save_counter={'value':0}
 async def capture_all_exports(request,revision=None):
  import csv,io,zipfile,xml.etree.ElementTree as ET
  with patch.object(runtime,'require_user_store',lambda:object()):
   j=await reports.download_report(request,jd.name,'json',revision)
   c=await reports.download_report(request,jd.name,'csv',revision)
   w=await reports.download_report(request,jd.name,'docx',revision)
   pp=await reports.download_report(request,jd.name,'pdf',revision)
  export_save_counter['value']+=1
  export_dir=export_save_root/(f"{export_save_counter['value']:02d}_"+('historical' if revision else 'current'))
  export_dir.mkdir(parents=True,exist_ok=True)
  (export_dir/'report.json').write_text(json.dumps(j,ensure_ascii=False,indent=2))
  (export_dir/'review_report.json').write_text(json.dumps(j['review_report'],ensure_ascii=False,indent=2))
  (export_dir/'report.csv').write_bytes(c.body);(export_dir/'report.docx').write_bytes(w.body)
  (export_dir/'report.pdf').write_bytes(Path(pp.path).read_bytes())
  (export_dir/'source.pdf').write_bytes(pdf.read_bytes())
  rows=list(csv.DictReader(io.StringIO(c.body.decode('utf-8-sig'))))
  with zipfile.ZipFile(io.BytesIO(w.body)) as z:xml=z.read('word/document.xml')
  docx_text=''.join(ET.fromstring(xml).itertext())
  with fitz.open(pp.path) as dd:pdf_text=''.join(page.get_text() for page in dd)
  pdf_text=''.join(pdf_text.split());docx_text=''.join(docx_text.split());sha=j['review_report_sha256']
  return {'saved_export_dir':str(export_dir),'saved_paths':{name:str(export_dir/name) for name in ['report.json','review_report.json','report.csv','report.docx','report.pdf','source.pdf']},'analysis_revision':j.get('analysis_revision'),'human_decisions':j['review_report'].get('human_decisions',[]),'formal_t1_ids':[i['id'] for i in j['issues'] if i.get('rule_id')=='BUD-107'],'sha256':sha,'csv_human_rows':[r for r in rows if r.get('record_type')=='human_decision'],'csv_summary_sha':rows[0].get('review_report_sha256'),'docx_has_decision_id':'DECISION_ID|BUD-107-1|' in docx_text,'docx_has_status':'confirmed' in docx_text,'docx_has_actor':'test-admin' in docx_text,'docx_has_note':'syntheticoriginal100confirmation' in docx_text,'docx_has_sha':sha in docx_text,'pdf_has_decision_id':'DECISION_ID|BUD-107-1|' in pdf_text,'pdf_has_status':'confirmed' in pdf_text,'pdf_has_actor':'test-admin' in pdf_text,'pdf_has_note':'syntheticoriginal100confirmation' in pdf_text,'pdf_has_sha':sha in pdf_text,'pdf_path':str(pp.path),'source_hash_unchanged':hashlib.sha256(pdf.read_bytes()).hexdigest()==digest}

 async def run():
  await main._run_pipeline(jd)
  before=snapshot();out["real_pipeline_before_correction"]=before
  receipts=before["fact_receipts"]
  target=next((x.get("operand_fact_ids",[])[0] for x in receipts if x.get("sub_check_id")=="T1:income_budget:text_vs_table" and x.get("operand_fact_ids")),None)
  if target is None:target=(c1.facts[0].fact_uid if c1 and c1.facts else "missing-fact")
  request=Request({"type":"http","method":"POST","path":f"/api/jobs/{jd.name}/corrections","headers":[],"server":("test",80),"scheme":"http","query_string":b""})

  target_issue=before['t1_issue_identities'][0]['id']
  workflow_body=json.dumps({'action':'update_issue','job_id':jd.name,'issue_id':target_issue,'status':'confirmed','note':'synthetic original100 confirmation'}).encode()
  async def receive_workflow():return {'type':'http.request','body':workflow_body,'more_body':False}
  workflow_request=Request({'type':'http','method':'POST','path':'/api/workflow','headers':[],'server':('test',80),'scheme':'http','query_string':b''},receive_workflow)
  with patch.object(runtime,'require_user_store',lambda:object()):
   workflow_state=await workflow_route.mutate_workflow(workflow_request)
  with patch.object(runtime,'require_user_store',lambda:object()):
   immediate_json=await reports.download_report(request,jd.name,'json',None)
  out['actual_workflow_before_correction']={'selected_issue_id':target_issue,'old_revision':before['canonical_revision'],'stored_records':list(workflow_state['issues'].values()),'stored_status_map':workflow.get_job_issue_decisions(jd.name),'canonical_human_decisions_after_update':snapshot()['human_decisions'],'immediate_json_human_decisions':immediate_json['review_report'].get('human_decisions',[]),'db_mirror':json.loads((root/'.issue_workflow.persistence.json').read_text()) if (root/'.issue_workflow.persistence.json').exists() else 'DATABASE_URL empty; production persist returns disabled'}

  out['actual_workflow_route_all_current_exports']=await capture_all_exports(request)
  basis=current_analysis_basis_token(jd.name,jd)
  # Register a manual source reference through the real correction route. Only this
  # preparation has the runner disabled; actual raw correction below uses registered main.
  runtime.set_pipeline_runner(None)
  prep=corrections.CorrectionRequest(analysis_basis_token=basis,reason="synthetic source location registration",changes=[{"target_type":"fact","target_id":target,"field":"manual_location","value":{"physical_page":1,"bbox":[205,155,315,195],"quote":"100.00"}}])
  with patch.object(runtime,"require_user_store",lambda:object()):
   prep_result=await corrections.create_job_correction(jd.name,prep,request)
  ev=read_jsonl_records(jd/"corrections.jsonl")[0]["manual_evidence"][0]["evidence_id"]
  runtime.set_pipeline_runner(main._run_pipeline)
  change=corrections.CorrectionRequest(analysis_basis_token=basis,reason="synthetic original total100 corrected90",changes=[{"target_type":"fact","target_id":target,"field":"raw_value","value":"90.00","evidence_ids":[ev]}])
  with patch.object(runtime,"require_user_store",lambda:object()):
   previous=asyncio.all_tasks();response=await corrections.create_job_correction(jd.name,change,request)
   spawned=asyncio.all_tasks()-previous
   if spawned:await asyncio.gather(*spawned)
  out["real_correction_route_and_main_rerun"]={"original_target_id":target,"preparation":prep_result,"response":response,"new_background_tasks":len(spawned),"after":snapshot(),"historical_version_dirs":sorted(x.name for x in (jd/"analysis_versions").iterdir() if x.is_dir())}
  # Real historical loader and report route (only TESTING auth store boundary patched).
  old_rev=before["basis"].split(":",1)[1]
  with patch.object(runtime,"require_user_store",lambda:object()):
   historical=await reports.download_report(request,jd.name,"json",old_rev)
   current=await reports.download_report(request,jd.name,"json",None)
  out["real_current_and_historical_json_download"]={"historical_status":historical["status"],"historical_canonical_revision":historical["review_report"].get("analysis_revision"),"current_canonical_revision":current["review_report"].get("analysis_revision"),"historical_t1_messages":[i.get("message") for i in historical["issues"] if i.get("rule_id")=="BUD-107"],"current_t1_messages":[i.get("message") for i in current["issues"] if i.get("rule_id")=="BUD-107"]}

  with patch.object(runtime,'require_user_store',lambda:object()):
   csv_response=await reports.download_report(request,jd.name,'csv',None)
  import csv,io
  csv_text=csv_response.body.decode('utf-8-sig')
  csv_rows=list(csv.DictReader(io.StringIO(csv_text)))
  out['actual_workflow_after_correction']={'old_issue_id':target_issue,'current_issue_ids':[i.get('id') for i in current['review_report']['issues']],'old_issue_still_in_current_formal':target_issue in [i.get('id') for i in current['review_report']['issues']],'current_human_decisions':current['review_report'].get('human_decisions',[]),'historical_human_decisions':historical['review_report'].get('human_decisions',[]),'csv_record_types':sorted(set(r.get('record_type','') for r in csv_rows)),'csv_has_confirmed_token':'confirmed' in csv_text,'csv_has_workflow_note':'synthetic original100 confirmation' in csv_text,'csv_column_names':list(csv_rows[0]) if csv_rows else []}

  out['actual_workflow_route_all_current_exports_after_reanalysis']=await capture_all_exports(request)
  out['actual_workflow_route_all_historical_exports_after_reanalysis']=await capture_all_exports(request,old_rev)

  # Same actual job: make a changed numeric issue in a later revision, then review it.
  basis2=current_analysis_basis_token(jd.name,jd);runtime.set_pipeline_runner(None)
  prep2=corrections.CorrectionRequest(analysis_basis_token=basis2,reason='synthetic new version evidence registration',changes=[{'target_type':'fact','target_id':target,'field':'manual_location','value':{'physical_page':1,'bbox':[205,155,315,195],'quote':'100.00'}}])
  with patch.object(runtime,'require_user_store',lambda:object()):await corrections.create_job_correction(jd.name,prep2,request)
  records2=read_jsonl_records(jd/'corrections.jsonl');ev2=records2[-1]['manual_evidence'][0]['evidence_id']
  runtime.set_pipeline_runner(main._run_pipeline)
  change2=corrections.CorrectionRequest(analysis_basis_token=basis2,reason='synthetic new numeric issue total110',changes=[{'target_type':'fact','target_id':target,'field':'raw_value','value':'110.00','evidence_ids':[ev2]}])
  with patch.object(runtime,'require_user_store',lambda:object()):
   previous2=asyncio.all_tasks();await corrections.create_job_correction(jd.name,change2,request)
   spawned2=asyncio.all_tasks()-previous2
   if spawned2:await asyncio.gather(*spawned2)
  workflow_body=json.dumps({'action':'update_issue','job_id':jd.name,'issue_id':target_issue,'status':'no_issue','note':'synthetic later110 review'}).encode()
  later_request=Request({'type':'http','method':'POST','path':'/api/workflow','headers':[],'server':('test',80),'scheme':'http','query_string':b''},receive_workflow)
  with patch.object(runtime,'require_user_store',lambda:object()):later_state=await workflow_route.mutate_workflow(later_request)
  old_view_after_new_decision=await capture_all_exports(request,old_rev)
  current_view_after_new_decision=await capture_all_exports(request)
  out['actual_historical_decision_overwrite']={'old_analysis_revision':old_rev,'old_view_before_later_decision':out['actual_workflow_route_all_historical_exports_after_reanalysis'],'old_view_after_later_decision':old_view_after_new_decision,'current_view_after_new_decision':current_view_after_new_decision,'latest_store_records':list(later_state['issues'].values()),'old_analysis_t1_messages':out['real_current_and_historical_json_download']['historical_t1_messages'],'current_analysis_t1_messages':snapshot()['t1_issue_messages']}

 asyncio.run(run())
 print(json.dumps(out,ensure_ascii=False,default=str))

