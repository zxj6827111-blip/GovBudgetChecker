"""将合格原文、年度快照和独立人工决定组合成决算写法依据。"""
from __future__ import annotations
from dataclasses import asdict
import os
import math
import re
from src.services.template_scope import Evidence, HumanDecision, resolve_material_scope
from src.services.annual_templates import ALGORITHM


def copy_pending(snapshot):
    # 单独保留模板能力缺口；原文明示、人工决定及其他检查仍按各自证据运行。
    return dict(snapshot.get('compatibility_pending') or {})


def enabled():
    return os.getenv('GBC_TEMPLATE_SCOPE','0').lower() in ('1','true','yes')


def budget_enabled():
    return enabled() and os.getenv('GBC_BUDGET_TEMPLATE_SCOPE','0').lower() in ('1','true','yes')


def budget_resolution_enabled(snapshot):
    # 位置能力可独立验收；兼容待核须保留回执，但不能越过预算总开关。
    caps=snapshot.get('capability_scope') or {}; pending=snapshot.get('compatibility_pending') or {}
    return bool(budget_enabled() and snapshot.get('available') and
                any(caps.get(k) or pending.get(k) for k in ('writing_auto','position_checks')))


def collect(page_texts, excluded_pages=(), report_kind='final', source_spans=()):
    found=[]
    for page,text in enumerate(page_texts,1):
        if page in excluded_pages: continue
        chars=[(i,c) for i,c in enumerate(text) if not c.isspace()]; flat=''.join(c for _,c in chars)
        patterns=[]
        if page==1:
            patterns=[('declaration','department',r'部门决算(?!单位构成)'),('declaration','unit',r'单位决算')]
        else:
            patterns=[('heading','department',r'(?:20\d{2})?年度部门决算(?:表|情况说明)'),
                      ('heading','unit',r'(?:20\d{2})?年度(?:单位)?决算(?:表|情况说明)'),
                      ('table_note','department',r'本表反映部门本年度[^。\n]{0,100}'),
                      ('table_note','unit',r'本表反映单位本年度[^。\n]{0,100}'),
                      ('declaration','department',r'本报告(?:为|属于)部门决算'),
                      ('declaration','unit',r'本报告(?:为|属于)单位决算')]
        if report_kind=='budget':
            if page==1:
                patterns=[('declaration','department',r'预算主管部门[:：]|部门预算'),('declaration','unit',r'预算单位[:：]|单位预算')]
            else:
                year=r'(?:(?:20\d{2}|20XX)年(?:度)?)?'
                patterns=[]
                for scope,prefix in [('department','部门'),('unit','(?:预算单位|单位)')]:
                    patterns.extend([
                        ('budget_financial_table',scope,year+prefix+r'(?:财务收支|收入|支出|财政拨款收支)预算总表'),
                        ('budget_function_table',scope,year+prefix+r'(?:一般公共预算支出功能分类预算表|政府性基金预算支出功能分类预算表|国有资本经营预算支出功能分类预算表|一般公共预算基本支出部门预算经济分类预算表)')])
        for kind,scope,pattern in patterns:
            for match in re.finditer(pattern,flat):
                start=chars[match.start()][0];end=chars[match.end()-1][0]+1
                before_line=text[text.rfind('\n',0,start)+1:start]
                prefix_flat=re.sub(r'\s+','',before_line)
                if kind in ('declaration','table_note'):
                    if re.search(r'引用|例如|示例|示范|请参考',prefix_flat) or prefix_flat.count('“')>prefix_flat.count('”') or prefix_flat.count('"')%2:continue
                    # 表注只认行首或注/说明标签后的原句，叙述中的引用不算表注。
                    if kind=='table_note' and prefix_flat and not re.fullmatch(r'(?:注|说明)[一二三\d]*[:：]?',prefix_flat):continue
                # 标题限于行首/部分标题/目录位置，避免把正文引用当成独立标题。
                if kind in ('heading','budget_financial_table','budget_function_table'):
                    before=text[text.rfind('\n',0,start)+1:start]
                    after=text[end:].split('\n',1)[0]
                    prefix=re.sub(r'\s+','',before)
                    if prefix and not re.fullmatch(r'(?:第[二三23]部分)?[\u4e00-\u9fff（）()·]{0,80}',prefix): continue
                    if re.search(r'请参考|例如|参见|引用|根据|按上述|本报告采用',prefix): continue
                    if after.strip() and not re.fullmatch(r'[\s.·…\d]*',after): continue
                overlaps=[span for span in source_spans if span.get('qualified',True) and span['page']==page and span['start']<end and span['end']>start]
                # 不跨 OCR 块拼接强信号；保留不足状态，避免断裂或重复块制造证据。
                if overlaps and not any(span['start']<=start and span['end']>=end for span in overlaps):continue
                found.append(Evidence(scope,kind,page,text[start:end],kind,True,start,end,tuple(overlaps)))
    return tuple(found)


def template_applicability(profile, page_texts):
    """仅为模板选择核实原件画像，不改写金额链路已保存的画像。"""
    from src.services.document_profile_resolver import resolve_document_profile
    from src.schemas.document_profile import SOURCE_COVER_TITLE
    original = resolve_document_profile(page_texts=page_texts)
    proof = {'profile_year': profile.report_year.to_dict(),
             'original_year': original.report_year.to_dict(),
             'original_kind': original.report_kind.to_dict(), 'verified': False}
    if any(c.get('field') in ('report_year', 'fiscal_year', 'report_kind', 'jurisdiction')
           for c in [*profile.conflicts, *original.conflicts]):
        return None, {**proof, 'reason': '原件年度、文种或适用范围存在未解决冲突'}
    if original.report_year.source != SOURCE_COVER_TITLE or not original.year:
        return None, {**proof, 'reason': '缺少原件封面报告年度依据，文件名不能单独选模板'}
    if original.kind not in ('final', 'budget') or original.kind != profile.kind:
        return None, {**proof, 'reason': '原件文种与画像不一致或尚未核实'}
    if profile.year is not None and profile.year != original.year:
        return None, {**proof, 'reason': '文件名或画像年度与原件封面相反，须先核实年度'}
    selected = profile.model_copy(deep=True)
    selected.report_year = original.report_year.model_copy(deep=True)
    selected.report_year.rejected.append(profile.report_year.to_dict())
    selected.report_kind = original.report_kind.model_copy(deep=True)
    return selected, {**proof, 'verified': True, 'reason': '原件封面独立核实年度与文种'}


async def load(job_id, file_hash, profile, page_texts, archive_scope=None, excluded_pages=(), source_spans=(), frozen_bindings=None, archive_basis=None):
    from src.db.connection import DatabaseConnection
    from src.services import annual_templates
    snapshot={'available':False,'reason':'模板能力关闭' if not enabled() else '年度、文种或适用依据尚未核实','algorithm':ALGORITHM,'target_year':profile.year,'report_kind':profile.kind}
    selection_profile, applicability = template_applicability(profile, page_texts)
    if not applicability['verified'] and enabled():snapshot['reason']=applicability['reason']
    decision=None
    if os.getenv('DATABASE_URL'):
        conn=await DatabaseConnection.acquire()
        try:
            raw=await conn.fetchrow('''SELECT d.* FROM document_writing_scope_decisions d JOIN analysis_jobs j
                ON j.job_uuid=d.job_uuid AND j.structured_document_version_id=d.document_version_id
                WHERE d.job_uuid=$1 AND d.file_hash=$2 ORDER BY d.revision DESC LIMIT 1''',job_id,file_hash)
            decision=dict(raw) if raw else None
            if enabled() and selection_profile is not None and (profile.kind=='final' or (profile.kind=='budget' and budget_enabled())):
                snapshot=await annual_templates.select_frozen(conn,frozen_bindings,selection_profile,page_texts) if frozen_bindings is not None else await annual_templates.select_for_profile(conn,selection_profile,page_texts)
        finally: await DatabaseConnection.release(conn)
    elif enabled() and os.getenv('GBC_TEMPLATE_EXPORT') and selection_profile is not None and (profile.kind=='final' or (profile.kind=='budget' and budget_enabled())):
        from src.services.annual_templates import offline_for_profile
        snapshot=offline_for_profile(os.environ['GBC_TEMPLATE_EXPORT'],os.getenv('GBC_TEMPLATE_EXPORT_SHA256'),selection_profile,page_texts)
    evidence=collect(page_texts,excluded_pages,profile.kind,source_spans)
    caps=snapshot.get('capability_scope') or {}
    human=HumanDecision(decision['writing_scope'],str(decision['request_id']),False) if decision else None
    # 只有独立正文引用经人工核对时，人工裁决才可用来核验封面。
    if human and decision:
        import json
        saved=decision.get('source_evidence') or []
        if isinstance(saved,str):saved=json.loads(saved)
        independent=any(e.get('dimension')=='human_cover_independent' and e.get('value') is True for e in saved)
        human=HumanDecision(human.value,human.decision_id,independent)
    # 阶段 0：冻结的"上传所选组织层级"只是检查基准（department/unit 才可用）。
    selected_level=(archive_basis or {}).get('level') if isinstance(archive_basis,dict) else None
    resolution=asdict(resolve_material_scope(evidence=evidence,template_qualified=bool(snapshot.get('available') and caps.get('writing_auto')),human=human,archive_scope=archive_scope if archive_scope in ('unit','department') else None,selected_level=selected_level,required_categories=('budget_financial_table','budget_function_table') if profile.kind=='budget' else ('heading','table_note')))
    resolution.update(template_capability_pending=copy_pending(snapshot),template_applicability=applicability,unqualified_ocr_candidates=[x for x in source_spans if x.get('qualified') is False],snapshot=snapshot,algorithm=ALGORITHM,domain=profile.kind,template_enabled=enabled(),decision_revision=decision.get('revision') if decision else None,archive_scope=archive_scope,archive_basis={key:archive_basis.get(key) for key in ('organization_id','organization_name','level','source')} if isinstance(archive_basis,dict) else None,archive_conflict=bool(archive_scope in ('unit','department') and resolution.get('value') and resolution['value']!=archive_scope))
    if decision:
        resolution['human_decision']={key:decision.get(key) for key in
            ('request_id','revision','writing_scope','actor','note','evidence_reference','conflict_acknowledged')}
        resolution['human_decision']['request_id']=str(decision['request_id'])
    if profile.kind=='budget' and not budget_resolution_enabled(snapshot):
        return {}
    return resolution


def ensure_document_resolution(document, profile):
    """独立规则入口也只消费统一裁决；没有运行快照时不访问数据库或猜模板。"""
    if profile.kind!='final' or getattr(profile,'writing_scope',None):return
    snapshot={'available':False,'reason':'独立入口未提供已批准年度快照','algorithm':ALGORITHM,'target_year':profile.year}
    pages=getattr(document,'writing_page_texts',None) or getattr(document,'page_texts',[]) or []
    selection_profile, applicability=template_applicability(profile,pages)
    if not applicability['verified']:snapshot['reason']=applicability['reason']
    if enabled() and os.getenv('GBC_TEMPLATE_EXPORT') and selection_profile is not None:
        from src.services.annual_templates import offline_for_profile
        snapshot=offline_for_profile(os.environ['GBC_TEMPLATE_EXPORT'],os.getenv('GBC_TEMPLATE_EXPORT_SHA256'),selection_profile,pages)
    proof=collect(getattr(document,'writing_page_texts',None) or getattr(document,'page_texts',[]) or [],source_spans=getattr(document,'writing_source_spans',()) or ())
    resolution=asdict(resolve_material_scope(evidence=proof,template_qualified=bool(snapshot.get('available') and (snapshot.get('capability_scope') or {}).get('writing_auto'))))
    resolution.update(template_capability_pending=copy_pending(snapshot),template_applicability=applicability,snapshot=snapshot,algorithm=ALGORITHM)
    profile.writing_scope=resolution
    document.document_profile=profile


def qualified_identity_view(page_texts, artifact):
    """仅构造身份文字视图，不修改数字规则文本、单元格或财政事实。"""
    from src.services.ocr_qualification import bound_ocr_block
    pages=list(page_texts); spans=[]
    for block in getattr(artifact,'text_blocks',[]) or []:
        page=block.page_number
        if block.extraction_method=='ocr' and (type(page) is not int or not 1<=page<=len(pages)):
            spans.append({'page':None,'start':0,'end':0,'qualified':False,'source_method':'unqualified_ocr',
                'source_block_id':block.block_uid,'text':block.text,'reason':'OCR声称页码不属于本原件'})
    for page, native in enumerate(pages,1):
        spans.append({'page':page,'start':0,'end':len(native),'source_method':'native_text'})
        seen={re.sub(r'\s+','',native)}
        for block in getattr(artifact,'text_blocks',[]) or []:
            confidence=block.confidence
            if block.page_number!=page or block.extraction_method!='ocr':continue
            score_ok=(not isinstance(confidence,bool) and isinstance(confidence,(int,float))
                      and math.isfinite(confidence) and .98<=confidence<=1)
            try:bound=score_ok and bound_ocr_block(block,artifact)
            except (AttributeError,KeyError,TypeError,ValueError):bound=False
            if not bound:
                spans.append({'page':page,'start':0,'end':0,'qualified':False,
                    'source_method':'unqualified_ocr','source_block_id':block.block_uid,'text':block.text,
                    'reason':'OCR置信度不合格' if not score_ok else 'OCR未通过同源页面、请求、预测、几何和模型绑定校验'})
                continue
            normalized=re.sub(r'\s+','',block.text)
            if not normalized or any(normalized in existing for existing in seen):continue
            start=len(pages[page-1])+1
            pages[page-1]+='\n'+block.text
            spans.append({'page':page,'start':start,'end':start+len(block.text),'source_method':'ocr',
                'source_block_id':block.block_uid,'bbox':list(block.bbox) if block.bbox else None,
                'location_level':'region' if block.bbox else 'unresolved','confidence':block.confidence})
            seen.add(normalized)
    return pages,spans


def identity_document(document):
    """身份消费者使用同一合格文字；其他规则仍保留原数字视图。"""
    import copy
    pages=getattr(document,'writing_page_texts',None)
    if not pages:return document
    result=copy.copy(document);result.page_texts=pages
    return result


def prepare_identity_document(document):
    from src.schemas.document_profile import DocumentProfile
    from src.services.document_profile_resolver import resolve_document_profile
    value=getattr(document,'document_profile',None)
    if getattr(document,'report_kind',None)=='final' and not getattr(value,'writing_scope',None):
        if not isinstance(value,DocumentProfile):
            value=resolve_document_profile(explicit_report_kind='final',page_texts=getattr(document,'writing_page_texts',None) or document.page_texts)
        ensure_document_resolution(document,value)
    return identity_document(document)
