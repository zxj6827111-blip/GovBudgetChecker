from pathlib import Path
import sys, os, json, shutil, hashlib, asyncio
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT));OUT=Path(__file__).parent
instance=OUT/"final_identity_pipeline_instance";org=instance/"org";uploads=instance/"uploads";org.mkdir(parents=True,exist_ok=True);uploads.mkdir(exist_ok=True)
for name in ["organizations.json","job_org_links.json"]:
 source=ROOT/"outputs/intake_remediation_20261024/org_cleanup/cleaned"/name
 if source.exists():shutil.copy2(source,org/name)
os.environ.update(TESTING="true",OCR_ENABLED="false",DATABASE_URL="",AI_ASSIST_ENABLED="false",GOVBUDGET_AUTH_ENABLED="false",UPLOAD_DIR=str(uploads),ORG_DATA_DIR=str(org),PDF_PARSE_ISOLATION_ENABLED="false")
from scripts.verify_intake_remediation import _worker_init
_worker_init(str(org),str(uploads))
from api import main as api_main
from src.services.pdf_parser import PDFParser
source=Path.home()/"Desktop/决算测试上海市/2023/上海市文化和旅游局/上海鲁迅纪念馆2023年度决算.pdf"
artifact=PDFParser(None).build_parse_artifact(str(source));target=next(t for t in artifact.table_fragments if t.table_code.startswith("UNKNOWN"))
job=uploads/"codex-identity-invalid";job.mkdir();shutil.copy2(source,job/source.name)
status={"job_id":job.name,"filename":source.name,"fiscal_year":"2023","report_year":2023,"doc_type":"dept_final","report_kind":"final","checksum":hashlib.sha256(source.read_bytes()).hexdigest(),"status":"queued","use_local_rules":True,"use_ai_assist":False,"mode":"legacy","organization_name":"上海鲁迅纪念馆"}
(job/"status.json").write_text(json.dumps(status,ensure_ascii=False))
record={"record_id":"identity-invalid","actor":"验收员","reason":"验证失败校正是否如实呈现","changes":[{"target_type":"table_instance","target_id":target.fragment_uid,"field":"table_identity","value":{"table_code":"NOT_A_REGISTERED_TABLE"},"location":{"physical_page":target.page_number,"bbox":list(target.bbox)}}]}
(job/"corrections.jsonl").write_text(json.dumps(record,ensure_ascii=False)+"\n")
asyncio.run(api_main._run_pipeline(job))
result=json.loads((job/"status.json").read_text())
proof={"job_id":job.name,"target_code":target.table_code,"submitted_field":"table_identity","submitted_code":"NOT_A_REGISTERED_TABLE","task_status":result.get("status"),"fact_overlay":result.get("fact_overlay"),"review_reasons":result.get("review_reasons"),"pipeline_error":result.get("error")}
(OUT/"identity_pipeline_result.json").write_text(json.dumps(proof,ensure_ascii=False,indent=2));print(json.dumps(proof,ensure_ascii=False,indent=2))

assert not proof["pipeline_error"], proof
assert proof["fact_overlay"]["status"] == "failed", proof
assert proof["fact_overlay"]["table_identity_overlay"]["unresolved"], proof
assert "correction_overlay_incomplete" in {r["code"] for r in proof["review_reasons"]}, proof
