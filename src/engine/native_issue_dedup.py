"""按真实操作数及同一原文声明合并重复审校问题。

只合并已明确属于同一种算式的规则族；相同数字、差额或页码不足以去重。
各规则的执行回执仍分别保留，多个失败回执可指向同一交付问题。
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
import re
import unicodedata

from .fact_context import _canonical_budget_stage, operand_rejection_reason
from .native_narrative_checks import _Text


_SUM_FAMILIES = ({'V33-117', 'V33-243'}, {'V33-120', 'V33-241'}, {'V33-101', 'V33-115'})
_YOY = {'V33-234', 'CMM-007'}
_TEXT = {'CMM-001', 'V33-108', 'V33-105', 'V33-221'}
_NUMBER = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
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


def _sum_key(item, family, context):
    refs = _location(item).get('operand_refs') or []
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
    return ('sum', tuple(sorted(family)), identities[parent], tuple(sorted(children, key=repr)), _version(item, context))


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


def _key(item, doc, context):
    severity = str(_get(item, 'severity') or '')
    if severity == 'manual_review':
        return None
    rule = _rule(item)
    for family in _SUM_FAMILIES:
        if rule in family:
            key = _sum_key(item, family, context)
            if key is not None or severity != 'info':
                return key
    if severity == 'info':
        return _pooled_info_key(item, context)
    if rule in _YOY:
        return _yoy_key(item, doc)
    if rule in _TEXT:
        return _text_key(item, doc, context)
    return None


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


def _merged(group, doc=None):
    primary = max(group, key=_quality)
    result = deepcopy(primary)
    location = deepcopy(_location(primary))
    rules = list(dict.fromkeys(rule for item in group for rule in [
        _rule(item), *(_get(item, 'related_rule_ids') or []),
        *(_location(item).get('related_rule_ids') or [])] if rule))
    relations = list(dict.fromkeys(relation for item in group for relation in _relation_ids(item)))
    aliases = list(dict.fromkeys(str(value) for item in group for value in [
        _get(item, 'id'), *(_get(item, 'merged_issue_ids') or []),
        *(_location(item).get('merged_issue_ids') or [])] if value))
    # Preserve every original operand and source reference; distinct relation
    # names may reference the same original cell without manufacturing evidence.
    for key in ('operand_refs', 'table_refs'):
        refs = _unique([ref for item in group for ref in _location(item).get(key) or []])
        if refs:
            location[key] = _repair_literal_refs(refs, doc) if key == 'table_refs' else refs
        else:
            location.pop(key, None)
    pages = {page for item in group for page in [
        _location(item).get('page'), *(_location(item).get('pages') or [])]
             if isinstance(page, int) and not isinstance(page, bool) and page > 0}
    if pages:
        location['pages'] = sorted(pages)
    location.update(related_rule_ids=rules, check_relation_ids=relations, merged_issue_ids=aliases,
                    related_findings=[{'rule_id': _rule(item), 'message': _get(item, 'message'),
                                       'check_relation_id': _get(item, 'check_relation_id'),
                                       'location': deepcopy(_location(item))} for item in group])
    _set(result, 'location', location)
    for key in ('fact_ids', 'evidence_ids', 'tags'):
        values = list(dict.fromkeys(value for item in group for value in _get(item, key, []) or []))
        if values:
            _set(result, key, values)
    _set(result, 'evidence', _unique([ref for item in group for ref in _get(item, 'evidence', []) or []]))
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
    groups, order = {}, []
    for index, item in enumerate(issues):
        key = _key(item, doc, context)
        if key is None:
            order.append(('single', index))
            continue
        if key not in groups:
            groups[key] = []
            order.append(('group', key))
        groups[key].append(item)
    result = [issues[key] if kind == 'single' else (
        _merged(groups[key], doc) if len(groups[key]) > 1 else groups[key][0]) for kind, key in order]
    receipt_list = receipts if receipts is not None else getattr(doc, 'fact_receipts', None)
    if receipt_list is not None:
        link_merged_issue_receipts(result, receipt_list)
    return result
