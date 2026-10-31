"""决算写法裁决的纯函数基础；调用者负责审核证据、年度绑定与身份。"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Literal

Scope = Literal['unit', 'department']


@dataclass(frozen=True)
class Evidence:
    scope: Scope
    kind: Literal['declaration', 'heading', 'table_note', 'budget_financial_table', 'budget_function_table']
    page: int
    text: str
    position: str
    qualified: bool = False
    start: int = 0
    end: int = 0
    source_blocks: tuple[dict, ...] = ()


@dataclass(frozen=True)
class HumanDecision:
    value: Literal['unit', 'department', 'unknown']
    decision_id: str
    # 人工依据仅引用封面时不能用来校验同一封面。
    cover_independent: bool = False


@dataclass(frozen=True)
class ScopeResolution:
    observed_template_style: str
    status: str
    value: str | None
    source: str
    reason: str
    cover_independent_scope: str | None
    evidence: tuple[Evidence, ...]
    human_decision_id: str | None
    # 封面主体与所选组织不一致：所选层级未用作检查基准，转面板提示人工核对归档。
    archive_subject_mismatch: bool = False


#: 封面名前的附件/序号编号（“附件1”“1”），以及“（本级）/(本级)/本级/本部”标注。
_COVER_INDEX_RE = re.compile(r"^(?:附件)?[0-9０-９]{1,3}(?=[一-鿿])")
_LEVEL_ANNOTATION_RE = re.compile(r"(?:（本级）|\(本级\)|本级|本部)+$")


def _compact_name(text) -> str:
    """比较用归一：NFKC（全角/半角括号与数字）+ 去空白。"""
    import unicodedata
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text or "")))


def cover_matches_selected_org(cover_name, selected_org_name):
    """封面主体名称与所选组织是否一致；任一方识别不到返回 None（不判定）。

    允许的差异：目录名多“单位”后缀（与 check_subject 的 verified_cover_alias
    同规则：目录名 == 封面名 + “单位”）、“（本级）/(本级)/本级/本部”标注、
    封面名前的“附件1/1”编号。封面主体是所选组织名加后缀（下属单位）或名称
    不同 → False；比较前去掉全部空白。
    """
    cover = _compact_name(cover_name)
    org = _compact_name(selected_org_name)
    if len(cover) < 4 or len(org) < 4:
        return None
    cover = _COVER_INDEX_RE.sub("", cover, count=1)
    cover_stripped = _LEVEL_ANNOTATION_RE.sub("", cover)
    org_stripped = _LEVEL_ANNOTATION_RE.sub("", org)
    if cover_stripped == org_stripped:
        return True
    # 目录名多“单位”后缀（与 verified_cover_alias 同方向：目录名更长）。
    if org_stripped == cover_stripped + "单位" or org == cover + "单位":
        return True
    # 其余一律视为不一致：前缀更长的是下属单位，非前缀的是不同名称。
    return False


def resolve_material_scope(*, evidence: tuple[Evidence, ...],
                           template_qualified: bool,
                           human: HumanDecision | None = None,
                           archive_scope: Scope | None = None,
                           selected_level: str | None = None,
                           cover_name: str | None = None,
                           selected_org_name: str | None = None,
                           required_categories: tuple[str,...] = ('heading','table_note')) -> ScopeResolution:
    """按 v3 裁决表处理已合格证据，不从文件名、金额范围或多数票推断。

    ``selected_level`` 是分析开始时冻结的"上传所选组织层级"检查基准（阶段 0）。
    优先级：人工写法决定 > 所选层级（仅 department/unit）> 现有路径（正文明示
    声明等）> 依据不足。层级为 city/district/government/unknown、组织查不到或
    未选择时不使用所选层级，原路径不变；它不宣称"身份已核实"，只是检查基准，
    因此同时可作为 cover_independent_scope 用来核对封面。

    封面主体一致性（``cover_name`` × ``selected_org_name``）：两者都能识别且
    不一致（下属单位或另一名称）时，不使用所选层级、走原路径，并在结果中记
    ``archive_subject_mismatch``；任一方识别不到时维持现行为，仍使用所选层级。
    """
    if human and human.value not in ('unit', 'department', 'unknown'):
        raise ValueError('无效的写法决定')
    if archive_scope not in (None, 'unit', 'department'):
        raise ValueError('无效的归档层级')
    if selected_level not in ('unit', 'department'):
        selected_level = None
    if required_categories == ('budget_financial_table', 'budget_function_table'):
        # 阶段 0 约定：所选层级只用于决算写法裁决；预算路径保持基线不变
        # （开启预算模板开关时，封面与正文冲突仍按 conflict 裁决）。
        selected_level = None
    valid = tuple(e for e in evidence if e.qualified and e.scope in ('unit', 'department')
                  and e.kind in ('declaration', *required_categories) and e.page > 0 and e.text.strip())
    body = tuple(e for e in valid if e.kind in required_categories and e.page > 1)
    by_scope = {scope: {e.kind for e in body if e.scope == scope} for scope in ('unit', 'department')}
    present = {scope for scope, kinds in by_scope.items() if kinds}
    if len(present) == 2:
        observed = 'mixed'
    elif len(present) == 1 and by_scope[next(iter(present))] == set(required_categories):
        observed = next(iter(present))
    else:
        observed = 'insufficient'
    # 未批准的模板只展示观察结果，不以其正文信号产生正式裁决。
    effective_body = body if template_qualified else ()
    declarations = {e.scope for e in valid if e.kind == 'declaration'}
    body_scopes = {e.scope for e in effective_body}
    subject_mismatch = False
    use_selected = False
    if human:
        if human.value == 'unknown':
            status, value, source, reason = 'insufficient', None, 'human', 'human_unknown'
        else:
            status, value, source, reason = 'confirmed', human.value, 'human', 'human_decision'
    elif selected_level:
        # 封面主体与所选组织不一致（下属单位或另一名称）时不用所选层级，
        # 记 archive_subject_mismatch 并落到下方原路径继续裁决。
        if cover_matches_selected_org(cover_name, selected_org_name) is False:
            subject_mismatch = True
        else:
            use_selected = True
    if use_selected:
        status, value, source, reason = 'confirmed', selected_level, 'archive', 'upload_selection_level'
    elif not human:
        if len(declarations) > 1 or len(body_scopes) > 1:
            status, value, source, reason = 'conflict', None, 'unresolved', 'conflicting_evidence'
        elif declarations and body_scopes and declarations != body_scopes:
            status, value, source, reason = 'conflict', None, 'unresolved', 'declaration_body_conflict'
        elif declarations:
            status, value, source, reason = 'confirmed', next(iter(declarations)), 'declaration', 'explicit_declaration'
        elif template_qualified and observed in ('unit', 'department'):
            if archive_scope and archive_scope != observed:
                status, value, source, reason = 'conflict', None, 'unresolved', 'archive_conflict'
            else:
                status, value, source, reason = 'confirmed', observed, 'template', 'qualified_template'
        else:
            status, value, source, reason = 'insufficient', None, 'unresolved', 'insufficient_evidence'
    independent = None
    if status == 'confirmed':
        if source == 'human' and human and human.cover_independent:
            independent = value
        elif source == 'template':
            independent = value
        elif source == 'archive':
            independent = value
        elif source == 'declaration' and ((template_qualified and observed == value) or any(e.kind == 'declaration' and e.page > 1 and e.scope == value for e in valid)):
            independent = value
    return ScopeResolution(observed, status, value, source, reason, independent, valid,
                           human.decision_id if human else None, subject_mismatch)


# ---------------------------------------------------------------------------
# "写法全面相反"判定（阶段 0）：正文标题与表注全部指向另一侧时的待复核分支。
# ---------------------------------------------------------------------------

# 与 checklist_support 同口径的目录引导线；此处本地实现避免 services→engine 循环导入。
_TOC_LEADER_RE = re.compile(r'(?:\.{4,}|…{2,}|·{4,}|-{6,}|_{6,})')
# 目录式页码尾巴：标题与页码之间一个及以上空白即算（单空格目录续页条目不得当作正文证据）。
_TOC_PAGE_TAIL_RE = re.compile(r'(?:[.·…_\-]{3,}|\s+|\t)\s*\d{1,4}\s*$')
_SENTENCE_TAIL_RE = re.compile(r'[，。；：、！？”)]$')


def _looks_like_toc_page(text: str) -> bool:
    head = re.sub(r'\s+', '', '\n'.join(str(text or '').splitlines()[:6]))
    return head.startswith('目录') or '目录' in head[:12]


def _toc_style_line(page_text: str, start: int, end: int) -> bool:
    """证据起始所在物理行是否是目录式行：点线引导，或"标题 + 页码尾巴"。

    只看起始物理行：collect 在去空白串上匹配，证据可能跨软换行（如无句号
    表注把下一行页脚页码一起吃进证据）；若按证据终点找行尾，会把正文表注
    与下一行页脚拼起来误判成目录条目。``end`` 保留在签名里仅为兼容调用方。
    """
    line_start = page_text.rfind('\n', 0, start) + 1
    line_end = page_text.find('\n', line_start)
    line_end = len(page_text) if line_end < 0 else line_end
    line = page_text[line_start:line_end]
    if _TOC_LEADER_RE.search(line):
        return True
    tail = _TOC_PAGE_TAIL_RE.search(line.rstrip())
    if not tail:
        return False
    # 页码尾巴之前不得有句读收尾的叙述；纯“标题 空白 页码”才算目录式行。
    return not _SENTENCE_TAIL_RE.search(line[:tail.start()].rstrip())


def comprehensive_writing_contrast(*, evidence, selected_scope: str | None,
                                   page_texts,
                                   required_categories: tuple[str, ...] = ('heading', 'table_note')) -> dict:
    """只用合格的原生正文证据判断"写法全面相反"；封面与目录不参与。

    三条同时满足才算全面相反：
    (a) 第一类正文位置（heading）至少 1 处指向另一侧；
    (b) 第二类正文位置（table_note）至少 1 处指向另一侧；
    (c) 正文没有任何一处合格证据指向所选一侧。
    缺少任一类、混写、只有 OCR 证据或来源不可证（空来源块），都不算全面相反。
    """
    result = {'comprehensive_contrast': False, 'selected_scope': selected_scope,
              'other_scope': None, 'opposite': {}, 'selected_hits': [], 'excluded': []}
    if selected_scope not in ('unit', 'department'):
        result['excluded'].append({'reason': 'no_selected_level'})
        return result
    other = 'department' if selected_scope == 'unit' else 'unit'
    result['other_scope'] = other
    pages = list(page_texts or [])
    counted = []
    for item in evidence:
        if not isinstance(item, dict):
            item = asdict_item(item)
        page = item.get('page') or 0
        kind = item.get('kind')
        record = {'page': page, 'kind': kind}
        if page <= 1:
            result['excluded'].append({**record, 'reason': 'cover_page'})
            continue
        if kind not in (*required_categories, 'declaration'):
            result['excluded'].append({**record, 'reason': 'irrelevant_kind'})
            continue
        blocks = item.get('source_blocks') or ()
        # collect() 把 qualified 一律置真，不能证明证据来自原生文本；
        # 只计来源块全部为 native_text 的证据，OCR 块（含高置信度）一律不计。
        if not blocks or any(b.get('source_method') != 'native_text' for b in blocks):
            result['excluded'].append({**record, 'reason': 'non_native_source'})
            continue
        text_page = pages[page - 1] if 0 < page <= len(pages) else ''
        if _looks_like_toc_page(text_page):
            result['excluded'].append({**record, 'reason': 'toc_page'})
            continue
        if _toc_style_line(text_page, item.get('start') or 0, item.get('end') or 0):
            result['excluded'].append({**record, 'reason': 'toc_style_line'})
            continue
        counted.append(item)
    for category in required_categories:
        result['opposite'][category] = [
            {'page': i.get('page'), 'text': i.get('text')}
            for i in counted if i.get('kind') == category and i.get('scope') == other]
    result['selected_hits'] = [
        {'page': i.get('page'), 'kind': i.get('kind'), 'text': i.get('text')}
        for i in counted if i.get('scope') == selected_scope]
    comprehensive = all(len(result['opposite'][c]) >= 1 for c in required_categories) and not result['selected_hits']
    result['comprehensive_contrast'] = comprehensive
    return result


def asdict_item(item):
    """容错：允许调用方直接传 Evidence dataclass。"""
    from dataclasses import asdict
    try:
        return asdict(item)
    except TypeError:
        return dict(item)
