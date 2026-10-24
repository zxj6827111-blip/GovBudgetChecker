"""收入表横向关系从有效事实取数，认证空白仅作为本关系的观察项。"""
from .native_hierarchy_checks import _table_view, _compare, check_native_detail_tables
from .native_table_arithmetic import select, receipt, finish, cluster_rounding
from .fact_context import _column_mappings_for_instance
from .rule_outcome import RuleDeferred

CODE = 'FIN_02_income'
SOURCES = ('fiscal_allocation', 'superior_subsidy_income', 'business_income',
           'operational_income', 'subsidiary_income', 'other_income')


def check_income_sources(rule, doc):
    view, error = _table_view(doc, CODE)
    if error:
        return finish(rule, [], [error])
    instance, table, _, _, rows, facts = view
    mappings = _column_mappings_for_instance(instance, table)
    roles = [m['canonical_measure'] for m in mappings]
    if any(roles.count(role) != 1 for role in ('total_actual', *SOURCES)):
        return finish(rule, [], ['收入表来源列未绑定至唯一已确认模板'])
    issues, unresolved = [], []
    for code in rows:
        terms, errors = [], []
        for role in ('total_actual', *SOURCES):
            term, error = select(doc, facts, role, code=code, allow_native_blank=role in SOURCES)
            if error:
                errors.append(error)
            if term is not None:
                terms.append(term)
        key = f'{CODE}:{code}:income_sources'
        if errors:
            unresolved.extend(errors)
            receipt(rule, doc, key, terms, status='insufficient_data',
                    formula='income = sum(sources)', reason='；'.join(errors))
            parent = next((term for term in terms if term.indicator == 'total_actual'), None)
            known = [term for term in terms if term.indicator != 'total_actual']
            if parent is not None and known and parent.normalized_value != sum(term.normalized_value for term in known):
                from .native_table_arithmetic import journal
                from .native_three_public_ratios import operand_ref
                ids, evidence_ids = journal(rule, doc, terms)
                message = (f'收入决算表科目{code}：原件合计{parent.normalized_value:.2f}万元，'
                           f'已确认分项合计{sum(term.normalized_value for term in known):.2f}万元；'
                           '尚有空白或未知分项，不能认定完整勾稽，待人工复核。' + '；'.join(errors))
                issues.append(rule._issue(message, {'table_code': CODE, 'code': code,
                    'operand_refs': [operand_ref(term, 'known') for term in terms]}, 'manual_review',
                    evidence_text=message, fact_ids=ids, evidence_ids=evidence_ids,
                    check_relation_id=f'{rule.code}:{key}:incomplete_clue'))
            continue
        found, missing = _compare(rule, doc, '收入决算表', key, code, 'total_actual',
                                  terms[0], terms[1:])
        issues.extend(found)
        unresolved.extend(missing)
    try:
        issues.extend(check_native_detail_tables(rule, doc, table_codes=(CODE,)))
    except RuleDeferred as signal:
        issues.extend(signal.partial_issues)
        unresolved.extend(signal.unresolved_reasons or [signal.detail])
    return finish(rule, cluster_rounding(rule, issues), unresolved)
