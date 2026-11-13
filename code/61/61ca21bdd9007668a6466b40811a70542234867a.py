"""原生表格勾稽：显式操作数、当前版本和完整行身份共同约束计算。"""
from __future__ import annotations

import re
from decimal import Decimal

from .amount_math import classify_amount_diff
from .fact_context import _canonical_budget_stage, operand_rejection_reason
from .native_three_public_ratios import operand_ref
from .rule_outcome import RuleDeferred


def table_facts(doc, code):
    context = getattr(doc, 'fact_context', None)
    artifact = getattr(doc, 'parse_artifact', None)
    if context is None or artifact is None:
        return [], '缺少当前版本财政事实'
    if (context.file_hash != artifact.source_file_hash
            or context.parse_run_uid != artifact.parse_run_uid):
        return [], '财政事实与当前解析产物版本不一致'
    facts = [f for f in context.facts if f.table_code == code]
    if not facts:
        return [], f'{code}没有可用金额事实'
    if len({f.instance_uid for f in facts}) != 1 or not facts[0].instance_uid:
        return [], f'{code}存在多个或未识别的表实例'
    return facts, None


def select(doc, facts, indicator, *, code=None, name=None, stage='决算'):
    context = doc.fact_context
    candidates = [f for f in facts if f.indicator == indicator
                  and (code is None or f.subject_code == code)
                  and (name is None or (name in re.sub(r'\s+', '', f.subject_name or '')
                       and not any(other in re.sub(r'\s+', '', f.subject_name or '')
                                   for other in ('人员经费合计', '公用经费合计') if other != name)))
                  and _canonical_budget_stage(f.budget_stage) == stage]
    if len(candidates) != 1:
        return None, f'{code or name or indicator}的{indicator}/{stage}操作数缺失或不唯一'
    fact = candidates[0]
    reason = operand_rejection_reason(fact, context.org_name)
    if (reason or context.fiscal_year is None or fact.fiscal_year != context.fiscal_year
            or fact.period not in (None, '本年')):
        return None, reason or '操作数年度或期间不一致'
    return fact, None


def journal(rule, doc, facts):
    from .rules_v33 import _v33_fact_identity
    ids, evidence = _v33_fact_identity(*facts)
    records = getattr(doc, 'structured_consumption', None)
    if records is None:
        records = doc.structured_consumption = []
    if facts:
        records.append({'rule_id': rule.code, 'table_code': facts[0].table_code,
                        'instance_uid': facts[0].instance_uid, 'fact_uids': ids,
                        'fact_version': doc.fact_context.version_token,
                        'normalized_unit': '万元'})
    return ids, evidence


def receipt(rule, doc, relation, facts, *, status, formula, reason=None):
    from .rules_v33 import _v33_relation_receipt
    ids, evidence = journal(rule, doc, facts)
    _v33_relation_receipt(doc, rule.code, relation, status=status,
                          operand_fact_ids=ids, formula=formula, incomplete_reason=reason)
    return ids, evidence


def finish(rule, issues, unresolved):
    if unresolved:
        reasons = list(dict.fromkeys(unresolved))
        raise RuleDeferred(rule.code, detail='；'.join(reasons), partial_issues=issues,
                           unresolved_reasons=reasons)
    return issues


def cluster_rounding(rule, issues):
    """同尾差提示合并，但保留每个独立关系的全部操作数和原始页。"""
    result, groups = [], {}
    for issue in issues:
        if issue.severity != 'info':
            result.append(issue)
            continue
        diff = re.search(r'差额([\d.]+)万元', issue.message)
        if not diff:
            result.append(issue)
            continue
        groups.setdefault(diff.group(1), []).append(issue)
    for diff, group in groups.items():
        if len(group) == 1:
            result.extend(group)
            continue
        refs = [ref for issue in group for ref in issue.location['operand_refs']]
        pages = sorted({page for issue in group for page in issue.location['pages']})
        result.append(rule._issue(f'表内{len(group)}处合计相差{diff}万元，可能为取整误差。',
            {'table_code': group[0].location['table_code'], 'page': pages[0] if pages else None,
             'pages': pages, 'operand_refs': refs,
             'relations': [issue.check_relation_id for issue in group],
             'fact_context_version_token': group[0].location['fact_context_version_token']},
            'info', evidence_text='\n'.join(issue.message for issue in group),
            fact_ids=list(dict.fromkeys(uid for issue in group for uid in issue.fact_ids)),
            evidence_ids=list(dict.fromkeys(uid for issue in group for uid in issue.evidence_ids))))
    return result


def relation(rule, doc, key, label, parent, children, *, hints=True):
    facts = [parent] + children
    if any((f.instance_uid, f.org_scope, f.fund_scope, f.budget_stage, f.fiscal_year)
           != (parent.instance_uid, parent.org_scope, parent.fund_scope,
               parent.budget_stage, parent.fiscal_year) for f in children):
        reason = f'{label}操作数的表实例或口径不一致'
        receipt(rule, doc, key, facts, status='insufficient_data', formula='total = sum(parts)', reason=reason)
        return [], [reason]
    total = sum((f.normalized_value for f in children), Decimal('0'))
    level, diff = classify_amount_diff(parent.normalized_value, total, n_children=len(children))
    ids, evidence = receipt(rule, doc, key, facts,
                            status='fail' if level == 'mismatch' else 'pass',
                            formula='total = sum(parts) (万元)')
    if level != 'mismatch' and not (hints and level == 'rounding_hint'):
        return [], []
    refs = [operand_ref(f, f'{key}:{i}') for i, f in enumerate(facts)]
    pages = sorted({e.get('physical_page') for f in facts for e in f.extra_dims.get('evidence_refs', [])
                    if e.get('physical_page')})
    severity = 'error' if level == 'mismatch' else 'info'
    message = (f'{label}：表内合计{parent.normalized_value:.2f}万元，分项之和{total:.2f}万元，'
               f'差额{diff:.2f}万元。' + ('可能为取整误差。' if severity == 'info' else '超过金额容差。'))
    return [rule._issue(message, {'table_code': parent.table_code,
            'page': pages[0] if pages else None, 'pages': pages, 'operand_refs': refs,
            'fact_context_version_token': doc.fact_context.version_token}, severity,
            evidence_text=message, check_relation_id=f'{rule.code}:{key}',
            fact_ids=ids, evidence_ids=evidence)], []


def check_basic_economics(rule, doc):
    facts, reason = table_facts(doc, 'FIN_06_basic_expenditure')
    if reason:
        return finish(rule, [], [reason])
    issues, unresolved = [], []
    # An amount-bearing unidentified row may be a child or a class total.
    # Never omit it and compare the remaining subset as a complete hierarchy.
    unknown = [f for f in facts if f.subject_code != 'total'
               and not re.fullmatch(r'3\d{2}(?:\d{2})?', str(f.subject_code or ''))]
    if unknown:
        reason = '经济分类金额行的科目编码无法唯一识别，明细归集未完成'
        receipt(rule, doc, 'economic:identity', unknown, status='insufficient_data',
                formula='class = sum(complete children)', reason=reason)
        unresolved.append(reason)
    for name, codes in [('人员经费合计', ('301', '303')), ('公用经费合计', ('302', '310'))]:
        parent, error = select(doc, facts, 'total_actual', name=name)
        children = []
        for code in codes:
            child, cr = select(doc, facts, 'total_actual', code=code)
            error = error or cr
            if child is not None:
                children.append(child)
        key = f'economic:summary:{name}'
        if error:
            unresolved.append(error)
            receipt(rule, doc, key, [f for f in [parent] + children if f is not None],
                    status='insufficient_data', formula='summary = classes', reason=error)
        else:
            found, missing = relation(rule, doc, key, name, parent, children, hints=rule.code == 'V33-117')
            issues.extend(found)
            unresolved.extend(missing)
    if not unknown:
        for code in sorted({f.subject_code for f in facts if re.fullmatch(r'3\d{2}', str(f.subject_code or ''))}):
            parent, error = select(doc, facts, 'total_actual', code=code)
            children = []
            for child_code in sorted({f.subject_code for f in facts
                                      if str(f.subject_code or '').startswith(code) and len(f.subject_code or '') == 5}):
                child, cr = select(doc, facts, 'total_actual', code=child_code)
                error = error or cr
                if child is not None:
                    children.append(child)
            if not children:
                continue
            if error:
                unresolved.append(error)
                receipt(rule, doc, f'economic:class:{code}', children, status='insufficient_data',
                        formula='class = sum(children)', reason=error)
                continue
            found, missing = relation(rule, doc, f'economic:class:{code}', f'经济分类{code}明细归集',
                                      parent, children, hints=rule.code == 'V33-117')
            issues.extend(found)
            unresolved.extend(missing)
    return finish(rule, cluster_rounding(rule, issues), unresolved)


def check_three_public_arithmetic(rule, doc):
    facts, reason = table_facts(doc, 'FIN_07_three_public')
    if reason:
        return finish(rule, [], [reason])
    issues, unresolved = [], []
    for stage in ('年初预算', '决算'):
        for parent_key, parts in [('total_actual', ('overseas', 'vehicle_purchase_operation', 'reception')),
                                  ('vehicle_purchase_operation', ('vehicle_purchase', 'vehicle_operation'))]:
            operands, errors = [], []
            for key in (parent_key,) + parts:
                fact, error = select(doc, facts, key, code='total', stage=stage)
                if error:
                    errors.append(error)
                if fact is not None:
                    operands.append(fact)
            key = f'three_public:{stage}:{parent_key}:sum'
            if errors:
                reason = '；'.join(errors)
                unresolved.extend(errors)
                receipt(rule, doc, key, operands, status='insufficient_data', formula='total = sum(parts)', reason=reason)
            else:
                found, missing = relation(rule, doc, key, f'三公经费{stage}{parent_key}分项合计',
                                          operands[0], operands[1:])
                issues.extend(found)
                unresolved.extend(missing)
    return finish(rule, cluster_rounding(rule, issues), unresolved)


def check_expenditure_arithmetic(rule, doc):
    facts, reason = table_facts(doc, 'FIN_03_expenditure')
    if reason:
        return finish(rule, [], [reason])
    issues, unresolved = [], []
    if any(f.subject_code != 'total' and not re.fullmatch(r'\d{3}(?:\d{2}){0,2}', str(f.subject_code or '')) for f in facts):
        unresolved.append('支出金额行的科目编码无法唯一识别，层级归集未完成')
    codes = sorted({f.subject_code for f in facts if f.subject_code == 'total'
                    or re.fullmatch(r'\d{3}(?:\d{2}){0,2}', str(f.subject_code or ''))})
    if 'total' not in codes:
        unresolved.append('支出决算表未找到唯一合计行')
    indicators = ('total_actual', 'basic_actual', 'project_actual', 'to_superior_actual',
                  'operational_actual', 'subsidiary_actual')
    for code in codes:
        operands, errors = [], []
        for indicator in indicators:
            fact, error = select(doc, facts, indicator, code=code)
            if fact is not None:
                operands.append(fact)
            if error:
                errors.append(error)
        key = f'expenditure:horizontal:{code}'
        if errors:
            unresolved.extend(errors)
            receipt(rule, doc, key, operands, status='insufficient_data', formula='total = sum(5 parts)', reason='；'.join(errors))
        else:
            found, missing = relation(rule, doc, key, f'支出决算表{code}横向合计', operands[0], operands[1:])
            issues.extend(found)
            unresolved.extend(missing)
    if not unresolved:
        for code in codes:
            if code == 'total':
                child_codes = [c for c in codes if len(c) == 3]
            else:
                child_codes = [c for c in codes if c.startswith(code) and len(c) == len(code) + 2]
            if not child_codes:
                continue
            for indicator in indicators:
                parent, _ = select(doc, facts, indicator, code=code)
                children = [select(doc, facts, indicator, code=c)[0] for c in child_codes]
                found, missing = relation(rule, doc, f'expenditure:vertical:{indicator}:{code}',
                    f'支出决算表{code}/{indicator}层级合计', parent, children)
                issues.extend(found)
                unresolved.extend(missing)
    if len(unresolved) > 12:
        # Relation receipts retain each row and reason; the rule summary
        # should remain readable for a table with many explicit blank cells.
        grouped, other = {}, []
        for reason in unresolved:
            match = re.fullmatch(r'(.+)的(.+)/决算操作数缺失或不唯一', reason)
            if match:
                grouped.setdefault(match.group(2), []).append(match.group(1))
            else:
                other.append(reason)
        unresolved = other + [f'支出表{key}金额操作数未绑定：科目{",".join(codes[:8])}'
                              f'（共{len(codes)}行，逐行原因已保留在关系回执）'
                              for key, codes in grouped.items()]
    return finish(rule, cluster_rounding(rule, issues), unresolved)
