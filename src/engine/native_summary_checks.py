"""财政总表关系只使用同一版本、原始列身份和可回溯的单元格。"""
from __future__ import annotations

import re
from dataclasses import replace
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


def check_detail_total_pair(rule, doc, table_code, indicator, name, *,
                            summary_table='FIN_01_income_expenditure_total', detail_indicator='total_actual'):
    """只核对总表金额格与明细表合计行原格，明细其它格缺口不参与此关系。"""
    from .native_hierarchy_checks import DETAIL_TABLES, _select, _table_view

    summary, summary_error = table_facts(doc, summary_table)
    summary_fact = None
    if not summary_error:
        summary_fact, summary_error = _pick(doc, summary, indicator, (name,), allow_native_blank=True)
    view, detail_error = _table_view(doc, table_code, extra_measures=(detail_indicator,))
    detail_fact = None
    if not detail_error:
        if sum(m['canonical_measure'] == detail_indicator for m in view[2]) == 1:
            detail_fact, detail_error = _select(doc, view, 'total', detail_indicator)
            if (detail_error or isinstance(detail_fact, NativeBlankTerm)) and view[5]:
                from .native_table_arithmetic import derive_missing_detail_total_fact
                derived = derive_missing_detail_total_fact(doc, view[5], detail_indicator, stage='决算')
                if derived is not None:
                    detail_fact, detail_error = derived, None
        else:
            detail_error = '明细表合计行所需金额列身份未唯一绑定'
    if summary_fact is not None and detail_fact is not None:
        scopes = summary_fact.fund_scope, detail_fact.fund_scope
        # 表二整表为全口径，财政拨款收入列的业务角色独立对应表四的财政拨款收入总额。
        scope_ok = scopes[0] == scopes[1] or (
            summary_table == T4 and detail_indicator == 'fiscal_allocation'
            and scopes == ('fiscal_appropriation', 'all_funds'))
        if not scope_ok or summary_fact.org_scope != detail_fact.org_scope:
            detail_error = '总表与明细表合计行的汇总口径不一致'
    summary_title = '财政拨款收入支出决算总表' if summary_table == T4 else '收入支出决算总表'
    detail_name = '财政拨款收入合计' if detail_indicator == 'fiscal_allocation' else '合计'
    label = f'{summary_title}{name}与{DETAIL_TABLES[table_code]}{detail_name}'
    issues, unresolved = [], []
    _relation(rule, doc, f'{table_code}:summary_total', label,
              [('总表', summary_fact, summary_error), ('明细表合计行', detail_fact, detail_error)],
              [1], issues, unresolved)
    return finish(rule, issues, unresolved)


def scoped_expense_summary_rows(doc):
    """供旧说明三规则读取经原格确认的合计行，逐字段保留未知值。"""
    from .native_hierarchy_checks import _select, _table_view
    from .rule_outcome import RuleDeferred

    view, error = _table_view(doc, 'FIN_03_expenditure')
    if error:
        raise RuleDeferred('V33-220', error)
    unit = view[1].unit_hint
    factor = {'元': Decimal('0.0001'), '万元': Decimal('1'), '亿元': Decimal('10000')}.get(unit)
    if factor is None:
        raise RuleDeferred('V33-220', '支出决算表合计行金额单位未确认')
    labels = ['项目']
    values = ['合计']
    for indicator, label in (('total_actual', '本年支出合计'),
                             ('basic_actual', '基本支出'), ('project_actual', '项目支出')):
        labels.append(label)
        fact = None
        if sum(m['canonical_measure'] == indicator for m in view[2]) == 1:
            fact, _ = _select(doc, view, 'total', indicator)
            if (fact is None or isinstance(fact, NativeBlankTerm)) and view[5]:
                from .native_table_arithmetic import derive_missing_detail_total_fact
                derived = derive_missing_detail_total_fact(doc, view[5], indicator, stage='决算')
                if derived is not None:
                    fact = derived
        # 认证空白交给调用方的原有证据路径处理。
        values.append(str(fact.normalized_value / factor) if fact is not None
                      and not isinstance(fact, NativeBlankTerm) and fact.unit == unit else '')
    return [labels, values], unit


def _pick(doc, facts, indicator, names, *, allow_native_blank=False):
    candidates = [f for f in facts if f.indicator == indicator
                  and re.sub(r'\s+', '', f.subject_name or '') in names]
    if len(candidates) != 1:
        if not candidates and allow_native_blank and facts:
            term = blank_term(doc, facts[0].table_code, indicator, names=names)
            if term is not None:
                return term, None
        reason = f'{indicator}/{"或".join(names)}原始操作数缺失或不唯一'
        from .native_table_arithmetic import record_operand_gap
        record_operand_gap(doc, facts, candidates, reason, indicator=indicator, code=None,
                           name='或'.join(names), stage='决算')
        return None, reason
    fact = candidates[0]
    if isinstance(fact, NativeBlankTerm):
        if allow_native_blank:
            from .native_empty_operands import valid_blank_observation
            observation = (fact.extra_dims or {}).get('native_blank_observation')
            if (
                observation
                and fact.normalized_value is not None
                and valid_blank_observation(
                    doc.parse_artifact,
                    doc.fact_context,
                    observation,
                    getattr(doc, 'page_texts', None),
                )
            ):
                return fact, None
        return None, '空白观察项未通过当前原件复验'
    reason = operand_rejection_reason(fact, doc.fact_context.org_name)
    if (reason or fact.fiscal_year != doc.fact_context.fiscal_year
            or _canonical_budget_stage(fact.budget_stage) != '决算'
            or fact.unit not in ('元', '万元', '亿元')
            or not fact.extra_dims.get('evidence_refs')):
        reason = reason or '操作数年度、单位、阶段或原始证据未确认'
        from .native_table_arithmetic import record_operand_rejection
        record_operand_rejection(doc, fact, reason, indicator=indicator, stage='决算')
        return None, reason
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
    if all(isinstance(f, NativeBlankTerm) for f in facts):
        receipt(rule, doc, key, facts, status='not_applicable', formula=label,
                reason='原件完整网格各项均为空白，无发生额关系')
        return
    exceeds = amount_difference_exceeds_policy(
        lhs - rhs, rounding_envelope(len(roles) - 1)
    )
    missing_summary_cell = isinstance(roles[0][1], NativeBlankTerm) and exceeds
    mismatch = exceeds
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
    pages = sorted({ref['page'] for ref in refs} | {
        obs.get('physical_page') for obs in blank_observations(facts) if obs.get('physical_page')
    })
    location = {'page': refs[0]['page'] if refs else (pages[0] if pages else None), 'pages': pages,
                'table_code': facts[0].table_code,
                'section': '收入支出决算总表' if facts[0].table_code == 'FIN_01_income_expenditure_total' else '财政拨款收入支出决算总表',
                'relation_type': key, 'fact_source_refs': refs,
                'operand_refs': [operand_ref(fact, role) for role, fact, _ in roles
                                 if not isinstance(fact, NativeBlankTerm)],
                'native_blank_sources': blank_observations(facts),
                'expected': str(rhs), 'actual': '' if missing_summary_cell else str(lhs),
                'difference_wan': str(lhs - rhs),
                **({'defect_type': 'missing_summary_cell'} if missing_summary_cell else {}),
                'fact_context_version_token': doc.fact_context.version_token}
    cell_label = '总计' if roles[0][0] == 'parent' else roles[0][0]
    msg = (
        f'{label}不成立：表内{cell_label}单元格漏填留空，按组成项复算应为{rhs:.2f}万元。'
        if missing_summary_cell
        else f'{label}不成立：表内{lhs:.2f}万元，复算{rhs:.2f}万元，差额{lhs-rhs:.2f}万元。'
    )
    issues.append(rule._issue(msg, location, 'error',
                evidence_text='\n'.join(ref['span'] for ref in refs),
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


def derive_grant_summary_total_fact(doc, facts, *, side='expenditure'):
    """当表四（FIN_04）收入总计或支出总计漏填留空时，按已核实的本年收支与结转结余之和生成推导事实供表文核对继续执行。"""
    if side == 'income':
        parts = [
            _pick(doc, facts, 'income_actual', _NAMES['opening'], allow_native_blank=True),
            _pick(doc, facts, 'income_actual', _NAMES['income'], allow_native_blank=True),
        ]
        ind = 'income_actual'
    else:
        parts = [
            _pick(doc, facts, 'expenditure_actual', _NAMES['expense'], allow_native_blank=True),
            _pick(doc, facts, 'expenditure_actual', _NAMES['closing'], allow_native_blank=True),
        ]
        ind = 'expenditure_actual'
    if any(err or f is None or f.normalized_value is None for f, err in parts):
        return None
    operands = [f for f, _ in parts]
    money = [f for f in operands if not isinstance(f, NativeBlankTerm)]
    if not money:
        return None
    total_val = sum((f.normalized_value for f in operands), Decimal('0'))
    if total_val == 0:
        return None
    template = money[0]
    refs = [
        dict(ref)
        for f in money
        for ref in (f.extra_dims or {}).get('evidence_refs', [])
        if isinstance(ref, dict)
    ]
    if not refs:
        return None
    factor = {'元': Decimal('0.0001'), '万元': Decimal('1'), '亿元': Decimal('10000')}.get(template.unit)
    raw_str = f'{(total_val / factor):.2f}' if factor else f'{total_val:.2f}'
    return replace(
        template,
        fact_uid=f'{template.instance_uid}:derived_grant_total:{ind}',
        subject_code='total',
        subject_name='总计',
        raw_value=raw_str,
        normalized_value=total_val,
        extra_dims={
            **dict(template.extra_dims or {}),
            'derived_missing_total': True,
            'derived_from_fact_uids': [f.fact_uid for f in money],
            'derived_note': '表四总计格漏填，按本年收支与结转结余实算合计继续核对',
            'evidence_refs': refs,
        },
    )


def check_grant_general_vs_function_total(rule, doc):
    facts, reason = table_facts(doc, T4)
    functional, other_reason = table_facts(doc, T5)
    if reason or other_reason:
        return finish(rule, [], [error for error in (reason, other_reason) if error])
    left, lerror = _pick(doc, facts, 'general_public_budget', _NAMES['expense'], allow_native_blank=True)
    right, rerror = _pick(doc, functional, 'total_actual', ('合计', '总计'), allow_native_blank=True)
    if (rerror or isinstance(right, NativeBlankTerm)) and functional:
        from .native_table_arithmetic import derive_missing_detail_total_fact
        derived = derive_missing_detail_total_fact(doc, functional, 'total_actual', stage='决算')
        if derived is not None:
            right, rerror = derived, None
    issues, unresolved = [], []
    _relation(rule, doc, 'grant_general_vs_function_total', '财政拨款总表一般公共预算本年支出=功能分类表合计',
              [('财政拨款总表一般公共预算本年支出', left, lerror),
               ('功能分类表合计', right, rerror)], [1], issues, unresolved)
    return finish(rule, issues, unresolved)


def _equation_zero_child(doc, facts, values, labels):
    """收入支出决算总表仅缺一个组成项时，用总计残差判断它是否为 0。

    - 只允许父项（总计）和另外两个组成项都已确认；
    - 缺失项不得存在任何候选事实（候选被拒是身份/年度问题，不能补零）；
    - 残差必须在政策容差内为 0；非零残差保留取数不足，不拿它凑恒等式。
    """
    errors = [index for index, (_, error) in enumerate(values) if error]
    if len(errors) != 1:
        return None
    index = errors[0]
    if index == 0:
        return None
    parent = values[0][0]
    if parent is None:
        return None
    present = [values[i][0] for i in range(1, 4) if i != index and values[i][0] is not None]
    if len(present) != 2:
        return None
    wanted = {re.sub(r"\s+", "", name) for name in labels[index]}
    if any(
        f.indicator == "expenditure_actual"
        and re.sub(r"\s+", "", f.subject_name or "") in wanted
        for f in facts
    ):
        return None
    residual = parent.normalized_value - sum((f.normalized_value for f in present), Decimal("0"))
    if amount_difference_exceeds_policy(residual, rounding_envelope(3)):
        return None
    return replace(
        parent,
        fact_uid=f"equation-zero:{parent.fact_uid}:{labels[index][0]}",
        indicator="expenditure_actual",
        raw_value="0",
        normalized_value=Decimal("0"),
        subject_name=labels[index][0],
        recognition_status="equation_residual_zero",
        extra_dims={
            **dict(parent.extra_dims or {}),
            "equation_residual_zero": "other_terms_reconcile",
            "evidence_refs": [],
        },
    )


def check_total_expenditure_balance(rule, doc):
    facts, reason = table_facts(doc, 'FIN_01_income_expenditure_total')
    if reason:
        return finish(rule, [], [reason])
    labels = (('总计', '支出总计'), ('本年支出合计',), ('结余分配',), ('年末结转和结余',))
    values = [_pick(doc, facts, 'expenditure_actual', names, allow_native_blank=True)
              for names in labels]
    zero_child = _equation_zero_child(doc, facts, values, labels)
    if zero_child is not None:
        index = next(i for i, (_, error) in enumerate(values) if error)
        values[index] = (zero_child, None)
    roles = [(role, *value) for role, value in zip(('parent', 'child', 'child', 'child'), values, strict=True)]
    issues, unresolved = [], []
    _relation(rule, doc, 'total_expenditure_balance', '支出总计=本年支出合计+结余分配+年末结转和结余',
              roles, [1, 1, 1], issues, unresolved)
    return finish(rule, issues, unresolved)
