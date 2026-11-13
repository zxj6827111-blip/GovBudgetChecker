"""v8 首轮布局读法的真实开发页验证；保留 v5 失败断点，不重置其补读计数。"""
from pathlib import Path
import os,sys
BASE=Path(__file__).resolve().parent;ROOT=BASE.parents[1];RUNTIME=BASE/'wide-paragraph-runtime-v10'
sys.path.insert(0,str(RUNTIME));os.environ['OCR_SERVICE_URL']='http://127.0.0.1:19017'
import asyncio,io,hashlib,json,time
import fitz,httpx
from PIL import Image
from src.services.ocr_client import check_ocr_health,ocr_base_url,ocr_page
from src.services.ocr_tiles import tile_boxes,make_tile,png_bytes,TILING_VERSION
from src.services.input_geometry import Affine
from src.services.ocr_tiled_pipeline import append_tile_results
from src.services.fiscal_source_profile import source_document_profile
from src.schemas.parse_artifact import ParseArtifact
OUT=BASE/'wide-source-paragraph-v10';OUT.mkdir(exist_ok=False)
SOURCE=ROOT/'artifacts/pdf-input-v2-20261018-joint/additional-official-originals-4/OFFICIAL_c866e2344d35/source.pdf'
def write(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2))
def hashes():return {str(p.relative_to(RUNTIME)):hashlib.sha256(p.read_bytes()).hexdigest() for p in RUNTIME.rglob('*.py')}
async def main():
    before=hashes();write(OUT/'code-before.json',before)
    health=await check_ocr_health(timeout_seconds=10)
    assert health.get('ok') and health['service_url']==ocr_base_url()=='http://127.0.0.1:19017'
    assert health['model_fingerprint']=='9e20de91cfdde807ce68673637b8c4cb2614998f4082bf9c90c569eec66e6cea'
    os.environ['OCR_MODEL_FINGERPRINT']=health['model_fingerprint'];write(OUT/'health.json',health)
    source_hash=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert source_hash=='c866e2344d35ee37b99df5ad54a0370e7b5d24bea515550dc590fc38baa96ad8'
    with fitz.open(SOURCE) as pdf:
        pix=pdf[0].get_pixmap(matrix=fitz.Matrix(300/72,300/72))
        image=Image.open(io.BytesIO(pix.tobytes('png'))).convert('RGB')
        affine=Affine(a=72/300,d=72/300,e=pix.x*72/300,f=pix.y*72/300)
    parent=hashlib.sha256(png_bytes(image)).hexdigest()
    boxes=[box for box in tile_boxes(image) if box[2]-box[0]>960 and box[3]-box[1]<640]
    assert len(boxes)==1,'mechanism expected one complete source heading strip'
    write(OUT/'predeclared-plan.json',{'source_sha256':source_hash,'page':1,'tiling_version':TILING_VERSION,'boxes':boxes,'unscaled_original_rgb':True,'role':'new_version_first_pass_layout_probe','prior_v5_checkpoint_preserved':True,'blind_acceptance':False,'independent_amount_acceptance':False})
    origins=[]
    async def guard(request):
        assert request.url.host=='127.0.0.1' and request.url.port==19017
        origins.append({'host':request.url.host,'port':request.url.port,'path':request.url.path});write(OUT/'origins.json',origins)
    artifact=ParseArtifact(source_hash,page_count=49,ocr_model_fingerprint=health['model_fingerprint'])
    async with httpx.AsyncClient(event_hooks={'request':[guard]}) as client:
        for index,box in enumerate(boxes):
            request=make_tile(image,box,parent_sha=parent,file_hash=source_hash,page=1,pixel_to_pdf=affine,root_image_sha256=parent)
            write(OUT/f'request-{index}.json',request.context)
            (OUT/f'original-source-crop-{index}.png').write_bytes(request.image)
            started=time.monotonic()
            response=await ocr_page(client,image_png=request.image,page_number=1,timeout_seconds=120,tile_context=request.context)
            write(OUT/f'response-{index}.json',response.to_dict())
            assert response.ok,response.error
            append_tile_results(artifact,[(request.context,response)],page_number=1,layout_sha='layout_not_needed_for_this_bound_source_crop',page_recovery={},page_render={'dpi':300,'original_parent_image_sha256':parent})
            write(OUT/'elapsed.json',{'seconds':time.monotonic()-started})
    profile=source_document_profile(artifact)
    write(OUT/'artifact.json',artifact.to_dict());write(OUT/'source-profile.json',profile)
    after=hashes();assert after==before;write(OUT/'code-after.json',after)
    write(OUT/'receipt.json',{'source_code_unchanged':True,'all_inference_endpoint_guarded':True,'source_hash_unchanged':hashlib.sha256(SOURCE.read_bytes()).hexdigest()==source_hash,'prior_run_counters_not_reset':True,'requests':len(origins),'profile_recovered':bool(profile['org_name'] and profile['report_year'] and profile['report_kind']),'report_year':profile['report_year'],'org_name':profile['org_name'],'report_kind':profile['report_kind'],'blind_acceptance':False,'amount_identity_acceptance':False})
    print(json.dumps({'profile':{k:profile[k] for k in ['org_name','report_year','report_kind','reasons']},'requests':len(origins)},ensure_ascii=False),flush=True)
asyncio.run(main())
