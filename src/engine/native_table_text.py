"""决算表文比对只使用原文显式业务、阶段、单位和唯一事实身份。"""
from __future__ import annotations

import re
import unicodedata
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
            and '基本支出' not in title and '本支出' not in title and '三公' not in title)


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


def compare(rule, doc, text, start, end, value, fact, key, label, section, *,
            disclosure_sources=(), unresolved=None):
    refs = text.refs(start, end, '原文金额声明', unique_context=True)
    from .native_empty_operands import NativeBlankTerm, independent_blank_zero, blank_observations, blank_operand_refs
    if isinstance(fact, NativeBlankTerm) and not independent_blank_zero(doc, fact):
        reason = (f'{label}原文明确为零/无支出，表内对应格经原件认证为空白'
                  if value == 0 else f'{label}原文有金额而表内对应原件格留空，需核对披露')
        receipt(rule, doc, key, [fact], status='not_applicable' if value == 0 else 'insufficient_data',
                formula='explicit narrative disclosure + certified blank observation', reason=reason,
                disclosure_sources=[{**ref, 'file_hash': doc.fact_context.file_hash} for ref in refs])
        if value != 0:
            if unresolved is None:
                raise RuleDeferred(rule.code, detail=reason)
            # 调用方还有其他金额声明时，只挂起当前关系，循环结束再汇总。
            unresolved.append(reason)
        return []
    ids, evidence = receipt(rule, doc, key, [fact],
            status='fail' if amount_difference_exceeds_policy(value - fact.normalized_value) else 'pass',
            formula='explicit narrative amount == current table amount (万元)',
            **({'disclosure_sources': list(disclosure_sources)} if disclosure_sources else {}))
    if not amount_difference_exceeds_policy(value - fact.normalized_value):
        return []
    pages = sorted({ref['page'] for ref in refs} | {e.get('physical_page') for e in fact.extra_dims.get('evidence_refs', [])
                                                 if e.get('physical_page')})
    quote = '\n'.join(ref['span'] for ref in refs)
    return [rule._issue(f'{label}表文不一致：原文{value:.2f}万元，表内{fact.normalized_value:.2f}万元。',
             {'page': refs[0]['page'], 'pages': pages, 'section': section,
              'table_code': fact.table_code, 'table_refs': refs,
              'operand_refs': [] if isinstance(fact, NativeBlankTerm) else [operand_ref(fact, key)],
              'native_blank_sources': blank_observations([fact]),
              'native_blank_operands': blank_operand_refs([fact], key),
              'fact_context_version_token': doc.fact_context.version_token}, 'warn',
             evidence_text=quote, check_relation_id=f'{rule.code}:{key}',
             fact_ids=ids, evidence_ids=evidence)]


def context_rejection(doc, compact, start, end):
    """区分金额缺失与已有金额的主体/年度冲突；不放宽业务身份。"""
    context = doc.fact_context
    begin = max(compact.rfind(ch, 0, start) for ch in '。；！？') + 1
    proof = compact[begin:end]
    years = re.findall(r'20\d{2}(?=年)', proof)
    if any(int(year) != context.fiscal_year for year in years):
        return ('scope_unconfirmed', 'year', '金额声明包含其他年度，需核对期间口径')
    if _foreign_owner(proof, context.org_name):
        return ('subject_conflict', 'organization', '金额已披露，但原文单位名称与材料主体不一致，需先核对主体名称')
    return None


def context_guard(doc, compact, start, end):
    return context_rejection(doc, compact, start, end) is not None


def record_context_rejection(doc, text, start, end, rejection):
    from .diagnostic_fields import blocker, record_gap
    code, dimension, reason = rejection
    refs = text.refs(start, end, '身份待核的原文金额声明')
    pages = sorted({ref['page'] for ref in refs})
    message = f'第{"、".join(map(str, pages))}页：{reason}'
    record_gap(doc, message, reason_codes=[code], blocked_by=[blocker(
        dimension=dimension, ref=message, reason_code=code)])
    return message


def _inside_full_quotation(compact, start, end):
    """整句引文可嵌套指标短引号；不能被内部“三公”的闭引号截断。"""
    for opening, closing in (('“', '”'), ('「', '」'), ('『', '』'), ('‘', '’')):
        stack = []
        for index, char in enumerate(compact):
            if char == opening:
                stack.append(index)
            elif char == closing and stack:
                left = stack.pop()
                if left <= start and end <= index:
                    return True
        if any(left <= start for left in stack):
            return True
    # 同一种直引号不能表达嵌套；断言首尾都在引用态时仍按引文拒收。
    return compact[:start].count('"') % 2 == compact[:end].count('"') % 2 == 1


def _three_public_absence_span(doc, text, title, begin, end):
    """本年财政拨款三公整体明确无支出，可核对各决算格；不证明预算为零。

    只接受原说明中的事实断言，复用零证据的否定/假设/引用/模板作用域守卫。
    与“合计碰巧为0”不同，明确无发生额的范围涵盖各分项；表格仍须逐格认证。
    """
    from .narrative_zero_evidence import (
        _clauses, _frame_scope_spans, _inside_quote_span, _is_assertive_context,
        _NON_FISCAL_SCOPE_RE,
    )
    context = getattr(doc, 'fact_context', None)
    if not context or not context.org_name or context.fiscal_year is None:
        return None
    # 章节范围含标题。标题里的“一般公共预算”只限定资金口径，不是正文中的预算期声明。
    heading = ''.join(unicodedata.normalize('NFKC', title).split())
    if text.compact.startswith(heading, begin, end):
        begin += len(heading)
    body = text.compact[begin:end]
    # 预测、局部期间或后置否定不能证明全年实际未发生。语义范围不清时
    # 仍保持未知，不能由当前表格的零值反推文字声明的期间与真实性。
    if re.search(r'预计|计划|预期|可能|拟|截至|截止|上半年|下半年|季度|月度|'
                 r'\d{1,2}月|本月|本季|不属实|不成立|不正确|不准确|有误|'
                 r'(?:不能|未能|尚未|无法)(?:确认|确定)|未必|不一定', body):
        return None
    absent = re.compile(r'(?:没有|无)(?:一般公共预算)?(?:财政拨款)?["“”]?三公["“”]?经费'
                        r'(?:财政拨款)?(?:支出决算|支出)(?=[。；;，,]|$)')
    frames, candidates = _frame_scope_spans(text), []
    for left, right in _clauses(text.compact):
        left, right = max(left, begin), min(right, end)
        if left >= right:
            continue
        clause = text.compact[left:right]
        matches = list(absent.finditer(clause))
        if not matches or _NON_FISCAL_SCOPE_RE.search(clause):
            continue
        if (not _is_assertive_context(text.compact, left, clause, frames)
                or context_guard(doc, text.compact, left, right)
                or '预算' in clause
                or re.search(r'并非|不是|不代表|不能说明|暂无|是否|未核实|待核实|'
                             r'(?:不能|未能|尚未|无法)(?:确认|确定)|不确定|未必|不一定', clause)):
            continue
        # 无主语的后句不能越过前面的异年/异主体声明继承当前材料身份。
        # 范围内出现外来身份时保守交回复核，不改动其他表文检查的语境规则。
        scope = text.compact[begin:right]
        if (any(int(year) != context.fiscal_year for year in re.findall(r'20\d{2}(?=年)', scope))
                or _foreign_owner(scope, context.org_name)):
            continue
        for match in matches:
            # 引号可跨句读；在整段原文中判定，不能先按句号切断闭引号。
            if (not _inside_quote_span(text.compact, left + match.start(), left + match.end())
                    and not _inside_full_quotation(text.compact, left + match.start(), left + match.end())):
                candidates.append((left, right))
    if not candidates:
        return None
    # 同节若还有正数/百分比披露，不用总体无支出声明覆盖它们，交原检查逐项核对。
    if (re.search(r'\d+(?:\.\d+)?[%％]', body)
            or any(_wan(m['amount'], m['unit']) != 0 for m in re.finditer(
                rf'(?P<amount>{_NUMBER})(?P<unit>{_UNIT})', body))):
        return None
    return candidates[0]


def _compare_absence(rule, doc, text, span, fact, key, label, title):
    sources = [{**ref, 'file_hash': doc.fact_context.file_hash}
               for ref in text.refs(*span, '原文明确无三公支出；非印刷金额0')]
    issues = compare(rule, doc, text, *span, Decimal('0'), fact, key,
                     f'{label}（原文明确无三公支出）', title, disclosure_sources=sources)
    for issue in issues:
        issue.amounts_provenance = '原文明确无发生额，按0核对；未在原件中补写数字'
    return issues


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
    absence_span = _three_public_absence_span(doc, text, title, begin, end)
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
        if absence_span:
            fact, error = select(doc, facts, 'total_actual', code='total', stage='决算', allow_native_blank=True)
            if error:
                unresolved.append(error)
            else:
                issues.extend(_compare_absence(rule, doc, text, absence_span, fact,
                              'three_public:text:决算:explicit_absence', '三公经费合计决算', title))
        else:
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
            from .native_empty_operands import NativeBlankTerm
            phase_error = phase_reason(doc, fact, stage, "total_actual")
            if phase_error:
                text_val = _wan(match.group('amount'), match.group('unit'))
                diff = abs(text_val - fact.normalized_value) if fact.normalized_value is not None else None
                explain_re = re.compile(
                    r'预算调整|调整预算|调整后预算|全年预算|年中预算调整|追加预算|调减预算|核减预算|预算调减|预算追加'
                )
                if (
                    not isinstance(fact, NativeBlankTerm)
                    and fact.normalized_value is not None
                    and diff is not None
                    and amount_difference_exceeds_policy(text_val - fact.normalized_value)
                    and not explain_re.search(text.compact[begin:end])
                ):
                    refs = text.refs(begin + offset, match.end(), '原文金额声明', unique_context=True)
                    t_pages = sorted({
                        e.get('physical_page')
                        for e in fact.extra_dims.get('evidence_refs', [])
                        if e.get('physical_page')
                    })
                    t_page_note = f"第{'、'.join(map(str, t_pages))}页" if t_pages else ''
                    pages = sorted({ref['page'] for ref in refs} | set(t_pages))
                    quote = '\n'.join(ref['span'] for ref in refs)
                    ids, evidence = receipt(
                        rule, doc, key, [fact], status='fail',
                        formula='explicit narrative budget == table generic 预算数 (万元)',
                        reason=(
                            f'说明第{refs[0]["page"]}页写{stage}为{text_val:.2f}万元，'
                            f'三公表{t_page_note}“预算数”为{fact.normalized_value:.2f}万元，相差{diff:.2f}万元'
                        ),
                    )
                    issues.append(rule._issue(
                        f'三公经费合计{stage}表文差异（表头仅列“预算数”）：'
                        f'说明第{refs[0]["page"]}页写{stage}为{text_val:.2f}万元，'
                        f'三公表{t_page_note}“预算数”列示{fact.normalized_value:.2f}万元，'
                        f'相差{diff:.2f}万元，且说明未披露预算调整或口径差异。'
                        '请核实三公表“预算数”系调整后/全年预算口径还是存在填报差错。',
                        {
                            'page': refs[0]['page'], 'pages': pages, 'section': title,
                            'table_code': fact.table_code, 'table_refs': refs,
                            'budget_stage': stage,
                            'table_instance_uid': fact.instance_uid,
                            'operand_refs': [operand_ref(fact, key)],
                            'fact_context_version_token': doc.fact_context.version_token,
                            'defect_type': 'three_public_budget_phase_discrepancy',
                            'difference_wan': f'{diff:.2f}',
                        },
                        'warn',
                        evidence_text=quote,
                        check_relation_id=f'{rule.code}:{key}',
                        fact_ids=ids,
                        evidence_ids=evidence,
                    ))
                    continue
                unresolved.append(phase_error)
                receipt(rule, doc, key, [fact], status="insufficient_data", formula="text = table", reason=phase_error)
                continue
            issues.extend(compare(rule, doc, text, begin + offset, match.end(),
                 _wan(match.group('amount'), match.group('unit')), fact, key,
                 f'三公经费合计{stage}', title, unresolved=unresolved))
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
    explicit_pattern = re.compile(
        rf'财政拨款(?:收入支出|收支)(?:总计|合计|总额)(?:为|共计|是)?(?P<amount>{_NUMBER})(?P<unit>{_UNIT})'
    )
    explicit_income_pattern = re.compile(
        rf'财政拨款收入(?:总计|合计|总额)(?:为|共计|是)?(?P<amount>{_NUMBER})(?P<unit>{_UNIT})'
    )
    explicit_expenditure_pattern = re.compile(
        rf'财政拨款支出(?:总计|合计|总额)(?:为|共计|是)?(?P<amount>{_NUMBER})(?P<unit>{_UNIT})'
    )
    # R5：说明四允许「收入总计/支出总计」分开叙述（环境监测站实样：分别写
    # 财政拨款收入总计 1850.05 万元、支出总计 1850.05 万元），不要求原文
    # 必须使用合并短语「收入支出总计」。合并短语与单独「支出总计」都对
    # 支出侧操作数；单独「收入总计」对收入侧操作数（各自的原表列身份）。
    pattern = re.compile(
        rf'(?:财政拨款)?(?:收入支出|收支)(?:总计|合计|总额)(?:为|共计|是)?(?P<amount>{_NUMBER})(?P<unit>{_UNIT})'
    )
    income_pattern = re.compile(
        rf'财政拨款收入(?:总计|合计|总额)(?:为|共计|是)?(?P<amount>{_NUMBER})(?P<unit>{_UNIT})'
    )
    expenditure_pattern = re.compile(
        rf'(?:财政拨款)?支出(?:总计|合计|总额)(?:为|共计|是)?(?P<amount>{_NUMBER})(?P<unit>{_UNIT})'
    )
    if not ranges:
        # 少数材料漏印「四、财政拨款收入支出决算总体情况说明」标题，但紧接着说明三仍写了明确的
        # 「财政拨款收入支出总计 X 万元」一句；仅当显式带「财政拨款」前缀时兜底取数，不放宽到非财政口径。
        _, fallback_ranges = sections(doc, lambda title: title.startswith('三、') and '支出决算' in title)
        if fallback_sections := [
            r for r in fallback_ranges
            if any(
                p.search(text.compact, r[1], r[2])
                for p in (explicit_pattern, explicit_income_pattern, explicit_expenditure_pattern)
            )
        ]:
            ranges = fallback_sections
            pattern = explicit_pattern
            income_pattern = explicit_income_pattern
            expenditure_pattern = explicit_expenditure_pattern
    if not ranges:
        return finish(rule, [], ['未定位到说明四财政拨款总体情况原文范围'])
    title, begin, end = ranges[0]
    claims = list(pattern.finditer(text.compact, begin, end))
    combined_spans = {(claim.start(), claim.end()) for claim in claims}
    income_claims = [claim for claim in income_pattern.finditer(text.compact, begin, end)
                     if not any(lo <= claim.start() < hi for lo, hi in combined_spans)]
    expenditure_claims = [claim for claim in expenditure_pattern.finditer(text.compact, begin, end)
                          if not any(lo <= claim.start() < hi for lo, hi in combined_spans)]
    issues, unresolved = [], []
    if not claims and not expenditure_claims:
        return finish(rule, [], ['说明四未披露可绑定的财政拨款收支总计金额及单位'])
    fact, error = _pick(doc, facts, 'expenditure_actual', _NAMES['total'])
    for claim in claims + expenditure_claims:
        key = f'grant:text:total:{claim.start()}'
        rejection = context_rejection(doc, text.compact, claim.start(), claim.end())
        invalid = error or (record_context_rejection(doc, text, claim.start(), claim.end(), rejection)
                            if rejection else None)
        if invalid:
            unresolved.append(invalid)
            receipt(rule, doc, key, [fact] if fact else [], status='insufficient_data',
                    formula='说明四财政拨款总计 = T4支出总计', reason=invalid)
            continue
        issues.extend(compare(rule, doc, text, claim.start(), claim.end(),
                      _wan(claim['amount'], claim['unit']), fact, key,
                      '说明4↔T4财政拨款总计', title))
    for claim in income_claims:
        key = f'grant:text:income_total:{claim.start()}'
        income_fact, income_error = _pick(doc, facts, 'income_actual', _NAMES['total'])
        rejection = context_rejection(doc, text.compact, claim.start(), claim.end())
        invalid = (income_error or
                   (record_context_rejection(doc, text, claim.start(), claim.end(), rejection)
                    if rejection else None))
        if invalid:
            unresolved.append(invalid)
            receipt(rule, doc, key, [income_fact] if income_fact else [], status='insufficient_data',
                    formula='说明四财政拨款收入总计 = T4收入总计', reason=invalid)
            continue
        issues.extend(compare(rule, doc, text, claim.start(), claim.end(),
                      _wan(claim['amount'], claim['unit']), income_fact, key,
                      '说明4↔T4财政拨款收入总计', title))
    return finish(rule, issues, unresolved)


def _income_sections(doc):
    return sections(doc, lambda title: '收入决算情况说明' in title
                    and not any(word in title for word in ('财政拨款', '政府性基金', '国有资本')))


def _income_total_with_trailing_dot(doc, text, begin, end):
    """只恢复本节唯一合计声明中两位小数后的多余句点，不修补小数位。"""
    if text.compact.count('本年收入合计', begin, end) != 1:
        return None
    pattern = (r'本年收入合计(?:为|是)?(?P<amount>(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})\.'
               rf'(?P<unit>{_UNIT})(?=[，,。；;]|$)')
    matches = list(re.compile(pattern).finditer(text.compact, begin, end))
    if len(matches) != 1:
        return None
    match = matches[0]
    if (context_guard(doc, text.compact, match.start(), match.end())
            or _inside_full_quotation(text.compact, match.start(), match.end())):
        return None
    return match


def recovered_income_total(doc):
    text, ranges = _income_sections(doc)
    if not ranges:
        return None
    _title, begin, end = ranges[0]
    match = _income_total_with_trailing_dot(doc, text, begin, end)
    if match is None:
        return None
    refs = text.refs(match.start(), match.end(), '本年收入合计原文')
    return (_wan(match['amount'], match['unit']), refs) if refs else None


def check_income_structure(rule, doc):
    facts, reason = table_facts(doc, 'FIN_02_income')
    if reason:
        return finish(rule, [], [reason])
    text, ranges = _income_sections(doc)
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
        # C5（2026-11-14 复验）：原件合计行对应格经网格认证为真空白时，按已
        # 确认政策生成带来源的空白观测操作数参与核对（blank_term 的行/列/格
        # 身份与原格认证门槛全部保持）；横线/无/识别不清/缺失格一律不在此列，
        # 仍按取数不足处理。说明写 0 时由 compare 的空白路径判 not_applicable，
        # 说明写非 0 时保留 insufficient_data 阻断，不用正文 0 反填表格。
        fact, error = select(doc, facts, labels[label], code='total', allow_native_blank=True)
        key = f'income:text:{labels[label]}:{match.start()}'
        if error:
            unresolved.append(error)
            receipt(rule, doc, key, [], status='insufficient_data', formula='text = table', reason=error)
            continue
        issues.extend(compare(rule, doc, text, match.start(), match.end(),
                       _wan(match.group('amount'), match.group('unit')), fact, key, label, title,
                       unresolved=unresolved))
    if '财政拨款收入' not in seen:
        typo_pat = re.compile(rf"(?<!财)政拨款收入(?:为|共计|合计为|是)?(?P<amount>{_NUMBER})(?P<unit>{_UNIT})")
        for match in typo_pat.finditer(text.compact, begin, end):
            if context_guard(doc, text.compact, match.start(), match.end()):
                continue
            seen.add('财政拨款收入')
            fact, error = select(doc, facts, 'fiscal_allocation', code='total', allow_native_blank=True)
            key = f'income:text:fiscal_allocation:{match.start()}'
            if error:
                unresolved.append(error)
                receipt(rule, doc, key, [], status='insufficient_data', formula='text = table', reason=error)
                continue
            issues.extend(compare(rule, doc, text, match.start(), match.end(),
                           _wan(match.group('amount'), match.group('unit')), fact, key, '财政拨款收入', title,
                           unresolved=unresolved))
    if '本年收入合计' not in seen:
        match = _income_total_with_trailing_dot(doc, text, begin, end)
        if match is None:
            unresolved.append('收入决算说明未识别到本年度收入合计金额')
        else:
            fact, error = select(doc, facts, 'total_actual', code='total')
            if error:
                unresolved.append(error)
            else:
                refs = text.refs(match.start(), match.end(), '本年收入合计原文')
                issues.extend(compare(rule, doc, text, match.start(), match.end(),
                               _wan(match['amount'], match['unit']), fact, f'income:text:total_actual:{match.start()}',
                               '本年收入合计', title, disclosure_sources=refs))
    return finish(rule, issues, unresolved)


def check_three_public_details(rule, doc, *, require_vehicle_details=True, check_percentages=False):
    """Compare original detail declarations with uniquely bound current FIN_07 cells."""
    from .native_three_public_ratios import _SUBJECT, LABELS
    from .native_empty_operands import NativeBlankTerm, independent_blank_zero
    from .native_no_activity import require_no_activity_disposition
    require_no_activity_disposition(rule, doc)
    facts, reason = table_facts(doc, 'FIN_07_three_public')
    if reason:
        return finish(rule, [], [reason])
    text, ranges = sections(doc, lambda title: '三公' in title and '决算' in title)
    if not ranges:
        return finish(rule, [], ['未定位到三公经费决算说明原文'])
    title, begin, end = ranges[0]
    absence_span = _three_public_absence_span(doc, text, title, begin, end)
    pattern = re.compile(
        rf'(?:支出)?(?:决算|预算)?(?:数)?(?:为|是)?'
        rf'(?:(?P<amount>{_NUMBER})(?P<unit>{_UNIT})|(?P<zero>0+(?:\.0+)?)(?=[。；;，,、]|$))'
    )
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
        # 后置无支出句式（如「因公出国（境）费无支出无预算」）：AGENTS.md 三公
        # 专项与 D-010 口径——写明未发生视为已披露。紧随主体词、不含金额的
        # 无支出/未发生短语与前置无表达同等对待；短语必须以句界收尾，防止
        # 把后续金额吞进无支出声明。
        post_absence = (re.match(r'(?:无|没有|未)(?:相应)?(?:支出|发生|安排)(?:无(?:相应)?预算|预算)?',
                                 text.compact[at:]) if at < end else None)
        post_absence_end = at + post_absence.end() if post_absence else None
        absent = (
            (absence is not None and (at == end or text.compact[at] in '。；;，,'))
            or (post_absence is not None
                and (post_absence_end == end or text.compact[post_absence_end] in '。；;，,、'))
        )
        statement_end = (post_absence_end if post_absence else at) if absent else (match.end() if match else at)
        # 前置无表达的“无/没有”词长度；后置无支出句式时前缀长度为 0
        #（声明从主体词起，无支出短语已含在 statement_end 范围内）。
        absence_prefix_len = len(absence.group()) if absence is not None else 0
        if (not match and not absent) or context_guard(doc, text.compact, subject.start(), statement_end):
            continue
        seen.add(indicator)
        fact, error = select(doc, facts, indicator, code='total', stage='决算', allow_native_blank=True)
        key = f'three_public:detail:{indicator}:{subject.start()}'
        value = Decimal('0') if (absent or match.group('zero')) else _wan(match.group('amount'), match.group('unit'))
        if error:
            unresolved.append(f'{LABELS[indicator]}：{error}')
            receipt(rule, doc, key, [], status='insufficient_data', formula='text = table', reason=error)
            continue
        if indicator == 'vehicle_purchase_operation' and value == 0 and fact.normalized_value == 0:
            start = subject.start() - absence_prefix_len if absent else subject.start()
            zero_vehicle_sources = [{**ref, 'file_hash': doc.fact_context.file_hash}
                                    for ref in text.refs(start, statement_end, '明确公务用车分项无支出')]
        if isinstance(fact, NativeBlankTerm) and (value == 0 or not independent_blank_zero(doc, fact)):
            if value == 0:
                start = subject.start() - absence_prefix_len if absent else subject.start()
                sources = [{**ref, 'file_hash': doc.fact_context.file_hash}
                           for ref in text.refs(start, statement_end, '明确无此项说明')]
                receipt(rule, doc, key, [fact], status='not_applicable', formula='明确无此项说明与原件空白格一致',
                        reason=f'{LABELS[indicator]}原文明确无支出，表内原件单元格为空白', disclosure_sources=sources)
            else:
                reason = f'{LABELS[indicator]}说明有金额而表内原件留空，需核对披露'
                unresolved.append(reason)
                receipt(rule, doc, key, [fact], status='insufficient_data', formula='text = table', reason=reason)
            continue
        statement_start = subject.start() - absence_prefix_len if absent else subject.start()
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
            if absence_span:
                term, error = select(doc, facts, indicator, code='total', stage='决算', allow_native_blank=True)
                if error:
                    unresolved.append(f'{LABELS[indicator]}：{error}')
                else:
                    issues.extend(_compare_absence(rule, doc, text, absence_span, term,
                                  f'three_public:detail:{indicator}:explicit_absence', LABELS[indicator], title))
                continue
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
            from .diagnostic_fields import record_gap, text_missing_operand
            refs = text.refs(begin, min(begin + 1, end), '三公经费说明')
            year = getattr(getattr(doc, 'fact_context', None), 'fiscal_year', None)
            record_gap(doc, reason, reason_codes=['text_statement_unparsed'], missing_operands=[
                text_missing_operand(section='三公经费决算说明', role=f'{LABELS[indicator]}显式决算金额',
                                     page=refs[0]['page'] if refs else None,
                                     row_key=LABELS[indicator], column='决算数', stage='决算',
                                     year=year if isinstance(year, int) and year >= 1900 else None)])
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
