from pathlib import Path
import dataclasses,json,sys,time
from concurrent.futures import ThreadPoolExecutor,as_completed
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from src.services.pdf_parse_process import _parse_pdf_sync
from src.schemas.parse_artifact import ParseArtifact
from src.services.document_profile_resolver import resolve_document_profile
from src.engine.fact_context import build_fact_context_from_artifact
from src.services.structure_view import build_structure_view
from src.engine.pipeline import build_document,run_rules_with_outcomes

def check(p):
 t=time.monotonic();parsed=_parse_pdf_sync(p,{'timeout_seconds':120,'memory_mb':1024,'max_pages':800})
 artifact=ParseArtifact.from_dict(parsed['parse_artifact'])
 profile=resolve_document_profile(path=str(p),page_texts=parsed['page_texts'],explicit_report_kind='final')
 doc=build_document(str(p),parsed['page_texts'],parsed['page_tables'],parsed['filesize'])
 doc.document_profile=profile;doc.parse_artifact=artifact
 doc.fact_context=build_fact_context_from_artifact(artifact,org_name=profile.organization_name.value,fiscal_year=profile.year,report_kind='final',doc_type='dept_final')
 doc.structure_view=build_structure_view(parsed['page_texts'],artifact)
 issues,outcomes=run_rules_with_outcomes(doc,report_kind='final')
 folder=OUT/'shanghai/native_final_closure'/p.stem;folder.mkdir(parents=True,exist_ok=True)
 result={'filename':p.name,'elapsed_seconds':round(time.monotonic()-t,2),'scope':'native-only rule diagnostics; no API/DB/OCR, not final service or business acceptance','issues':[dataclasses.asdict(i) for i in issues],'outcomes':[dataclasses.asdict(o) for o in outcomes]}
 (folder/'diagnostic.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n')
 print(p.name,len(issues),flush=True)
 return result
if __name__=='__main__':
 with ThreadPoolExecutor(max_workers=3) as pool:
  pending=[pool.submit(check,p) for p in sorted(Path('/Users/zhangxingjin/Desktop/决算测试上海市').glob('*.pdf'))]
  results=[future.result() for future in as_completed(pending)]
 (OUT/'shanghai/native_final_closure_all.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str)+'\n')
