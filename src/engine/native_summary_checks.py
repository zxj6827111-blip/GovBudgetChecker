"""财政总表关系只使用同一版本、原始列身份和可回溯的单元格。"""
from __future__ import annotations

import re
from decimal import Decimal

from .amount_math import rounding_envelope
from .fact_context import _canonical_budget_stage, operand_rejection_reason
from .rule_policy import amount_difference_exceeds_policy
from .native_table_arithmetic import table_facts, receipt, finish
from .native_three_public_ratios import operand_ref
from .native_empty_operands import NativeBlankTerm, blank_term, blank_observations

T4 = 'FIN_04_fiscal_grant_total'
T5 = 'FIN_05_general_public_expenditure'
_COLUMNS = {'expenditure_actual': '合计', 'general_public_budget': '一般公共预算',
            'government_fund_budget': '政府性基金预算', 'state_capital_budget': '国有资本经营预算'}
_NAMES = {'income': ('本年收入合计',), 'expense': ('本年支出合计',),
          'opening': ('年初财政拨款结转和结余', '年初结转和结余'),
          'closing': ('年末财政拨款结转和结余', '年末结转和结余'), 'total': ('总计',)}


def _pick(doc, facts, indicator, names, *, allow_native_blank=False):
    candidates = [f for f in facts if f.indicator == indicator
                  and re.sub(r'\s+', '', f.subject_name or '') in names]
    if len(candidates) != 1:
        if not candidates and allow_native_blank and facts:
            term = blank_term(doc, facts[0].table_code, indicator, names=names)
            if term is not None:
                return term, None
        return None, f'{indicator}/{"或".join(names)}原始操作数缺失或不唯一'
    fact = candidates[0]
    reason = operand_rejection_reason(fact, doc.fact_context.org_name)
    if (reason or fact.fiscal_year != doc.fact_context.fiscal_year
            or _canonical_budget_stage(fact.budget_stage) != '决算'
            or fact.unit not in ('元', '万元', '亿元')
            or not fact.extra_dims.get('evidence_refs')):
        return None, reason or '操作数年度、单位、阶段或原始证据未确认'
    return fact, None


def _relation(rule, doc, key, label, roles, coefficients, issues, unresolved):
    facts = [fact for _, fact, _ in roles if fact is not None]
    reason = '；'.join(error for _, _, error in roles if error)
    if reason:
        unresolved.append(f'{label}：{reason}')
        receipt(rule, doc, key, facts, status='insufficient_data', formula=label, reason=reason)
        return
    lhs = roles[0][1].normalized_value
    rhs = sum((fact.normalized_value * coefficient for (_, fact, _), coefficient
               in zip(roles[1:], coefficients, strict=True)), Decimal('0'))
    if isinstance(roles[0][1], NativeBlankTerm) and rhs != 0:
        reason = f'{label}合计格原件留空而组成项有金额，需核对合计披露'
        unresolved.append(reason)
        receipt(rule, doc, key, facts, status='insufficient_data', formula=label, reason=reason)
        return
    if all(isinstance(f, NativeBlankTerm) for f in facts):
        receipt(rule, doc, key, facts, status='not_applicable', formula=label,
                reason='原件完整网格各项均为空白，无发生额关系')
        return
    mismatch = amount_difference_exceeds_policy(lhs - rhs, rounding_envelope(len(roles) - 1))
    ids, evidence = receipt(rule, doc, key, facts, status='fail' if mismatch else 'pass', formula=label)
    if not mismatch:
        return
    refs = []
    for role, fact, _ in roles:
        if isinstance(fact, NativeBlankTerm):
            continue
        for ref in fact.extra_dims.get('evidence_refs', []):
            if ref.get('physical_page') and ref.get('quote'):
                refs.append({'page': ref['physical_page'], 'span': ref['quote'], 'role': role,
                             'fact_uid': fact.fact_uid, 'source_cell_id': ref.get('source_cell_id')})
    pages = sorted({ref['page'] for ref in refs})
    location = {'page': refs[0]['page'] if refs else None, 'pages': pages,
                'table_code': facts[0].table_code,
                'section': '收入支出决算总表' if facts[0].table_code == 'FIN_01_income_expenditure_total' else '财政拨款收入支出决算总表',
                'relation_type': key, 'fact_source_refs': refs,
                'operand_refs': [operand_ref(fact, role) for role, fact, _ in roles
                                 if not isinstance(fact, NativeBlankTerm)],
                'native_blank_sources': blank_observations(facts),
                'expected': str(rhs), 'actual': str(lhs), 'difference_wan': str(lhs - rhs),
                'fact_context_version_token': doc.fact_context.version_token}
    issues.append(rule._issue(f'{label}不成立：表内{lhs:.2f}万元，复算{rhs:.2f}万元，差额{lhs-rhs:.2f}万元。',
                location, 'error', evidence_text='\n'.join(ref['span'] for ref in refs),
                check_relation_id=f'{rule.code}:{key}', fact_ids=ids, evidence_ids=evidence))


def check_grant_summary(rule, doc):
    facts, reason = table_facts(doc, T4)
    if reason:
        return finish(rule, [], [reason])
    issues, unresolved = [], []
    income = {kind: _pick(doc, facts, 'income_actual', labels, allow_native_blank=True)
              for kind, labels in _NAMES.items() if kind in ('opening', 'income', 'total')}
    expenditure = {indicator: {kind: _pick(doc, facts, indicator, labels, allow_native_blank=True)
                   for kind, labels in _NAMES.items() if kind in ('expense', 'closing', 'total')}
                   for indicator in _COLUMNS}
    def role(label, value):
        return (label, *value)
    _relation(rule, doc, 'income_balance', '财政拨款收入总计=年初结转和结余+本年收入',
              [role('收入总计', income['total']), role('年初结转和结余', income['opening']),
               role('本年收入', income['income'])], [1, 1], issues, unresolved)
    for indicator, values in expenditure.items():
        label = _COLUMNS[indicator]
        _relation(rule, doc, f'expense_balance:{indicator}', f'财政拨款{label}支出总计=本年支出+年末结转和结余',
                  [role('支出总计', values['total']), role('本年支出', values['expense']),
                   role('年末结转和结余', values['closing'])], [1, 1], issues, unresolved)
    total = expenditure['expenditure_actual']
    _relation(rule, doc, 'total_balance', '财政拨款收入总计=支出总计',
              [role('收入总计', income['total']), role('支出总计', total['total'])], [1], issues, unresolved)
    _relation(rule, doc, 'closing_balance', '财政拨款年末结转和结余=年初结转和结余+本年收入-本年支出',
              [role('年末结转和结余', total['closing']), role('年初结转和结余', income['opening']),
               role('本年收入', income['income']), role('本年支出', total['expense'])], [1, 1, -1], issues, unresolved)
    return finish(rule, issues, unresolved)


def check_grant_general_vs_function_total(rule, doc):
    facts, reason = table_facts(doc, T4)
    functional, other_reason = table_facts(doc, T5)
    if reason or other_reason:
        return finish(rule, [], [error for error in (reason, other_reason) if error])
    left, lerror = _pick(doc, facts, 'general_public_budget', _NAMES['expense'])
    right, rerror = _pick(doc, functional, 'total_actual', ('合计', '总计'))
    issues, unresolved = [], []
    _relation(rule, doc, 'grant_general_vs_function_total', '财政拨款总表一般公共预算本年支出=功能分类表合计',
              [('财政拨款总表一般公共预算本年支出', left, lerror),
               ('功能分类表合计', right, rerror)], [1], issues, unresolved)
    return finish(rule, issues, unresolved)


def check_total_expenditure_balance(rule, doc):
    facts, reason = table_facts(doc, 'FIN_01_income_expenditure_total')
    if reason:
        return finish(rule, [], [reason])
    values = [_pick(doc, facts, 'expenditure_actual', labels, allow_native_blank=True) for labels in
              (('总计', '支出总计'), ('本年支出合计',), ('结余分配',), ('年末结转和结余',))]
    roles = [(role, *value) for role, value in zip(('parent', 'child', 'child', 'child'), values, strict=True)]
    issues, unresolved = [], []
    _relation(rule, doc, 'total_expenditure_balance', '支出总计=本年支出合计+结余分配+年末结转和结余',
              roles, [1, 1, 1], issues, unresolved)
    return finish(rule, issues, unresolved)
