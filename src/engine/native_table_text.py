"""决算表文比对只使用原文显式业务、阶段、单位和唯一事实身份。"""
from __future__ import annotations

import re
from decimal import Decimal

from .native_narrative_checks import _Text, _NUMBER, _UNIT, _wan, _foreign_owner
from .rule_policy import amount_difference_exceeds_policy
from .native_table_arithmetic import table_facts, select, receipt, finish
from .native_three_public_ratios import operand_ref, subject_before
from .rule_outcome import RuleDeferred


def is_general_expenditure_title(title):
    """“一般公共预算财政拨款支出决算情况说明”章节（说明五）的标题判定。

    上海 2022 版把三公章节写成“一般公共预算财政拨款“三公”经费支出决算情况说明”，
    基本支出章节也带“一般公共预算”；两者都不是说明五，取最后一个匹配时会读错章节。
    """
    title = re.sub(r'\s+', '', title)
    return ('一般公共预算' in title and '支出决算情况说明' in title
            and '基本支出' not in title and '三公' not in title)


def sections(doc, match):
    from .rules_v33 import _nar_repeat_sections, _txt_fund_merged_pages
    text = _Text.from_pages(doc.page_texts)
    merged, offsets = _txt_fund_merged_pages(doc)
    # Both helpers concatenate original pages with one newline. Require the
    # same offsets rather than trying to locate a normalized amount afterwards.
    if merged != text.raw[:-1] and merged != text.raw:
        return text, []
    ranges = [(title, *text.compact_range(begin, end))
              for title, begin, end in _nar_repeat_sections(merged, doc, offsets)
              if match(re.sub(r'\s+', '', title))]
    return text, ranges[-1:]


def compare(rule, doc, text, start, end, value, fact, key, label, section):
    refs = text.refs(start, end, '原文金额声明')
    from .native_empty_operands import NativeBlankTerm
    if isinstance(fact, NativeBlankTerm):
        reason = (f'{label}原文明确为零/无支出，表内对应格经原件认证为空白'
                  if value == 0 else f'{label}原文有金额而表内对应原件格留空，需核对披露')
        receipt(rule, doc, key, [fact], status='not_applicable' if value == 0 else 'insufficient_data',
                formula='explicit narrative disclosure + certified blank observation', reason=reason,
                disclosure_sources=[{**ref, 'file_hash': doc.fact_context.file_hash} for ref in refs])
        if value != 0:
            raise RuleDeferred(rule.code, detail=reason)
        return []
    ids, evidence = receipt(rule, doc, key, [fact],
            status='fail' if amount_difference_exceeds_policy(value - fact.normalized_value) else 'pass',
            formula='explicit narrative amount == current table amount (万元)')
    if not amount_difference_exceeds_policy(value - fact.normalized_value):
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
    from .native_no_activity import require_no_activity_disposition
    require_no_activity_disposition(rule, doc)
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
        fact, error = select(doc, facts, 'total_actual', code='total', stage=stage, allow_native_blank=True)
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


def check_three_public_details(rule, doc, *, require_vehicle_details=True, check_percentages=False):
    """Compare original detail declarations with uniquely bound current FIN_07 cells."""
    from .native_three_public_ratios import _SUBJECT, LABELS
    from .native_empty_operands import NativeBlankTerm
    from .native_no_activity import require_no_activity_disposition
    require_no_activity_disposition(rule, doc)
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
    zero_vehicle_sources = []
    stated = {}  # 原文显式金额：indicator -> (金额, 原文起止)
    for subject in _SUBJECT.finditer(text.compact, detail_begin, end):
        indicator = subject.lastgroup
        if indicator == 'total_actual':
            continue
        # Subject regex accepts 费 and standalone subjects ending in 支出.
        at = subject.end()
        if text.compact.startswith('支出', at):
            at += 2
        match = pattern.match(text.compact, at, end)
        absence = re.search(r'(?:无|没有|未发生)$', text.compact[max(detail_begin, subject.start() - 4):subject.start()])
        absent = absence is not None and (at == end or text.compact[at] in '。；;，,')
        statement_end = at if absent else (match.end() if match else at)
        if (not match and not absent) or context_guard(doc, text.compact, subject.start(), statement_end):
            continue
        seen.add(indicator)
        fact, error = select(doc, facts, indicator, code='total', stage='决算', allow_native_blank=True)
        key = f'three_public:detail:{indicator}:{subject.start()}'
        value = Decimal('0') if absent else _wan(match.group('amount'), match.group('unit'))
        if error:
            unresolved.append(f'{LABELS[indicator]}：{error}')
            receipt(rule, doc, key, [], status='insufficient_data', formula='text = table', reason=error)
            continue
        if indicator == 'vehicle_purchase_operation' and value == 0 and fact.normalized_value == 0:
            start = subject.start() - len(absence.group()) if absent else subject.start()
            zero_vehicle_sources = [{**ref, 'file_hash': doc.fact_context.file_hash}
                                    for ref in text.refs(start, statement_end, '明确公务用车分项无支出')]
        if isinstance(fact, NativeBlankTerm):
            if value == 0:
                start = subject.start() - len(absence.group()) if absent else subject.start()
                sources = [{**ref, 'file_hash': doc.fact_context.file_hash}
                           for ref in text.refs(start, statement_end, '明确无此项说明')]
                receipt(rule, doc, key, [fact], status='not_applicable', formula='明确无此项说明与原件空白格一致',
                        reason=f'{LABELS[indicator]}原文明确无支出，表内原件单元格为空白', disclosure_sources=sources)
            else:
                reason = f'{LABELS[indicator]}说明有金额而表内原件留空，需核对披露'
                unresolved.append(reason)
                receipt(rule, doc, key, [fact], status='insufficient_data', formula='text = table', reason=reason)
            continue
        statement_start = subject.start() - len(absence.group()) if absent else subject.start()
        stated.setdefault(indicator, (value, statement_start, statement_end))
        issues.extend(compare(rule, doc, text, statement_start, statement_end, value, fact, key, LABELS[indicator], title))
    required = (('overseas', 'vehicle_purchase', 'vehicle_operation', 'reception')
                if require_vehicle_details else ('overseas', 'vehicle_purchase_operation', 'reception'))
    # 三项里只有一项通篇未提（上海 2022 常见“因公出国”零支出不写）：由原文三公决算合计
    # 减去另两项的显式金额得出，与表内一致才算已披露；两项以上未提仍转人工。
    # “无因公出国（境）团组”这类否定提法与推导出的 0 一致，不算另有提法。
    mentioned = {match.lastgroup for match in _SUBJECT.finditer(text.compact, begin, end)
                 if not re.search(r'(?:无|没有|未发生|未安排)$', text.compact[max(begin, match.start() - 4):match.start()])}
    totals = set()
    for match in re.finditer(rf'(?:支出决算(?:数)?|决算数)(?:为|共计|合计为|是)?(?P<amount>{_NUMBER})(?P<unit>{_UNIT})',
                             text.compact[begin:end]):
        if (subject_before(text.compact[begin:begin + match.start()])[0] == 'total_actual'
                and not context_guard(doc, text.compact, begin + match.start(), begin + match.end())):
            totals.add((_wan(match.group('amount'), match.group('unit')), begin + match.start(), begin + match.end()))
    stated_total = next(iter(totals)) if len({value for value, *_ in totals}) == 1 else None

    def stated_amount(name):
        if name in stated:
            return stated[name][0]
        if name == 'vehicle_purchase_operation' and {'vehicle_purchase', 'vehicle_operation'} <= stated.keys():
            return stated['vehicle_purchase'][0] + stated['vehicle_operation'][0]
        return None

    for indicator in required:
        if indicator not in seen:
            top = {'overseas', 'vehicle_purchase_operation', 'reception'}
            if indicator in top and indicator not in mentioned and stated_total is not None:
                others = [stated_amount(other) for other in sorted(top - {indicator})]
                term, error = select(doc, facts, indicator, code='total', stage='决算', allow_native_blank=True)
                table_value = (Decimal('0') if isinstance(term, NativeBlankTerm)
                               else term.normalized_value if term is not None else None)
                if (None not in others and not error and table_value is not None
                        and not amount_difference_exceeds_policy(stated_total[0] - sum(others) - table_value)):
                    sources = [{**ref, 'file_hash': doc.fact_context.file_hash}
                               for ref in text.refs(stated_total[1], stated_total[2], '三公经费决算合计原文')]
                    receipt(rule, doc, f'three_public:detail:{indicator}:by_total', [term],
                            status='not_applicable' if isinstance(term, NativeBlankTerm) else 'pass',
                            formula='omitted three-public item = stated total - stated other items == table',
                            reason=f'{LABELS[indicator]}原文未提，由三公决算合计{stated_total[0]}万元减其余两项得出，与表内一致',
                            disclosure_sources=sources)
                    continue
            if indicator in ('vehicle_purchase', 'vehicle_operation') and zero_vehicle_sources:
                term, error = select(doc, facts, indicator, code='total', stage='决算', allow_native_blank=True)
                if not error and term.normalized_value == 0:
                    receipt(rule, doc, f'three_public:detail:{indicator}:no_vehicle_activity', [term],
                            status='not_applicable', formula='explicit zero vehicle subtotal + source verified empty/zero child',
                            reason='原文明确公务用车购置及运行无支出，原件细项为已核实空白或零',
                            disclosure_sources=zero_vehicle_sources)
                    continue
            sibling = {'vehicle_purchase': 'vehicle_operation', 'vehicle_operation': 'vehicle_purchase'}.get(indicator)
            mention = {'vehicle_purchase': r'公务用车购置(?!及)', 'vehicle_operation': r'公务用车运行'}.get(indicator)
            if (sibling and 'vehicle_purchase_operation' in stated and sibling in stated
                    # 原文另有该项的提法（“购置情况待补充”“乙局无购置支出”）时不推导，仍转人工。
                    and not re.search(mention, text.compact[detail_begin:end])):
                # 原文写了“购置及运行维护费 X 万元，其中：运行维护 Y 万元”而未写购置：
                # 未写的一项由两句显式金额相减得出，与表内一致才算已披露，不按缺失补零。
                (subtotal, *span), (other, *other_span) = stated['vehicle_purchase_operation'], stated[sibling]
                term, error = select(doc, facts, indicator, code='total', stage='决算', allow_native_blank=True)
                table_value = (Decimal('0') if isinstance(term, NativeBlankTerm)
                               else term.normalized_value if term is not None else None)
                if not error and table_value is not None and not amount_difference_exceeds_policy(
                        subtotal - other - table_value):
                    sources = [{**ref, 'file_hash': doc.fact_context.file_hash}
                               for start, stop in (span, other_span)
                               for ref in text.refs(start, stop, '公务用车小计与另一分项原文')]
                    receipt(rule, doc, f'three_public:detail:{indicator}:by_subtotal', [term],
                            status='not_applicable' if isinstance(term, NativeBlankTerm) else 'pass',
                            formula='omitted vehicle detail = stated subtotal - stated sibling == table',
                            reason=f'{LABELS[indicator]}未单独写明，由原文小计{subtotal}万元减{LABELS[sibling]}'
                                   f'{other}万元得出，与表内一致', disclosure_sources=sources)
                    continue
            reason = f'{LABELS[indicator]}具体说明未识别到显式金额'
            unresolved.append(reason)
            receipt(rule, doc, f'three_public:detail:{indicator}:missing', [], status='insufficient_data', formula='text = table', reason=reason)
    try:
        issues.extend(check_three_public_totals(rule, doc))
    except RuleDeferred as signal:
        issues.extend(signal.partial_issues or [])
        unresolved.extend(signal.unresolved_reasons or [signal.detail])
    if check_percentages:
        from .native_three_public_ratios import check_shares
        from .rules_v33 import _final_section_scope
        found, missing = check_shares(rule, doc, _final_section_scope(doc, ('三公',)), doc.fact_context.fiscal_year)
        issues.extend(found)
        unresolved.extend(missing)
    return finish(rule, issues, unresolved)
