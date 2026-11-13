"""决算表文比对只使用原文显式业务、阶段、单位和唯一事实身份。"""
from __future__ import annotations

import re
from decimal import Decimal

from .native_narrative_checks import _Text, _NUMBER, _UNIT, _wan, _foreign_owner
from .native_table_arithmetic import table_facts, select, receipt, finish
from .native_three_public_ratios import operand_ref, subject_before


def sections(doc, match):
    from .rules_v33 import _nar_repeat_sections, _txt_fund_merged_pages
    text = _Text.from_pages(doc.page_texts)
    merged, offsets = _txt_fund_merged_pages(doc)
    # Both helpers concatenate original pages with one newline. Require the
    # same offsets rather than trying to locate a normalized amount afterwards.
    if merged != text.raw.rstrip('\n') and merged != text.raw:
        return text, []
    ranges = [(title, *text.compact_range(begin, end))
              for title, begin, end in _nar_repeat_sections(merged, doc, offsets)
              if match(re.sub(r'\s+', '', title))]
    return text, ranges[-1:]


def compare(rule, doc, text, start, end, value, fact, key, label, section):
    refs = text.refs(start, end, '原文金额声明')
    ids, evidence = receipt(rule, doc, key, [fact],
            status='fail' if abs(value - fact.normalized_value) > Decimal('.5') else 'pass',
            formula='explicit narrative amount == current table amount (万元)')
    if abs(value - fact.normalized_value) <= Decimal('.5'):
        return []
    pages = sorted({ref['page'] for ref in refs} | {e.get('physical_page') for e in fact.extra_dims.get('evidence_refs', [])
                                                 if e.get('physical_page')})
    quote = '\n'.join(ref['span'] for ref in refs)
    return [rule._issue(f'{label}表文不一致：原文{value:.2f}万元，表内{fact.normalized_value:.2f}万元。',
             {'page': refs[0]['page'], 'pages': pages, 'section': section,
              'table_code': fact.table_code, 'table_refs': refs,
              'operand_refs': [operand_ref(fact, key)],
              'fact_context_version_token': doc.fact_context.version_token}, 'warn',
             evidence_text=quote, check_relation_id=f'{rule.code}:{key}',
             fact_ids=ids, evidence_ids=evidence)]


def context_guard(doc, compact, start, end):
    context = doc.fact_context
    # An explicit foreign period/owner is not an operand for this material.
    # Inspect the current sentence, preserving soft line wraps.
    begin = max(compact.rfind(ch, 0, start) for ch in '。；！？') + 1
    proof = compact[begin:end]
    years = re.findall(r'20\d{2}(?=年)', proof)
    return (any(int(year) != context.fiscal_year for year in years)
            or _foreign_owner(proof, context.org_name))


def check_three_public_totals(rule, doc):
    facts, reason = table_facts(doc, 'FIN_07_three_public')
    if reason:
        return finish(rule, [], [reason])
    text, ranges = sections(doc, lambda title: '三公' in title and '决算' in title)
    if not ranges:
        return finish(rule, [], ['未找到三公经费决算说明原文范围'])
    title, begin, end = ranges[0]
    pattern = re.compile(rf'(?P<phase>年初预算|全年预算|调整后预算|支出决算(?:数)?|决算数)'
                         rf'(?:为|共计|合计为|是)?(?P<amount>{_NUMBER})(?P<unit>{_UNIT})')
    claims = {}
    for match in pattern.finditer(text.compact, begin, end):
        indicator, _ = subject_before(text.compact[begin:match.start()])
        if indicator != 'total_actual' or context_guard(doc, text.compact, match.start(), match.end()):
            continue
        phase = match.group('phase')
        stage = '决算' if '决算' in phase else phase
        claims.setdefault(stage, []).append(match)
    issues, unresolved = [], []
    if '决算' not in claims:
        unresolved.append('三公总体说明缺少显式决算金额，不能用预算金额替代')
    for stage, matches in claims.items():
        fact, error = select(doc, facts, 'total_actual', code='total', stage=stage)
        if error:
            unresolved.append(error)
            receipt(rule, doc, f'three_public:text:{stage}', [], status='insufficient_data',
                    formula='explicit narrative amount == table amount', reason=error)
            continue
        for match in matches:
            key = f'three_public:text:{stage}:{match.start()}'
            # Include the literal business subject in the source quote.
            _, offset = subject_before(text.compact[begin:match.start()])
            from .native_budget_phase import phase_reason
            phase_error = phase_reason(doc, fact, stage, "total_actual")
            if phase_error:
                unresolved.append(phase_error)
                receipt(rule, doc, key, [fact], status="insufficient_data", formula="text = table", reason=phase_error)
                continue
            issues.extend(compare(rule, doc, text, begin + offset, match.end(),
                 _wan(match.group('amount'), match.group('unit')), fact, key,
                 f'三公经费合计{stage}', title))
    return finish(rule, issues, unresolved)


def check_fiscal_grant_total(rule, doc):
    """只在说明四内比较财政拨款支出总计，保留原句及总计原格。

    说明一的全部资金收支总计经常与说明四印在同一页；页级首个
    “收入支出总计”既不能确定说明身份，也不能确定财政拨款口径。
    """
    from .native_summary_checks import _pick, _NAMES, T4

    facts, reason = table_facts(doc, T4)
    if reason:
        return finish(rule, [], [reason])
    text, ranges = sections(doc, lambda title: '财政拨款收入支出决算总体情况说明' in title)
    if not ranges:
        return finish(rule, [], ['未定位到说明四财政拨款总体情况原文范围'])
    title, begin, end = ranges[0]
    pattern = re.compile(rf'(?:财政拨款)?(?:收入支出|收支)(?:总计|合计|总额)'
                         rf'(?:为|共计|是)?(?P<amount>{_NUMBER})(?P<unit>{_UNIT})')
    claims = list(pattern.finditer(text.compact, begin, end))
    issues, unresolved = [], []
    if not claims:
        return finish(rule, [], ['说明四未披露可绑定的财政拨款收支总计金额及单位'])
    fact, error = _pick(doc, facts, 'expenditure_actual', _NAMES['total'])
    for claim in claims:
        key = f'grant:text:total:{claim.start()}'
        invalid = error or ('说明四总计声明属于其他年度或主体' if context_guard(
            doc, text.compact, claim.start(), claim.end()) else None)
        if invalid:
            unresolved.append(invalid)
            receipt(rule, doc, key, [fact] if fact else [], status='insufficient_data',
                    formula='说明四财政拨款总计 = T4支出总计', reason=invalid)
            continue
        issues.extend(compare(rule, doc, text, claim.start(), claim.end(),
                      _wan(claim['amount'], claim['unit']), fact, key,
                      '说明4↔T4财政拨款总计', title))
    return finish(rule, issues, unresolved)


def check_income_structure(rule, doc):
    facts, reason = table_facts(doc, 'FIN_02_income')
    if reason:
        return finish(rule, [], [reason])
    text, ranges = sections(doc, lambda title: '收入决算情况说明' in title
            and not any(word in title for word in ('财政拨款', '政府性基金', '国有资本')))
    if not ranges:
        return finish(rule, [], ['未找到收入决算情况说明原文范围'])
    title, begin, end = ranges[0]
    labels = {'本年收入合计': 'total_actual', '财政拨款收入': 'fiscal_allocation',
              '上级补助收入': 'superior_subsidy_income', '事业收入': 'business_income',
              '经营收入': 'operational_income', '附属单位上缴收入': 'subsidiary_income',
              '其他收入': 'other_income'}
    pattern = re.compile(rf"(?P<label>{'|'.join(labels)})(?:为|共计|合计为|是)?"
                         rf'(?P<amount>{_NUMBER})(?P<unit>{_UNIT})')
    issues, unresolved, seen = [], [], set()
    for match in pattern.finditer(text.compact, begin, end):
        if context_guard(doc, text.compact, match.start(), match.end()):
            continue
        label = match.group('label')
        seen.add(label)
        fact, error = select(doc, facts, labels[label], code='total')
        key = f'income:text:{labels[label]}:{match.start()}'
        if error:
            unresolved.append(error)
            receipt(rule, doc, key, [], status='insufficient_data', formula='text = table', reason=error)
            continue
        issues.extend(compare(rule, doc, text, match.start(), match.end(),
                       _wan(match.group('amount'), match.group('unit')), fact, key, label, title))
    if '本年收入合计' not in seen:
        unresolved.append('收入决算说明未识别到本年度收入合计金额')
    return finish(rule, issues, unresolved)


def check_three_public_details(rule, doc):
    """Compare original detail declarations with uniquely bound current FIN_07 cells."""
    from .native_three_public_ratios import _SUBJECT, LABELS
    facts, reason = table_facts(doc, 'FIN_07_three_public')
    if reason:
        return finish(rule, [], [reason])
    text, ranges = sections(doc, lambda title: '三公' in title and '决算' in title)
    if not ranges:
        return finish(rule, [], ['未定位到三公经费决算说明原文'])
    title, begin, end = ranges[0]
    pattern = re.compile(rf'(?:支出)?(?:决算|预算)?(?:数)?(?:为|是)?(?P<amount>{_NUMBER})(?P<unit>{_UNIT})')
    issues, unresolved, seen = [], [], set()
    # The concrete-detail subsection takes precedence over its summary.
    detail = re.search(r'[（(]二[）)]具体情况说明', text.compact[begin:end])
    detail_begin = begin + detail.end() if detail else begin
    for subject in _SUBJECT.finditer(text.compact, detail_begin, end):
        indicator = subject.lastgroup
        if indicator == 'total_actual':
            continue
        # Subject regex accepts 费 and standalone subjects ending in 支出.
        at = subject.end()
        if text.compact.startswith('支出', at):
            at += 2
        match = pattern.match(text.compact, at, end)
        if not match or context_guard(doc, text.compact, subject.start(), match.end()):
            continue
        seen.add(indicator)
        fact, error = select(doc, facts, indicator, code='total', stage='决算')
        key = f'three_public:detail:{indicator}:{subject.start()}'
        value = _wan(match.group('amount'), match.group('unit'))
        if error:
            unresolved.append(f'{LABELS[indicator]}：{error}')
            receipt(rule, doc, key, [], status='insufficient_data', formula='text = table', reason=error)
            continue
        issues.extend(compare(rule, doc, text, subject.start(), match.end(), value, fact, key, LABELS[indicator], title))
    for indicator in ('overseas', 'vehicle_purchase', 'vehicle_operation', 'reception'):
        if indicator not in seen:
            reason = f'{LABELS[indicator]}具体说明未识别到显式金额'
            unresolved.append(reason)
            receipt(rule, doc, f'three_public:detail:{indicator}:missing', [], status='insufficient_data', formula='text = table', reason=reason)
    try:
        issues.extend(check_three_public_totals(rule, doc))
    except Exception as signal:
        from .rule_outcome import RuleDeferred
        if not isinstance(signal, RuleDeferred):
            raise
        issues.extend(signal.partial_issues or [])
        unresolved.extend(signal.unresolved_reasons or [signal.detail])
    return finish(rule, issues, unresolved)
