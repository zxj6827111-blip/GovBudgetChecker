"""只按原件封面、原表题，或目录与各部分标题的明确文字绑定主体范围。"""
import re

#: 决算：目录与正文“第X部分”标题写“××年度部门决算表/情况说明”是部门口径；
#: 只写“××年度决算表/情况说明”是单位口径（上海 2022 单位决算的标准写法）。
_FINAL_DEPT_RE=re.compile(r'部门决算单位构成|年度部门决算(?:表|情况说明)')
_FINAL_ANY_RE=re.compile(r'年度(?:单位)?决算(?:表|情况说明)')
_FINAL_DEPT_HEADING_RE=re.compile(r'年度部门决算(?:表|情况说明)')
_BUDGET_TABLES=r'(?=财务收支预算总表|收入预算总表|支出预算总表|财政拨款收支预算总表|预算编制说明)'
_BUDGET_DEPT_RE=re.compile(r'部门'+_BUDGET_TABLES)
_BUDGET_UNIT_RE=re.compile(r'单位'+_BUDGET_TABLES)


def heading_scope(body,kind=None,*,majority=False):
    """按目录与正文标题判部门/单位口径；判不出或两种都像时返回 None。

    封面本身是清单 A-02 的检查对象（部门决算封面漏写“部门”），不能拿来反推，
    所以这里只看封面以外的标题。CMM-018 与解析层的主体范围共用这一口径。

    ``majority``：两种字样都出现时按 2:1 多数取口径。只给解析层的主体范围用——
    它只要求同一份材料内的事实口径一致；单位材料里残留一处“年度部门决算情况说明”
    是模板问题，由结构检查报出，不能因此把整份金额挡在规则之外。
    """
    body=re.sub(r'\s+','',str(body or ''))
    counts=[]  # (部门字样数, 单位字样数, 部门是否按 2:1 多数认定)
    if kind in (None,'final'):
        dept=len(_FINAL_DEPT_RE.findall(body))
        unit=len(_FINAL_ANY_RE.findall(body))-len(_FINAL_DEPT_HEADING_RE.findall(body))
        counts.append((dept,max(unit,0),True))
    if kind in (None,'budget'):
        counts.append((len(_BUDGET_DEPT_RE.findall(body)),len(_BUDGET_UNIT_RE.findall(body)),False))
    scopes=set()
    for dept,unit,dept_by_ratio in counts:
        if dept and (not unit or (dept_by_ratio or majority) and dept>=2*unit):scopes.add('department')
        elif unit and (not dept or majority and unit>=2*dept):scopes.add('unit')
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


def organization_scope(artifact,local_title=None):
    text=''.join(_witness_blocks(artifact,lambda page:page==1))
    texts=([local_title]if local_title else[])+[text]+[table.title for table in artifact.table_fragments if table.page_number==1
                 and table.extraction_method in {'lines','lines_strict','text'}]
    headings=set();labels=set()
    for value in texts:
        if re.search(r'部门(?:汇总)?(?:预算|决算)',value):headings.add('department')
        if re.search(r'单位(?:预算|决算)',value):headings.add('unit')
        if re.search(r'(?:预算|决算)主管部门',value):labels.add('department')
        if re.search(r'(?:预算|决算)单位[:：]',value):labels.add('unit')
        if local_title and value==local_title and headings:
            return next(iter(headings))if len(headings)==1 else None
    scopes=headings or labels
    if scopes:
        return next(iter(scopes))if len(scopes)==1 else None
    # 封面只写“××年度决算”时，所有表格的主体范围仍由目录与各部分标题明确写出；
    # 一字不提时才保持未确认，不能因封面漏字把整份材料的金额全部挡在规则之外。
    return heading_scope('\n'.join(_witness_blocks(artifact,lambda page:page>1)),majority=True)
