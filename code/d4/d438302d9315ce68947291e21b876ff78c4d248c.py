import asyncio,json,os
from pathlib import Path
from src.services.pdf_parser import PDFParser
from src.services.ocr_pipeline import maybe_ocr_scanned_pages
from src.services.ocr_fact_promotion import promote_ocr_candidates
from src.services.ocr_qualification import qualified_ocr_cover_owner
from src.engine.fact_context import build_fact_context_from_artifact
OUT=Path(__file__).resolve().parent
r=json.loads((OUT/'ocr_runtime.json').read_text())
os.environ.update(OCR_ENABLED='true',OCR_SERVICE_URL=r['url'],OCR_MODEL_FINGERPRINT=r['model_fingerprint'])
async def main():
 path='outputs/remediation_20261006_round13/original_materials/zhejiang_budget.pdf'
 a=PDFParser(None).build_parse_artifact(path)
 o=await maybe_ocr_scanned_pages(path,{'low_text_pages':[1,16,18]},a)
 o['promotion']=promote_ocr_candidates(a,organization=qualified_ocr_cover_owner(a),year=2024,report_kind='budget')
 c=build_fact_context_from_artifact(a,org_name=qualified_ocr_cover_owner(a),fiscal_year=2024,report_kind='budget')
 o['facts_summary']=c.summary() if c else None
 (OUT/'ocr'/'probe_facts.json').write_text(json.dumps(c.to_dict() if c else {},ensure_ascii=False,indent=2,default=str))
 (OUT/'ocr'/'probe_artifact.json').write_text(json.dumps(a.to_dict(),ensure_ascii=False,indent=2,default=str))
 (OUT/'ocr'/'probe_summary.json').write_text(json.dumps(o,ensure_ascii=False,indent=2,default=str))
 print(json.dumps(o,ensure_ascii=False))
asyncio.run(main())
