"""附加模板规范：核实标题与对应表实例，引用或同页无关表格不构成完成。

目录区排除（2026-10-31 修复 outputs/tpl_trial_review_20261031/review.json F2）：
``is_toc_page`` 只识别带"目录"表头的页，目录续页（无表头）上的同文条目曾被
当作正文命中，掩盖正文缺章节。``toc_region_pages`` 从"目录"表头页向后传播：
连续页若无叙述句读且行行都是编号条目/点线页码形态，仍视为目录区；遇到第一页
不具备该形态即停止（正文紧随目录之后，故传播不会吞掉正文标题页）。目录区内
命中不计为正文证据；点线引导/页码尾巴式条目行另在行级排除。正文折行标题的
既有正例行为不回退。

边界提示（如实记录）：若某正文页紧邻目录续页、且整页只有一个孤立的编号标题
而无任何其他文字，它会被并入目录区；本语料（2025 决算样本）不存在该形态，
``run_template_trial`` 的目录三例对照在真实样本上验证了传播边界。
"""
from __future__ import annotations
import re
from typing import Iterable, List, Set, Tuple
from src.engine.checklist_support import compact,is_toc_page,iter_pages,issue_at,report_kind
from src.services.template_anchor_slots import instantiate_anchor

_NUMBER=re.compile(r'^(?:第[一二三四五六七八九十百\d]+部分|[一二三四五六七八九十百\d]+[、.．]|[（(][一二三四五六七八九十百\d]+[）)])')

# --- 目录条目形态（中文正则集中区；与 checklist_support._TOC_LEADER_RE 同口径） ---
_TOC_ENTRY_NUMBER_RE=re.compile(r'^(?:第[一二三四五六七八九十百\d]+部分|[一二三四五六七八九十百\d]+[、.．]|[（(][一二三四五六七八九十百\d]+[）)])')
_TOC_LEADER_DOTS_RE=re.compile(r'(?:\.{4,}|…{2,}|·{4,}|_{6,})')
_TOC_PAGE_TAIL_RE=re.compile(r'(?:[.．…·]{2,}|\s)\d{1,4}\s*$')


def is_toc_entry_line(line) -> bool:
    """行级目录条目：点线引导行，或"编号条目+页码尾巴"行（如"一、概况……12"）。

    正文标题不带页码尾巴，不会被该判据排除；判据只用于兜底，页级排除见
    :func:`toc_region_pages`。
    """
    stripped=str(line or '').strip()
    if not stripped:
        return False
    if _TOC_LEADER_DOTS_RE.search(stripped):
        return True
    if not _TOC_ENTRY_NUMBER_RE.match(compact(stripped)):
        return False
    return bool(_TOC_PAGE_TAIL_RE.search(stripped))


def _toc_entry_page(text) -> bool:
    """目录条目页形态：非空行全是编号条目/页码行，且整页无叙述句读。"""
    lines=[line.strip() for line in str(text or '').splitlines() if line.strip()]
    if not lines:
        return False
    for line in lines:
        flat_line=compact(line)
        if _TOC_ENTRY_NUMBER_RE.match(flat_line):
            continue
        if _TOC_LEADER_DOTS_RE.search(flat_line) or (flat_line.isdigit() and len(flat_line) <= 4):
            continue
        return False
    # 目录条目不含句读；叙述句（。，；等）一出现即不再是目录形态。
    return not re.search(r'[，。；：！？]', ''.join(lines))


def toc_region_pages(pages: Iterable[str]) -> Set[int]:
    """从"目录"表头页向后传播的目录区页集合（1 起始页码）。

    is_toc_page 识别表头页；其后连续的目录条目形态页（目录续页）并入目录区；
    遇到第一页不具备该形态即停止传播（再次出现"目录"表头页可重新开区）。
    """
    region: Set[int] = set()
    run = False
    for index, text in enumerate(pages, start=1):
        text = str(text or '')
        if not text.strip():
            continue
        if is_toc_page(text):
            region.add(index)
            run = True
            continue
        if run and _toc_entry_page(text):
            region.add(index)
            continue
        run = False
    return region


def _title(text):
    return _NUMBER.sub('',compact(text),count=1)


def _heading_context(lines, index):
    # 独占编号属于同一标题块；后续行重入也须看到编号前的引用或否定语境。
    for j in range(index - 1, -1, -1):
        text = compact(lines[j])
        if text and not _NUMBER.fullmatch(text):
            return text
    return ''


def _headings(doc, expected):
    found=[]
    target=_title(expected)
    # 目录区（含目录续页）内的同文条目不充当正文证据（review F2 修复）。
    toc_pages=toc_region_pages(list(getattr(doc,'page_texts',None) or []))
    for page,text in iter_pages(doc):
        if page<=1 or page in toc_pages:continue
        lines=text.splitlines(keepends=True)
        offset=0
        for i,line in enumerate(lines):
            start=offset
            offset+=len(line)
            if is_toc_entry_line(line):continue
            previous=_heading_context(lines,i)
            if re.search(r'(?:尚未提供|未提供|未编制|详见|参见|引用|例如|提及|缺少)[：:]?$',previous):continue
            # 连续折行只要仍是完整标题的前缀就继续，行数不能充当缺失证据。
            # 空行、句末标点或无关文字仍中止；不跨页、不查找正文中的子串。
            value=''
            raw_end=start
            for j in range(i,len(lines)):
                part=lines[j]
                if not part.strip() or re.search(r'[，。；!?！？]$',part.strip()):break
                value+=part.strip()
                current=_title(value)
                if current==target:
                    found.append({'page':page,'start':start+len(line)-len(line.lstrip()),
                    'end':raw_end+len(part.rstrip()),'text':value})
                    break
                if not target.startswith(current):break
                raw_end+=len(part)
    return found


def _matching_table_instances(doc, expected, headings):
    artifact=getattr(doc,'parse_artifact',None)
    if artifact is None:return []
    from ocr_service.protocol import valid_bbox
    fragments={}
    for fragment in artifact.table_fragments:
        fragments.setdefault(fragment.fragment_uid,[]).append(fragment)
    pages={h['page'] for h in headings}
    matched=[]
    for table in artifact.logical_tables:
        if table.identity_conflict or _title(table.title)!=_title(expected) or not table.fragment_uids:continue
        for uid in table.fragment_uids:
            physical=fragments.get(uid,[])
            if len(physical)!=1:continue
            fragment=physical[0]
            # page_text_fallback 是“整页纯文本占位表”，无原格证据（解析器同语）：
            # 事实投影/层级检查等全部消费者都排除它，标题-实例匹配同样不算合格表。
            if fragment.extraction_method=='page_text_fallback':continue
            if (fragment.page_number not in pages or not table.page_start<=fragment.page_number<=table.page_end
                or _title(fragment.title)!=_title(expected) or not fragment.rows or not valid_bbox(fragment.bbox)):continue
            if fragment.extraction_method=='ocr':
                # OCR 表不能仅靠重命名后的片段通过：必须绑定真实候选网格与位置。
                from src.services.ocr_qualification import bound_table
                bound=False
                for candidate in artifact.ocr_table_candidates:
                    if candidate.page_number!=fragment.page_number or list(candidate.page_bbox or [])!=list(fragment.bbox):continue
                    canonical,reasons=bound_table(candidate,artifact)
                    if canonical is not None and not reasons and canonical['rows']==fragment.rows:
                        bound=True
                        break
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
    issues=[]
    pending=[]
    for anchor in config.get('anchors') or []:
        if anchor.get('kind')!=kind:continue
        scope=anchor.get('scope')
        if scope!='all':
            if resolution.get('status')!='confirmed':
                pending.append('附加模板要求的部门/单位适用条件未核实：'+anchor['text'])
                continue
            if scope!=resolution.get('value'):continue
        field=getattr(getattr(doc,'document_profile',None),'organization_name',None)
        name=getattr(field,'value',None) if getattr(field,'source',None)!='filename' else None
        try:expected=instantiate_anchor(anchor,anchor.get('source_report_year',snapshot.get('source_report_year')),snapshot.get('target_year'),name)
        except ValueError as exc:
            pending.append(str(exc)+'：'+anchor['text'])
            continue
        headings=_headings(doc,expected)
        if headings:
            if kind=='normative_table':
                instances=_matching_table_instances(doc,expected,headings)
                if len({i['instance_uid'] for i in instances})!=1:
                    pending.append('标题尚未唯一关联对应表实例及原件位置：'+expected)
            continue
        if not absence_ready:
            pending.append('材料部分页面缺少合格结构证据，尚不能确认模板要求缺失：'+expected)
            continue
        first=str((doc.page_texts or [''])[0])
        issues.append(issue_at(rule,'缺少经审核的模板要求标题：“'+expected+'”；依据：'+anchor['basis'],1,first,0,len(first.split('\n')[0]),
            severity='warn',checklist_item='E-06' if kind=='normative_table' else 'A-01',document_level=True,
            template_package=snapshot.get('package_id'),target_year=snapshot.get('target_year'),source_report_year=snapshot.get('source_report_year'),
            template_anchor=anchor,expected=expected))
    return issues,pending
