"""模板维护入口；普通账号不能通过修改模板改变检查门槛。"""
from __future__ import annotations
import asyncio
import tempfile
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from api.auth_utils import require_admin
from api.material_access import with_connection as db_connection
from uuid import UUID
import asyncpg

async def with_connection(callback):
    try:
        return await db_connection(callback)
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(409,"记录已存在或修订冲突，请刷新") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422,"原件、标识或输入数据无效；请核对后重试") from exc
from src.services import annual_templates as service
from src.services.disclosure_template_import import SUPPORTED

router=APIRouter(prefix='/api/admin/templates')

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')
class Pair(Strict):
    unit_source_id: UUID
    department_source_id: UUID
    previous_package_id: UUID|None=None
class Review(Strict):
    revision:int=Field(gt=0)
    basis:str=Field(min_length=3,max_length=2000)
    visual_verified:bool
    decision:Literal['same','new','pending']
    slot_reviews:dict[str,list[str]]=Field(default_factory=dict)
    anchors:list[dict]=Field(default_factory=list,max_length=100)
    accept_native_toc_variant:bool=False
class Publish(Strict):
    revision:int=Field(gt=0)
    version:str=Field(min_length=1,max_length=100)
    capabilities:dict[str,bool]
    validation:dict
class Bind(Strict):
    region:str=Field(min_length=1,max_length=100)
    overlay:str=Field(default='',max_length=100)
    report_kind:Literal['budget','final']
    target_year:int=Field(ge=1900,le=2100)
    package_id:UUID
    expected_revision:int=Field(ge=0)
    adoption_kind:Literal['issued','verified_same','not_received','historical_reference','rollback']
    capabilities:dict[str,bool]
    basis:str=Field(min_length=3,max_length=2000)
    compatibility_reference:str|None=None
    compatibility:dict|None=None
    restore_revision:int|None=None
    source_draft_id:str|None=None


def actor(request):
    _,_,user=require_admin(request)
    return str(user.get('username') or 'unknown')

@router.get('')
async def catalog(request:Request):
    actor(request)
    return await with_connection(service.catalog)

@router.post('/sources')
async def upload(request:Request, file:UploadFile=File(...), report_kind:Literal['final','budget']=Form(...),
                 material_type:Literal['unit','department']=Form(...), source_report_year:int|None=Form(None),
                 issuer:str=Form(...), description:str=Form(...), issue_date:str=Form(''), release_reference:str=Form('')):
    who=actor(request)
    if len(issuer)>300 or len(description)>4000 or len(release_reference)>2000:raise HTTPException(422,'来源说明超过长度限制')
    if issue_date:
        from datetime import date
        try:date.fromisoformat(issue_date)
        except ValueError:raise HTTPException(422,'下发日期格式应为 YYYY-MM-DD')
    if (source_report_year is not None and not 1900<=source_report_year<=2100) or len(description.strip())<3 or len(issuer.strip())<2: raise HTTPException(422,'来源信息不完整')
    suffix=Path(file.filename or '').suffix.lower()
    if suffix not in SUPPORTED: raise HTTPException(422,'支持 DOC、DOCX、PDF、XLS；WPS 尚未验证')
    # 流式限制上传，不执行宏；临时文件与受管原件名称均由服务端生成。
    with tempfile.TemporaryDirectory(prefix='gbc-template-') as temp:
        target=Path(temp)/('upload'+suffix); count=0
        with target.open('wb') as handle:
            while chunk:=await file.read(1024*1024):
                count+=len(chunk)
                if count>20*1024*1024: raise HTTPException(413,'模板原件超过20MB')
                handle.write(chunk)
        values=dict(original_filename=Path(file.filename or "").name,report_kind=report_kind,material_type=material_type,source_report_year=source_report_year,issuer=issuer,description=description,issue_date=issue_date)
        return await with_connection(lambda conn:service.register_source(conn,path=target,metadata={**values,'release_reference':release_reference},actor=who))

@router.get('/sources/{identifier}')
async def original(identifier:str, request:Request):
    actor(request)
    data,manifest,directory=await with_connection(lambda conn:service.source(conn,identifier))
    return FileResponse(directory/manifest['original'],filename='template'+Path(manifest['original']).suffix)

@router.post('/drafts')
async def draft(body:Pair,request:Request):
    who=actor(request)
    return await with_connection(lambda conn:service.create_draft(conn,body.model_dump(mode='json'),who))

@router.post('/drafts/{identifier}/review')
async def review(identifier:str,body:Review,request:Request):
    who=actor(request); values=body.model_dump(mode='json'); revision=values.pop('revision')
    return await with_connection(lambda conn:service.review(conn,identifier,revision,values,who))

@router.post('/drafts/{identifier}/publish')
async def publish(identifier:str,body:Publish,request:Request):
    who=actor(request); values=body.model_dump(mode='json'); revision=values.pop('revision')
    return await with_connection(lambda conn:service.publish(conn,identifier,revision,values,who))

@router.post('/bindings')
async def bind(body:Bind,request:Request):
    who=actor(request)
    return await with_connection(lambda conn:service.bind(conn,body.model_dump(mode='json'),who))

@router.get('/export')
async def export(request:Request):
    actor(request)
    return {'schema':'gbc.template-export.v1',**await with_connection(service.catalog)}

@router.get('/impact')
async def impact(request:Request,package_id:str|None=None,target_year:int|None=None,
                 binding_revision:int|None=None,algorithm:str|None=None,capability:str|None=None,
                 report_kind:Literal['final','budget']|None=None):
    actor(request)
    from api import runtime
    def scan():
        import json
        records=[]; unreadable=[]
        for path in runtime.UPLOAD_ROOT.glob('*/status.json'):
            try:
                payload=json.loads(path.read_text()); resolution=((payload.get('result') or {}).get('meta') or {}).get('document_scope_resolution') or {}
                snap=resolution.get('snapshot') or {}
                meta=(payload.get('result') or {}).get('meta') or {}
                profile=meta.get('document_profile') or {}
                kind=(profile.get('report_kind') or {}).get('value') or payload.get('report_kind')
                year=snap.get('target_year') or (profile.get('report_year') or {}).get('value') or payload.get('report_year')
                if kind not in ('final','budget') or (package_id and snap.get('package_id') and snap.get('package_id')!=package_id and (snap.get('municipal_reference') or {}).get('package_id')!=package_id) or (target_year and year!=target_year): continue
                legacy=not resolution
                if report_kind and kind!=report_kind:continue
                if binding_revision is not None and snap.get('revision') is not None and snap['revision']!=binding_revision:continue
                if algorithm and resolution.get('algorithm') and resolution['algorithm']!=algorithm:continue
                if capability and snap.get('capability_scope') and not snap['capability_scope'].get(capability):continue
                records.append({'job_id':path.parent.name,'report_kind':kind,'package_id':snap.get('package_id'),'binding_revision':snap.get('revision'),
                                'target_year':year,'capability_scope':snap.get('capability_scope'),'legacy_requires_review':legacy,'algorithm':resolution.get('algorithm') or 'legacy-without-writing-snapshot', 'source':resolution.get('source') or 'legacy'})
            except (OSError,ValueError):unreadable.append(path.parent.name)
        return {'items':records,'unreadable_job_ids':unreadable,'complete':not unreadable,'automatic_reanalysis':False}
    return await asyncio.to_thread(scan)


@router.get('/export-package')
async def export_package(request:Request):
    actor(request)
    target=await with_connection(service.export_bundle)
    return FileResponse(target,filename='annual-template-audit.zip',media_type='application/zip')
