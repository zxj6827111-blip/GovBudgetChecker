from pathlib import Path
import sys,json,hashlib,os
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT));OUT=Path(__file__).parent
os.environ.update(TESTING="true",DATABASE_URL="",AI_ASSIST_ENABLED="false",OCR_ENABLED="false",GOVBUDGET_AUTH_ENABLED="false",PDF_PARSE_ISOLATION_ENABLED="false")
from scripts.verify_intake_remediation import _parse_one
from src.services.pdf_parser import PDFParser
from src.services import source_reconstruction
pdf=Path.home()/"Desktop/决算测试上海市/2022/上海市教育委员会/上海市材料工程学校2022年度决算.pdf"
task={"path":str(pdf),"rel":"2022/上海市教育委员会/上海市材料工程学校2022年度决算.pdf","sha256":hashlib.sha256(pdf.read_bytes()).hexdigest(),"year":2022,"org_level":"unit"}
original_coverage=PDFParser._covered_amount_tokens
original_resolver=source_reconstruction.resolve_native_table_candidates
traces={};variant=""
def info(t):
 return {"uid":t.fragment_uid,"code":t.table_code,"method":t.extraction_method,"profile":t.geometry_profile,"bbox":list(t.bbox),"shape":[len(t.rows),max(map(len,t.rows),default=0)],"priority":PDFParser(None)._table_priority(t),"coverage":original_coverage(PDFParser(None),t),"duplicate":PDFParser(None)._duplicate_source_claims(t),"structure":PDFParser(None)._structure_priority(t)}
def traced(policy,existing,incoming):
 result=original_resolver(policy,existing,incoming)
 if any(t.page_number==14 for t in existing+incoming):
  traces.setdefault(variant,[]).append({"existing":[info(t) for t in existing if t.page_number==14],"incoming":[info(t) for t in incoming if t.page_number==14],"selected":[info(t) for t in result if t.page_number==14]})
 return result
source_reconstruction.resolve_native_table_candidates=traced
results={}
for variant in ["coverage_enabled","coverage_disabled_previous_policy"]:
 PDFParser._covered_amount_tokens=original_coverage if variant=="coverage_enabled" else lambda self,table:0
 r=_parse_one(task)
 results[variant]={k:r.get(k) for k in ["admitted","pending","amount_cells","fact_count","pending_reasons","fragments","error"]}
PDFParser._covered_amount_tokens=original_coverage;source_reconstruction.resolve_native_table_candidates=original_resolver
(OUT/"material_school_controlled_ab.json").write_text(json.dumps({"source_sha256":task["sha256"],"note":"受控诊断：同码同原件，仅在进程内关闭覆盖择优以还原此前策略；不作正式验收采集","results":results,"page14_selection_trace":traces},ensure_ascii=False,indent=2))
print(json.dumps(results,ensure_ascii=False,indent=2))
import pymupdf
with pymupdf.open(pdf) as d:d[13].get_pixmap(dpi=100).save(OUT/"material_school_page14.png")
