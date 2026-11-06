"""叙述零只证明同身份决算空格；原格不回写，预算不借决算零。

先切物理页/句读边界，再识别水平金额断言，最后裁决身份与冲突。
未形成断言的表头、远处资金科目不参与零声明的身份判断。
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Optional

from .native_narrative_checks import _Text, _foreign_owner

_LABELS = {
    'overseas': (r'因公出国[（(]境[）)]费',),
    'reception': (r'公务接待费',),
    'vehicle_purchase': (r'公务用车购置(?:费)?(?!及运行)',),
    'vehicle_purchase_operation': (r'公务用车购置及运行维护费',),
    'vehicle_operation': (r'公务用车运行维护(?:费)?',),
    'total_actual': (r'[“”"\s]*三公[“”"\s]*经费',),
}
_NON_FISCAL_SCOPE_RE = re.compile(r'非财政拨款|其他资金|事业收入|经营收入|上级补助|附属单位上缴|自有资金|非财政补助')
_CLAUSE_SPLIT = re.compile(r'[。；;]')
_NUMBER = r'[+\-−]?\d[\d,]*(?:\.\d+)?'
_NO_ACTIVITY = re.compile(r'(?:20\d{2}年度|本年度|本年)(?:没有|无)[“”"\s]*三公[“”"\s]*经费财政拨款支出')
_TOTAL_DUAL_LEVEL = re.compile(
    rf'(?:[“”"\s]*三公[“”"\s]*经费)(?:财政拨款)?支出年初预算(?:数)?(?:为|是)?'
    rf'{_NUMBER}万元[，,]支出决算(?:数)?(?:为|是)?(?P<num>{_NUMBER})万元')


def _level_pattern(labels):
    return re.compile(rf'(?:{"|".join(labels)})(?:财政拨款)?(?:支出)?(?:决算(?:数)?)?(?:为|是)?(?P<num>{_NUMBER})万元')


def _clauses(compact: str):
    start = 0
    for match in _CLAUSE_SPLIT.finditer(compact):
        yield start, match.start()
        start = match.end()
    if start < len(compact):
        yield start, len(compact)


def _units(text, org):
    # PDF 表格常无句号；物理页边界不能被删除后变成整本材料的一句话。
    uncertain = False
    for left, right in text.pages:
        start, stop = text.compact_range(left, right)
        for a, b in _clauses(text.compact[start:stop]):
            clause = text.compact[start + a:start + b]
            yield start + a, start + b, uncertain
            claim = _NO_ACTIVITY.search(clause)
            if (claim and not _assertive(clause, claim.start(), claim.end(), org)) or (
                    clause.endswith(('：', ':', '，', ',')) and
                    not all(_neutral_fragment(p, org) for p in re.split(r'[，,:：]', clause))):
                uncertain = True
            # 分号和换页不能自动解除尚未结束的限定，句号才结束当前语句。
            if text.compact[start + b:start + b + 1] == '。':
                uncertain = False


def _amount_assertions(clause):
    """同一规范表示用于取零和反证，双阶段句只取明确决算值。"""
    for key, labels in _LABELS.items():
        for match in _level_pattern(labels).finditer(clause):
            yield key, match
    for match in _TOTAL_DUAL_LEVEL.finditer(clause):
        yield 'total_actual', match


def _neutral_fragment(fragment, org):
    # 只消费有语法含义的中性前缀，不删除任意逗号/冒号之前的文本。
    fragment = fragment.strip('，,:：')
    if org:
        fragment = re.sub(r'^' + re.escape(org) + r'20\d{2}年度(?:部门|单位)?决算', '', fragment)
    heading = r'(?:[一二三四五六七八九十\d]+[、.．]|[（(][一二三四五六七八九十\d]+[）)])?'
    fragment = re.sub(r'^' + heading + r'(?:财政拨款)?[“”"]*三公[“”"]*经费(?:财政拨款)?支出决算(?:具体)?情况说明', '', fragment)
    fragment = re.sub(r'^' + heading, '', fragment)
    if org and fragment.startswith(org):
        fragment = fragment[len(org):]
    fragment = re.sub(r'^(?:20\d{2}年度?|本年度|本年|本部门|本单位|财政拨款|其中[:：]?)+', '', fragment)
    return not fragment


def _assertive(clause, start, stop, org):
    """完整前缀逐段解析。否定/条件等未知限定跨逗号和冒号继续生效。"""
    prefix = clause[:start]
    # 正常说明会先披露别的分项或“决算中”，这些是可核实的独立语法单元。
    prefix = re.sub(r'[“”"]*三公[“”"]*经费(?:财政拨款)?支出决算中', '', prefix)
    for _, match in sorted(_amount_assertions(prefix), key=lambda item: item[1].start(), reverse=True):
        prefix = prefix[:match.start()] + prefix[match.end():]
    prefix = re.sub(r'(?:占|完成(?:年初)?预算的?)' + _NUMBER + r'[%％]', '', prefix)
    if not all(_neutral_fragment(part, org) for part in re.split(r'[，,:：]', prefix)):
        return False
    suffix = clause[stop:]
    # 断言尾部只接受可解析的占比/后续金额，不以“没遇到几个否定词”视为肯定。
    for _, match in sorted(_amount_assertions(suffix), key=lambda item: item[1].start(), reverse=True):
        suffix = suffix[:match.start()] + suffix[match.end():]
    suffix = re.sub(r'(?:占|完成(?:年初)?预算的?)' + _NUMBER + r'[%％]', '', suffix)
    return all(_neutral_fragment(part, org) for part in re.split(r'[，,:：]', suffix))


def _section_is_final(text: _Text, position: int, doc: Any = None) -> bool:
    prefix = text.compact[:position]
    index = max(prefix.rfind('情况说明'), prefix.rfind('说明'))
    if index >= 0:
        return '决算' in prefix[max(0, index - 60):index + 12]
    return getattr(doc, 'report_kind', None) == 'final'


def narrative_zero_sources(doc: Any, *, table_code: str, indicator: str, stage: str) -> Optional[list]:
    if table_code != 'FIN_07_three_public' or stage != '决算' or indicator not in _LABELS:
        return None
    context = getattr(doc, 'fact_context', None)
    org, year = getattr(context, 'org_name', None), getattr(context, 'fiscal_year', None)
    if not org or year is None:
        return None
    text = _Text.from_pages(getattr(doc, 'page_texts', []) or [])
    explicit, absence = [], []
    conflict = False
    for begin, end, uncertain_scope in _units(text, org):
        clause = text.compact[begin:end]
        no_activity = _NO_ACTIVITY.search(clause)
        matches = list(_level_pattern(_LABELS[indicator]).finditer(clause))
        if indicator == 'total_actual':
            matches += list(_TOTAL_DUAL_LEVEL.finditer(clause))
        # 全部无支出必须同时经受所有分项正数的反证。
        all_amounts = [(m, key) for key, m in _amount_assertions(clause)]
        relevant = matches or no_activity or all_amounts
        if not relevant:
            continue
        if ('预算' in clause and '决算' not in clause) or not (
                '决算' in clause or _section_is_final(text, begin, doc)):
            continue
        identity_bad = (_foreign_owner(clause, org) or _NON_FISCAL_SCOPE_RE.search(clause)
                        or any(int(y) != year for y in re.findall(r'20\d{2}(?=年)', clause)))
        for m, key in all_amounts:
            if not identity_bad and Decimal(m['num'].replace(',', '').replace('−', '-')) != 0:
                # 不确定语气的非零也足以阻止总述给出确定零；不能忽略矛盾线索。
                conflict = True
        for m in matches:
            if uncertain_scope or identity_bad or not _assertive(clause, m.start(), m.end(), org):
                # 一个同指标不确定/否定/异口径的声明不能与另一零声明拼接成确定结论。
                return None
            if Decimal(m['num'].replace(',', '').replace('−', '-')) != 0:
                return None
            explicit.append((begin, end))
        if no_activity and not uncertain_scope and not identity_bad and _assertive(clause, no_activity.start(), no_activity.end(), org):
            absence.append((begin, end))
    if indicator == 'total_actual' and conflict:
        return None
    chosen = explicit + ([] if conflict else absence)
    return [r for a, b in chosen for r in text.refs(a, b, '同身份说明明确该项决算为0')] or None


def explicit_no_activity_sources(doc, text):
    """兼容旧调用；总述的身份及所有分项冲突统一由同一裁决路径处理。"""
    return narrative_zero_sources(doc, table_code='FIN_07_three_public', indicator='total_actual', stage='决算')
