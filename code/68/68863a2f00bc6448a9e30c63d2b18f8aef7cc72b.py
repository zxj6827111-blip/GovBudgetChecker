"""按采集时的冻结代码续跑同一旧断点，保留其补读轮次；非盲验。"""
from pathlib import Path
import sys
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
sys.path.insert(0,str(ROOT/'artifacts/pdf-input-v2-20261018-joint/implementation'))
import asyncio,json,os,time
import httpx
from src.schemas.parse_artifact import ParseArtifact
from src.services.ocr_tiled_pipeline import recognize_document_tiles
async def main():
 out=ROOT/'artifacts/pdf-input-v2-20261018-stitch/captured-development-page-pinned'
 receipt=json.loads((out/'receipt.json').read_text())
 artifact=ParseArtifact.from_dict(json.loads((out/'artifact.json').read_text()))
 async with httpx.AsyncClient() as client:
  health=(await client.get('http://127.0.0.1:19017/health',timeout=20)).json()
 assert all(health[key]==receipt['health'][key] for key in ('model_fingerprint','pipeline_fingerprint'))
 os.environ['OCR_MODEL_FINGERPRINT']=health['model_fingerprint']
 start=time.monotonic()
 summary=await recognize_document_tiles(receipt['source'],[3],artifact,health,checkpoint_path=out/'checkpoint')
 (out/'artifact-resume-1.json').write_text(json.dumps(artifact.to_dict(),ensure_ascii=False))
 (out/'receipt-resume-1.json').write_text(json.dumps({'scope':'same_checkpoint_same_frozen_code_development_capture','summary':summary,'seconds':time.monotonic()-start},ensure_ascii=False,indent=2))
 print(json.dumps(summary,ensure_ascii=False),flush=True)
asyncio.run(main())
