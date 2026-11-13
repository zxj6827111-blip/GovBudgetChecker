"""空表说明属于披露证据，不是金额为零的财政事实。"""
from __future__ import annotations

import re

from .native_empty_operands import blank_term
from .rule_outcome import RuleDeferred, RuleNotApplicable


def empty_three_public_evidence(doc):
    """须同时具备原页无支出说明和十二个可核验的原生空格。"""
    context = getattr(doc, 'fact_context', None)
    if context is None:
        return None
    terms = []
    for stage in ('年初预算', '决算'):
        for indicator in ('total_actual', 'overseas', 'vehicle_purchase_operation',
                          'vehicle_purchase', 'vehicle_operation', 'reception'):
            term = blank_term(doc, 'FIN_07_three_public', indicator, code='total', stage=stage, calculation=False)
            if term is None:
                return None
            terms.append(term)
    pages = {term.extra_dims['native_blank_observation']['physical_page'] for term in terms}
    from .native_narrative_checks import _Text, _foreign_owner
    from .native_table_text import _inside_full_quotation
    from .narrative_zero_evidence import _frame_scope_spans, _is_assertive_context
    text = _Text.from_pages(getattr(doc, 'page_texts', []) or [])
    frames = _frame_scope_spans(text)
    # 表种、主体、年度和十二个原格均已在上方认证，表下等效措辞可省略
    # “财政拨款”“支出”；仍必须明确写“故本表无数据”，正文无支出不能代替。
    pattern = re.compile(r'(?:说明[：:])?[^。；]{0,100}(?:本年度|本年)(?:没有|无)'
                         r'(?:一般公共预算)?(?:财政拨款)?["“”]?三公["“”]?经费(?:支出)?[，,]故本表无数据[。]?')
    for match in pattern.finditer(text.compact):
        refs = text.refs(match.start(), match.end(), '原表明确无三公支出说明')
        clause_start = max(text.compact.rfind(ch, 0, match.start()) for ch in '。；;') + 1
        clause_end = (match.end() if text.compact[match.end() - 1] == '。' else
                      next((i + match.end() for i, ch in enumerate(text.compact[match.end():])
                            if ch in '。；;'), len(text.compact)))
        clause = text.compact[clause_start:clause_end]
        if (not refs or any(ref['page'] not in pages for ref in refs)
                or _foreign_owner(match.group(), context.org_name)
                or any(int(y) != context.fiscal_year for y in re.findall(r'20\d{2}(?=年)', match.group()))
                or re.search(r'并非|不是|不代表|不能说明|暂无|可能|预计|计划|未必|不一定|截至|截止', clause)
                or not _is_assertive_context(text.compact, clause_start, clause, frames)
                or _inside_full_quotation(text.compact,
                    match.start() + match.group().rfind('本年'), match.end())):
            continue
        return terms, [{**ref, 'file_hash': context.file_hash} for ref in refs]
    return None


def require_no_activity_disposition(rule, doc):
    """空表没有数字可复算；正数披露仍须核对，不能被无支出说明覆盖。

    百分比（完成率口径）不构成披露矛盾：分母为 0 的完成率表述由
    V33-TREND-COMPLETION-RATE 独立报告，不得连带阻断金额加总、分项
    齐备与表文一致检查（2026-11-10 宜川任务 5753d874… 裁定）。
    认证空白格按既定口径（空白=0）参与金额勾稽，回执携带十二格认证。
    """
    proof = empty_three_public_evidence(doc)
    if proof is None:
        return
    from .native_table_text import sections, context_guard
    from .native_narrative_checks import _NUMBER, _UNIT, _wan
    from .native_table_arithmetic import receipt
    from .diagnostic_fields import record_gap
    text, ranges = sections(doc, lambda title: '三公' in title and '决算' in title)
    conflicts = []
    for _title, begin, end in ranges:
        for match in re.finditer(rf'(?P<amount>{_NUMBER})(?P<unit>{_UNIT})', text.compact[begin:end]):
            start, finish = begin + match.start(), begin + match.end()
            if not context_guard(doc, text.compact, start, finish) and _wan(match['amount'], match['unit']) > 0:
                page = next((ref.get('page') for ref in text.refs(start, finish, '') if ref.get('page')), None)
                conflicts.append({'source': 'text', 'section': '三公经费支出决算情况说明',
                                  'role': '与认证空白表冲突的正数披露',
                                  'page': page, 'quote': match.group()})
    terms, sources = proof
    if conflicts:
        reason = ('原表明确无三公支出且十二格原件留空，但说明存在正数金额披露，'
                  '与按0口径的表内金额矛盾，需核对披露')
        # 具体诊断：正数披露位置须逐处人工核对，诊断不再落入 rule_no_reason。
        record_gap(doc, reason, reason_codes=['text_binding_failed'],
                   missing_operands=conflicts, preferred_action='human_confirm')
        receipt(rule, doc, 'three_public:explicit_empty', terms,
                status='insufficient_data',
                formula='literal no-activity disclosure + certified native blank grid',
                reason=reason, disclosure_sources=sources)
        raise RuleDeferred(rule.code, detail=reason)
    reason = ('原表明确本年度无三公支出且十二格原件留空（认证空白格按既定口径为0，'
              '金额加总与表文一致恒成立），金额复算不适用；说明中的完成率百分比'
              '由预算完成率专项规则独立核对')
    receipt(rule, doc, 'three_public:explicit_empty', terms,
            status='not_applicable',
            formula='literal no-activity disclosure + certified native blank grid',
            reason=reason, disclosure_sources=sources)
    raise RuleNotApplicable(rule.code, detail=reason)
