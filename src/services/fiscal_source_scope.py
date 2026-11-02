"""只按原件封面、原表题，或目录与各部分标题的明确文字绑定主体范围。"""
import re

#: 决算：目录与正文“第X部分”标题写“××年度部门决算表/情况说明”是部门口径；
#: 只写“××年度决算表/情况说明”是单位口径（上海 2022 单位决算的标准写法）。
_FINAL_DEPT_RE=re.compile(r'部门决算单位构成|年度部门决算(?:表|情况说明)')
_FINAL_ANY_RE=re.compile(r'年度(?:单位)?决算(?:表|情况说明)')
_BUDGET_TABLES=r'(?=财务收支预算总表|收入预算总表|支出预算总表|财政拨款收支预算总表|预算编制说明)'
_BUDGET_DEPT_RE=re.compile(r'部门'+_BUDGET_TABLES)
_BUDGET_UNIT_RE=re.compile(r'单位'+_BUDGET_TABLES)


def heading_scope(body,kind=None):
    """按目录与正文标题判部门/单位口径；只出现一侧字样时才返回，两侧都有返回 None。

    封面本身是清单 A-02 的检查对象（部门决算封面漏写“部门”），不能拿来反推，
    所以这里只看封面以外的标题。决算写法已改由 ``resolve_material_scope`` 统一裁决，
    生产调用方只剩第三阶段验收前的预算 ``body_scope``；两侧混写不按多数票取口径。
    """
    body=re.sub(r'\s+','',str(body or ''))
    counts=[]  # (部门字样数, 单位字样数)
    if kind in (None,'final'):
        # “年度决算表”不含“年度部门决算表”，单位计数不得再减去部门标题数。
        counts.append((len(_FINAL_DEPT_RE.findall(body)),len(_FINAL_ANY_RE.findall(body))))
    if kind in (None,'budget'):
        counts.append((len(_BUDGET_DEPT_RE.findall(body)),len(_BUDGET_UNIT_RE.findall(body))))
    scopes=set()
    for dept,unit in counts:
        if dept and not unit:scopes.add('department')
        elif unit and not dept:scopes.add('unit')
    return next(iter(scopes))if len(scopes)==1 else None


def _witness_blocks(artifact,pages):
    witnesses=[]
    for block in artifact.text_blocks:
        if not pages(block.page_number):continue
        if block.extraction_method!='native_text':
            from src.services.ocr_qualification import bound_ocr_block
            if block.extraction_method!='ocr' or(block.confidence or 0)<.98 or not bound_ocr_block(block,artifact):continue
        witnesses.append(block.text)
    return witnesses


def organization_scope(artifact, local_title=None):
    """材料范围仅由明确封面/实际构成正文决定，局部表题和模板多数票不定论。"""
    from src.services.org_binding_decision import source_scope_evidence

    decision = artifact.processing_manifest.get('document_binding', {}).get('scope_confirmation')
    if decision and decision.get('file_hash') == artifact.source_file_hash:
        from src.services.document_scope import MATERIAL_SCOPE_LEVEL
        return MATERIAL_SCOPE_LEVEL[decision['material_scope']]

    pages = sorted({block.page_number for block in artifact.text_blocks})
    max_page = max(pages, default=1)
    texts = ["\n".join(_witness_blocks(artifact, lambda p, page=page: p == page))
             for page in range(1, max_page + 1)]
    scope, _evidence, _conflict = source_scope_evidence(texts)
    return scope
