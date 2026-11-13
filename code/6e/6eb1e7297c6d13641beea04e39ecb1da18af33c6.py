import sys,asyncio,json,hashlib,io,shutil,time
from pathlib import Path
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker');sys.path.insert(0,str(ROOT))
OUT=ROOT/'outputs/pdf_acceptance_shanghai2022_20261021'
import httpx,pymupdf as fitz
from PIL import Image
from src.services.ocr_tiles import make_tile,recognize_tiles
from src.services.ocr_client import ocr_page
from src.services.input_geometry import Affine
async def main():
 out=OUT/'real_ocr_probes';out.mkdir(exist_ok=True)
 health=httpx.get('http://127.0.0.1:19021/health').json()
 (out/'health.json').write_text(json.dumps(health,indent=2))
 import os
 os.environ.update(OCR_SERVICE_URL='http://127.0.0.1:19021',OCR_MODEL_FINGERPRINT=health['model_fingerprint'])
 sources=json.loads((OUT/'source_manifest.json').read_text())
 scan=next(r for r in sources if r['doc_id']=='SH2022-46')
 with fitz.open(scan['frozen_path']) as pdf:
  page=pdf[6];pix=page.get_pixmap(matrix=fitz.Matrix(300/72,300/72).prerotate(90))
  image=Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
  affine=Affine(a=0,b=-.24,c=.24,d=0,e=0,f=page.rect.height)
  # Auxiliary manually oriented crop; does not claim the production full-page path rotated it.
  box=(1300,720,1812,976)
  req=make_tile(image,box,parent_sha=hashlib.sha256(pix.tobytes('png')).hexdigest(),file_hash=scan['sha256'],page=7,pixel_to_pdf=affine,inference_mode='text',operations=[{'operation':'manual_auxiliary_orthogonal_rotation','angle':90}])
  (out/'scan_p7_actual_input.png').write_bytes(req.image)
  (out/'scan_p7_request.json').write_text(json.dumps(req.context,indent=2))
 async with httpx.AsyncClient() as client:
  res=await ocr_page(client,image_png=req.image,page_number=7,tile_context=req.context,region_only=True)
  (out/'scan_p7_response.json').write_text(json.dumps(res.to_dict(),ensure_ascii=False,indent=2))
 # Actual original cover checkpoint resume: retain all old records and make bounded scheduling progress.
 checkpoints=list((OUT/'ocr_enabled_first_observation/uploads/6f6b3b9c0af069e1cbf9e407f5d06317').glob('ocr_checkpoint.json.versions/*/page-1.v3.json'))
 record={'scan_crop_ok':res.ok,'scan_crop_scope':'auxiliary_crop_only_not_whole_page_accuracy','scan_model_provenance':res.provenance,'resume_exercised':False}
 if checkpoints:
  saved=json.loads(checkpoints[0].read_text());target=out/'resume/page-1.v3.json';target.parent.mkdir(exist_ok=True)
  shutil.copy2(checkpoints[0],target)
  # Reuse original input PNG bytes for cached requests too.
  if (checkpoints[0].parent/'ocr_inputs').exists():shutil.copytree(checkpoints[0].parent/'ocr_inputs',target.parent/'ocr_inputs',dirs_exist_ok=True)
  source=next(r for r in sources if r['sha256']==saved['identity']['file'])
  with fitz.open(source['frozen_path']) as pdf:
   pix=pdf[0].get_pixmap(matrix=fitz.Matrix(300/72,300/72));image=Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
  before=set(saved['completed']);start=time.monotonic()
  async with httpx.AsyncClient() as client:
   results,rechecks,state=await recognize_tiles(image,file_hash=source['sha256'],page=1,pixel_to_pdf=Affine(*saved['identity']['pixel_to_pdf']),client=client,model=health['model_fingerprint'],pipeline=health['pipeline_fingerprint'],checkpoint_path=target,soft_budget=1,operations=saved['identity']['operations'],original_parent_sha=saved['identity']['original_page_render'])
  after=set(state['completed']);preserved=all(state['completed'][k]==saved['completed'][k] for k in before)
  record.update(resume_exercised=True,old_completed_tiles=len(before),new_completed_tiles=len(after),prior_records_preserved=preserved,identity_equal=state['identity']==saved['identity'],status=state['status'],pending=len(state['pending']),elapsed_seconds=time.monotonic()-start,complete=False)
 (out/'probe_receipt.json').write_text(json.dumps(record,ensure_ascii=False,indent=2));print(json.dumps(record,ensure_ascii=False))
asyncio.run(main())
