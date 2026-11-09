"""按真实操作数及同一原文声明合并重复审校问题。

只合并已明确属于同一种算式的规则族；相同数字、差额或页码不足以去重。
各规则的执行回执仍分别保留，多个失败回执可指向同一交付问题。
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import unicodedata

from .fact_context import _canonical_budget_stage, operand_rejection_reason
from .native_narrative_checks import _Text


_SUM_FAMILIES = ({'V33-117', 'V33-243'}, {'V33-120', 'V33-241'}, {'V33-101', 'V33-115'})
#: “合计 = Σ明细”类规则。分组键是合计原格 + 明细原格的事实身份，与规则族无关：
#: V33-005（各表通用的合计↔明细）与上面各族就同一组原格各报一次时是同一个算式。
_SUM_RULES = frozenset().union(*_SUM_FAMILIES) | {'V33-005', 'V33-233'}
#: 同一张表“缺失 / 为空表而无空表说明”的根缺陷：不同规则各报一次时只保留一条，
#: 其余作为附注（related_findings）留在主条里。按表身份分组，不按文字相似。
_TABLE_DEFECT_RULES = {
    'V33-002': 'table_missing', 'V33-201': 'table_missing',
    'V33-109': 'empty_table_statement', 'V33-114': 'empty_table_statement',
}
#: 根缺陷组的主条：缺表取“缺失表：X”（V33-201 的“T3缺失，无法勾稽”只是后果）；
#: 空表取 V33-114（带表字段与建议语句，表名按原件；V33-109 用九表规范名）。
_TABLE_DEFECT_PRIMARY = ('V33-002', 'V33-114')
_YOY = {'V33-234', 'CMM-007'}
_TEXT = {'CMM-001', 'V33-108', 'V33-105', 'V33-221'}
_UNIT_MAGNITUDE = {'V33-222', 'CMM-011'}
_NUMBER = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
_DECISION_LITERAL = re.compile(
    rf'(?:支\s*出\s*决\s*算(?:\s*数)?|决\s*算\s*数)(?:\s*为|\s*是)?\s*'
    rf'(?P<amount>{_NUMBER})\s*(?P<unit>亿元|万元|元)'
)
_YUAN_LITERAL = re.compile(rf'(?<![\d.,，])(?P<amount>{_NUMBER})\s*(?P<unit>元)(?![/／每])')

#: 缺陷等价关系必须显式声明，不能只因金额相同就合并。
DEFECT_AMOUNT_UNIT_MISSING_WAN = "amount_unit_missing_wan"
PROBLEM_GROUP_ID_PREFIX = "pg-"
_CHANGE = re.compile(rf'(?:增加|减少){_NUMBER}(?:亿元|万元|元)[，,]?(?:增长|下降|增加|减少){_NUMBER}[%％]')
_THREE_TOTAL = re.compile(
    rf'三公经费(?:财政拨款)?(?:支出)?(?:年初预算|全年预算|调整后预算|支出决算(?:数)?|决算数)'
    rf'(?:为|共计|合计为|是)?{_NUMBER}(?:亿元|万元|元)')
_BUSINESS = (
    ('vehicle_purchase_operation', r'公务用车购置及运行(?:维护)?费?'),
    ('vehicle_purchase', r'公务用车购置费?'),
    ('vehicle_operation', r'公务用车运行(?:维护)?费?'),
    ('overseas', r'因公出国(?:\(境\))?费?'),
    ('reception', r'公务接待费?'),
    ('general_public_expenditure', r'一般公共预算(?:财政拨款)?(?:支出)?'),
    ('fiscal_income_expenditure', r'财政拨款收入支出(?:总计|合计|总额)?'),
    ('income_expenditure', r'收入支出(?:总计|合计|总额)'),
    ('running_cost', r'机关运行经费(?:支出)?'),
    ('income', r'本年收入(?:合计|总计|总额)'),
    ('expenditure', r'本年支出(?:合计|总计|总额)'),
    ('three_public_total', r'三公经费'),
)


def _get(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _set(item, key, value):
    if isinstance(item, dict):
        item[key] = value
    elif hasattr(type(item), 'model_fields') and key not in type(item).model_fields:
        # IssueItem has a fixed public schema; additive relation metadata is
        # always carried in location as well, so it survives that adapter.
        return
    else:
        setattr(item, key, value)


def _rule(item):
    return str(_get(item, 'rule_id') or _get(item, 'rule') or '')


def _location(item):
    value = _get(item, 'location')
    return value if isinstance(value, dict) else {}


def _compact(text):
    return re.sub(r'[\s“”"「」]', '', unicodedata.normalize('NFKC', str(text or '')))


def _decimal(value):
    try:
        number = Decimal(str(value).replace(',', '').strip())
        return number if number.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _unique(items):
    result, seen = [], set()
    for item in items:
        key = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
        if key not in seen:
            seen.add(key)
            result.append(deepcopy(item))
    return result


def _resolve_ref(ref, context):
    """需要 fact 身份或唯一完整业务身份；数字不能充当查找键。"""
    if not isinstance(ref, dict):
        return None
    uid = str(ref.get('fact_uid') or '')
    if context is not None:
        if uid:
            candidates = [f for f in context.facts if f.fact_uid == uid]
        else:
            if not all(ref.get(k) for k in ('table_code', 'indicator', 'budget_stage')):
                return None
            if ref.get('subject_code') is None and not ref.get('subject_name'):
                return None
            candidates = [f for f in context.facts if f.table_code == ref['table_code']
                          and f.indicator == ref['indicator']
                          and _canonical_budget_stage(f.budget_stage) == _canonical_budget_stage(ref['budget_stage'])
                          and (f.subject_code == ref['subject_code'] if ref.get('subject_code') is not None
                               else f.subject_name == ref['subject_name'])
                          and (not ref.get('instance_uid') or f.instance_uid == ref['instance_uid'])]
        if len(candidates) != 1:
            return None
        fact = candidates[0]
        if operand_rejection_reason(fact, context.org_name):
            return None
        if context.fiscal_year is not None and fact.fiscal_year != context.fiscal_year:
            return None
        for key in ('table_code', 'indicator', 'instance_uid', 'subject_code'):
            if ref.get(key) is not None and ref[key] != getattr(fact, key):
                return None
        if (ref.get('budget_stage') and _canonical_budget_stage(ref['budget_stage'])
                != _canonical_budget_stage(fact.budget_stage)):
            return None
        if 'value' in ref and _decimal(ref['value']) != fact.normalized_value:
            return None
        return (fact.fact_uid, fact.table_code, fact.instance_uid, fact.indicator,
                _canonical_budget_stage(fact.budget_stage), fact.fiscal_year,
                fact.org_name, fact.org_scope, fact.fund_scope)
    # Post-binding reports can be merged without an in-memory context only
    # when they already carry both a fact UID and its semantic identity.
    if not uid or not all(ref.get(k) for k in ('table_code', 'instance_uid', 'indicator', 'budget_stage')):
        return None
    return (uid, ref['table_code'], ref['instance_uid'], ref['indicator'],
            _canonical_budget_stage(ref['budget_stage']), ref.get('fiscal_year'),
            ref.get('org_name'), ref.get('org_scope'), ref.get('fund_scope'))


def _version(item, context):
    version = _get(item, 'fact_version') or _location(item).get('fact_context_version_token')
    if context is not None and version and version != context.version_token:
        return None
    return str(version or getattr(context, 'version_token', '') or '')


def _sum_key(item, context, doc=None):
    location = _location(item)
    if _version(item, context) is None:
        return None
    bundle = location.get('relations') or []
    if bundle:
        # 只合并完整且完全等价的一组算式；共享一式不足以吞并其他独立关系。
        members = [_sum_key({'location': relation.get('location', {})}, context, doc)
                   for relation in bundle if isinstance(relation, dict)]
        if len(members) != len(bundle) or any(key is None for key in members):
            return None
        return ('sum_bundle', tuple(sorted(set(members), key=repr)), _version(item, context))
    refs = location.get('operand_refs') or []
    if len(refs) < 2 or _version(item, context) is None:
        return None
    # A pooled info finding may contain many independent relations. It must
    # not be mistaken for a single large sum with its first amount as parent.
    relations = {ref.get('relation_id') for ref in refs if isinstance(ref, dict) and ref.get('relation_id')}
    if len(relations) > 1:
        return None
    identities = [_resolve_ref(ref, context) for ref in refs]
    if any(value is None for value in identities) or len(set(identities)) != len(identities):
        return None
    parents = [i for i, ref in enumerate(refs) if ref.get('role') in ('parent', '合计', 'expense_total', 'income_total')]
    if not parents:
        parents = [i for i, ref in enumerate(refs) if str(ref.get('role') or '').endswith(':0')]
    if len(parents) != 1:
        return None
    parent = parents[0]
    children = [value for index, value in enumerate(identities) if index != parent]
    blank_ids = []
    for observation in location.get('native_blank_sources') or []:
        if context is None or not isinstance(observation, dict):
            return None
        # 原格集合也是算式身份；金额相同、相同页码或空白数量不能替代它。
        required = ('file_hash', 'parse_run_uid', 'table_code', 'instance_uid',
                    'source_cell_id', 'indicator', 'subject_code', 'budget_stage')
        if (any(observation.get(key) is None for key in required)
                or observation['file_hash'] != context.file_hash
                or observation['parse_run_uid'] != context.parse_run_uid
                or observation.get('presence') != 'blank'
                or observation.get('calculation') is not True):
            return None
        from .native_empty_operands import valid_blank_observation
        artifact = getattr(doc, 'parse_artifact', None)
        if artifact is None or not valid_blank_observation(artifact, context, observation,
                                                         getattr(doc, 'page_texts', None)):
            return None
        blank_ids.append(tuple(str(observation[key]) for key in required))
    if len(set(blank_ids)) != len(blank_ids):
        return None
    return ('sum', identities[parent], tuple(sorted(children, key=repr)),
            tuple(sorted(blank_ids)), _version(item, context))


def _unit_magnitude_anchor(item, doc=None):
    """V33-222/CMM-011 的同一原文字面量锚点。

    只接受“原文以元列示、表格为同额万元”的窄场景：同一文件哈希、同一物理页、
    同一最小字面量（金额+元）和同一原文中的唯一出现区间，才声明缺陷等价。
    金额相同、同页或区间部分重叠都不够。

    去重发生在证据序列化之前：V33-222 的 location 只有说明句 span，
    CMM-011 只有 pos/消息字面量；两边都先回到原页文本，再取同一个
    “金额+元”区间，不能依赖展示用 bbox 或事实 UID。
    """
    rule = _rule(item)
    location = _location(item)
    page = location.get("page")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        return None
    pages = getattr(doc, "page_texts", None) if doc is not None else None
    if not pages or page > len(pages):
        return None
    page_text = str(pages[page - 1] or "")
    if rule == "V33-222":
        if location.get("unit_magnitude_error") is not True:
            return None
        if str(location.get("declared_unit") or "") != "元":
            return None
        spans = [
            str(ref.get("span") or ref.get("quote"))
            for ref in location.get("table_refs") or []
            if isinstance(ref, dict)
            and str(ref.get("role") or "") == "原文决算金额声明"
            and (ref.get("span") or ref.get("quote"))
        ]
        if len(spans) != 1:
            return None
        span = spans[0]
        if page_text.count(span) != 1:
            return None
        span_start = page_text.find(span)
        matches = list(_DECISION_LITERAL.finditer(span))
        if len(matches) != 1:
            return None
        match = matches[0]
        amount = match.group("amount").replace(",", "")
        if match.group("unit") != "元":
            return None
        start = span_start + match.start("amount")
        end = span_start + match.end("unit")
    elif rule == "CMM-011":
        message = str(_get(item, "message") or "")
        literal_match = re.search(r"[“\"]([\d.,]+)元[”\"]", message)
        if literal_match is None or "." not in literal_match.group(1):
            return None
        amount = literal_match.group(1).replace(",", "")
        pattern = re.compile(re.escape(amount) + r"\s*元")
        matches = list(pattern.finditer(page_text))
        # 必须在原页唯一出现；多个出现位置无法证明两条规则指向同一处。
        if len(matches) != 1:
            return None
        start, end = matches[0].start(), matches[0].end()
    else:
        return None
    file_hash = ""
    context = getattr(doc, "fact_context", None)
    if context is not None:
        file_hash = str(getattr(context, "file_hash", "") or "")
    if not file_hash:
        for ref in location.get("table_refs") or []:
            if isinstance(ref, dict) and ref.get("file_hash"):
                file_hash = str(ref["file_hash"])
                break
    if not file_hash:
        return None
    literal = _compact(f"{amount}元")
    return {
        "file_hash": file_hash,
        "physical_page": page,
        "literal": literal,
        "literal_amount": amount,
        "literal_unit": "元",
        "text_span": [start, end],
        "defect_type": DEFECT_AMOUNT_UNIT_MISSING_WAN,
    }


def _unit_magnitude_key(item, doc=None):
    anchor = _unit_magnitude_anchor(item, doc)
    if anchor is None:
        return None
    return (
        anchor["defect_type"],
        anchor["file_hash"],
        anchor["physical_page"],
        anchor["literal"],
        tuple(anchor["text_span"]),
    )


def problem_group_id(key) -> str:
    """稳定组 ID：只由缺陷类别、文件哈希、物理页、最小字面量与原文区间构成。

    不含随机 UUID、标题、提示语或展示 bbox；原文提取基准变化时必须重新
    验证唯一重定位，不能承诺无条件稳定。
    """
    payload = json.dumps(list(key), ensure_ascii=False, separators=(",", ":"))
    return PROBLEM_GROUP_ID_PREFIX + hashlib.sha256(payload.encode()).hexdigest()[:20]


def _source_spans(item):
    refs = _location(item).get('table_refs') or []
    result = [str(ref.get('span') or ref.get('quote') or '') for ref in refs if isinstance(ref, dict)]
    if not any(result):
        # The literal source quote is usable; calculated explanations without
        # a recognizable business declaration will not satisfy the patterns.
        result = [str(_get(item, 'evidence_text') or '')]
    return ''.join(_compact(value) for value in result)


def _claim_position(item, token, doc):
    """相同声明必须定位到同一个原文出现位置，不能仅靠相同页码。"""
    pages = getattr(doc, 'page_texts', None) if doc is not None else None
    if not pages:
        return None
    text = _Text.from_pages(pages)
    # _Text preserves quotes, whereas statement extraction often omits them.
    # Normalize once and retain a map to the original compact offsets.
    positions, chars = [], []
    for index, char in enumerate(text.compact):
        if char not in '“”"「」':
            positions.append(index)
            chars.append(char)
    source = ''.join(chars)
    matches = [match for match in re.finditer(re.escape(token), source)]
    if len(matches) != 1:
        return None
    match = matches[0]
    raw_start = text.positions[positions[match.start()]]
    raw_end = text.positions[positions[match.end() - 1]] + 1
    return raw_start, raw_end


def _business(item, source, claim_start):
    indicator = _compact(_location(item).get('indicator'))
    if indicator:
        for key, pattern in _BUSINESS:
            if indicator == key or re.fullmatch(pattern, indicator):
                return key
    prefix = source[:claim_start]
    matches = []
    for key, pattern in _BUSINESS:
        matches.extend((match.end(), len(match.group()), key) for match in re.finditer(pattern, prefix))
    return max(matches)[2] if matches else None


def _yoy_key(item, doc):
    location = _location(item)
    source = _source_spans(item)
    matches = list(_CHANGE.finditer(source))
    if len(matches) != 1:
        return None
    match = matches[0]
    business = _business(item, source, match.start())
    position = _claim_position(item, match.group(), doc)
    current = _decimal(location.get('current_amount_wan', location.get('current_amount')))
    delta = _decimal(location.get('change_amount_wan', location.get('delta_amount')))
    declared = _decimal(location.get('declared_percent'))
    year = location.get('current_year', location.get('fiscal_year'))
    if None in (business, position, current, delta, declared, year):
        return None
    # CMM-007 values retain the original amount unit; V33-234 uses 万元.
    if 'current_amount_wan' not in location:
        factor = {'元': Decimal('.0001'), '万元': Decimal('1'), '亿元': Decimal('10000')}.get(location.get('unit'))
        delta_factor = {'元': Decimal('.0001'), '万元': Decimal('1'), '亿元': Decimal('10000')}.get(location.get('delta_unit') or location.get('unit'))
        if factor is None or delta_factor is None:
            return None
        current, delta = current * factor, delta * delta_factor
    direction = _compact(location.get('direction'))
    if direction not in ('增加', '减少'):
        return None
    # Cross-rule scope labels are different headings for the same original
    # claim; its exact original position and business identify the relation.
    return ('yoy', position, business, str(year), direction, current, delta, declared)


def _text_key(item, doc, context):
    refs = _location(item).get('operand_refs') or []
    if len(refs) != 1 or _version(item, context) is None:
        return None
    identity = _resolve_ref(refs[0], context)
    if identity is None:
        return None
    if identity[1] == 'FIN_07_three_public' and identity[3] == 'total_actual':
        pattern = _THREE_TOTAL
    elif identity[1] == 'FIN_04_fiscal_grant_total' and identity[3] == 'expenditure_actual':
        pattern = re.compile(rf'(?:财政拨款)?(?:收入支出|收支)(?:总计|合计|总额)'
                             rf'(?:为|共计|是)?{_NUMBER}(?:亿元|万元|元)')
    else:
        return None
    matches = list(pattern.finditer(_source_spans(item)))
    if len(matches) != 1:
        return None
    position = _claim_position(item, matches[0].group(), doc)
    return ('text_vs_table', identity, position, _version(item, context)) if position is not None else None


def _table_defect_key(item):
    """缺表/空表无说明的根缺陷键：(类型, 表代码)；认不出唯一表身份时 None。"""
    rule = _rule(item)
    kind = _TABLE_DEFECT_RULES.get(rule)
    if kind is None:
        return None
    message = str(_get(item, 'message') or _get(item, 'title') or '')
    table = str(_location(item).get('table') or '')
    if rule == 'V33-002':
        match = re.search(r'缺失表[:：]\s*(?P<name>[^，。；,;\s]+)', message)
        name = table or (match.group('name') if match else '')
    elif rule == 'V33-201':
        # 只有“T3(支出决算表)缺失，无法勾稽”这一句是缺表的后果；金额不一致是独立问题。
        name = '支出决算表' if re.search(r'T3[（(]支出决算表[)）]缺失', message) else ''
    elif rule == 'V33-109':
        match = re.search(r'【(?P<name>[^】]+)】为空表', message)
        name = match.group('name') if match else ''
    else:
        match = re.search(r'空表缺少说明[:：](?P<name>.+?)为空表', message)
        name = table or (match.group('name') if match else '')
    if not name:
        return None
    from src.services.obligation_finding_coverage import _table_codes_in
    codes = _table_codes_in(name)
    return (kind, codes.pop()) if len(codes) == 1 else None


def _keys(item, doc, context):
    """该问题的全部分组键；共享任一键的问题合并为一条（可传递）。"""
    severity = str(_get(item, 'severity') or '')
    if severity == 'manual_review':
        return []
    table_defect = _table_defect_key(item)
    if table_defect is not None:
        return [table_defect]
    rule = _rule(item)
    keys = []
    if rule in _SUM_RULES:
        key = _sum_key(item, context, doc)
        if key is None and severity != 'info':
            return []
        if key is not None:
            keys.append(key)
    if severity == 'info':
        # 同一组原格的取整提示：合计↔明细规则按算式键，其余按原格事实集合键；
        # 两键都算，V33-005 与 V33-120/V33-241 就同一处 0.02 各报一次时合成一条。
        pooled = _pooled_info_key(item, context)
        return keys + ([pooled] if pooled is not None else [])
    if keys:
        return keys
    if rule in _YOY:
        key = _yoy_key(item, doc)
    elif rule in _TEXT:
        key = _text_key(item, doc, context)
    elif rule in _UNIT_MAGNITUDE:
        key = _unit_magnitude_key(item, doc)
    else:
        key = None
    return [key] if key is not None else []


def _key(item, doc, context):
    """单键视图（兼容旧调用）：取 :func:`_keys` 的第一个键。"""
    keys = _keys(item, doc, context)
    return keys[0] if keys else None


def _pooled_info_key(item, context):
    """提示级问题：多条规则就同一组原格事实各报一次（如同一处 0.01 取整差），按事实集合合并。

    每个操作数都须带事实身份、且集合完全相同才算同一处；身份不全时不合并。
    """
    refs = [ref for ref in _location(item).get('operand_refs') or [] if isinstance(ref, dict)]
    uids = [ref.get('fact_uid') for ref in refs]
    version = _version(item, context)
    if len(uids) < 2 or not all(uids) or version is None:
        return None
    return ('info', tuple(sorted(set(uids))), version)


def _quality(item):
    location = _location(item)
    exact = location.get('location_level') in ('exact_cell', 'exact_span')
    verified = location.get('verification_status') == 'verified'
    source_refs = [r for r in location.get('table_refs') or [] if isinstance(r, dict)]
    exact_refs = sum(bool(r.get('bbox') and r.get('verification_status') == 'verified') for r in source_refs)
    identity_refs = sum(bool(r.get('fact_uid')) for r in location.get('operand_refs') or [] if isinstance(r, dict))
    return (bool(exact and verified and location.get('bbox')), exact_refs,
            bool(location.get('page')), identity_refs, len(source_refs))


def _relation_ids(item):
    return list(dict.fromkeys(str(value) for value in [
        _get(item, 'check_relation_id'), *(_get(item, 'check_relation_ids') or []),
        *(_location(item).get('check_relation_ids') or [])] if value))


def _repair_literal_refs(refs, doc):
    if doc is None or not getattr(doc, 'page_texts', None):
        return refs
    text = _Text.from_pages(doc.page_texts)
    result = []
    for ref in refs:
        span = ref.get('span')
        needle = re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(span or '')))
        if len(needle) >= 8 and text.compact.count(needle) == 1:
            start = text.compact.index(needle)
            for source in text.refs(start, start + len(needle), ref.get('role')):
                result.append({**ref, **source})
        else:
            result.append(ref)
    return _unique(result)


def _merged(group, doc=None, *, key=None):
    if key is not None and key and key[0] in set(_TABLE_DEFECT_RULES.values()):
        # 根缺陷组：主条按规则身份取（缺失表/空表说明本身），证据质量其次。
        primary = max(group, key=lambda item: (_rule(item) in _TABLE_DEFECT_PRIMARY, _quality(item)))
    else:
        primary = max(group, key=_quality)
    result = deepcopy(primary)
    location = deepcopy(_location(primary))
    rules = list(dict.fromkeys(rule for item in group for rule in [
        _rule(item), *(_get(item, 'related_rule_ids') or []),
        *(_location(item).get('related_rule_ids') or [])] if rule))
    relations = list(dict.fromkeys(relation for item in group for relation in _relation_ids(item)))
    aliases = list(dict.fromkeys(str(value) for item in group for value in [
        _get(item, 'id'), *(_get(item, 'merged_issue_ids') or []),
        *(_location(item).get('merged_issue_ids') or []),
        _location(item).get('pre_group_issue_id')] if value))
    # 缺表是根缺陷，V33-201 的跨表引用只是无法核对的后果，并不是“缺表”的证据。
    # 混入这些无 span 的引用会让双模式适配后的原文绑定整体失效；保留主规则证据，
    # 后果的完整定位仍留在 related_findings 中。算式类仍须合并全部操作数证明。
    proof_members = [primary] if key and key[0] == 'table_missing' else group
    for field in ('operand_refs', 'table_refs', 'native_blank_sources', 'native_blank_operands'):
        refs = _unique([ref for item in proof_members for ref in _location(item).get(field) or []])
        if refs:
            location[field] = _repair_literal_refs(refs, doc) if field == 'table_refs' else refs
        else:
            location.pop(field, None)
    pages = {page for item in group for page in [
        _location(item).get('page'), *(_location(item).get('pages') or [])]
             if isinstance(page, int) and not isinstance(page, bool) and page > 0}
    if pages:
        location['pages'] = sorted(pages)
    location.update(related_rule_ids=rules, check_relation_ids=relations, merged_issue_ids=aliases,
                    related_findings=[{'rule_id': _rule(item), 'message': _get(item, 'message'),
                                       'check_relation_id': _get(item, 'check_relation_id'),
                                       'location': deepcopy(_location(item))} for item in group])
    if key is not None and key and key[0] == DEFECT_AMOUNT_UNIT_MISSING_WAN:
        anchor = None
        for item in group:
            candidate = _unit_magnitude_anchor(item, doc)
            if candidate is not None:
                anchor = candidate
                break
        if anchor is not None:
            location["problem_group_id"] = problem_group_id(key)
            location["source_anchor"] = anchor
            location["defect_type"] = DEFECT_AMOUNT_UNIT_MISSING_WAN
            location["member_issue_ids"] = aliases
            location["member_rules"] = rules
            # 成员各自的证据与回执原样保留；主卡片采用质量最高的成员。
            location["member_evidence"] = [
                {
                    "rule_id": _rule(item),
                    "issue_id": _get(item, "id"),
                    "check_relation_id": _get(item, "check_relation_id"),
                    "message": _get(item, "message"),
                    "evidence": deepcopy(_get(item, "evidence", []) or []),
                }
                for item in group
            ]
    _set(result, 'location', location)
    for key in ('fact_ids', 'evidence_ids', 'tags'):
        members = group if key == 'tags' else proof_members
        values = list(dict.fromkeys(value for item in members for value in _get(item, key, []) or []))
        if values:
            _set(result, key, values)
    _set(result, 'evidence', _unique([ref for item in proof_members for ref in _get(item, 'evidence', []) or []]))
    for key, values in (('related_rule_ids', rules), ('check_relation_ids', relations), ('merged_issue_ids', aliases)):
        _set(result, key, values)
    priorities = {'critical': 6, 'high': 5, 'error': 5, 'warn': 4, 'warning': 4,
                  'medium': 3, 'low': 2, 'info': 1}
    strongest = max(group, key=lambda item: priorities.get(str(_get(item, 'severity')), 0))
    _set(result, 'severity', _get(strongest, 'severity'))
    return result


def link_merged_issue_receipts(issues, receipts):
    """已赋报告id后，把每条独立失败回执关联到保留的问题id。"""
    by_relation, aliases = {}, {}
    for issue in issues:
        issue_id = str(_get(issue, 'id') or '')
        if not issue_id:
            continue
        for relation in _relation_ids(issue):
            by_relation.setdefault(relation, []).append(issue_id)
        for old in _get(issue, 'merged_issue_ids', []) or _location(issue).get('merged_issue_ids') or []:
            aliases[str(old)] = issue_id
    for receipt in receipts or []:
        if not isinstance(receipt, dict):
            continue
        existing = [aliases.get(str(old), str(old)) for old in receipt.get('issue_refs') or []]
        if receipt.get('status') == 'fail':
            relation = f"{receipt.get('rule_id') or ''}:{receipt.get('sub_check_id') or ''}"
            existing += by_relation.get(relation, [])
        receipt['issue_refs'] = list(dict.fromkeys(existing))


def deduplicate_native_issues(issues, doc=None, *, receipts=None):
    """输入 Issue、IssueItem 或已序列化字典；保持顺序和各条独立回执。

    建议在全部规则执行完成、报告id分配完成之后调用。文段关系需要原页
    文本唯一定位；缺文本/缺身份时保留原问题，不能猜测它们是同一问题。
    """
    context = getattr(doc, 'fact_context', None) if doc is not None else None
    # 共享任一分组键的问题并为一组（并查集，按首次出现的位置排序）。
    parent = list(range(len(issues)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    owner, item_keys = {}, []
    for index, item in enumerate(issues):
        # 记录“未并组时该条会得到的报告 id”。旧链接/旧客户端仍可按它查询，
        # 但只在当前分析的问题集合内解析，不跨版本。
        rule = _rule(item)
        # V33-227 的每条名称差异都是独立问题，不参与跨规则合并；分组展示号
        # 会随前序问题增删整体位移，写成引用既无必要也会制造假漂移。
        if rule and rule != "V33-227":
            location = _location(item)
            location.setdefault("pre_group_issue_id", f"{rule}-{index + 1}")
            _set(item, "location", location)
        keys = _keys(item, doc, context)
        item_keys.append(keys)
        for key in keys:
            if key in owner:
                left, right = find(owner[key]), find(index)
                if left != right:
                    parent[max(left, right)] = min(left, right)
            else:
                owner[key] = index
    members = {}
    for index in range(len(issues)):
        members.setdefault(find(index), []).append(index)
    result = []
    for root in sorted(members):
        indices = members[root]
        if len(indices) == 1:
            result.append(issues[indices[0]])
            continue
        group = [issues[index] for index in indices]
        keys = [key for index in indices for key in item_keys[index]]
        key = next((k for k in keys if k and k[0] == DEFECT_AMOUNT_UNIT_MISSING_WAN), keys[0])
        result.append(_merged(group, doc, key=key))
    _link_related_symptoms(result)
    receipt_list = receipts if receipts is not None else getattr(doc, 'fact_receipts', None)
    if receipt_list is not None:
        link_merged_issue_receipts(result, receipt_list)
    return result


def _link_related_symptoms(issues) -> None:
    """V33-251 等关联症状只建立引用，不并入单位缺陷组。

    关联依据是同一原文锚点（物理页 + 最小字面量），不是金额相近；
    症状仍需独立处理和独立回执。
    """
    groups = {}
    for item in issues:
        location = _location(item)
        anchor = location.get("source_anchor")
        group_id = location.get("problem_group_id")
        if not isinstance(anchor, dict) or not group_id:
            continue
        key = (anchor.get("physical_page"), anchor.get("literal"))
        groups.setdefault(key, []).append(group_id)
    if not groups:
        return
    for item in issues:
        location = _location(item)
        related = location.get("related_problem_group_ids")
        if isinstance(related, list) and related:
            continue
        anchors = location.get("unit_normalized_items") or []
        found = []
        for anchor in anchors:
            if not isinstance(anchor, dict):
                continue
            key = (anchor.get("page"), _compact(f"{anchor.get('amount')}{anchor.get('unit')}"))
            for group_id in groups.get(key, []):
                if group_id not in found:
                    found.append(group_id)
        if found:
            location["related_problem_group_ids"] = found
            _set(item, "location", location)
