"""开发扫描原件的真实图块响应采集，非独立材料验收。"""
import asyncio,hashlib,json,time
from pathlib import Path
import httpx
from src.services.fiscal_input_builder import FiscalInputBuilder
from src.services.ocr_tiled_pipeline import recognize_document_tiles

async def main():
 root=Path('/Volumes/workSSD/software development/GovBudgetChecker')
 out=root/'artifacts/pdf-input-v2-20261018-stitch/captured-development-page';out.mkdir(exist_ok=False)
 source=root/'artifacts/pdf-input-v2-20261018/official-originals/OFFICIAL_95c2b8afdcbf/source.pdf'
 artifact=FiscalInputBuilder().read_native(source)
 async with httpx.AsyncClient() as client:
  health=(await client.get('http://127.0.0.1:19017/health',timeout=20)).json()
 assert health.get('model_fingerprint')=='9e20de91cfdde807ce68673637b8c4cb2614998f4082bf9c90c569eec66e6cea'
 start=time.monotonic()
 async def progress(data):
  print(json.dumps(data,ensure_ascii=False),flush=True)
 summary=await recognize_document_tiles(source,[3],artifact,health,checkpoint_path=out/'checkpoint',progress_callback=progress)
 (out/'artifact.json').write_text(json.dumps(artifact.to_dict(),ensure_ascii=False,indent=2))
 receipt={'scope':'real_ocr_development_capture_not_blind_acceptance','source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'health':health,'summary':summary,'seconds':time.monotonic()-start}
 (out/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
 print(json.dumps({'status':summary['status'],'seconds':receipt['seconds']},ensure_ascii=False),flush=True)

asyncio.run(main())
