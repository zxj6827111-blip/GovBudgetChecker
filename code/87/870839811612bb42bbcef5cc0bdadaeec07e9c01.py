"""决算公开补充核验：功能说明、基金总额、支出占比和绩效数量。

操作数来自同一解析版本的唯一财政事实或同一绩效范围的原文声明。
未披露金额、评级数量及空白表格均不补零；关系证据不足时明确上报。
"""
from __future__ import annotations

import re
from decimal import Decimal

from .native_narrative_checks import (
    _Text, _NUMBER, _UNIT, _wan, _metadata, _sections, _foreign_owner,
    _CALIBER_CHANGE,
)
from .native_table_arithmetic import table_facts, select, receipt, finish
from .native_table_text import sections
from .native_three_public_ratios import operand_ref


GENERAL = 'FIN_05_general_public_expenditure'
FUND = 'FIN_08_gov_fund'
_ITEM = re.compile(
    r'(?P<klass>[^\d，,。；;：:、.()“”]{1,60}?)\(类\)'
    r'(?P<kuan>[^\d，,。；;：:、.()“”]{1,60}?)\(款\)'
    r'(?P<xiang>[^\d，,。；;：:、.()“”]{1,60}?)\(项\)')
_DECISION = re.compile(rf'(?:支出决算(?:数)?|决算数)(?:为|是)?'
                       rf'(?P<amount>{_NUMBER})(?P<unit>{_UNIT})')
_GENERAL_AMOUNT = re.compile(
    rf'一般公共预算(?:财政拨款)?支出(?:决算(?:数)?)?(?:为|共计|合计为|是)?'
    rf'(?P<amount>{_NUMBER})(?P<unit>{_UNIT})')
_SHARE = re.compile(rf'[，,]占本年支出(?:合计|总额)(?:的)?(?P<pct>{_NUMBER})%')
_FUND_LABEL = r'政府性基金预算(?:财政拨款)?'
_FUND_INCOME = re.compile(
    rf'{_FUND_LABEL}(?:本年)?收入(?:合计|决算(?:数)?)?(?:为|共计|是)?'
    rf'(?P<amount>{_NUMBER})(?P<unit>{_UNIT})')
_FUND_EXPENDITURE = re.compile(
    rf'{_FUND_LABEL}(?:本年)?支出(?:合计|决算(?:数)?)?(?:为|共计|是)?'
    rf'(?P<amount>{_NUMBER})(?P<unit>{_UNIT})')
_BARE_EXPENDITURE = re.compile(
    rf'[，,](?:本年)?支出(?:合计|决算(?:数)?)?(?:为|是)?'
    rf'(?P<amount>{_NUMBER})(?P<unit>{_UNIT})')
_SELF_COUNT = re.compile(
    r'(?:绩效自评(?:的)?(?:20\d{2}年度)?项目|'
    r'(?:开展|组织)?(?:项目支出)?绩效自评(?:项目)?(?:共|涉及)?)'
    r'(?P<count>\d+)个(?:项目)?')
_RATING = re.compile(
    r'(?:绩效)?评级为[“"「]?(?P<rating>不合格|合格|优|良)[”"」]?'
    r'(?:的)?项目(?P<count>\d+)个')


def _name(value):
    # Only the optional terminal 支出 is a documented naming variation. Do not
    # shorten subject stems: names such as 其他支出 are distinct identities.
    return re.sub(r'支出$', '', re.sub(r'\s+', '', str(value or '')))


def _unresolved(rule, doc, key, reason, unresolved, facts=(), formula='text = table'):
    unresolved.append(reason)
    receipt(rule, doc, key, list(facts), status='insufficient_data',
            formula=formula, reason=reason)


def _source_refs(facts):
    result = []
    for fact in facts:
        for ref in (fact.extra_dims or {}).get('evidence_refs', []):
            if ref.get('physical_page') and ref.get('quote'):
                result.append({'page': ref['physical_page'], 'span': ref['quote'],
                               'bbox': ref.get('bbox'), 'role': '原始表格金额',
                               'fact_uid': fact.fact_uid,
                               'source_cell_id': ref.get('source_cell_id'),
                               'evidence_id': ref.get('evidence_id')})
    return result


def _location(doc, refs, facts, section, relation, **extra):
    fact_source_refs = _source_refs(facts)
    pages = sorted({ref['page'] for ref in list(refs) + fact_source_refs if ref.get('page')})
    result = {'page': refs[0]['page'] if refs else None, 'pages': pages,
            'bbox': None, 'section': section, 'relation_type': relation,
            # Source binding authenticates fiscal cells through fact identity,
            # not a repeated bare number anywhere on the same table page.
            'table_refs': list(refs), 'fact_source_refs': fact_source_refs,
            'fact_context_version_token': getattr(getattr(doc, 'fact_context', None),
                                                  'version_token', None), **extra}
    if facts:
        result['operand_refs'] = [operand_ref(fact, f'{relation}:{i}')
                                  for i, fact in enumerate(facts)]
    return result


def _quote(location):
    return '\n'.join(ref['span'] for ref in location['table_refs'] + location['fact_source_refs'])


def _guard(doc, text, begin, start, end):
    context = getattr(doc, 'fact_context', None)
    if context is None or context.fiscal_year is None:
        return '未识别到当前材料的财政主体或年度'
    # Only the declaration prefix carries owner/year identity. Purpose text
    # legitimately names subordinate beneficiaries and is not a foreign owner.
    sentence = max((text.compact.rfind(ch, begin, start) for ch in '。；;'), default=begin - 1) + 1
    proof = text.compact[max(begin, sentence):end]
    years = re.findall(r'(20\d{2})(?:年度|年)', proof)
    if any(int(year) != context.fiscal_year for year in years):
        return '原文声明属于其他年度，不能与当前决算事实比较'
    prefix = text.compact[max(begin, sentence):start]
    if re.search(r'(?:上年|去年|以前年度|年初预算|年初结转)(?:度|同期)?[^，,。；;]*$', prefix):
        return '原文声明采用上年或年初口径，不能替代本年决算金额'
    if _foreign_owner(proof, context.org_name):
        return '原文声明包含其他主体，不能与本材料事实跨主体比较'
    if _CALIBER_CHANGE.search(proof):
        return '原文声明采用调整口径，需人工确认可比范围'
    return None


def _amount(rule, doc, text, begin, end, amount, unit, fact, key, label, section):
    declared = _wan(amount, unit)
    difference = declared - fact.normalized_value
    failed = abs(difference) > Decimal('.5')
    ids, evidence = receipt(rule, doc, key, [fact], status='fail' if failed else 'pass',
            formula='explicit narrative amount == current table amount (万元)')
    if not failed:
        return []
    refs = text.refs(begin, end, '原文决算金额声明')
    original = str(fact.raw_value or '').replace(',', '').replace('，', '').strip()
    original = re.sub(r'(?:亿元|万元|元)$', '', original)
    unit_error = (unit != fact.unit and re.fullmatch(_NUMBER, original or '')
                  and Decimal(original) == Decimal(amount.replace(',', '')))
    message = (f'{label}金额单位口径错误：' if unit_error else f'{label}表文金额不一致：')
    message += (f'原文{amount}{unit}折合{declared}万元，表内{fact.normalized_value:.2f}万元，'
                f'相差{abs(difference):.4f}万元。')
    location = _location(doc, refs, [fact], section, 'final_disclosure_amount',
                         table_code=fact.table_code, subject_code=fact.subject_code,
                         indicator=fact.indicator, declared_amount_wan=str(declared),
                         table_amount_wan=str(fact.normalized_value),
                         declared_unit=unit, difference_wan=str(difference),
                         tolerance_wan='0.5', unit_magnitude_error=bool(unit_error),
                         portable_rule_id='R001' if unit_error else 'D-005')
    return [rule._issue(message, location, 'error',
                        evidence_text=_quote(location),
                        check_relation_id=f'{rule.code}:{key}', fact_ids=ids,
                        evidence_ids=evidence)]


def _hierarchy_fact(doc, facts, item):
    parent = ''
    for group, width in [('klass', 3), ('kuan', 5), ('xiang', 7)]:
        candidates = {str(f.subject_code) for f in facts
                      if len(str(f.subject_code or '')) == width
                      and str(f.subject_code).isdigit()
                      and str(f.subject_code).startswith(parent)
                      and _name(f.subject_name) == _name(item[group])}
        if len(candidates) != 1:
            return None, f'功能分类{item[group]}的{group}科目身份缺失或不唯一'
        parent = next(iter(candidates))
    return select(doc, facts, 'total_actual', code=parent)


def _general_ranges(doc):
    return sections(doc, lambda title: '一般公共预算' in title and '支出决算情况说明' in title
                    and '基本支出' not in title)


def check_function_disclosures(rule, doc):
    """FIN_05 合计、类级结构及每个类款项显式决算金额和单位。"""
    text, ranges = _general_ranges(doc)
    if not ranges:
        return finish(rule, [], ['未找到一般公共预算财政拨款支出决算说明原文范围'])
    facts, reason = table_facts(doc, GENERAL)
    if reason:
        return finish(rule, [], [reason])
    title, begin, end = ranges[0]
    issues, unresolved = [], []
    totals = list(_GENERAL_AMOUNT.finditer(text.compact, begin, end))
    if not totals:
        _unresolved(rule, doc, 'general:text:total:missing',
                    '一般公共预算说明未识别到显式本年支出合计及金额单位', unresolved)
    for claim in totals:
        key = f'general:text:total:{claim.start()}'
        fact, error = select(doc, facts, 'total_actual', code='total')
        error = error or _guard(doc, text, begin, claim.start(), claim.end())
        if error:
            _unresolved(rule, doc, key, error, unresolved, [fact] if fact else [])
        else:
            issues.extend(_amount(rule, doc, text, claim.start(), claim.end(), claim['amount'],
                          claim['unit'], fact, key, '一般公共预算支出合计', title))
    # Class structure values are direct name+amount statements. A heading
    # followed by 年初预算 or a purpose paragraph cannot match this pattern.
    for code in sorted({str(f.subject_code) for f in facts
                        if re.fullmatch(r'\d{3}', str(f.subject_code or ''))}):
        identities = {_name(f.subject_name) for f in facts if f.subject_code == code}
        if len(identities) != 1 or not next(iter(identities)):
            continue
        label = next(iter(identities))
        pattern = re.compile(rf'{re.escape(label)}(?:支出)?(?:为)?'
                             rf'(?P<amount>{_NUMBER})(?P<unit>{_UNIT})')
        for claim in pattern.finditer(text.compact, begin, end):
            key = f'general:text:class:{code}:{claim.start()}'
            fact, error = select(doc, facts, 'total_actual', code=code)
            error = error or _guard(doc, text, begin, claim.start(), claim.end())
            if error:
                _unresolved(rule, doc, key, error, unresolved, [fact] if fact else [])
            else:
                issues.extend(_amount(rule, doc, text, claim.start(), claim.end(), claim['amount'],
                              claim['unit'], fact, key, label, title))
    items = list(_ITEM.finditer(text.compact, begin, end))
    for index, item in enumerate(items):
        stop = items[index + 1].start() if index + 1 < len(items) else end
        # Local numbered headings end an item even if the following entry has
        # no recognized 类款项 syntax. Never borrow its decision amount.
        raw_start, raw_stop = text.positions[item.end() - 1] + 1, text.positions[stop - 1] + 1
        next_heading = re.search(r'(?:\n|^)[ \t]*(?:\d+[、．]|\d+\.(?=\D))',
                                 text.raw[raw_start:raw_stop])
        if next_heading:
            _, stop = text.compact_range(raw_start, raw_start + next_heading.start())
        claims = list(_DECISION.finditer(text.compact, item.end(), stop))
        key = f'general:text:item:{item.start()}'
        fact, error = _hierarchy_fact(doc, facts, item)
        # 博物馆、图书馆等 are literal fiscal subjects, not publisher names.
        # Their hierarchy is already validated above; only the declaration
        # prefix can introduce a foreign owner or year for this item.
        error = error or _guard(doc, text, begin, item.start(), item.start())
        if len(claims) != 1:
            error = error or f'{item["xiang"]}条目显式决算金额缺失或不唯一，不能用预算金额替代'
        if error:
            _unresolved(rule, doc, key, error, unresolved, [fact] if fact else [])
        else:
            claim = claims[0]
            issues.extend(_amount(rule, doc, text, item.start(), claim.end(), claim['amount'],
                          claim['unit'], fact, key, item['xiang'], title))
    return finish(rule, issues, unresolved)


def check_fund_totals(rule, doc):
    """政府性基金本年总收入、总支出：不使用结转或项目金额替代。"""
    text, ranges = sections(doc, lambda title: '政府性基金' in title and '收入支出决算情况说明' in title)
    if not ranges:
        return finish(rule, [], ['未找到政府性基金收入支出决算说明原文范围'])
    title, begin, end = ranges[0]
    claims = []
    for claim in _FUND_INCOME.finditer(text.compact, begin, end):
        claims.append(('income_actual', '政府性基金本年收入', claim, claim.start()))
        # This bare expenditure has the same explicit fund subject only when
        # it directly follows the income amount, without another period/item.
        bare = _BARE_EXPENDITURE.match(text.compact, claim.end(), end)
        if bare:
            claims.append(('total_actual', '政府性基金本年支出', bare, claim.start()))
    claims += [('total_actual', '政府性基金本年支出', claim, claim.start())
               for claim in _FUND_EXPENDITURE.finditer(text.compact, begin, end)]
    if not claims:
        # An explicit no-income/no-expenditure statement is a valid empty
        # disclosure; no blank table cell is turned into an amount here.
        if re.search(r'(?:无|没有|未发生)[^。]{0,40}(?:政府性基金|收入和支出)', text.compact[begin:end]):
            receipt(rule, doc, 'fund:text:empty', [], status='not_applicable',
                    formula='explicit no government fund activity')
            return []
        return finish(rule, [], ['政府性基金说明未识别到明确的本年总收入或总支出金额'])
    facts, reason = table_facts(doc, FUND)
    if reason:
        return finish(rule, [], [reason])
    issues, unresolved = [], []
    for indicator, label, claim, proof_start in claims:
        key = f'fund:text:{indicator}:{claim.start()}'
        fact, error = select(doc, facts, indicator, code='total')
        error = error or _guard(doc, text, begin, proof_start, claim.end())
        if error:
            _unresolved(rule, doc, key, error, unresolved, [fact] if fact else [])
        else:
            issues.extend(_amount(rule, doc, text, proof_start, claim.end(), claim['amount'],
                          claim['unit'], fact, key, label, title))
    return finish(rule, issues, unresolved)


def _percentage(rule, doc, text, begin, end, numerator, denominator, declared, key, label, title):
    facts = [numerator, denominator]
    formula = 'current expenditure / explicitly scoped current expenditure total * 100'
    if numerator.org_scope != denominator.org_scope:
        return [], ['分子分母合并主体范围不一致']
    bottom = denominator.normalized_value
    if bottom <= 0:
        return [], ['支出占比的分母为零或负数，不能复算有限占比']
    computed = numerator.normalized_value / bottom * Decimal('100')
    difference = declared - computed
    failed = abs(difference) > Decimal('.1')
    ids, evidence = receipt(rule, doc, key, facts, status='fail' if failed else 'pass', formula=formula)
    if not failed:
        return [], []
    refs = text.refs(begin, end, '原文支出占比声明')
    location = _location(doc, refs, facts, title, 'final_expenditure_share',
                         table_code=numerator.table_code, declared_percent=str(declared),
                         computed_percent=str(computed), difference_pp=str(difference),
                         tolerance_pp='0.1', denominator_table_code=denominator.table_code,
                         denominator_subject_code=denominator.subject_code,
                         denominator_indicator=denominator.indicator, formula=formula)
    message = (f'{label}占比与明确分母不一致：原文{declared}%，'
               f'表内{numerator.normalized_value:.2f}÷{bottom:.2f}×100={computed:.2f}%。')
    return [rule._issue(message, location, 'error',
                       evidence_text=_quote(location),
                       check_relation_id=f'{rule.code}:{key}', fact_ids=ids,
                       evidence_ids=evidence)], []


def check_final_expenditure_shares(rule, doc):
    """一般公共支出占本年全口径支出；其类级占比以一般公共支出作分母。"""
    text, ranges = _general_ranges(doc)
    if not ranges:
        return finish(rule, [], ['未找到一般公共预算支出占比说明原文范围'])
    title, begin, end = ranges[0]
    facts, reason = table_facts(doc, GENERAL)
    if reason:
        return finish(rule, [], [reason])
    issues, unresolved = [], []
    for claim in _GENERAL_AMOUNT.finditer(text.compact, begin, end):
        share = _SHARE.match(text.compact, claim.end(), end)
        if not share:
            continue
        key = f'general:share:all_expenditure:{share.start()}'
        all_facts, error = table_facts(doc, 'FIN_03_expenditure')
        numerator, nr = select(doc, facts, 'total_actual', code='total')
        denominator, dr = select(doc, all_facts, 'total_actual', code='total') if not error else (None, error)
        error = nr or dr or _guard(doc, text, begin, claim.start(), share.end())
        if denominator is not None and denominator.fund_scope not in (None, 'all', 'all_funds'):
            error = error or '本年支出合计事实并非全资金口径，不能作为本年支出分母'
        operands = [fact for fact in (numerator, denominator) if fact is not None]
        if error:
            _unresolved(rule, doc, key, error, unresolved, operands, 'general / all expenditure * 100')
            continue
        found, missing = _percentage(rule, doc, text, claim.start(), share.end(), numerator,
                    denominator, Decimal(share['pct'].replace(',', '')), key,
                    '一般公共预算支出占本年支出合计', title)
        issues.extend(found)
        for error in missing:
            _unresolved(rule, doc, key, error, unresolved, operands, 'general / all expenditure * 100')
    for code in sorted({str(f.subject_code) for f in facts
                        if re.fullmatch(r'\d{3}', str(f.subject_code or ''))}):
        names = {_name(f.subject_name) for f in facts if f.subject_code == code}
        if len(names) != 1 or not next(iter(names)):
            continue
        label = next(iter(names))
        pattern = re.compile(rf'{re.escape(label)}(?:支出)?(?:为)?{_NUMBER}{_UNIT}'
                             rf'[，,]占(?:一般公共预算(?:财政拨款)?支出(?:的)?)?'
                             rf'(?P<pct>{_NUMBER})%')
        for claim in pattern.finditer(text.compact, begin, end):
            key = f'general:share:class:{code}:{claim.start()}'
            numerator, nr = select(doc, facts, 'total_actual', code=code)
            denominator, dr = select(doc, facts, 'total_actual', code='total')
            error = nr or dr or _guard(doc, text, begin, claim.start(), claim.end())
            operands = [fact for fact in (numerator, denominator) if fact is not None]
            if error:
                _unresolved(rule, doc, key, error, unresolved, operands, 'class / general * 100')
                continue
            found, missing = _percentage(rule, doc, text, claim.start(), claim.end(), numerator,
                       denominator, Decimal(claim['pct'].replace(',', '')), key,
                       f'{label}占一般公共预算支出', title)
            issues.extend(found)
            for error in missing:
                _unresolved(rule, doc, key, error, unresolved, operands, 'class / general * 100')
    return finish(rule, issues, unresolved)


def check_performance_counts(rule, doc):
    """同一自评范围的四项评级数量合计，不跨绩效目标/跟踪评价取总数。"""
    text = _Text.from_pages(getattr(doc, 'page_texts', None) or [])
    year, owner, year_refs = _metadata(doc, text)
    issues, unresolved = [], []
    for title, begin, end in _sections(text):
        if '绩效' not in title:
            continue
        totals = list(_SELF_COUNT.finditer(text.compact, begin, end))
        if not totals and _RATING.search(text.compact, begin, end):
            _unresolved(rule, doc, f'performance:self_evaluation_count:missing:{begin}',
                        '绩效评级已披露但未识别到同范围自评项目总数，不能借用绩效目标或跟踪评价数量',
                        unresolved, formula='self_evaluation_count = sum(4 rating counts)')
        for index, total in enumerate(totals):
            stop = totals[index + 1].start() if index + 1 < len(totals) else end
            # The standard rating parenthesis may contain semicolons and one
            # sentence stop. An explicit following task changes the scope.
            switch = re.search(r'绩效自评中|(?:部门|重点|财政)?(?:绩效)?评价(?:的|项目|工作)'
                               r'|绩效跟踪|编报绩效目标|其中\d+个(?:重点|专项)',
                               text.compact[total.end():stop])
            if switch:
                stop = total.end() + switch.start()
            ratings = list(_RATING.finditer(text.compact, total.end(), stop))
            key = f'performance:self_evaluation_ratings:{total.start()}'
            if not ratings:
                continue  # No classification declared: no sum relation exists.
            prefix_start = max((text.compact.rfind(ch, begin, total.start()) for ch in '。；;'), default=begin - 1) + 1
            proof = text.compact[max(begin, prefix_start):total.end()]
            local_years = [int(value) for value in re.findall(r'(20\d{2})年度', proof)]
            reason = None
            if year is None or (local_years and any(value != year for value in local_years)):
                reason = '绩效自评数量年度未识别到或属于其他年度'
            elif re.search(r'(?:上年|去年|以前年度)[^，,。；;]*$',
                           text.compact[max(begin, prefix_start):total.start()]):
                reason = '绩效自评数量采用上年范围，不能与本年自评混用'
            elif _foreign_owner(proof, owner) or _CALIBER_CHANGE.search(proof):
                reason = '绩效自评数量主体或统计范围需要确认'
            elif (len(ratings) != 4 or {claim['rating'] for claim in ratings} != {'优', '良', '合格', '不合格'}):
                reason = '同一自评范围的优、良、合格、不合格四项数量未完整且唯一披露，不能补零'
            if reason:
                _unresolved(rule, doc, key, reason, unresolved, formula='self_evaluation_count = sum(4 rating counts)')
                continue
            parts = {claim['rating']: int(claim['count']) for claim in ratings}
            declared, computed = int(total['count']), sum(parts.values())
            receipt(rule, doc, key, [], status='fail' if declared != computed else 'pass',
                    formula='self_evaluation_count = excellent + good + qualified + unqualified')
            if declared == computed:
                continue
            refs = text.refs(total.start(), ratings[-1].end(), '同一范围自评项目及四项评级数量')
            if not local_years:
                refs += year_refs
            location = _location(doc, refs, [], title, 'performance_self_evaluation_count',
                                 declared_count=declared, rating_counts=parts,
                                 computed_count=computed, difference_count=declared - computed,
                                 current_year=year, tolerance_count=0)
            issues.append(rule._issue(
                f'绩效自评项目数量与评级分项之和不一致：自评{declared}个，'
                f'优{parts["优"]}+良{parts["良"]}+合格{parts["合格"]}+不合格{parts["不合格"]}'
                f'={computed}个，相差{abs(declared - computed)}个。', location, 'error',
                evidence_text='\n'.join(ref['span'] for ref in refs),
                check_relation_id=f'{rule.code}:{key}'))
    return finish(rule, issues, unresolved)
