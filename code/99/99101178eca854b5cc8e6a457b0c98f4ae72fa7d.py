"""两份开发原件的真实方向推理；不计入未参与修复的独立验收。"""
from pathlib import Path
import sys,os
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
OUT=ROOT/'artifacts/pdf-input-v2-20261018-orientation'
IMPL=OUT/'implementation'
sys.path.insert(0,str(IMPL))
os.environ['OCR_SERVICE_URL']='http://127.0.0.1:19017'
import asyncio,hashlib,json,io,time,datetime
import fitz,httpx
from PIL import Image
from src.services.ocr_client import check_ocr_health,ocr_base_url
from src.services.ocr_orientation import select_orientation
from src.services.input_geometry import Affine

def hashes():
 return {str(p.relative_to(IMPL)):hashlib.sha256(p.read_bytes()).hexdigest() for name in ('src','api','ocr_service') for p in (IMPL/name).rglob('*.py')}
def write(p,data):p.write_text(json.dumps(data,ensure_ascii=False,indent=2))
async def main():
 out=OUT/'real-orientation';out.mkdir(exist_ok=False)
 before=hashes();write(out/'code-before.json',before)
 cases=[
  {'id':'nanlang_renda_page8','path':str(ROOT/'artifacts/pdf-input-v2-20261018-joint/additional-official-originals-4/OFFICIAL_c866e2344d35/source.pdf'),'page':8,'expected_angle':90,'scope':'development_orientation_original_previously_visually_inspected_excluded_from_blind_holdout'},
  {'id':'cangnan_budget_page3','path':str(ROOT/'artifacts/pdf-input-v2-20261018/official-originals/OFFICIAL_95c2b8afdcbf/source.pdf'),'page':3,'expected_angle':0,'scope':'existing_development_original'},
 ]
 for case in cases:case['source_sha256']=hashlib.sha256(Path(case['path']).read_bytes()).hexdigest()
 write(out/'predeclared-cases.json',{'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cases':cases})
 for attempt in range(12):
  health=await check_ocr_health(timeout_seconds=10)
  if health.get('ok'):break
  await asyncio.sleep(5)
 assert health.get('ok'),health
 assert health['service_url']==ocr_base_url()=='http://127.0.0.1:19017'
 assert health['model_fingerprint']=='9e20de91cfdde807ce68673637b8c4cb2614998f4082bf9c90c569eec66e6cea'
 os.environ['OCR_MODEL_FINGERPRINT']=health['model_fingerprint']
 write(out/'health.json',health)
 requests=[]
 async def guard(request):
  assert request.url.host=='127.0.0.1' and request.url.port==19017,'blocked unexpected endpoint'
  requests.append({'method':request.method,'host':request.url.host,'port':request.url.port,'path':request.url.path})
  write(out/'request-origins.json',requests)
 records=[]
 async with httpx.AsyncClient(event_hooks={'request':[guard]}) as client:
  for case in cases:
   with fitz.open(case['path']) as pdf:
    page=pdf[case['page']-1]
    pix=page.get_pixmap(matrix=fitz.Matrix(300/72,300/72))
    image=Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
    small=page.get_pixmap(matrix=fitz.Matrix(150/72,150/72))
    layout=Image.open(io.BytesIO(small.tobytes('png'))).convert('RGB')
    affine=Affine(a=72/300,d=72/300,e=pix.x*72/300,f=pix.y*72/300)
    start=time.monotonic()
    decision=await select_orientation(image,layout,file_hash=case['source_sha256'],page=case['page'],pixel_to_pdf=affine,
                                     model=health['model_fingerprint'],pipeline=health['pipeline_fingerprint'],client=client,
                                     checkpoint_path=out/(case['id']+'.json'),soft_budget=600)
    record={**case,'status':decision['status'],'chosen_angle':decision.get('angle'),'scores':decision.get('scores'),
            'requests':len(decision['completed']),'seconds':time.monotonic()-start,
            'direction_matches_predeclared':decision.get('angle')==case['expected_angle'] and decision['status']=='resolved',
            'blind_acceptance':False,'amount_or_identity_acceptance':False}
    records.append(record);write(out/'progress.json',records);print(json.dumps(record,ensure_ascii=False),flush=True)
 after=hashes();write(out/'code-after.json',after);assert after==before,'code changed during inference'
 write(out/'receipt.json',{'scope':'genuine_isolated_development_orientation_only','port':19017,'all_requests_endpoint_guarded':True,'records':records,
                          'source_code_unchanged':True,'independent_blind_acceptance':False})
asyncio.run(main())
