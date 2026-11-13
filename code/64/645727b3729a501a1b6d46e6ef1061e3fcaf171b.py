"""显式验证并复用旧真实探针，重放新方向判据；不冒充新的模型推理。"""
from pathlib import Path
import sys
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker');OUT=ROOT/'artifacts/pdf-input-v2-20261018-orientation';IMPL=OUT/'implementation'
sys.path.insert(0,str(IMPL))
import hashlib,json,io,asyncio
from PIL import Image
import fitz
from src.services.ocr_orientation import select_orientation,ORIENTATION_VERSION,probe_boxes
from src.services.ocr_tiles import make_tile,png_bytes,digest,save_checkpoint,_restore_record
from src.services.ocr_preprocessing import rotate
from src.services.input_geometry import Affine
from src.services.ocr_qualification import bound_response
import src.services.ocr_orientation as orientation

def write(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2))
async def no_inference(*args,**kwargs):raise AssertionError('replay is forbidden from new inference')
async def main():
 out=OUT/'orientation-geometry-replay';out.mkdir(exist_ok=False)
 old=OUT/'real-orientation';cases=json.loads((old/'predeclared-cases.json').read_text())['cases'];health=json.loads((old/'health.json').read_text())
 orientation.ocr_page=no_inference;records=[]
 for case in cases:
  path=Path(case['path']);assert hashlib.sha256(path.read_bytes()).hexdigest()==case['source_sha256']
  with fitz.open(path) as pdf:
   page=pdf[case['page']-1]
   pix=page.get_pixmap(matrix=fitz.Matrix(300/72,300/72));image=Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
   pix150=page.get_pixmap(matrix=fitz.Matrix(150/72,150/72));layout=Image.open(io.BytesIO(pix150.tobytes('png'))).convert('RGB')
   affine=Affine(a=72/300,d=72/300,e=pix.x*72/300,f=pix.y*72/300)
   original=(old/(case['id']+'.json')).read_bytes();saved=json.loads(original);unsigned={k:v for k,v in saved.items() if k!='checkpoint_sha256'}
   assert digest(unsigned)==saved['checkpoint_sha256']
   parent=hashlib.sha256(png_bytes(image)).hexdigest();identity={**saved['identity'],'version':ORIENTATION_VERSION}
   assert identity['parent']==parent and identity['boxes']==probe_boxes(image,layout)
   assert identity['model']==health['model_fingerprint'] and identity['pipeline']==health['pipeline_fingerprint']
   assert identity['file_hash']==case['source_sha256'] and identity['pixel_to_pdf']==affine.values()
   for record in saved['completed'].values():
    context=record['context'];crop_op=context['preprocessing'][0];box=crop_op['source_crop_box_px']
    rotations=[op['degrees_counterclockwise'] for op in context['preprocessing'] if op['operation']=='rotate'];angle=rotations[-1] if rotations else 0
    crop,inverse,operations=rotate(image.crop(box),angle)
    expected=make_tile(crop,(0,0,*crop.size),parent_sha=hashlib.sha256(png_bytes(crop)).hexdigest(),file_hash=case['source_sha256'],page=case['page'],pixel_to_pdf=inverse.then(Affine(e=box[0],f=box[1])).then(affine),root_image_sha256=parent,operations=[crop_op,*operations])
    assert expected.context==context,'recreated probe is not byte/geometrically identical'
    result=_restore_record(record,context);assert result is not None and result.ok
    assert result.provenance['tile_context']==context and bound_response(result.provenance) is not None
   checkpoint=out/(case['id']+'.json')
   migrated={'identity':identity,'completed':saved['completed'],'status':'pending','migration':{'source_checkpoint_sha256':hashlib.sha256(original).hexdigest(),'source_version':saved['identity']['version'],'new_inference':False,'input_png_and_transforms_recreated_and_verified':True}}
   save_checkpoint(checkpoint,migrated)
   decision=await select_orientation(image,layout,file_hash=case['source_sha256'],page=case['page'],pixel_to_pdf=affine,model=health['model_fingerprint'],pipeline=health['pipeline_fingerprint'],client=None,checkpoint_path=checkpoint)
   result={**case,'chosen_angle':decision['angle'],'status':decision['status'],'scores':decision['scores'],'direction_matches_predeclared':decision['angle']==case['expected_angle'] and decision['status']=='resolved','original_records_verified':len(saved['completed']),'new_inference':False,'blind_acceptance':False}
   records.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
 write(out/'receipt.json',{'scope':'verified_saved_real_ocr_probe_replay','decision_version':ORIENTATION_VERSION,'records':records,'new_inference':False,'all_expected_directions_match':all(r['direction_matches_predeclared'] for r in records),'full_amount_identity_acceptance':False})
asyncio.run(main())
