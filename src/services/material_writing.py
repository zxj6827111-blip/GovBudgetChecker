"""将合格原文、年度快照和独立人工决定组合成决算写法依据。"""
from __future__ import annotations
from dataclasses import asdict
import os
import math
import re
from src.services.template_scope import Evidence, HumanDecision, resolve_material_scope
from src.services.annual_templates import ALGORITHM


def enabled():
    return os.getenv('GBC_TEMPLATE_SCOPE','0').lower() in ('1','true','yes')


def budget_enabled():
    return enabled() and os.getenv('GBC_BUDGET_TEMPLATE_SCOPE','0').lower() in ('1','true','yes')


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
                overlaps=[span for span in source_spans if span['page']==page and span['start']<end and span['end']>start]
                # 不跨 OCR 块拼接强信号；保留不足状态，避免断裂或重复块制造证据。
                if overlaps and not any(span['start']<=start and span['end']>=end for span in overlaps):continue
                found.append(Evidence(scope,kind,page,text[start:end],kind,True,start,end,tuple(overlaps)))
    return tuple(found)


async def load(job_id, file_hash, profile, page_texts, archive_scope=None, excluded_pages=(), source_spans=(), frozen_bindings=None):
    from src.db.connection import DatabaseConnection
    from src.services import annual_templates
    snapshot={'available':False,'reason':'模板能力关闭' if not enabled() else '年度、文种或适用依据尚未核实','algorithm':ALGORITHM,'target_year':profile.year,'report_kind':profile.kind}
    decision=None
    if os.getenv('DATABASE_URL'):
        conn=await DatabaseConnection.acquire()
        try:
            raw=await conn.fetchrow('''SELECT d.* FROM document_writing_scope_decisions d JOIN analysis_jobs j
                ON j.job_uuid=d.job_uuid AND j.structured_document_version_id=d.document_version_id
                WHERE d.job_uuid=$1 AND d.file_hash=$2 ORDER BY d.revision DESC LIMIT 1''',job_id,file_hash)
            decision=dict(raw) if raw else None
            if (profile.kind=='final' or (profile.kind=='budget' and budget_enabled())) and enabled() and profile.year and not any(c.get('field') in ('report_year','fiscal_year','report_kind') for c in profile.conflicts) and profile.report_year.source!='filename' and profile.report_kind.source!='filename':
                snapshot=await annual_templates.select_frozen(conn,frozen_bindings,profile,page_texts) if frozen_bindings is not None else await annual_templates.select_for_profile(conn,profile, page_texts)
        finally: await DatabaseConnection.release(conn)
    elif enabled() and os.getenv('GBC_TEMPLATE_EXPORT') and profile.kind in ('final','budget') and (profile.kind!='budget' or budget_enabled()) and profile.year and profile.report_year.source!='filename' and not profile.conflicts:
        from src.services.annual_templates import offline_for_profile
        snapshot=offline_for_profile(os.environ['GBC_TEMPLATE_EXPORT'],os.getenv('GBC_TEMPLATE_EXPORT_SHA256'),profile,page_texts)
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
    resolution=asdict(resolve_material_scope(evidence=evidence,template_qualified=bool(snapshot.get('available') and caps.get('writing_auto')),human=human,archive_scope=archive_scope if archive_scope in ('unit','department') else None, required_categories=('budget_financial_table','budget_function_table') if profile.kind=='budget' else ('heading','table_note')))
    resolution.update(snapshot=snapshot,algorithm=ALGORITHM,domain=profile.kind,template_enabled=enabled(),decision_revision=decision.get('revision') if decision else None,archive_scope=archive_scope,archive_conflict=bool(archive_scope in ('unit','department') and resolution.get('value') and resolution['value']!=archive_scope))
    if decision:
        resolution['human_decision']={key:decision.get(key) for key in
            ('request_id','revision','writing_scope','actor','note','evidence_reference','conflict_acknowledged')}
        resolution['human_decision']['request_id']=str(decision['request_id'])
    if profile.kind=='budget' and not (budget_enabled() and snapshot.get('available') and caps.get('writing_auto')):
        return {}
    return resolution


def ensure_document_resolution(document, profile):
    """独立规则入口也只消费统一裁决；没有运行快照时不访问数据库或猜模板。"""
    if profile.kind!='final' or getattr(profile,'writing_scope',None):return
    snapshot={'available':False,'reason':'独立入口未提供已批准年度快照','algorithm':ALGORITHM,'target_year':profile.year}
    if enabled() and os.getenv('GBC_TEMPLATE_EXPORT') and profile.year and profile.report_year.source!='filename' and not profile.conflicts:
        from src.services.annual_templates import offline_for_profile
        snapshot=offline_for_profile(os.environ['GBC_TEMPLATE_EXPORT'],os.getenv('GBC_TEMPLATE_EXPORT_SHA256'),profile,getattr(document,'page_texts',[]) or [])
    proof=collect(getattr(document,'writing_page_texts',None) or getattr(document,'page_texts',[]) or [],source_spans=getattr(document,'writing_source_spans',()) or ())
    resolution=asdict(resolve_material_scope(evidence=proof,template_qualified=bool(snapshot.get('available') and (snapshot.get('capability_scope') or {}).get('writing_auto'))))
    resolution.update(snapshot=snapshot,algorithm=ALGORITHM)
    profile.writing_scope=resolution
    document.document_profile=profile


def qualified_identity_view(page_texts, artifact):
    """仅构造身份文字视图，不修改数字规则文本、单元格或财政事实。"""
    pages=list(page_texts); spans=[]
    for page, native in enumerate(pages,1):
        spans.append({'page':page,'start':0,'end':len(native),'source_method':'native_text'})
        seen={re.sub(r'\s+','',native)}
        for block in getattr(artifact,'text_blocks',[]) or []:
            confidence=block.confidence
            if (block.page_number!=page or block.extraction_method!='ocr' or isinstance(confidence,bool)
                or not isinstance(confidence,(int,float)) or not math.isfinite(confidence) or not .98<=confidence<=1):continue
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
