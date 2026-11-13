import os
import sys,json,hashlib,time
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path.cwd()))
import pdfplumber
from src.services.pdf_page_extract import extract_tables_from_page,extract_visible_text_from_page
from src.services.pdf_parser import PDFParser
from src.services.document_profile_resolver import resolve_document_profile
from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.pipeline import build_document,build_issues_payload
from src.services.rule_source_binding import bind_rule_sources, verify_bound_rule_locations
from src.services.evidence_guard import apply_evidence_completeness
OUT=Path(os.environ.get('CODEX_REVIEW_OUT',str(Path(__file__).resolve().parents[1]/'replay')))
OUT.mkdir(parents=True,exist_ok=True)
# 在读取系统新结果前依据原件冻结；仅覆盖明列关系，不据此声称整份真值完整。
expected=json.loads((Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/independent_70cd8d37_20261111/evidence/fresh_blind_expected_before_run.json')).read_text())
for prefix in expected:
 start=time.time();p=next(Path('/Volumes/workSSD/software development/GovBudgetChecker/uploads').glob(prefix+'*/*.pdf'));digest=hashlib.sha256(p.read_bytes()).hexdigest()
 print('START',prefix,flush=True)
 with pdfplumber.open(p) as pdf:
  texts=[extract_visible_text_from_page(page) for page in pdf.pages]
  tables=[extract_tables_from_page(page) for page in pdf.pages]
  artifact=PDFParser(None).build_parse_artifact_from_handle(pdf,file_hash=digest)
 profile=resolve_document_profile(filename=p.name,page_texts=texts)
 org=profile.organization_name.value
 doc=build_document(str(p),texts,tables,p.stat().st_size)
 doc.document_profile=profile;doc.parse_artifact=artifact
 doc.fact_context=build_fact_context_from_artifact(artifact,org_name=org,fiscal_year=profile.year,report_kind=profile.kind,doc_type='final',require_complete_identity=True,org_level='department' if prefix=='6000a11c' else 'unit')
 payload=build_issues_payload(doc,report_kind='final')
 bind_rule_sources(payload,page_texts=texts,source_file_hash=digest,artifact=artifact,fact_context=doc.fact_context,consumption=payload.get('structured_consumption',[]))
 verify_bound_rule_locations(payload,source_pdf_path=str(p))
 apply_evidence_completeness(payload,document_page_count=len(texts),page_texts=texts,source_file_hash=digest,source_pdf_path=str(p))
 facts=doc.fact_context.to_dict() if doc.fact_context else None
 report={'source':str(p.resolve()),'source_hash':digest,'profile':profile.model_dump(mode='json'),'logical_tables':[{'code':i.table_code,'pages':[i.page_start,i.page_end]} for i in artifact.logical_tables],'facts':facts,'payload':payload,'seconds':round(time.time()-start,2)}
 (OUT/(prefix+'_new_result.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str))
 (OUT/(prefix+'_artifact.json')).write_text(json.dumps(artifact.to_dict(),ensure_ascii=False,default=str))
 issues=payload['issues']['all'];summary=payload['rule_execution_summary']
 print('DONE',prefix,'facts',len(doc.fact_context.facts) if doc.fact_context else 0,'counts',{k:summary[k] for k in ['total_rules','pass','fail','insufficient_data','execution_error','parse_error']},'issues',len(issues),'seconds',report['seconds'],flush=True)
 for f in issues:
  print(f.get('rule_id'),f.get('severity'),f.get('location',{}).get('page'),(f.get('evidence_verification') or {}).get('status'),f.get('message','')[:250],flush=True)
 for u in summary['unresolved_rules']:
  print('UNRESOLVED',u.get('rule_id'),u.get('detail','')[:350],flush=True)
 assert hashlib.sha256(p.read_bytes()).hexdigest()==digest
