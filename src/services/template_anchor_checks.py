"""附加模板义务只做经审核的字面锚点检查；既有清单要求永远保留。"""
from __future__ import annotations
import re
from src.engine.checklist_support import compact,iter_pages,issue_at,report_kind


def additional_anchor_issues(rule,doc,kind):
    from src.services.material_writing import identity_document
    resolution=getattr(getattr(doc,'document_profile',None),'writing_scope',{}) or {}
    if report_kind(doc)!='final' and resolution.get('domain')!='budget':return [],[]
    doc=identity_document(doc)
    resolution=getattr(getattr(doc,'document_profile',None),'writing_scope',{}) or {}
    snapshot=resolution.get('snapshot') or {}
    if not snapshot.get('available') or not (snapshot.get('capability_scope') or {}).get('structure'):return [],[]
    config=snapshot.get('effective_config') or (snapshot.get('content') or {}).get('config') or {}
    body=compact('\n'.join(text for page,text in iter_pages(doc) if page>1))
    issues=[];pending=[]
    for anchor in config.get('anchors') or []:
        if anchor.get('kind')!=kind:continue
        scope=anchor.get('scope')
        if scope!='all':
            if resolution.get('status')!='confirmed':
                pending.append('附加模板要求的部门/单位适用条件未核实：'+anchor['text']);continue
            if scope!=resolution.get('value'):continue
        expected=anchor['text']
        if anchor.get('annual_slot'):
            expected=expected.replace(str(anchor.get('source_report_year',snapshot.get('source_report_year')) or '20XX'),str(snapshot.get('target_year')))
        if anchor.get('organization_slot'):
            field=getattr(getattr(doc,'document_profile',None),'organization_name',None)
            name=getattr(field,'value',None)
            if not name or getattr(field,'source',None)=='filename':
                pending.append('模板名称槽位缺少已核实机构名称：'+expected);continue
            expected=expected.replace(anchor['organization_slot'],name)
        if compact(expected) in body:
            if kind=='normative_table':
                pages=[page for page,text in iter_pages(doc) if page>1 and compact(expected) in compact(text)]
                tables=getattr(doc,'page_tables',[]) or []
                if not any(page<=len(tables) and tables[page-1] for page in pages):
                    pending.append('已匹配表名但表格结构尚未核实：'+expected)
            continue
        if any(not str(t or '').strip() for t in (doc.page_texts or [])[1:]):
            pending.append('材料部分页面缺少合格文本，尚不能确认模板要求缺失：'+expected)
            continue
        first=str((doc.page_texts or [''])[0])
        issues.append(issue_at(rule,'缺少经审核的模板要求：“'+expected+'”；依据：'+anchor['basis'],1,first,0,len(first.split('\n')[0]),
            severity='warn',checklist_item='E-06' if kind=='normative_table' else 'A-01',document_level=True,
            template_package=snapshot.get('package_id'),target_year=snapshot.get('target_year'),source_report_year=snapshot.get('source_report_year'),
            template_anchor=anchor,expected=expected))
    return issues,pending
