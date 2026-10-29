"""附加模板规范：核实标题与对应表实例，引用或同页无关表格不构成完成。"""
from __future__ import annotations
import re
from src.engine.checklist_support import compact,iter_pages,issue_at,report_kind
from src.services.template_anchor_slots import instantiate_anchor

_NUMBER=re.compile(r'^(?:第[一二三四五六七八九十百\d]+部分|[一二三四五六七八九十百\d]+[、.．]|[（(][一二三四五六七八九十百\d]+[）)])')


def _title(text):
    return _NUMBER.sub('',compact(text),count=1)


def _headings(doc, expected):
    found=[];target=_title(expected)
    for page,text in iter_pages(doc):
        if page<=1:continue
        lines=text.splitlines(keepends=True);offset=0
        for i,line in enumerate(lines):
            start=offset;offset+=len(line)
            previous=next((compact(p) for p in reversed(lines[:i]) if p.strip()),'')
            if re.search(r'(?:尚未提供|未提供|未编制|详见|参见|引用|例如|提及|缺少)[：:]?$',previous):continue
            # 只匹配整行标题或其连续折行；不查找正文中的子串。
            value=''
            for part in lines[i:i+3]:
                if not part.strip() or re.search(r'[，。；!?！？]$',part.strip()):break
                value+=part.strip()
                current=_title(value)
                if current==target:
                    found.append({'page':page,'start':start,'end':start+len(value),'text':value});break
                if not target.startswith(current):break
    return found


def _matching_table_instances(doc, expected, headings):
    artifact=getattr(doc,'parse_artifact',None)
    if artifact is None:return []
    from ocr_service.protocol import valid_bbox
    fragments={}
    for fragment in artifact.table_fragments:
        fragments.setdefault(fragment.fragment_uid,[]).append(fragment)
    pages={h['page'] for h in headings};matched=[]
    for table in artifact.logical_tables:
        if table.identity_conflict or _title(table.title)!=_title(expected) or not table.fragment_uids:continue
        for uid in table.fragment_uids:
            physical=fragments.get(uid,[])
            if len(physical)!=1:continue
            fragment=physical[0]
            if (fragment.page_number not in pages or not table.page_start<=fragment.page_number<=table.page_end
                or _title(fragment.title)!=_title(expected) or not fragment.rows or not valid_bbox(fragment.bbox)):continue
            if fragment.extraction_method=='ocr':
                # OCR 表不能仅靠重命名后的片段通过：必须绑定真实候选网格与位置。
                from src.services.ocr_qualification import bound_table
                bound=False
                for candidate in artifact.ocr_table_candidates:
                    if candidate.page_number!=fragment.page_number or list(candidate.page_bbox or [])!=list(fragment.bbox):continue
                    canonical,reasons=bound_table(candidate,artifact)
                    if canonical is not None and not reasons and canonical['rows']==fragment.rows:bound=True;break
                if not bound:continue
            matched.append({'instance_uid':table.instance_uid,'fragment_uid':uid,'page':fragment.page_number,'bbox':list(fragment.bbox)})
    return matched


def additional_anchor_issues(rule,doc,kind):
    from src.services.material_writing import identity_document
    from src.services.structure_view import structure_view_for_document
    resolution=getattr(getattr(doc,'document_profile',None),'writing_scope',{}) or {}
    if report_kind(doc)!='final' and resolution.get('domain')!='budget':return [],[]
    absence_ready=structure_view_for_document(doc).get('absence_check_ready')
    doc=identity_document(doc)
    snapshot=resolution.get('snapshot') or {}
    if not snapshot.get('available') or not (snapshot.get('capability_scope') or {}).get('structure'):return [],[]
    config=snapshot.get('effective_config') or (snapshot.get('content') or {}).get('config') or {}
    issues=[];pending=[]
    for anchor in config.get('anchors') or []:
        if anchor.get('kind')!=kind:continue
        scope=anchor.get('scope')
        if scope!='all':
            if resolution.get('status')!='confirmed':
                pending.append('附加模板要求的部门/单位适用条件未核实：'+anchor['text']);continue
            if scope!=resolution.get('value'):continue
        field=getattr(getattr(doc,'document_profile',None),'organization_name',None)
        name=getattr(field,'value',None) if getattr(field,'source',None)!='filename' else None
        try:expected=instantiate_anchor(anchor,anchor.get('source_report_year',snapshot.get('source_report_year')),snapshot.get('target_year'),name)
        except ValueError as exc:
            pending.append(str(exc)+'：'+anchor['text']);continue
        headings=_headings(doc,expected)
        if headings:
            if kind=='normative_table':
                instances=_matching_table_instances(doc,expected,headings)
                if len({i['instance_uid'] for i in instances})!=1:
                    pending.append('标题尚未唯一关联对应表实例及原件位置：'+expected)
            continue
        if not absence_ready:
            pending.append('材料部分页面缺少合格结构证据，尚不能确认模板要求缺失：'+expected);continue
        first=str((doc.page_texts or [''])[0])
        issues.append(issue_at(rule,'缺少经审核的模板要求标题：“'+expected+'”；依据：'+anchor['basis'],1,first,0,len(first.split('\n')[0]),
            severity='warn',checklist_item='E-06' if kind=='normative_table' else 'A-01',document_level=True,
            template_package=snapshot.get('package_id'),target_year=snapshot.get('target_year'),source_report_year=snapshot.get('source_report_year'),
            template_anchor=anchor,expected=expected))
    return issues,pending
