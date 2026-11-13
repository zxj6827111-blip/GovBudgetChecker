"""开发原件的专属 v3 实推理与断点续跑，绝不计入盲测。"""
from pathlib import Path
import sys,os
BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]
RUNTIME=BASE/'income-capture-runtime-v13'
sys.path.insert(0,str(RUNTIME))
os.environ['OCR_SERVICE_URL']='http://127.0.0.1:19017'
import asyncio,hashlib,json,time,datetime
import httpx
from src.services.fiscal_input_builder import FiscalInputBuilder
from src.services.ocr_client import check_ocr_health,ocr_base_url
from src.services.ocr_tiled_pipeline import recognize_document_tiles
from src.services.fiscal_source_profile import source_document_profile

OUT=BASE/'income-capture-v13';OUT.mkdir(exist_ok=False)
SOURCE=ROOT/'artifacts/pdf-input-v2-20261018-joint/additional-official-originals-4/OFFICIAL_c866e2344d35/source.pdf'
def write(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2))
def hashes():return {str(p.relative_to(RUNTIME)):hashlib.sha256(p.read_bytes()).hexdigest() for p in RUNTIME.rglob('*') if p.is_file() and p.suffix!='.pyc'}
async def main():
    before=hashes();write(OUT/'code-before.json',before)
    assert ocr_base_url()=='http://127.0.0.1:19017'
    source_hash=hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert source_hash=='c866e2344d35ee37b99df5ad54a0370e7b5d24bea515550dc590fc38baa96ad8'
    origins=[]
    original_client=httpx.AsyncClient
    async def guard(request):
        assert request.url.host=='127.0.0.1' and request.url.port==19017,'unexpected inference origin blocked'
        origins.append({'method':request.method,'host':request.url.host,'port':request.url.port,'path':request.url.path})
        write(OUT/'request-origins.json',origins)
    def guarded_client(*args,**kwargs):
        hooks=kwargs.setdefault('event_hooks',{})
        hooks['request']=[*hooks.get('request',[]),guard]
        return original_client(*args,**kwargs)
    httpx.AsyncClient=guarded_client
    health=await check_ocr_health(timeout_seconds=10)
    assert health.get('ok') and health['service_url']==ocr_base_url()
    assert health['model_fingerprint']=='9e20de91cfdde807ce68673637b8c4cb2614998f4082bf9c90c569eec66e6cea'
    os.environ['OCR_MODEL_FINGERPRINT']=health['model_fingerprint']
    write(OUT/'health.json',health)
    write(OUT/'scope.json',{'source_sha256':source_hash,'pages':list(range(7,15)),'scope':'development_original','blind_acceptance':False,'source_page_8_previously_inspected':True,'declared_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    builder=FiscalInputBuilder();artifact=builder.read_native(SOURCE)
    from src.schemas.parse_artifact import ParseArtifact
    saved=BASE/'wide-source-paragraph-v10/artifact.json'
    cover=ParseArtifact.from_dict(json.loads(saved.read_text()))
    assert cover.source_file_hash==artifact.source_file_hash
    artifact.coordinate_transforms.update(cover.coordinate_transforms)
    artifact.source_fragments.extend(cover.source_fragments)
    artifact.text_blocks.extend(cover.text_blocks)
    write(OUT/'saved-cover-cache.json',{'source':str(saved),'sha256':hashlib.sha256(saved.read_bytes()).hexdigest(),'new_cover_inference':False})
    summaries=[];pending=list(range(7,15));started=time.monotonic()
    for iteration in range(32):
        async def progress(item):
            write(OUT/'progress.json',{'iteration':iteration,'progress':item,'requests':len(origins),'elapsed':time.monotonic()-started})
        summary=await recognize_document_tiles(SOURCE,pending,artifact,health,checkpoint_path=OUT/'checkpoint',progress_callback=progress)
        summaries.append(summary)
        write(OUT/'summaries.json',summaries)
        write(OUT/'artifact.json',artifact.to_dict())
        facts=builder.facts(artifact)
        write(OUT/'facts.json',facts.to_dict())
        write(OUT/'source-profile.json',source_document_profile(artifact))
        write(OUT/'logical-diagnostics.json',artifact.reconstruction_diagnostics)
        print(json.dumps({'iteration':iteration,'status':summary['status'],'pending':summary['failed_pages'],'requests':len(origins),'facts':len(facts.facts),'definite_facts':len(facts.definite_facts()),'elapsed':round(time.monotonic()-started,2)}),flush=True)
        if summary['status']!='pending':break
        pending=summary['failed_pages']
    after=hashes();write(OUT/'code-after.json',after)
    assert before==after,'runtime code changed during inference'
    write(OUT/'receipt.json',{'all_inference_endpoint_guarded':True,'port':19017,'source_code_unchanged':True,'iterations':len(summaries),'status':summaries[-1]['status'],'requests':len(origins),'elapsed_seconds':time.monotonic()-started,'facts':len(facts.facts),'definite_facts':len(facts.definite_facts()),'blind_acceptance':False,'amount_identity_independent_acceptance':False})
asyncio.run(main())
