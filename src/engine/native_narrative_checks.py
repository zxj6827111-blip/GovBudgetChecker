"""决算原文中的独立数字关系；不从全文金额集合或表格倒推操作数。

只复算同一条说明里的本年金额、同比增减额、百分比，以及同一句政府
采购总额与货物/工程/服务三项。原文、计算值和推导基数分别留痕。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .amount_math import rounding_envelope
from .rule_policy import AMOUNT_TOLERANCE_WAN, amount_difference_exceeds_policy


_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_UNIT = r"(?:亿元|万元|元)"
_FACTORS = {'元': Decimal('.0001'), '万元': Decimal('1'), '亿元': Decimal('10000')}
_METRIC = (r"(?:一般公共预算(?:财政拨款)?支出(?:决算(?:数)?)?"
           r"|财政拨款收入支出(?:总计|合计|总额)"
           r"|收入支出(?:总计|合计|总额)"
           r"|机关运行经费支出(?:决算(?:数)?)?"
           r"|本年收入(?:合计|总计|总额)|本年支出(?:合计|总计|总额))")
_CURRENT = re.compile(
    rf"(?P<metric>{_METRIC})(?:为|共计|合计为|决算为|是)?"
    rf"(?P<current>{_NUMBER})(?P<unit>{_UNIT})"
)
_CHANGE = re.compile(
    rf"(?:与|较|比)(?P<previous_year>20\d{{2}}|上年|去年)(?:年度|年)?(?:相比)?[，,]?"
    rf"(?P<metric>{_METRIC}|收入支出|财政拨款收入支出)?"
    rf"(?P<direction>增加|减少)(?P<change>{_NUMBER})(?P<unit>{_UNIT})[，,]"
    rf"(?P<percent_direction>增长|下降|增加|减少)(?P<percent>{_NUMBER})[%％]"
)
_PROC_TOTAL = re.compile(
    rf"政府采购(?:支出)?(?:总额|金额|支出金额)"
    rf"(?:[（(][^）)]{{0,40}}[）)])?(?:为|共计|共|合计为|合计|是)?"
    rf"(?P<total>{_NUMBER})(?P<unit>{_UNIT})[，,]其中[:：]?"
)
_PROC_PART = re.compile(
    rf"(?:政府采购)?(?P<kind>货物|工程|服务)(?:采购)?(?:支出金额|金额|支出)?"
    rf"(?:为|共计|共|合计|[:：])?(?P<amount>{_NUMBER})(?P<unit>{_UNIT})"
)
_PROC_ABSENCE = re.compile(
    r'(?:^|(?<=[，,、；;]))(?:无|没有(?:发生)?|未发生)(?:政府)?'
    r'(?P<kind>货物|工程|服务)采购(?:支出)?(?=[，,、。；;]|$)'
)
_HEADINGS = re.compile(
    r"(?m)^[ \t]*(?:[一二三四五六七八九十百]+[、．]"
    r"|[（(][一二三四五六七八九十百]+[）)]"
    r"|\d+[、．]|\d+\.(?=\D))[^\n\r]{1,100}"
)
_ORG = re.compile(r"[\u4e00-\u9fff]{1,40}?(?:人民政府|委员会|办事处|办公室|管理所|中心|学校|医院|局|馆)")
_CALIBER_CHANGE = re.compile(r"口径.{0,12}调整|调整.{0,12}口径|可比口径|统计范围|剔除|重述")
_YOY_SECTION = re.compile(
    r'收入支出决算总体|财政拨款收入支出决算总体|一般公共预算(?:财政拨款)?支出决算(?:总体|情况)'
    r'|收入决算情况|(?<!基本)支出决算情况|机关运行经费支出情况'
)


@dataclass
class _Text:
    raw: str
    compact: str
    positions: list[int]
    pages: list[tuple[int, int]]

    @classmethod
    def from_pages(cls, pages):
        raw, bounds, footer_positions = '', [], set()
        for page_number, page in enumerate(pages, 1):
            start = len(raw)
            page_text = str(page or '')
            # An independently printed physical page number must not split
            # a narrative token (e.g. 支出 / 34 / 决算). Keep it in raw quotes
            # and offsets, but omit only a terminal standalone matching page.
            footer = re.search(r'(?:^|\n)[ \t]*(\d+)[ \t]*$', page_text)
            if footer and int(footer.group(1)) == page_number:
                footer_positions.update(range(start + footer.start(1), start + footer.end(1)))
            for marker in re.finditer(r'\(cid:\d+\)', page_text):
                footer_positions.update(range(start + marker.start(), start + marker.end()))
            raw += page_text
            bounds.append((start, len(raw)))
            raw += '\n'
        compact, positions = [], []
        for index, char in enumerate(raw):
            if index in footer_positions:
                continue
            for normalized in unicodedata.normalize('NFKC', char):
                if not normalized.isspace():
                    compact.append(normalized)
                    positions.append(index)
        return cls(raw, ''.join(compact), positions, bounds)

    def compact_range(self, raw_start, raw_end):
        from bisect import bisect_left
        return bisect_left(self.positions, raw_start), bisect_left(self.positions, raw_end)

    def refs(self, start, end, role):
        """Normalized ranges map back to unchanged, per-physical-page quotes."""
        if start >= end:
            return []
        raw_start, raw_end = self.positions[start], self.positions[end - 1] + 1
        refs = []
        for page, (left, right) in enumerate(self.pages, 1):
            quote = self.raw[max(left, raw_start):min(right, raw_end)] if max(left, raw_start) < min(right, raw_end) else ''
            if quote.strip():
                refs.append({'page': page, 'span': quote, 'role': role})
        return refs


def _field(value):
    return getattr(value, 'value', value)


def _metadata(doc, text):
    profile, context = getattr(doc, 'document_profile', None), getattr(doc, 'fact_context', None)
    year = (getattr(context, 'fiscal_year', None) or _field(getattr(profile, 'report_year', None))
            or getattr(doc, 'dominant_year', None) or getattr(doc, 'fiscal_year', None))
    owner = (getattr(context, 'org_name', None) or _field(getattr(profile, 'organization_name', None))
             or getattr(doc, 'organization_name', None))
    years = list(re.finditer(r'(20\d{2})年度(?:部门|单位)?决算', text.compact))
    if year is None and years and len({match.group(1) for match in years}) == 1:
        year = int(years[0].group(1))
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    if not owner:
        # The cover publisher is literal, not inferred from the filename.
        first = re.sub(r'\s+', '', str((getattr(doc, 'page_texts', None) or [''])[0]))
        match = re.match(r'(.+?)(?=20\d{2}年度(?:部门|单位)?决算)', first)
        owner = match.group(1) if match and _ORG.fullmatch(match.group(1)) else None
    year_refs = []
    if year is not None:
        # A contents page can repeat the same year/title several times. Select
        # a unique literal on one physical page so metadata cannot invalidate
        # otherwise complete, cross-page monetary evidence.
        candidates = []
        for matching in years:
            if int(matching.group(1)) != year:
                continue
            refs = text.refs(matching.start(), matching.end(), '材料年度')
            if len(refs) != 1:
                continue
            left, right = text.pages[refs[0]['page'] - 1]
            page = text.raw[left:right]
            compact_page = re.sub(r'\s+', '', unicodedata.normalize('NFKC', page))
            if compact_page.count(matching.group()) == 1:
                candidates.append((bool(re.search(r'目\s*录', page)), refs))
        if candidates:
            year_refs = min(candidates, key=lambda entry: entry[0])[1]
    return year, owner, year_refs


def _sections(text):
    headings = list(_HEADINGS.finditer(text.raw))
    # Contents lists are not the narrative body. Prefer the actual third-part
    # heading when the original text contains it outside a contents page.
    begin = 0
    for left, right in text.pages:
        raw_page = text.raw[left:right]
        if re.search(r'目\s*录', raw_page):
            continue
        match = re.search(r'第三部分[^\n]{0,100}决算情况说明', raw_page)
        if match:
            begin = left + match.end()
            break
    for index, heading in enumerate(headings):
        if heading.start() < begin:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text.raw)
        start, stop = text.compact_range(heading.end(), end)
        if start < stop:
            yield re.sub(r'\s+', '', heading.group()), start, stop


def _metric_key(label):
    if label.startswith('一般公共预算'):
        return 'general_public_expenditure'
    if label.startswith('财政拨款收入支出'):
        return 'fiscal_income_expenditure'
    if label.startswith('收入支出'):
        return 'income_expenditure'
    return 'income' if label.startswith('本年收入') else 'expenditure'


def _wan(amount, unit):
    return Decimal(amount.replace(',', '')) * _FACTORS[unit]


def _foreign_owner(proof, owner):
    # 原文与主体名都按 NFKC 归一后比较：原文的全角括号已是半角，主体名不归一时
    # “上海市水务局（上海市海洋局）”“中国（上海）…”永远认不出自己。画像里主体名的
    # 括号别称可能被略去，再去掉两边的括号段比一次（主体名至少 4 字才这样做）。
    proof = unicodedata.normalize('NFKC', str(proof or ''))
    compact_owner = re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(owner or '')))
    if compact_owner:
        proof = proof.replace(compact_owner, '')
        bare_owner = re.sub(r'\([^()]{1,20}\)', '', compact_owner)
        if len(bare_owner) >= 4:
            proof = re.sub(r'\([^()]{1,20}\)', '', proof).replace(bare_owner, '')
    proof = re.sub(r'本部门|本单位|本局|本中心|本办公室', '', proof)
    return bool(_ORG.search(proof))


def _receipt(rule, doc, relation, status, formula, reason=None, disclosure_sources=()):
    from .rules_v33 import _v33_relation_receipt
    _v33_relation_receipt(doc, rule.code, relation, status=status, formula=formula,
                          incomplete_reason=reason, disclosure_sources=disclosure_sources)


def _unresolved(rule, doc, text, start, relation, formula, reason, unresolved):
    refs = text.refs(start, min(start + 1, len(text.compact)), '关系位置')
    page = refs[0]['page'] if refs else None
    message = f'第{page}页原文关系未完成：{reason}'
    unresolved.append(message)
    _receipt(rule, doc, relation, 'insufficient_data', formula, message)


def _year_and_owner_reason(proof, current_prefix, year, owner):
    local_years = [int(value) for value in re.findall(r'(20\d{2})(?:年度|年)', current_prefix)]
    if year is None:
        return '未识别到明确的材料年度'
    if local_years and local_years[-1] != year:
        return '本年金额属于其他年度，不能与当前材料口径混用'
    if _foreign_owner(proof, owner):
        return '原文包含其他或未确认主体，不能跨主体拼接金额'
    if _CALIBER_CHANGE.search(proof):
        return '原文存在统计口径调整，需人工确认可比基数'
    return None


def _check_yoy(rule, doc, text, section, start, stop, year, owner, year_refs, issues, unresolved):
    block = text.compact[start:stop]
    for match in _CHANGE.finditer(block):
        relation = f'narrative_yoy_delta:p{match.start() + start}'
        formula = 'change / (current + change) * 100' if match['direction'] == '减少' else 'change / (current - change) * 100'
        currents = list(_CURRENT.finditer(block, 0, match.start()))
        if not currents:
            _unresolved(rule, doc, text, start + match.start(), relation, formula,
                        '同条同比说明缺少明确的本年总额及单位', unresolved)
            continue
        current = currents[-1]
        bridge = block[current.end():match.start()]
        # Adjacent sentences are the standard disclosure template. More than
        # one sentence, an intervening amount or 其中 changes the operand scope.
        if (bridge.count('。') > 1 or re.search(r'其中|分别|[；;]', bridge)
                or re.search(rf'{_NUMBER}{_UNIT}', bridge)):
            _unresolved(rule, doc, text, start + match.start(), relation, formula,
                        '本年总额与同比声明之间存在其他条目或分项，不能跨条目绑定', unresolved)
            continue
        if match['metric'] and _metric_key(match['metric']) != _metric_key(current['metric']):
            _unresolved(rule, doc, text, start + match.start(), relation, formula,
                        '本年金额与增减金额指标不同', unresolved)
            continue
        # Include the same paragraph's owner/year prefix, but no earlier entry.
        proof_start = max(block.rfind('。', 0, current.start()), block.rfind('；', 0, current.start()),
                          block.rfind(';', 0, current.start())) + 1
        if _CURRENT.search(block[proof_start:current.start()]):
            _unresolved(rule, doc, text, start + match.start(), relation, formula,
                        '同一条目前出现多个本年总额候选，需先确认同比归属', unresolved)
            continue
        proof = block[proof_start:match.end()]
        prefix = block[proof_start:current.start()]
        reason = _year_and_owner_reason(proof, prefix, year, owner)
        previous_year = year - 1 if year is not None and match['previous_year'] in {'上年', '去年'} else int(match['previous_year']) if match['previous_year'].isdigit() else None
        if not reason and previous_year != year - 1:
            reason = '比较年度并非材料上年，不能作为同比关系'
        # A later clause can explicitly qualify the basis as adjusted.
        tail_end = block.find('。', match.end())
        tail = block[match.end():tail_end if tail_end >= 0 else len(block)]
        if not reason and _CALIBER_CHANGE.search(tail):
            reason = '同比基数采用调整口径，需人工确认可比基数'
        current_amount, change = _wan(current['current'], current['unit']), _wan(match['change'], match['unit'])
        previous = current_amount + change if match['direction'] == '减少' else current_amount - change
        if not reason and (current_amount < 0 or previous <= 0):
            reason = '推导的上年基数不可作为有效分母'
        if reason:
            _unresolved(rule, doc, text, start + match.start(), relation, formula, reason, unresolved)
            continue
        computed = change / previous * Decimal('100')
        declared = Decimal(match['percent'].replace(',', ''))
        difference = abs(computed - declared)
        direction_matches = ((match['direction'] == '减少') == (match['percent_direction'] in {'下降', '减少'}))
        failed = difference > Decimal('.1') or not direction_matches
        _receipt(rule, doc, relation, 'fail' if failed else 'pass', formula)
        if not failed:
            continue
        claim_refs = text.refs(start + current.start(), start + match.end(), '本年金额及同比声明')
        refs = list(claim_refs)
        if not re.search(r'20\d{2}(?:年度|年)', prefix):
            refs += year_refs
        page = refs[0]['page']
        location = {'page': page, 'pages': sorted({ref['page'] for ref in refs}), 'section': section,
                    'relation_type': 'narrative_yoy_delta', 'table_refs': refs,
                    'current_year': year, 'previous_year': previous_year,
                    'current_amount_wan': str(current_amount), 'change_amount_wan': str(change),
                    'previous_amount_wan': str(previous), 'previous_amount_source': '由同条本年金额与增减额推导',
                    'computed_percent': str(computed), 'declared_percent': str(declared),
                    'difference_pp': str(difference), 'tolerance_pp': '0.1', 'formula': formula,
                    'direction': match['direction'], 'declared_percent_direction': match['percent_direction']}
        quote = '\n'.join(ref['span'] for ref in claim_refs)
        issues.append(rule._issue(
            f'同比比例与同条金额不一致：本年{current_amount:.2f}万元，{match["direction"]}{change:.2f}万元，'
            f'推导上年{previous:.2f}万元，按上年基数应为{computed:.2f}%，原文为{match["percent_direction"]}{declared}%。',
            location, 'error', evidence_text=quote, check_relation_id=f'{rule.code}:{relation}'))


def _check_procurement(rule, doc, text, section, start, stop, year, owner, year_refs, issues, unresolved):
    block = text.compact[start:stop]
    for total in _PROC_TOTAL.finditer(block):
        relation = f'procurement_total:p{start + total.start()}'
        formula = 'goods + works + services = procurement_total'
        sentence_end = block.find('。', total.end())
        sentence_end = sentence_end if sentence_end >= 0 else len(block)
        tail = block[total.end():sentence_end]
        parts = list(_PROC_PART.finditer(tail))
        absent = list(_PROC_ABSENCE.finditer(tail))
        kinds = [part['kind'] for part in parts] + [part['kind'] for part in absent]
        proof_start = max(block.rfind('。', 0, total.start()), block.rfind('；', 0, total.start()),
                          block.rfind(';', 0, total.start())) + 1
        proof = block[proof_start:sentence_end]
        reason = _year_and_owner_reason(proof, block[proof_start:total.start()], year, owner)
        if not reason and (any(int(value) != year for value in re.findall(r'(20\d{2})(?:年度|年)', tail))
                           or re.search(r'上年|去年|以前年度', tail)):
            reason = '采购分项属于其他年度，不能与当前采购总额拼接'
        complete = len(kinds) == 3 and set(kinds) == {'货物', '工程', '服务'}
        # 只列出部分分项（“其中：货物…、服务…”，没写工程）：各分项都不可能为负，
        # 已列分项之和恰为总额时未列分项只能是 0；超出总额无论未列分项是多少都不成立。
        # 未列分项在句中另有任何提法（“无工程采购预算”“并非无工程采购”）时不推导，仍转人工。
        partial = (bool(kinds) and len(kinds) == len(set(kinds)) and set(kinds) < {'货物', '工程', '服务'}
                   and not any(kind in tail for kind in {'货物', '工程', '服务'} - set(kinds)))
        if not reason and not complete and not partial:
            reason = '同一句政府采购汇总未唯一列出货物、工程、服务三项'
        if not reason and ('其中' in tail or re.search(r'另计|另行|分别采用|不含', tail)):
            reason = '采购分项存在嵌套分项或其他统计口径，不能直接求和'
        if reason:
            _unresolved(rule, doc, text, start + total.start(), relation, formula, reason, unresolved)
            continue
        values = {part['kind']: _wan(part['amount'], part['unit']) for part in parts}
        # “无工程采购”是有原文的无发生额声明，不新增或回写金额事实。
        values.update({part['kind']: Decimal('0') for part in absent})
        declared, computed = _wan(total['total'], total['unit']), sum(values.values(), Decimal('0'))
        difference = declared - computed
        failed = amount_difference_exceeds_policy(difference, rounding_envelope(len(values)))
        omitted = sorted({'货物', '工程', '服务'} - set(kinds))
        if omitted and failed and difference > 0:
            _unresolved(rule, doc, text, start + total.start(), relation, formula,
                        f'政府采购汇总未列出{"、".join(omitted)}，已列分项小于总额，差额可能属于未列分项',
                        unresolved)
            continue
        if omitted:
            formula = f'listed parts = procurement_total; omitted {"/".join(omitted)} >= 0'
        refs = text.refs(start + proof_start, start + sentence_end, '政府采购总额及三项分项')
        if not re.search(r'20\d{2}(?:年度|年)', block[proof_start:total.start()]):
            refs += year_refs
        file_hash = getattr(getattr(doc, 'fact_context', None), 'file_hash', None)
        _receipt(rule, doc, relation, 'fail' if failed else 'pass', formula,
                 disclosure_sources=[dict(ref, file_hash=file_hash) for ref in refs])
        if not failed:
            continue
        location = {'page': refs[0]['page'], 'pages': sorted({ref['page'] for ref in refs}),
                    'section': section, 'relation_type': 'procurement_total', 'table_refs': refs,
                    'current_year': year, 'declared_total_wan': str(declared),
                    'parts_wan': {kind: str(value) for kind, value in values.items()},
                    'computed_sum_wan': str(computed), 'difference_wan': str(difference),
                    'tolerance_wan': str(AMOUNT_TOLERANCE_WAN), 'formula': formula,
                    'explicit_absence_terms': [part['kind'] for part in absent],
                    'omitted_terms': omitted}
        quote = '\n'.join(ref['span'] for ref in refs)
        listed = '、'.join(kind for kind in ('货物', '工程', '服务') if kind in values)
        issues.append(rule._issue(
            (f'政府采购总额与货物、工程、服务三项之和不一致：原文总额{declared:.2f}万元，'
             f'三项之和{computed:.2f}万元，相差{abs(difference):.2f}万元。') if not omitted else
            (f'政府采购已列分项（{listed}）之和{computed:.2f}万元超过原文总额{declared:.2f}万元，'
             f'相差{abs(difference):.2f}万元。'),
            location, 'error', evidence_text=quote, check_relation_id=f'{rule.code}:{relation}'))


def check_narrative_relations(rule: Any, doc: Any):
    """Return (issues, unresolved) for provable, clause-bound native relations."""
    kind = _field(getattr(getattr(doc, 'document_profile', None), 'report_kind', None)) or getattr(doc, 'report_kind', None)
    if kind in {'budget', 'dept_budget', 'gov_budget'}:
        return [], []
    text = _Text.from_pages(getattr(doc, 'page_texts', None) or [])
    year, owner, year_refs = _metadata(doc, text)
    issues, unresolved = [], []
    for section, start, stop in _sections(text):
        # This helper covers the report totals, not item-specific 三公/机关运行
        # disclosures. Unsupported relations are left to their own checkers.
        if _YOY_SECTION.search(section) and not any(label in section for label in (
                '三公', '基本支出', '政府性基金', '国有资本')):
            _check_yoy(rule, doc, text, section, start, stop, year, owner, year_refs, issues, unresolved)
        _check_procurement(rule, doc, text, section, start, stop, year, owner, year_refs, issues, unresolved)
    return issues, list(dict.fromkeys(unresolved))
