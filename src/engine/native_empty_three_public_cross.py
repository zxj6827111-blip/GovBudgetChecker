"""完整原生空白三公表仍须与经济分类的实际支出比较，不能整条豁免。"""
from __future__ import annotations

import re
import unicodedata

from .native_empty_operands import NativeBlankTerm, blank_observations, blank_term
from .native_no_activity import empty_three_public_evidence
from .native_table_arithmetic import (
    _economic_operand, _economic_table_view, finish, receipt, table_facts,
)
from .native_three_public_ratios import operand_ref
from .rule_policy import amount_difference_exceeds_policy


def _name(view, code):
    if code not in view[4]:
        return ''
    # rows[key] 为多值登记（原件重复行不再整表拒收）；名称身份取首行，
    # 目标科目是否唯一由 _economic_operand 严格校验（重复即阻断本项）。
    row, _, mapping, _signature = view[4][code][0]
    # 只读取本车道的名称格；不把左右车道或数额拿来猜科目。
    return ''.join(re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(view[1].rows[row][i] or '')))
                   for i in mapping['source_label_cols']
                   if not str(view[1].rows[row][i] or '').strip().isdigit())


def check_certified_empty_three_public_cross(rule, doc):
    """不适用此窄分支返回None；适用后所有四项均保留独立回执及两表原格。"""
    proof = empty_three_public_evidence(doc)
    if proof is None:
        return None
    facts, error = table_facts(doc, 'FIN_06_basic_expenditure')
    if error:
        return finish(rule, [], [error])
    view, error = _economic_table_view(doc, facts)
    if error:
        return finish(rule, [], [error])
    from .rules_v33 import _SAN_GONG_ECON_ITEM_SPECS, _SAN_GONG_ECON_CODES_2025, _normalize_item_label

    revised = _name(view, '30209') == '差旅费'
    issues, unresolved = [], []
    for indicator, title, names, code in _SAN_GONG_ECON_ITEM_SPECS:
        if revised:
            code = _SAN_GONG_ECON_CODES_2025.get(indicator, code)
        expected_names = {_normalize_item_label(name) for name in names}
        # 同名异码也是冲突，不能挑选金额相同的一行继续比较。
        same_names = [key for key in view[4] if _normalize_item_label(_name(view, key)) in expected_names]
        key = f'empty_three_public_cross:{indicator}'
        reason = None
        basic = None
        if same_names != [code]:
            reason = f'基本支出表「{title}」名称与精确编码{code}未唯一共指同一原行'
        else:
            basic, reason = _economic_operand(doc, view, code)
        whole = blank_term(doc, 'FIN_07_three_public', indicator, code='total', stage='决算')
        if whole is None:
            reason = reason or f'三公表「{title}」决算原格未取得可计算的认证空白'
        terms = [term for term in (basic, whole) if term is not None]
        if reason:
            unresolved.append(reason)
            receipt(rule, doc, key, terms, status='insufficient_data',
                    formula='经济分类本项决算 <= 三公本项决算', reason=reason,
                    disclosure_sources=proof[1])
            continue
        mismatch = (basic.normalized_value > whole.normalized_value
                    and amount_difference_exceeds_policy(basic.normalized_value - whole.normalized_value))
        ids, evidence = receipt(rule, doc, key, terms, status='fail' if mismatch else 'pass',
                                formula='经济分类本项决算 <= 三公本项决算', disclosure_sources=proof[1])
        if not mismatch:
            continue
        sources = blank_observations(terms)
        pages = sorted({source['physical_page'] for source in sources}
                       | {ref['physical_page'] for fact in terms for ref in fact.extra_dims.get('evidence_refs', [])
                          if ref.get('physical_page')})
        message = (f'「{title}」经济分类{code}决算{basic.normalized_value:.2f}万元，'
                   f'大于三公表本项决算0.00万元，差额{basic.normalized_value:.2f}万元。'
                   '三公原件空白格按已确认约定作为0参与比较。')
        issues.append(rule._issue(message, {
            'page': pages[0] if pages else None, 'pages': pages,
            'table_code': 'FIN_06_basic_expenditure', 'subject_code': code,
            'table_refs': proof[1], 'native_blank_sources': sources,
            'operand_refs': [operand_ref(fact, 'economic_actual') for fact in terms
                             if not isinstance(fact, NativeBlankTerm)],
            'fact_context_version_token': doc.fact_context.version_token,
        }, 'error', evidence_text=message, fact_ids=ids, evidence_ids=evidence,
            check_relation_id=f'{rule.code}:{key}'))
    return finish(rule, issues, unresolved)
