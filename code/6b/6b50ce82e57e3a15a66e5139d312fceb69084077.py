"""本机真实 OCR 服务的合成混合页控制，不作为官方材料验收。"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE.parents[2]))

from api.main import _assess_page_extraction
from src.services.pdf_parser import PDFParser
from src.services.ocr_client import check_ocr_health
from src.services.ocr_pipeline import maybe_ocr_scanned_pages
from src.services.ocr_fact_promotion import promote_ocr_candidates
from src.services.table_reading_assessment import enrich_page_assessment,assess_table_reading
from src.services.table_import_receipt import build_table_import_receipt
from src.engine.fact_context import build_fact_context_from_artifact


async def main():
    path=BASE/'synthetic_native_text_image_table.pdf'
    a=PDFParser(None).build_parse_artifact(str(path))
    text='\n'.join(b.text for b in a.text_blocks)
    assessment=enrich_page_assessment(_assess_page_extraction([text],[[]]),a)
    assert assessment['low_text_pages']==[] and assessment['table_recovery_pages']==[1]
    before=assess_table_reading(a)
    health=await check_ocr_health()
    os.environ['OCR_ENABLED']='true'
    os.environ['OCR_MODEL_FINGERPRINT']=health['model_fingerprint']
    async def progress(p):print(p['phase'],p['physical_page'],flush=True)
    result=await maybe_ocr_scanned_pages(str(path),assessment,a,progress_callback=progress,checkpoint_path=BASE/'ocr_checkpoint.json')
    promotion=promote_ocr_candidates(a,file_hash=a.source_file_hash,organization='测试局',year=2025,report_kind='final')
    context=build_fact_context_from_artifact(a,org_name='测试局',fiscal_year=2025,report_kind='final')
    facts=context.facts if context else []
    after=assess_table_reading(a)
    receipt=dict(scope='synthetic local OCR engineering control, not real-original or blind acceptance',source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),page_assessment=assessment,health=health,before=before,ocr=result,promotion=promotion,after=after,reading_import=build_table_import_receipt(a,facts),fact_count=len(facts),operand_count=len(context.definite_facts()) if context else 0)
    (BASE/'live_ocr_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    (BASE/'live_ocr_artifact.json').write_text(json.dumps(a.to_dict(),ensure_ascii=False))
    (BASE/'live_ocr_facts.json').write_text(json.dumps([f.to_dict() for f in facts],ensure_ascii=False,indent=2))
    print(json.dumps({k:receipt[k] for k in ['fact_count','operand_count','after']},ensure_ascii=False),flush=True)


asyncio.run(main())
