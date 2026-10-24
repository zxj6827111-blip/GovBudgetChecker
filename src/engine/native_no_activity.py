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
            term = blank_term(doc, 'FIN_07_three_public', indicator, code='total', stage=stage)
            if term is None:
                return None
            terms.append(term)
    pages = {term.extra_dims['native_blank_observation']['physical_page'] for term in terms}
    from .native_narrative_checks import _Text, _foreign_owner
    text = _Text.from_pages(getattr(doc, 'page_texts', []) or [])
    pattern = re.compile(r'(?:说明[：:])?[^。；]{0,100}(?:本年度|本年)(?:没有|无)'
                         r'(?:一般公共预算)?财政拨款["“”]?三公["“”]?经费支出[，,]故本表无数据[。]?')
    for match in pattern.finditer(text.compact):
        refs = text.refs(match.start(), match.end(), '原表明确无三公支出说明')
        if (not refs or any(ref['page'] not in pages for ref in refs)
                or _foreign_owner(match.group(), context.org_name)
                or any(int(y) != context.fiscal_year for y in re.findall(r'20\d{2}(?=年)', match.group()))
                or re.search(r'并非|不是|不代表|不能说明|暂无', match.group())):
            continue
        return terms, [{**ref, 'file_hash': context.file_hash} for ref in refs]
    return None


def require_no_activity_disposition(rule, doc):
    """空表没有数字可复算；正数披露或百分比声明仍须核对，不能被无支出说明覆盖。"""
    proof = empty_three_public_evidence(doc)
    if proof is None:
        return
    from .native_table_text import sections, context_guard
    from .native_narrative_checks import _NUMBER, _UNIT, _wan
    from .native_table_arithmetic import receipt
    text, ranges = sections(doc, lambda title: '三公' in title and '决算' in title)
    conflict = False
    for _title, begin, end in ranges:
        for match in re.finditer(rf'(?P<amount>{_NUMBER})(?P<unit>{_UNIT})', text.compact[begin:end]):
            start, finish = begin + match.start(), begin + match.end()
            if not context_guard(doc, text.compact, start, finish) and _wan(match['amount'], match['unit']) > 0:
                conflict = True
        # 空白分母不是已披露的零分母，仍保留百分比核对缺口。
        if re.search(r'\d+(?:\.\d+)?[%％]', text.compact[begin:end]):
            conflict = True
    terms, sources = proof
    reason = ('原表明确无三公支出且十二格原件留空，但说明存在正数或百分比，需核对披露'
              if conflict else '原表明确本年度无三公支出且十二格原件留空，金额复算不适用')
    receipt(rule, doc, 'three_public:explicit_empty', terms,
            status='insufficient_data' if conflict else 'not_applicable',
            formula='literal no-activity disclosure + certified native blank grid',
            reason=reason, disclosure_sources=sources)
    if conflict:
        raise RuleDeferred(rule.code, detail=reason)
    raise RuleNotApplicable(rule.code, detail=reason)
