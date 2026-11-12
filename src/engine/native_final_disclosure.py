"""决算公开补充核验：功能说明、基金总额、支出占比和绩效数量。

操作数来自同一解析版本的唯一财政事实或同一绩效范围的原文声明。
未披露金额、评级数量及空白表格均不补零；关系证据不足时明确上报。
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from decimal import Decimal

from .amount_math import classify_amount_diff
from .rule_policy import AMOUNT_TOLERANCE_WAN, amount_difference_exceeds_policy
from .native_narrative_checks import (
    _Text, _NUMBER, _UNIT, _wan, _metadata, _sections, _foreign_owner,
    _CALIBER_CHANGE,
)
from .native_table_arithmetic import table_facts, select, receipt, finish
from .native_table_text import is_general_expenditure_title, sections
from .native_three_public_ratios import operand_ref
from src.services.fiscal_code_hierarchy import hierarchy_paths


GENERAL = 'FIN_05_general_public_expenditure'
FUND = 'FIN_08_gov_fund'
_ITEM = re.compile(
    r'(?P<klass>[^\d，,。；;：:、.()“”\-—–]{1,60}?)\(类\)[\-—–]?'
    r'(?P<kuan>[^\d，,。；;：:、.()“”\-—–]{1,60}?)\(款\)[\-—–]?'
    r'(?P<xiang>[^\d，,。；;：:、.()“”\-—–]{1,60}?)\(项\)')
#: 不带（类）（款）（项）标记的写法：“社会保障和就业支出-行政事业单位养老支出-行政单位离退休”，
#: 连接号可为 - — –（全角－经 NFKC 后为 -），可带引号。类名一律以“支出”结尾，日期、区间不会误中。
_DASH_ITEM = re.compile(
    r'(?:^|(?<=[。；;:、\d]))[“"]?'  # 编号后漏写“、”（“3“社会保障…”）也认
    r'(?P<klass>[\u4e00-\u9fff]{1,28}支出)[-—–]'
    r'(?P<kuan>[\u4e00-\u9fff()]{2,40}?)[-—–]'
    r'(?P<xiang>[\u4e00-\u9fff()]{2,60}?)[”"]?(?=[。.]|主要用于)')
# IR-02：原件文本层会在"支出决算"与"为"之间夹游离逗号/换行
# （自然资源研究院 P20"支出决算\n,\n为 2341.32 万元"），[，,]? 容忍该形态。
class _BareClaimWithUnit:
    """IR-02：裸金额声明继承条目首句单位后的声明视图（与正则 match 同接口）。"""

    __slots__ = ('_amount', '_unit', '_start', '_end')

    def __init__(self, amount, unit, start, end):
        self._amount, self._unit, self._start, self._end = amount, unit, start, end

    def __getitem__(self, key):
        if key == 'amount':
            return self._amount
        if key == 'unit':
            return self._unit
        raise KeyError(key)

    def start(self):
        return self._start

    def end(self):
        return self._end


def _dec(raw):
    from decimal import Decimal, InvalidOperation

    try:
        return Decimal(str(raw).replace(',', ''))
    except InvalidOperation:
        return None


_DECISION = re.compile(rf'(?:支出决算(?:数)?|决算数)[，,]?(?:为|是)?'
                       rf'(?P<amount>{_NUMBER})[，,]?(?:为)?(?P<unit>{_UNIT})')
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
_RATING_DECLARATION = re.compile(
    r'(?:绩效)?评级为[“"「]?(?P<rating>[^“”"「」\d，,。；;()]{1,12}?)[”"」]?'
    r'(?:的)?项目(?P<count>\d+)个')
_CANCELLED_PROJECTS = re.compile(r'(?:年内调整取消|年内取消|调整取消)(?:的)?项目(?P<count>\d+)个')
# 评级数量披露谓词：限定词必须与该谓词同子句才算声明范围（R4-01）。
_RATING_DISCLOSURE_PREDICATE = re.compile(
    r'(?:评级|评价)(?:结果|情况|等级)?(?:如下|见下)|(?:评级|评价)为')


def _name(value):
    # Only the optional terminal 支出 is a documented naming variation. Do not
    # shorten subject stems: names such as 其他支出 are distinct identities.
    return re.sub(r'支出$', '', re.sub(r'\s+', '', str(value or '')))


def _unresolved(rule, doc, key, reason, unresolved, facts=(), formula='text = table'):
    unresolved.append(reason)
    codes = []
    if facts:
        from src.engine.fact_context import operand_rejection_code
        context = getattr(doc, 'fact_context', None)
        codes = [code for code in (
            operand_rejection_code(fact, getattr(context, 'org_name', None)) for fact in facts
        ) if code]
    if not codes:
        text = str(reason or '')
        if '缺失或不唯一' in text:
            codes = ['operand_missing_or_ambiguous']
        elif '身份' in text or '识别状态' in text:
            codes = ['identity_unconfirmed']
        elif '未识别到' in text or '未找到' in text:
            codes = ['source_missing']
    receipt(rule, doc, key, list(facts), status='insufficient_data',
            formula=formula, reason=reason, reason_codes=list(dict.fromkeys(codes)))


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
    context = getattr(doc, 'fact_context', None)
    identity = {
        'fiscal_year': getattr(context, 'fiscal_year', None),
        'budget_stage': getattr(context, 'budget_stage', None),
        'org_name': getattr(context, 'org_name', None),
        'file_hash': getattr(context, 'file_hash', None),
    }
    # 表实例身份：同一表代码可能跨页续排，页码+行号+表代码共同定位原格。
    instances = sorted({
        str((fact.extra_dims or {}).get('table_instance_id'))
        for fact in facts or ()
        if (fact.extra_dims or {}).get('table_instance_id')
    })
    if len(instances) == 1:
        identity['table_instance_id'] = instances[0]
    identity.update(extra)
    result = {'page': refs[0]['page'] if refs else None, 'pages': pages,
            'bbox': None, 'section': section, 'relation_type': relation,
            # Source binding authenticates fiscal cells through fact identity,
            # not a repeated bare number anywhere on the same table page.
            'table_refs': list(refs), 'fact_source_refs': fact_source_refs,
            'fact_context_version_token': getattr(context, 'version_token', None),
            **identity}
    if facts:
        from .native_empty_operands import blank_observations, money_facts
        result['operand_refs'] = [operand_ref(fact, f'{relation}:{i}')
                                  for i, fact in enumerate(money_facts(facts))]
        observations = blank_observations(facts)
        if observations:
            result['native_blank_sources'] = observations
            result['pages'] = sorted(set(result['pages']) | {o['physical_page'] for o in observations})
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


def _amount(rule, doc, text, begin, end, amount, unit, fact, key, label, section, identity_sources=()):
    declared = _wan(amount, unit)
    difference = declared - fact.normalized_value
    failed = amount_difference_exceeds_policy(difference)
    refs = text.refs(begin, end, '原文决算金额声明')
    ids, evidence = receipt(rule, doc, key, [fact], status='fail' if failed else 'pass',
            formula='explicit narrative amount == current table amount (万元)',
            disclosure_sources=[dict(ref, file_hash=doc.fact_context.file_hash) for ref in refs]
                               + list(identity_sources))
    if not failed:
        return []
    original = str(fact.raw_value or '').replace(',', '').replace('，', '').strip()
    original = re.sub(r'(?:亿元|万元|元)$', '', original)
    unit_error = (unit != fact.unit and re.fullmatch(_NUMBER, original or '')
                  and Decimal(original) == Decimal(amount.replace(',', '')))
    message = (f'{label}金额单位口径错误：' if unit_error else f'{label}表文金额不一致：')
    message += (f'原文{amount}{unit}折合{declared}万元，表内{fact.normalized_value:.2f}万元，'
                f'相差{abs(difference):.4f}万元。')
    from .native_empty_operands import NativeBlankTerm
    if isinstance(fact, NativeBlankTerm):
        message += '表内对应原件格经认证为空白，按已确认约定作为0参与核对。'
    location = _location(doc, refs, [fact], section, 'final_disclosure_amount',
                         table_code=fact.table_code, subject_code=fact.subject_code,
                         indicator=fact.indicator, declared_amount_wan=str(declared),
                         table_amount_wan=str(fact.normalized_value),
                         declared_unit=unit, difference_wan=str(difference),
                         tolerance_wan=str(AMOUNT_TOLERANCE_WAN), unit_magnitude_error=bool(unit_error),
                         portable_rule_id='R001' if unit_error else 'D-005')
    return [rule._issue(message, location, 'error',
                        evidence_text=_quote(location),
                        check_relation_id=f'{rule.code}:{key}', fact_ids=ids,
                        evidence_ids=evidence)]


def _truncated_name_source(doc, fact, item, siblings):
    """仅把同范围唯一原文名称用于本条关系，不改写原表名称或全局别名。"""
    def literal(value):
        return re.sub(r'\s+', '', str(value or ''))
    raw, full = literal(fact.subject_name), literal(item['xiang'])
    if len(raw) < 8 or raw == full or not full.startswith(raw):
        return None
    choices = {literal(other['xiang']) for other in siblings
               if _name(other['klass']) == _name(item['klass'])
               and _name(other['kuan']) == _name(item['kuan'])
               and literal(other['xiang']).startswith(raw)}
    if choices != {full}:
        return None
    context, artifact = doc.fact_context, doc.parse_artifact
    if (fact.org_name != context.org_name or fact.fiscal_year != context.fiscal_year
            or fact.table_code != GENERAL or fact.fund_scope != 'general_public_budget'
            or fact.budget_stage != '决算'):
        return None
    # 必须在原生物理行同时找到编码和截断名；OCR候选、错行名称均不补全。
    physical = []
    for table in artifact.table_fragments:
        if table.table_code != GENERAL:
            continue
        for cell in table.cells:
            if (cell.row_idx != fact.row_order or cell.ocr_provenance or not cell.bbox
                    or cell.raw_text != fact.subject_name):
                continue
            codes = [c for c in table.cells if c.row_idx == cell.row_idx
                     and c.raw_text == fact.subject_code and c.bbox and not c.ocr_provenance]
            if len(codes) == 1:
                physical.append(cell)
    if len(physical) != 1:
        return None
    cell = physical[0]
    return {'file_hash': context.file_hash, 'page': cell.page_number,
            'span': cell.raw_text, 'bbox': list(cell.bbox),
            'role': '同范围唯一类款项原文补全依据，原表名称保留',
            'subject_code': fact.subject_code, 'fact_uid': fact.fact_uid,
            'raw_subject_name': fact.subject_name, 'resolved_subject_name': item['xiang'],
            'basis': 'unique_native_code_parent_and_narrative_prefix'}


def _record_text_gap(doc, text, item, *, codes, role):
    """DH-02：说明条目绑不到表内科目时登记文字侧缺数位置（页码来自原页引用）。"""
    from .diagnostic_fields import record_gap, text_missing_operand

    texts = [text] if isinstance(text, str) else list(text)
    source = _Text.from_pages(doc.page_texts).refs(item.start(), item.end(), '原文科目')
    page = source[0]['page'] if source else None
    year = getattr(getattr(doc, 'fact_context', None), 'fiscal_year', None)

    def level_value(key):
        # _ITEM / _DASH_ITEM 是 re.Match，命名组用下标取；普通映射用 get。
        try:
            return item[key]
        except Exception:  # noqa: BLE001 - 两种取值口径都取不到就是空
            return None

    # 金额在原文里可能已披露；名称对应失败不能描述成原文缺数。
    if "text_binding_failed" in codes or "name_mismatch_confirmed" in codes:
        role = '科目名称对应关系'
    record_gap(doc, texts, reason_codes=codes, missing_operands=[text_missing_operand(
        section='支出决算具体情况说明', role=role, page=page,
        row_key=str(level_value('xiang') or level_value('kuan') or level_value('klass') or ''),
        column='决算金额', stage='决算', year=year if isinstance(year, int) and year >= 1900 else None)])

def _name_difference_hint(doc, item):
    """A2：类/款/项名差异提示的原文出处（只取页码，不伪造 bbox）。"""
    text = _Text.from_pages(getattr(doc, 'page_texts', []) or [])
    source = text.refs(item.start(), item.end(), '原文科目')
    return source[0]['page'] if source else None


def _item_source_line(doc, item):
    """A2：条目在原始页文本中的所在行（显式编码通常印在行首科目名旁）。

    行与 head 都按引擎同款管道归一（NFKC + 去空白），全角/半角括号不致错配。
    """
    try:
        head = re.sub(r'\s+', '', str(item[0] or ''))[:10]
        if not head:
            return ''
        for page in (getattr(doc, 'page_texts', []) or []):
            for line in str(page or '').splitlines():
                normalized = ''.join(
                    ch for ch in unicodedata.normalize('NFKC', line) if not ch.isspace())
                if head in normalized:
                    return line
    except Exception:  # noqa: BLE001 - item 非 Match 或页文本缺失时无原文行
        return ''
    return ''


def _chain_visible_in_original(doc, chain):
    """A2：链上每级「编码+名称」在同一原文行共现（判据 1 与链收尾核验用）。"""
    pages = [str(p or '') for p in (getattr(doc, 'page_texts', []) or [])]
    for code, name in chain:
        code_re = re.compile(rf'(?<!\d){re.escape(str(code))}(?!\d)')
        name_n = _name(name)
        found = False
        for page in pages:
            for line in page.splitlines():
                if code_re.search(line) and name_n and name_n in _name(line):
                    found = True
                    break
            if found:
                break
        if not found:
            return False
    return True


def _original_table_rows(doc, pages, codes=None):
    """完整原表行清单（A2/IR03）：给定 1 基页码集合上的「行首编码+名称」。

    只读 doc.page_texts 原始文本行，不经解析产物——"已解析唯一"不等于
    原件唯一。行形态：行首 3-7 位编码 + 名称（NFKC 归一）；名称取编码后
    的连续非数字前缀（金额数字跟在名称之后，不属名称）。

    IR-02（Codex 独立核验 2026-11-10）：原表文本层会把长名称拆到金额行
    的上下两行（"一般公共服务\n201 207.40 207.40\n支出"），纯编码+金额行
    用单行正则匹配不到，导致类级行（207 等）整体缺席、A2 身份链在
    "类行必须在原表存在"判据上失败。这里按「编码行上一行是名称头、
    下一行是名称尾」重建拆行类目；编码歧义（201 vs 20120）用调用方
    已知的合法编码集合（``codes``）消解，无提示集合时跳过歧义行
    （fail-closed，不猜）。
    """
    rows = []
    seen_codes = []
    all_pages = getattr(doc, 'page_texts', []) or []
    hint = {str(c) for c in (codes or ())}

    def _split_name_rows(page_lines):
        parsed = []
        amount_tail = re.compile(
            r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
            r'(?:\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)?\s*$')
        for index, normalized in enumerate(page_lines):
            match = re.match(r'^(\d{3,7})(\D+)', normalized)
            if match and not match.group(2).startswith('.'):
                parsed.append((match.group(1), match.group(2)))
                continue
            # IR-02：纯"编码+金额"行（名称被文本层拆到上下两行）。
            # 编码宽度按长→短消歧（'2012902' 不得被 '201' 抢走）；候选编码
            # 必须全为数字且在调用方已知编码集合内；余部必须能整段解释为
            # 金额（一到两列），否则跳过（fail-closed，不猜）。
            code = None
            for width in (7, 5, 3):
                prefix = normalized[:width]
                if not prefix.isdigit():
                    continue
                if hint and prefix not in hint:
                    continue
                rest = normalized[width:].strip()
                if rest and not amount_tail.fullmatch(rest):
                    continue
                code = prefix
                break
            if code is None:
                continue
            head = page_lines[index - 1] if index > 0 and re.match(r'^\D', page_lines[index - 1]) else ''
            tail = ''
            if index + 1 < len(page_lines) and re.match(r'^\D+$', page_lines[index + 1]):
                tail = page_lines[index + 1]
            name = head + tail
            if name:
                parsed.append((code, name))
        return parsed

    for page_number in pages:
        if not isinstance(page_number, int) or not 1 <= page_number <= len(all_pages):
            continue
        normalized_lines = [
            ''.join(ch for ch in unicodedata.normalize('NFKC', line) if not ch.isspace())
            for line in str(all_pages[page_number - 1] or '').splitlines()
        ]
        rows.extend(_split_name_rows(normalized_lines))
    # 同页同码重复行（名称拆分多段重复）保序去重
    deduped = []
    for code, name in rows:
        if (code, name) not in deduped:
            deduped.append((code, name))
    return deduped


def _general_table_pages(doc, facts):
    """原表扫描范围（1 基页码）——必须是**可证明完整**的逻辑表范围（R2-03）。

    事实证据页只说明"解析幸存的事实落在哪"，不能自证续页不存在：facts 只
    覆盖第 1 页时，第 2 页续表里的同名候选会被漏掉，候选集被人为缩小后
    "唯一性"误通过（审核方跨页负控实证）。因此：

    1. 解析产物声明了 FIN_05 逻辑表范围（logical_tables 的 page_start/
       page_end，或 table_fragments 片段所在页）→ 范围与事实证据页取并集；
    2. 解析产物没有任何 FIN_05 范围信息 → 全部页（按构造完整；过宽只会
       让唯一性更难满足，fail-closed 不误绑）。

    范围完整性核验：声明的页必须落在文档页数内，越界声明视为产物不完整，
    退回全部页——绝不在"只扫了事实页"的不完整范围上做唯一性裁决。
    """
    fact_pages = set()
    for fact in facts:
        if getattr(fact, 'table_code', '') != GENERAL:
            continue
        for ref in (getattr(fact, 'extra_dims', None) or {}).get('evidence_refs', []) or []:
            page = ref.get('physical_page')
            if isinstance(page, int) and page > 0:
                fact_pages.add(page)
    total_pages = len(getattr(doc, 'page_texts', []) or [])
    artifact_pages = set()
    artifact = getattr(doc, 'parse_artifact', None)
    for table in getattr(artifact, 'logical_tables', None) or []:
        if getattr(table, 'table_code', '') != GENERAL:
            continue
        start, end = getattr(table, 'page_start', None), getattr(table, 'page_end', None)
        if isinstance(start, int) and isinstance(end, int) and 1 <= start <= end:
            artifact_pages.update(range(start, end + 1))
    for fragment in getattr(artifact, 'table_fragments', None) or []:
        if getattr(fragment, 'table_code', '') != GENERAL:
            continue
        for cell in getattr(fragment, 'cells', None) or []:
            page = getattr(cell, 'page_number', None)
            if isinstance(page, int) and page > 0:
                artifact_pages.add(page)
    if artifact_pages and (not total_pages or max(artifact_pages) <= total_pages):
        return sorted(fact_pages | artifact_pages)
    # 无可信产物范围（或范围越界=产物不完整）→ 全部页：完整按构造成立，
    # 候选只会更多、唯一性只会更难——不会在缩小后的候选集上误绑。
    return sorted(range(1, total_pages + 1))


def _original_name_candidates(doc, pages, name, width):
    """原表中与给定名称同宽度的全部编码候选（IR03：原件权威唯一性）。

    行名与目标名同名，或行名是目标名的前缀（原表行名截断形态，如
    "机关事业单位基本养老保"）。任何宽度过滤按编码位数执行。
    """
    name_n = _name(name)
    if not name_n:
        return set()
    candidates = set()
    for code, row_name in _original_table_rows(doc, pages):
        if len(code) != width:
            continue
        row_n = _name(row_name)
        if row_n and (row_n == name_n or name_n.startswith(row_n)):
            candidates.add(code)
    return candidates


def _native_grid_name_rows(doc, pages):
    """折行原表的码名身份：原生文字按格定位，且原文七位编码全集不能漏行。

    用于名称有差异时的独立金额核对；不扩展全局名称别名。解析表若漏掉
    原文中的任何项级编码，整条恢复路径拒收，不能拿幸存候选自证唯一。
    """
    artifact, context = getattr(doc, 'parse_artifact', None), getattr(doc, 'fact_context', None)
    if (not pages or not artifact or not context or artifact.source_file_hash != context.file_hash
            or artifact.parse_run_uid != context.parse_run_uid):
        return {}, {}
    if any(not isinstance(p, int) or not 1 <= p <= len(doc.page_texts) for p in pages):
        return {}, {}
    # 片段范围不能排除漏解析的续页：一直扫到下一张核心表的原文表头。
    # 找不到明确边界时扩大范围只会让完整性更难通过，不能缩小候选集合。
    pages = list(pages)
    next_table = re.compile(r'收入支出决算总表|收入决算表|支出决算表|'
        r'财政拨款收入支出决算总表|一般公共预算财政拨款基本支出决算表|'
        r'财政拨款[“"]?三公[”"]?经费支出决算表|'
        r'政府性基金预算财政拨款收入支出决算表|国有资本经营预算财政拨款收入支出决算表')
    for page in range(max(pages) + 1, len(doc.page_texts) + 1):
        head = re.sub(r'\s+', '', str(doc.page_texts[page - 1] or ''))[:180]
        if next_table.search(head) and '一般公共预算财政拨款支出决算表' not in head:
            break
        pages.append(page)
    raw_counts = Counter((page, m[1]) for page in pages for m in re.finditer(
        r'(?<!\d)(\d{7})(?!\d)', str(doc.page_texts[page - 1] or '')))
    if not raw_counts:
        return {}, {}
    tokens = [t for t in artifact.source_tokens
              if t.source_method == 'native_text' and t.physical_page in pages and t.bbox
              and t.source_ref.get('source_file_hash') == context.file_hash]
    if (any(not t.token_uid or not t.source_ref.get('source_block_id') for t in tokens)
            or len({t.token_uid for t in tokens}) != len(tokens)):
        return {}, {}
    rows, refs, certified_counts = {}, {}, Counter()
    for table in artifact.table_fragments:
        if table.table_code != GENERAL or table.extraction_method != 'lines':
            continue
        for code_cell in table.cells:
            code = str(code_cell.raw_text or '').strip()
            if (not re.fullmatch(r'\d{3}|\d{5}|\d{7}', code)
                    or code_cell.page_number not in pages or code_cell.ocr_provenance or code_cell.is_header):
                continue
            # 类/款/项三列在数据行常合并成一格，逻辑 col_idx 会留下两个占位列。
            # 相邻关系必须按原件网格边界判定，不能把“索引+1”当作物理相邻。
            names = [c for c in table.cells if c.row_idx == code_cell.row_idx
                     and c.col_idx > code_cell.col_idx and c.page_number == code_cell.page_number
                     and c.bbox and code_cell.bbox
                     and abs(code_cell.bbox[2] - c.bbox[0]) <= .5
                     and abs(code_cell.bbox[1] - c.bbox[1]) <= .5
                     and abs(code_cell.bbox[3] - c.bbox[3]) <= .5]
            if len(names) != 1 or names[0].ocr_provenance:
                continue
            # row_idx 不能自证同排：必须为原网格中左右相邻、上下边界相同的两格。
            if (not code_cell.bbox or not names[0].bbox
                    or abs(code_cell.bbox[2] - names[0].bbox[0]) > .5
                    or abs(code_cell.bbox[1] - names[0].bbox[1]) > .5
                    or abs(code_cell.bbox[3] - names[0].bbox[3]) > .5):
                continue
            proof = []
            for cell in (code_cell, names[0]):
                if not cell.bbox:
                    break
                x0, y0, x1, y1 = cell.bbox
                native = sorted([t for t in tokens if t.physical_page == cell.page_number
                    and x0 - .5 <= t.bbox[0] <= t.bbox[2] <= x1 + .5
                    and y0 - .5 <= t.bbox[1] <= t.bbox[3] <= y1 + .5],
                    key=lambda t: (t.bbox[1], t.bbox[0]))
                literal = lambda v: re.sub(r'\s+', '', str(v or ''))
                if not native or literal(''.join(t.raw_text for t in native)) != literal(cell.raw_text):
                    break
                proof.extend({'file_hash': context.file_hash, 'page': t.physical_page,
                              'span': t.raw_text, 'bbox': list(t.bbox),
                              'source_block_id': t.source_ref.get('source_block_id'),
                              'role': '原生格内码名身份'} for t in native)
            else:
                rows.setdefault(code, set()).add(literal(names[0].raw_text))
                refs.setdefault(code, []).extend(proof)
                if len(code) == 7:
                    certified_counts[(code_cell.page_number, code)] += 1
    if raw_counts != certified_counts or any(len(names) != 1 for names in rows.values()):
        return {}, {}
    return {code: next(iter(names)) for code, names in rows.items()}, refs


def _bind_klass_via_independent_chain(doc, facts, item, paths, aliases):
    """A2（R2.2 附页 QP-04）：类名缺口时按独立身份链绑定类级，不用近似名称。

    准入（全部成立才绑定；修正编码不等于已证明绑定）：
    1) 说明原文条目行带显式 3 位类编码，且该编码在表内唯一 → 直接身份约束；
    2) 无编码时：款名、项名的**权威候选集来自完整原表行扫描**（IR03）——
       原表中该名称各唯一命中、项编码挂在款编码之下、类行在原表存在，且
       与已解析 facts 的唯一命中一致（双向一致：原件多候选而解析只产出一个、
       或原件与解析各指不同编码，一律拒绝）。
    返回绑定的 3 位类编码；不成立返回 None（金额保持未知，名称差异照报）。
    """
    # 判据 1：原文显式编码（条目行内、类名标记之前的行前缀——金额印在
    # （项）之后的同一物理行上，不截前缀会把"7,650.32"里的"650"误当类编码）。
    # dash 连接形态的条目没有（类）标记，前缀即整行：金额段恰为表内 3 位
    # 类编码且与类名原件共现的概率趋零，且共现核验不过只导致"不救回"
    # （fail-closed，维持原有 text_binding_failed 路径），不会误绑。
    line = _item_source_line(doc, item)
    prefix = re.split(r"[（(]类[)）]", line, maxsplit=1)[0]
    explicit = [c for c in re.findall(r'(?<!\d)(\d{3})(?!\d)', prefix)
                if len(paths.get(c, '')) == 3]
    if len(explicit) == 1:
        table_name = next((_name(f.subject_name) for f in facts
                           if str(f.subject_code) == explicit[0]
                           and f.indicator == 'total_actual' and _name(f.subject_name)), '')
        if table_name and _chain_visible_in_original(doc, [(explicit[0], table_name)]):
            if aliases is not None:
                aliases.append({'role': 'explicit_klass_code_in_narrative',
                                'klass_code': explicit[0]})
            return explicit[0]
        return None
    # 判据 2：独立层级链（IR03：唯一性以**完整原表行扫描**为权威）
    def unique_by_name(width, name):
        if not name:
            return None
        hits = {code for code, path in paths.items()
                if len(path) == width
                and {_name(f.subject_name) for f in facts
                     if str(f.subject_code) == code
                     and f.indicator == 'total_actual'
                     and _name(f.subject_name)} == {_name(name)}}
        return next(iter(hits)) if len(hits) == 1 else None

    table_pages = _general_table_pages(doc, facts)
    if not table_pages:
        return None  # 原表范围拿不到 → 不绑定（fail-closed）
    # 原件权威候选集：名称在**完整原表**中的全部同宽度编码。
    kuan_original = _original_name_candidates(doc, table_pages, item['kuan'], 5)
    if len(kuan_original) != 1:
        return None
    xiang_original = _original_name_candidates(doc, table_pages, item['xiang'], 7)
    if len(xiang_original) != 1:
        return None
    kuan_code = next(iter(kuan_original))
    xiang_code = next(iter(xiang_original))
    if not xiang_code.startswith(kuan_code):
        return None
    # 与解析 facts 双向一致：facts 的唯一命中（若存在）必须同码；facts 缺行
    # 或多行同名的（解析漏行/重复行）同样拒绝。
    if unique_by_name(5, item['kuan']) != kuan_code:
        return None
    if unique_by_name(7, item['xiang']) != xiang_code:
        return None
    klass_code = kuan_code[:3]
    # 类行必须在原表存在（层级链来源完整性：款行挂在真实类行之下）
    if not any(code == klass_code for code, _row_name
                in _original_table_rows(doc, table_pages, codes=set(paths))):
        return None
    table_klass_name = next((_name(f.subject_name) for f in facts
                             if str(f.subject_code) == klass_code
                             and f.indicator == 'total_actual'
                             and _name(f.subject_name)), '')
    table_klass_name_raw = next((str(f.subject_name) for f in facts
                                 if str(f.subject_code) == klass_code
                                 and f.indicator == 'total_actual'
                                 and _name(f.subject_name)), '')
    chain = [
        (klass_code, table_klass_name),
        (kuan_code, item['kuan']),
        (xiang_code, item['xiang']),
    ]
    if not all(table_klass_name and _name(name) for _c, name in chain[1:]):
        return None
    if not _chain_visible_in_original(doc, chain):
        return None
    if aliases is not None:
        aliases.append({'role': 'independent_hierarchy_chain',
                        'klass_code': klass_code, 'kuan_code': kuan_code,
                        'xiang_code': xiang_code})
        if _name(item['klass']) != table_klass_name:
            table_page = next((ref.get('physical_page') for f in facts
                               if str(f.subject_code) == klass_code
                               and f.indicator == 'total_actual'
                               for ref in (getattr(f, 'extra_dims', None) or {}).get('evidence_refs', []) or []
                               if ref.get('physical_page')), None)
            aliases.append({'role': 'klass_name_difference',
                            'narrative_name': str(item['klass']),
                            'table_name': table_klass_name_raw or table_klass_name,
                            'klass_code': klass_code,
                            'table_page': table_page})
    return klass_code


def _leaf_only_paths(facts):
    """FIN_05 只列项级（7 位）行、没有任何类/款行时返回层级路径，否则 None。

    有些单位的一般公共预算表只印项级明细（如 2080501、2120101），不印 208、
    20805 这类汇总行。此时说明里的（类）（款）名称在表内无行可对，只能按项名
    绑定项级行；类金额按编码前 3 位汇总（见 :func:`leaf_only_classes`）。表内只要
    有一行 3/5 位（含补零写法）就是常规层级表，不走这里。
    """
    paths = hierarchy_paths({str(f.subject_code) for f in facts
                             if str(f.subject_code or '').isdigit()})
    if not paths or len(set(paths.values())) != len(paths):
        return None
    if any(len(path) != 7 for path in paths.values()):
        return None
    return paths


def _strict_leaf_fact(doc, facts, code):
    """与 :func:`select` 同口径地取项级决算数，但不登记取数缺口（用于汇总推导）。"""
    from .fact_context import _canonical_budget_stage, operand_rejection_reason
    context = doc.fact_context
    candidates = [fact for fact in facts
                  if fact.subject_code == code and fact.indicator == 'total_actual'
                  and _canonical_budget_stage(fact.budget_stage) == '决算']
    if len(candidates) > 1:
        # 同 select：不同原格上整行重复印刷的副本按值收敛；同一原格登记多条仍不唯一。
        cells = {tuple(sorted({ref.get('source_cell_id') for ref in (fact.extra_dims or {}).get('evidence_refs', [])
                               if ref.get('source_cell_id')})) for fact in candidates}
        if len(cells) > 1:
            unique = {}
            for fact in candidates:
                unique.setdefault((str(fact.raw_value), str(fact.normalized_value), fact.unit), fact)
            candidates = list(unique.values())
    if len(candidates) != 1:
        return None
    fact = candidates[0]
    if (fact.normalized_value is None or operand_rejection_reason(fact, context.org_name)
            or context.fiscal_year is None or fact.fiscal_year != context.fiscal_year
            or fact.period not in (None, '本年')):
        return None
    return fact


def leaf_only_classes(doc, facts=None):
    """项级表的类金额与类名：{类编码: {'sum', 'leaves', 'name'}}；常规层级表返回 {}。

    - 金额：该类编码前缀下全部项级行决算数之和；任何一行取不到可用金额，该类不给金额。
    - 类名：只取同一说明节“具体情况”条目的写法——条目的项名在表内唯一对应某个
      项级编码，同一类编码下各条目写的（类）名一致才采用。不用科目字典，也不按
      金额猜名称；取不到类名时 ``name`` 为 None。
    """
    if facts is None:
        facts, reason = table_facts(doc, GENERAL)
        if reason:
            return {}
    paths = _leaf_only_paths(facts)
    if paths is None:
        return {}
    # 项级行必须读全：各项之和等于表内合计行（显示舍入包络内）才推导类金额。
    # 解析丢了类行、同时也丢了项行时，这里放弃推导，不拿残缺明细冒充类合计。
    leaves_all = [_strict_leaf_fact(doc, facts, code) for code in sorted(paths)]
    total = _strict_leaf_fact(doc, facts, 'total')
    if total is None or any(fact is None for fact in leaves_all):
        return {}
    from .amount_math import rounding_envelope
    leaf_sum = sum((Decimal(str(f.normalized_value)) for f in leaves_all), Decimal('0'))
    if amount_difference_exceeds_policy(Decimal(str(total.normalized_value)) - leaf_sum,
                                        rounding_envelope(len(leaves_all))):
        return {}
    classes = {}
    for code in sorted(paths):
        classes.setdefault(code[:3], []).append(code)
    names = {}
    text, ranges = _general_ranges(doc)
    if ranges:
        _title, begin, end = ranges[0]
        for item in [*_ITEM.finditer(text.compact, begin, end), *_DASH_ITEM.finditer(text.compact, begin, end)]:
            codes = {str(f.subject_code) for f in facts
                     if str(f.subject_code) in paths and f.indicator == 'total_actual'
                     and _name(f.subject_name) == _name(item['xiang'])}
            if len(codes) == 1:
                names.setdefault(next(iter(codes))[:3], set()).add(re.sub(r'\s+', '', item['klass']))
    result = {}
    for prefix, codes in classes.items():
        leaves = [_strict_leaf_fact(doc, facts, code) for code in codes]
        if any(fact is None for fact in leaves):
            continue
        written = names.get(prefix) or set()
        result[prefix] = {'sum': sum((Decimal(str(f.normalized_value)) for f in leaves), Decimal('0')),
                          'leaves': leaves, 'name': next(iter(written)) if len(written) == 1 else None}
    return result


def _native_name_variant_code(doc, facts, name, width, parent, paths, aliases):
    """有限名称变体须由完整原生网格及逐级编码链证明，绝不用金额或编辑距离。"""
    from src.services.fiscal_subject_names import literal_subject_name, subject_name_candidates

    rows, refs = _native_grid_name_rows(doc, _general_table_pages(doc, facts))
    native_paths = hierarchy_paths(rows)
    names = {code: value for code, value in rows.items()
             if len(native_paths.get(code, '')) == width
             and native_paths[code].startswith(parent)}
    candidates = subject_name_candidates(name, names)
    if len(candidates) != 1:
        return None
    code = candidates[0]
    if paths.get(code) != native_paths[code]:
        return None
    chain = []
    for size in (3, 5, 7):
        if size > width:
            break
        matching = [key for key, path in native_paths.items() if path == native_paths[code][:size]]
        if len(matching) != 1:
            return None
        key = matching[0]
        fact, error = select(doc, facts, 'total_actual', code=key)
        if (error or not fact or fact.table_code != GENERAL
                or fact.fund_scope != 'general_public_budget' or fact.budget_stage != '决算'
                or literal_subject_name(fact.subject_name) != literal_subject_name(rows[key])):
            return None
        chain.append(key)
    # 仅改变本条关系的定位依据，原表名称和事实快照保持原样。
    if aliases is not None:
        aliases.extend(ref for key in chain for ref in refs[key])
        aliases.append({'role': 'native_subject_name_variant', 'subject_code': code,
                        'narrative_name': name, 'table_name': rows[code],
                        'basis': 'unique_native_code_parent_and_name_variant'})
    return code


def _hierarchy_fact(doc, facts, item, *, siblings=(), aliases=None):
    paths = hierarchy_paths({str(f.subject_code) for f in facts
                             if str(f.subject_code or '').isdigit()})
    if len(set(paths.values())) != len(paths):
        return None, '功能分类编码的层级身份有冲突'
    parent = ''
    klass = ''
    # 项级表没有类/款行：（类）（款）名称在表内无行可对，直接按项名绑定项级行；
    # 类名是否写对由 V33-227 按同节条目与类金额另行核对。
    levels = ([('xiang', 7)] if _leaf_only_paths(facts) is not None
              else [('klass', 3), ('kuan', 5), ('xiang', 7)])

    def rebind_leaf_under_class():
        # 款名写错不能阻断独立金额检查：只在原表同类下项名唯一、
        # 原文款名也确实存在时重新绑定，绝不按金额相近或科目字典猜配。
        # 名称冲突仍由同一检查义务中的 V33-227 单独报告。原文款名在表内根本
        # 不存在（如旧名“行政事业单位离退休（款）”）时不重新绑定，转人工。
        leaf_codes = {str(f.subject_code) for f in facts
                      if re.fullmatch(r'\d{7}', str(f.subject_code or ''))
                      and paths.get(str(f.subject_code), '').startswith(klass)
                      and _name(f.subject_name) == _name(item['xiang'])}
        if len(leaf_codes) != 1:
            return None
        leaf_code = next(iter(leaf_codes))
        actual_parent = next((code for code, path in paths.items()
                              if path == paths[leaf_code][:5]), None)
        parents = [f for f in facts if f.subject_code == actual_parent
                   and f.indicator == 'total_actual']
        if len(parents) != 1 or not _name(parents[0].subject_name):
            return None
        for path in (klass, parent, paths[actual_parent]):
            code = next(code for code, candidate in paths.items() if candidate == path)
            _, error = select(doc, facts, 'total_actual', code=code)
            if error:
                return None, error
        return select(doc, facts, 'total_actual', code=leaf_code)

    def bind_under_parent_word_order_error():
        # 款名仅为同字异序时，可用完整原表中逐字唯一的项名及原件层级链
        # 独立核对金额；名称错误仍由 V33-227 报告。不同字、旧名、同名多项
        # 或原件链缺失均不绑定，更不按金额相同猜配。
        pages = _general_table_pages(doc, facts)
        native_rows, native_refs = _native_grid_name_rows(doc, pages)
        original = {code for code, name in native_rows.items()
                    if len(code) == 7 and _name(name) == _name(item['xiang'])}
        if len(original) != 1:
            return None
        leaf_code = next(iter(original))
        if leaf_code not in paths or not paths[leaf_code].startswith(klass):
            return None
        parent_code = next((c for c, path in paths.items() if path == paths[leaf_code][:5]), None)
        parent_fact, error = select(doc, facts, 'total_actual', code=parent_code) if parent_code else (None, 'missing')
        if error or not parent_fact:
            return None
        actual, stated = _name(parent_fact.subject_name), _name(item['kuan'])
        if actual == stated or Counter(actual) != Counter(stated):
            return None
        klass_code = next((c for c, path in paths.items() if path == klass), None)
        klass_fact, error = select(doc, facts, 'total_actual', code=klass_code) if klass_code else (None, 'missing')
        chain = [(klass_code, klass_fact.subject_name if klass_fact else ''),
                 (parent_code, parent_fact.subject_name), (leaf_code, item['xiang'])]
        if error or not klass_fact or not all(
                _name(native_rows.get(code)) == _name(name) for code, name in chain):
            return None
        fact, error = select(doc, facts, 'total_actual', code=leaf_code)
        if not error and _name(fact.subject_name) == _name(item['xiang']):
            if aliases is not None:
                aliases.extend(ref for code, _ in chain for ref in native_refs[code])
                aliases.append({'role': 'independent_hierarchy_chain_with_parent_word_order',
                                'klass_code': klass_code, 'kuan_code': parent_code, 'xiang_code': leaf_code,
                                'narrative_name': item['kuan'], 'table_name': parent_fact.subject_name})
            return fact, None
        return None

    def bind_exact_leaf_independent_of_parent_label():
        # 原文款名有误时，须两条不同项名各自逐字唯一地落入同一原生款链。
        # 不采用旧名词典、编辑距离或金额匹配；原文款名差异仍交名称规则报告。
        pages = _general_table_pages(doc, facts)
        native_rows, native_refs = _native_grid_name_rows(doc, pages)
        codes = {code for code, name in native_rows.items()
                 if len(code) == 7 and paths.get(code, '').startswith(klass)
                 and _name(name) == _name(item['xiang'])}
        if len(codes) != 1:
            return None
        leaf = next(iter(codes))
        parent_code = next((code for code, path in paths.items() if path == paths[leaf][:5]), None)
        klass_code = next((code for code, path in paths.items() if path == klass), None)
        corroborated = set()
        source_text, ranges = _general_ranges(doc)
        for other in siblings:
            if _name(other['klass']) != _name(item['klass']) or _name(other['kuan']) != _name(item['kuan']):
                continue
            scope = next((begin for _title, begin, end in ranges if begin <= other.start() < end), None)
            if scope is None or _guard(doc, source_text, scope, other.start(), other.start()):
                continue
            matches = {code for code, name in native_rows.items()
                       if len(code) == 7 and paths.get(code, '').startswith(paths[leaf][:5])
                       and _name(name) == _name(other['xiang'])}
            if len(matches) == 1:
                corroborated.update(matches)
        if len(corroborated) < 2 or leaf not in corroborated:
            return None
        chain = []
        for code in (klass_code, parent_code, leaf):
            fact, error = select(doc, facts, 'total_actual', code=code) if code else (None, 'missing')
            if error or fact is None or _name(native_rows.get(code)) != _name(fact.subject_name):
                return None
            chain.append(fact)
        if aliases is not None:
            aliases.extend(ref for fact in chain for ref in native_refs[fact.subject_code])
            aliases.extend(ref for code in sorted(corroborated - {leaf}) for ref in native_refs[code])
            aliases.append(dict(role='independent_exact_leaf_chain', klass_code=klass_code,
                                kuan_code=parent_code, xiang_code=leaf,
                                corroborating_leaf_codes=sorted(corroborated),
                                narrative_parent=item['kuan'], table_parent=chain[1].subject_name))
        return chain[-1], None

    for group, width in levels:
        candidates = {str(f.subject_code) for f in facts
                      if len(paths.get(str(f.subject_code), '')) == width
                      and str(f.subject_code).isdigit()
                      and paths[str(f.subject_code)].startswith(parent)
                      and _name(f.subject_name) == _name(item[group])}
        if group != 'xiang' and len(candidates) != 1:
            bound_code = _native_name_variant_code(doc, facts, item[group], width, parent, paths, aliases)
            if bound_code is not None:
                candidates = {bound_code}
        if group == 'klass' and not candidates:
            # A2（R2.2 QP-04）：类名缺口按独立身份链处理——显式编码或
            # 款/项逐字唯一的层级链 + 原件共现核验。不成立则落入既有的
            # "找不到同名科目"失败（金额仍未知，名称差异照报）。
            chain_klass = _bind_klass_via_independent_chain(doc, facts, item, paths, aliases)
            if chain_klass is not None:
                parent = paths[chain_klass]
                klass = parent
                continue
        if len(candidates) != 1:
            if group == 'kuan' and not candidates:
                bound = bind_under_parent_word_order_error()
                if bound is not None:
                    return bound
                bound = bind_exact_leaf_independent_of_parent_label()
                if bound is not None:
                    return bound
            if group == 'xiang' and not candidates:
                matches = []
                for f in facts:
                    if (f.indicator == 'total_actual' and len(paths.get(str(f.subject_code), '')) == 7
                            and paths[str(f.subject_code)].startswith(parent)):
                        proof = _truncated_name_source(doc, f, item, siblings)
                        if proof:
                            matches.append((f, proof))
                if len(matches) == 1:
                    fact, error = select(doc, facts, 'total_actual', code=matches[0][0].subject_code)
                    if not error and aliases is not None:
                        aliases.append(matches[0][1])
                    return fact, error
            if group == 'xiang':
                # 已有的原格截断补全证据优先，避免把抽断的“缴费”误作可选后缀。
                bound_code = _native_name_variant_code(doc, facts, item[group], width, parent, paths, aliases)
                if bound_code is not None:
                    return select(doc, facts, 'total_actual', code=bound_code, allow_native_blank=True)
            if group == 'xiang' and not candidates:
                rebound = rebind_leaf_under_class()
                if rebound is not None:
                    return rebound
            level = {'klass': '类', 'kuan': '款', 'xiang': '项'}[group]
            if not candidates:
                # 仅解释原文差异，不把近似名称或相同金额升级为同一科目身份。
                near = {}
                requested = _name(item[group])
                if group == 'xiang':
                    for f in facts:
                        code = str(f.subject_code)
                        actual = _name(f.subject_name)
                        if (f.indicator != 'total_actual' or len(paths.get(code, '')) != 7
                                or not paths[code].startswith(parent) or not 0 < len(actual)-len(requested) <= 2):
                            continue
                        chars = iter(actual)
                        if all(char in chars for char in requested):
                            near[code] = f
                if len(near) == 1:
                    candidate = next(iter(near.values()))
                    refs = candidate.extra_dims.get('evidence_refs', [])
                    pages = sorted({ref['physical_page'] for ref in refs if ref.get('physical_page')})
                    text = _Text.from_pages(doc.page_texts)
                    source = text.refs(item.start(), item.end(), '原文科目')
                    reason = (f"科目名称差异候选：正文第{source[0]['page'] if source else '?'}页写“{item[group]}”；"
                                  f"同款表格第{'、'.join(map(str,pages)) or '?'}页编码{candidate.subject_code}写“{candidate.subject_name}”。"
                                  "名称不一致，尚未绑定金额；请核对科目名称后确认，不能仅凭金额相同认定一致。")
                    _record_text_gap(doc, reason, item, codes=['name_mismatch_confirmed'], role='说明科目对应金额')
                    return None, reason
                reason = (f'说明中的{level}“{item[group]}”在表内找不到同名科目'
                          f'（可能使用了旧科目名称或写错），无法与表格金额对应')
                _record_text_gap(doc, reason, item, codes=['text_binding_failed'], role='说明科目对应金额')
                return None, reason
            reason = f'说明中的{level}“{item[group]}”在表内对应多个科目，无法唯一对应'
            _record_text_gap(doc, reason, item, codes=['text_binding_failed'], role='说明科目对应金额')
            return None, reason
        parent = paths[next(iter(candidates))]
        if group == 'klass':
            klass = parent
    code = next(code for code, path in paths.items() if path == parent)
    return select(doc, facts, 'total_actual', code=code, allow_native_blank=True)


def _general_ranges(doc):
    return sections(doc, is_general_expenditure_title)


def _zero_item_parent_reconciles(doc, facts, item) -> bool:
    """说明列示为 0、表内却省略该零值项时，父级款合计能否证明它确为 0。

    只处理“项名在表内找不到”的情形；父级款合计必须等于表内已列项之和。
    若未列项其实有金额，父级合计会大于已列项之和，本函数返回 False，
    调用方继续按取数不足/问题处理，不静默补零。
    """
    codes = {str(f.subject_code) for f in facts if str(f.subject_code or "").isdigit()}
    paths = hierarchy_paths(codes)
    if len(set(paths.values())) != len(paths):
        return False

    def unique_code(width: int, prefix: str, name: str):
        matches = {
            code
            for code in codes
            if len(paths.get(code, "")) == width
            and paths[code].startswith(prefix)
            and {
                _name(f.subject_name)
                for f in facts
                if str(f.subject_code) == code and f.indicator == "total_actual"
            }
            == {_name(name)}
        }
        return next(iter(matches)) if len(matches) == 1 else None

    klass_code = unique_code(3, "", item["klass"])
    if klass_code is None:
        return False
    kuan_code = unique_code(5, paths[klass_code], item["kuan"])
    if kuan_code is None:
        return False
    parent, error = select(doc, facts, "total_actual", code=kuan_code)
    if error or parent is None:
        return False
    children = [
        f
        for f in facts
        if f.indicator == "total_actual"
        and len(paths.get(str(f.subject_code), "")) == 7
        and paths[str(f.subject_code)].startswith(paths[kuan_code])
        and f.instance_uid == parent.instance_uid
        and f.budget_stage == parent.budget_stage
        and f.fund_scope == parent.fund_scope
    ]
    if not children:
        return False
    total = sum((f.normalized_value for f in children), Decimal("0"))
    level, _ = classify_amount_diff(parent.normalized_value, total, n_children=len(children))
    return level in {"ok", "rounding_hint", "within_policy_tolerance"}


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
    paths = hierarchy_paths({str(f.subject_code) for f in facts
                             if str(f.subject_code or '').isdigit()})
    for code in sorted(code for code, path in paths.items() if len(path) == 3):
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
    # 项级表：类金额 = 该类下各项合计，类名取同节具体情况条目的写法。
    for code, entry in sorted(leaf_only_classes(doc, facts).items()):
        if not entry['name']:
            continue
        label = _name(entry['name'])
        pattern = re.compile(rf'{re.escape(label)}(?:支出)?(?:\(类\))?(?:为)?'
                             rf'(?P<amount>{_NUMBER})(?P<unit>{_UNIT})')
        note = (f'表内未列类级行，按{code}类下{len(entry["leaves"])}个项级行合计核算')
        for claim in pattern.finditer(text.compact, begin, end):
            key = f'general:text:class:{code}:{claim.start()}'
            error = _guard(doc, text, begin, claim.start(), claim.end())
            if error:
                _unresolved(rule, doc, key, error, unresolved, entry['leaves'])
                continue
            issues.extend(_amount_from_derived(
                rule, doc, text, claim.start(), claim.end(), claim['amount'], claim['unit'],
                (entry['sum'], tuple(entry['leaves']), note), key, label, title,
                subject_code=code, derived_from='leaf_rows', basis='该类各项级行合计'))
    items = sorted([*_ITEM.finditer(text.compact, begin, end), *_DASH_ITEM.finditer(text.compact, begin, end)],
                   key=lambda match: match.start())
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
        aliases = []
        fact, error = _hierarchy_fact(doc, facts, item, siblings=items, aliases=aliases)
        # A2（R2.2 QP-04）：类名差异在身份链成立时只作提示，金额照常核对。
        name_hint = next((a for a in aliases if isinstance(a, dict)
                          and a.get('role') == 'klass_name_difference'), None)
        if name_hint and not error:
            hint_page = _name_difference_hint(doc, item)
            # A2/复核矩阵：类名提示带**双侧位置**（说明页 + 表内页），
            # 不能只有说明单侧。
            hint_pages = sorted({p for p in (hint_page, name_hint.get('table_page')) if p})
            issues.append(rule._issue(
                f"说明第{hint_page or '?'}页写类名“{name_hint['narrative_name']}”，表内第"
                f"{name_hint.get('table_page') or '?'}页编码 "
                f"{name_hint['klass_code']} 科目为“{name_hint['table_name']}”；"
                "本条目已按款/项独立身份链核对金额，类名差异请核对是否漏字。",
                {'pages': hint_pages,
                 'page': hint_page or (hint_pages[0] if hint_pages else None),
                 'klass_code': name_hint['klass_code'],
                 'narrative_class_name': name_hint['narrative_name'],
                 'table_class_name': name_hint['table_name']} if hint_pages else {},
                'info',
                # IR-03：证据只引说明侧原文类名（缺陷所在页）；表内类名与编码
                # 在 message/location 结构化携带，不用拼贴说明冒充原文。
                evidence_text=name_hint['narrative_name'],
                check_relation_id=f'{rule.code}:general:text:item:{item.start()}:klass_name',
            ))
        # 博物馆、图书馆等 are literal fiscal subjects, not publisher names.
        # Their hierarchy is already validated above; only the declaration
        # prefix can introduce a foreign owner or year for this item.
        error = error or _guard(doc, text, begin, item.start(), item.start())
        if not claims:
            # IR-02（Codex 独立核验 2026-11-10）：同条目唯一单位继承——
            # 显式决算声明漏写单位（宜川 P29"支出决算为4.19。"，同条目首句
            # 已有"4.19 万元"）时，裸声明金额与条目首句带单位金额一致即按该
            # 单位归一继续核对；漏单位缺陷由 CMM-011 独立交付。依据只来自
            # 同条目原文，不跨条目、不按表值凑平、不跨条目继承。
            bare = re.compile(
                rf'(?:支出决算(?:数)?|决算数)[，,]?(?:为|是)?(?P<amount>{_NUMBER})'
                rf'(?!(?:亿元|万元|元))')
            bare_claims = list(bare.finditer(text.compact, item.end(), stop))
            first_stop = text.compact.find('。', item.end(), stop)
            if first_stop == -1:
                first_stop = stop
            header = re.compile(
                rf'(?P<amount>{_NUMBER})(?P<unit>{_UNIT})').search(
                text.compact, item.end(), first_stop)
            if len(bare_claims) == 1 and header is not None:
                try:
                    if _dec(bare_claims[0]['amount']) == _dec(header['amount']):
                        claims = [_BareClaimWithUnit(
                            bare_claims[0]['amount'], header['unit'],
                            bare_claims[0].start(), bare_claims[0].end())]
                except (ValueError, ArithmeticError):
                    pass
        if len(claims) != 1:
            decision_error = f'{item["xiang"]}条目显式决算金额缺失或不唯一，不能用预算金额替代'
            error = error or decision_error
            # DH-02：说明条目内无法唯一确定显式决算金额；位置落在条目原文页。
            _record_text_gap(doc, [decision_error, error], item,
                             codes=['operand_missing'],
                             role=f'{item["xiang"]}条目显式决算金额')
        if error:
            # 零额项被表内省略：说明明确写 0，且父级款合计 = 表内已列项之和。
            # 这不是“找不到科目”的取数不足，而是零值行未列示；不弹补核。
            if (
                len(claims) == 1
                and _wan(claims[0]["amount"], claims[0]["unit"]) == 0
                and "说明中的项" in error
                and "在表内找不到同名科目" in error
                and _zero_item_parent_reconciles(doc, facts, item)
            ):
                receipt(
                    rule,
                    doc,
                    key,
                    [],
                    status="not_applicable",
                    formula="说明列示决算为0且表内未列零值行；父级款合计=已列项之和",
                )
                continue
            _unresolved(rule, doc, key, error, unresolved, [fact] if fact else [])
        else:
            claim = claims[0]
            issues.extend(_amount(rule, doc, text, item.start(), claim.end(), claim['amount'],
                          claim['unit'], fact, key, item['xiang'], title, identity_sources=aliases))
    return finish(rule, issues, unresolved)


def _fund_total_from_classes(doc, facts):
    """合计行本年支出被支出子表头占位时，按类级行合计给出总额。

    区级模板把「合计/基本支出/项目支出」三个叶表头印在合计行同一原行上
    （:func:`src.services.fiscal_column_identity.mixed_expenditure_header_cells`
    已按表头处理这些单元格），所以合计行的本年支出没有金额事实。此时类级行
    齐全可读就用它们的和作总额——这是"合计 = 各类之和"的勾稽，不是空白补零：
    任何一个 3 位编码行缺原始值或读不出金额就放弃推导，交由调用方按取数不足
    上报。
    """
    classes = [f for f in facts
               if f.indicator == 'total_actual'
               and re.fullmatch(r'\d{3}', str(f.subject_code or ''))]
    codes = [str(f.subject_code) for f in classes]
    if not classes or len(set(codes)) != len(codes):
        return None, (), ''
    if any(not str(f.raw_value or '').strip() or f.normalized_value is None for f in classes):
        return None, (), ''
    # 类级行必须与合计行同一操作数口径：年度、期间、阶段与主体都要过 select 同款判据。
    from .fact_context import _canonical_budget_stage, operand_rejection_reason
    context = doc.fact_context
    for fact in classes:
        if (operand_rejection_reason(fact, context.org_name)
                or context.fiscal_year is None or fact.fiscal_year != context.fiscal_year
                or fact.period not in (None, '本年')
                or _canonical_budget_stage(fact.budget_stage) != '决算'):
            return None, (), ''
    total = sum((f.normalized_value for f in classes), Decimal('0'))
    return total, tuple(classes), (
        '合计行本年支出单元格印的是支出子表头（合计/基本支出/项目支出），未印数；'
        '按类级行合计核算')


def _amount_from_derived(rule, doc, text, begin, end, amount, unit, derived, key, label, section,
                         *, subject_code='total', derived_from='class_rows', basis='类级行合计'):
    """说明金额 × 推导金额（类级行合计 / 项级行合计）的比对；推导依据写进 evidence/formula。"""
    value, operands, note = derived
    declared = _wan(amount, unit)
    difference = declared - value
    failed = amount_difference_exceeds_policy(difference)
    refs = text.refs(begin, end, '原文决算金额声明')
    formula = ('explicit narrative amount == sum of class rows (total cell not printed)'
               if derived_from == 'class_rows' else
               'explicit narrative class amount == sum of leaf rows under the class code (no class row printed)')
    ids, evidence = receipt(rule, doc, key, list(operands), status='fail' if failed else 'pass',
            formula=formula,
            disclosure_sources=[dict(ref, file_hash=doc.fact_context.file_hash) for ref in refs])
    if not failed:
        return []
    location = _location(doc, refs, list(operands), section, 'final_disclosure_amount',
                         table_code=operands[0].table_code, subject_code=subject_code,
                         indicator='total_actual', declared_amount_wan=str(declared),
                         table_amount_wan=str(value), declared_unit=unit,
                         difference_wan=str(difference), tolerance_wan=str(AMOUNT_TOLERANCE_WAN),
                         derived_total_from=derived_from, derived_total_note=note)
    message = (f'{label}表文金额不一致：原文{amount}{unit}折合{declared}万元，'
               f'表内{basis}{value:.2f}万元（{note}），'
               f'相差{abs(difference):.4f}万元。')
    return [rule._issue(message, location, 'error',
                        evidence_text=_quote(location),
                        check_relation_id=f'{rule.code}:{key}', fact_ids=ids,
                        evidence_ids=evidence)]


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
        derived = None
        if error and indicator == 'total_actual':
            value, operands, note = _fund_total_from_classes(doc, facts)
            if value is not None:
                derived = (value, operands, note)
                error = None
        error = error or _guard(doc, text, begin, proof_start, claim.end())
        if error:
            _unresolved(rule, doc, key, error, unresolved,
                        [fact] if fact else list(derived[1]) if derived else [])
        elif derived is not None:
            issues.extend(_amount_from_derived(rule, doc, text, proof_start, claim.end(),
                          claim['amount'], claim['unit'], derived, key, label, title))
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
    # 原文写成整数或一位小数（“占11%”）时，本身就是按该位四舍五入后的值：
    # 容差取 0.1 个百分点与原文末位半个单位中的较大者，两位小数仍按 0.1。
    exponent = declared.as_tuple().exponent
    places = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    tolerance = max(Decimal('.1'), Decimal('0.5').scaleb(-places))
    failed = abs(difference) > tolerance
    ids, evidence = receipt(rule, doc, key, facts, status='fail' if failed else 'pass', formula=formula)
    if not failed:
        return [], []
    refs = text.refs(begin, end, '原文支出占比声明')
    location = _location(doc, refs, facts, title, 'final_expenditure_share',
                         table_code=numerator.table_code, declared_percent=str(declared),
                         computed_percent=str(computed), difference_pp=str(difference),
                         tolerance_pp=str(tolerance), denominator_table_code=denominator.table_code,
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
    """同一自评范围已披露评级数量的闭合，不跨任务取数或给未披露档次补零。"""
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
            scope_stop = stop
            # The standard rating parenthesis may contain semicolons and one
            # sentence stop. An explicit following task changes the scope.
            switch = re.search(r'绩效自评中|(?:部门|重点|财政)?(?:绩效)?评价(?:的|项目|工作)'
                               r'|绩效跟踪|编报绩效目标|其中\d+个(?:重点|专项)',
                               text.compact[total.end():stop])
            if switch:
                stop = total.end() + switch.start()
            ratings = list(_RATING.finditer(text.compact, total.end(), stop))
            declarations = list(_RATING_DECLARATION.finditer(text.compact, total.end(), stop))
            key = f'performance:self_evaluation_ratings:{total.start()}'
            if len(declarations) != len(ratings):
                _unresolved(rule, doc, key, '评级类别未识别或不是互斥的标准评级，不能据此认定数量闭合',
                            unresolved, formula='self_evaluation_count = sum(distinct standard rating counts)')
                continue
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
            elif len({claim['rating'] for claim in ratings}) != len(ratings):
                reason = '同一自评范围的评级类别重复披露，数量不能唯一绑定'
            rating_proof = text.compact[total.end():ratings[-1].end()]
            if reason is None and (
                    any(int(value) != year for value in re.findall(r'(20\d{2})(?:年度|年)', rating_proof))
                    or re.search(r'上年|去年|以前年度', rating_proof)
                    or _foreign_owner(rating_proof, owner) or _CALIBER_CHANGE.search(rating_proof)):
                reason = '评级分项数量年度、主体或统计范围与本次自评不一致'
            parts = {claim['rating']: int(claim['count']) for claim in ratings}
            declared, computed = int(total['count']), sum(parts.values())
            complete = set(parts) == {'优', '良', '合格', '不合格'}
            cancelled = list(_CANCELLED_PROJECTS.finditer(text.compact, total.end(), stop))
            cancelled_count = 0
            evidence_end = ratings[-1].end()
            if cancelled:
                # “绩效自评中发现的问题……”会结束取数范围，但右括号可在其后；
                # 只用完整声明范围验证括号，不从后续任务借用任何数量。
                local = text.compact[total.end():scope_stop]
                included = re.search(r'\(其中[，,:：]?', local)
                close = local.find(')', included.end()) if included else -1
                last = cancelled[-1]
                offset = last.start() - total.end()
                # 取消项目必须明确列在同一“其中”范围内；另列的取消数量不借来凑总数。
                if (len(cancelled) != 1 or included is None or offset < included.end()
                        or close < 0 or offset >= close
                        or any(not (included.end() <= claim.start() - total.end() < close)
                               for claim in ratings)
                        or re.search(r'另有|另计|不含|不包括|另行', local[max(0, offset - 12):offset])):
                    reason = reason or '取消项目未明确纳入同一自评项目总数范围，不能跨口径拼接'
                else:
                    cancellation_proof = text.compact[total.end():last.end()]
                    if (any(int(value) != year for value in re.findall(r'(20\d{2})(?:年度|年)', cancellation_proof))
                            or re.search(r'上年|去年|以前年度', cancellation_proof)
                            or _foreign_owner(cancellation_proof, owner)
                            or _CALIBER_CHANGE.search(cancellation_proof)):
                        reason = reason or '取消项目的年度、主体或统计范围与本次自评不一致'
                    else:
                        cancelled_count = int(last['count'])
                        computed += cancelled_count
                        evidence_end = max(evidence_end, last.end())
            # D0 RC-3a：区分"完整构成"与"部分列举"（计划第二节裁定：不得把
            # 部分列举当完整构成要求加总，造成过度阻断）。
            # - 完整构成（四档全部披露）：加总等式必须成立，缺项不得当零；
            #   加总不等（小于或大于总数）都是确定矛盾，照常交付。
            # - 部分列举（如"其中，绩效评级为'优'的项目50个"）："其中"表明
            #   是子集披露而非完整构成——已披露各档数量不超过总数即为一致
            #   （不等式校验），不再要求加总闭合，也不转人工复核（宜川部门
            #   决算 53 个自评/优 50 个实证：旧实现把合法部分列举整段阻断）。
            # - 已披露之和超过总数（无论完整与否）：矛盾，照常交付。
            # IR-05（Codex 独立核验 2026-11-10）：区分"部分列举"与"完整构成
            # 声明"必须依据原文措辞，不能只看四档标签是否齐备。原文用
            # "全部/所有…评级"或"评级结果如下"声明完整构成时，即使四档
            # 没有逐项出现（如"其余评级均为0"），也必须按加总等式核对：
            # "全部53个，优50个，其余评级均为0个"=50≠53 是确定矛盾，
            # 不得因缺四档标签而当作部分列举放行。
            # R2-04/R3-01/R4-01：范围限定必须附着于**实际评级数量的披露谓词**，
            # 不能按"全部/部分…评级"词串就近取舍：
            #   - "部分项目评级资料已归档"的谓词是归档而非评级数量披露，
            #     插在完整声明的前后（句号/逗号/括号/断行）都不改变本次
            #     完整构成关系；
            #   - "全部项目评级结果如下"在对象/任务未变时始终约束本组数量，
            #     不因句号把声明与评级数字分开而失效；
            #   - 只有同一范围内同时出现带披露谓词的完整与部分声明（真实
            #     冲突）才进入明确歧义，不默认部分优先。
            scope_compact = text.compact[total.end():stop]

            def _qualifier_governs_ratings(match: 're.Match[str]') -> bool:
                """限定词所在子句必须含评级数量披露谓词（如下/见下/评级为）。"""
                clause_start = 0
                for boundary in '。；;，,（(':
                    found = scope_compact.rfind(boundary, 0, match.start())
                    if found >= 0:
                        clause_start = max(clause_start, found + 1)
                clause_end = len(scope_compact)
                for boundary in '。；;，,）)：:':
                    found = scope_compact.find(boundary, match.end())
                    if found >= 0:
                        clause_end = min(clause_end, found)
                clause = scope_compact[clause_start:clause_end]
                return bool(_RATING_DISCLOSURE_PREDICATE.search(clause))

            partial_declared = any(
                _qualifier_governs_ratings(match) for match in re.finditer(
                    r'(?:其中)?部分(?:项目|单位|自评项目)?'
                    r'[^。；;，,（）()]{0,12}(?:评级|评价)', scope_compact))
            complete_declared = any(
                _qualifier_governs_ratings(match) for match in re.finditer(
                    r'(?:全部|所有|整体|全量)(?:项目|自评项目|单位)?'
                    r'[^。；;，,（）()]{0,12}(?:评级|评价)', scope_compact))
            # R5-01/R6-01：无条件披露与限定词走同一声明依据，且必须绑定
            # **同组实际数量**——声明打开一个组，组内向后扫描实际评级
            # 数量；组终止符只有不同披露对象标题（资料/材料/清单）与本组
            # 末尾（下一自评总数/任务切换，由 stop 界定）。句号、分号、
            # 括号、断行不终止组：主体/年度/任务未变时标点不改变业务
            # 关系。其后的其他材料评级数字不吸入本组。
            def _declaration_attracts_group_counts(match: 're.Match[str]') -> bool:
                window = scope_compact[match.end():]
                material = re.search(
                    r'(?:资料|材料|清单)[^。；]{0,10}如下', window)
                scan = window[:material.start()] if material else window
                return bool(_RATING.search(scan))

            declares_ratings = any(
                _declaration_attracts_group_counts(match) for match in re.finditer(
                    r'(?:评级|评价)(?:结果|情况|等级)?(?:如下|见下)',
                    scope_compact))
            if partial_declared and complete_declared:
                reason = reason or '同一评级声明范围内完整构成与部分列举冲突，范围不能唯一确定'
            complete_declaration = (not partial_declared
                                    and (complete_declared or declares_ratings))
            comparison_basis = ('complete_sum' if complete else
                                'declared_complete_sum' if complete_declaration else
                                'disclosed_lower_bound')
            partial_enumeration = (
                not complete and comparison_basis == 'disclosed_lower_bound'
                and computed <= declared
            )
            if reason:
                _unresolved(rule, doc, key, reason, unresolved,
                            formula='self_evaluation_count compared with sum(disclosed distinct rating counts)')
                continue
            refs = text.refs(total.start(), evidence_end, '同一范围自评项目、已披露评级及取消数量')
            if not local_years:
                refs += year_refs
            file_hash = getattr(getattr(doc, 'fact_context', None), 'file_hash', None)
            # 关系类型与比较公式必须一致：完整构成用等式；只有已明确为
            # 子集披露的部分列举才用不等式。不能对 fail 仍回执 ">="。
            formula = ('self_evaluation_count = sum(disclosed distinct rating counts)'
                       if comparison_basis in ('complete_sum', 'declared_complete_sum')
                       else 'self_evaluation_count >= sum(disclosed distinct rating counts)')
            if cancelled:
                formula += ' + explicitly included cancelled projects'
            inconsistent = (declared != computed) and not partial_enumeration
            receipt(rule, doc, key, [], status='fail' if inconsistent else 'pass',
                    formula=formula,
                    disclosure_sources=[dict(ref, file_hash=file_hash) for ref in refs])
            if not inconsistent:
                continue
            location = _location(doc, refs, [], title, 'performance_self_evaluation_count',
                                 declared_count=declared, rating_counts=parts,
                                 computed_count=computed, difference_count=declared - computed,
                                 current_year=year, tolerance_count=0,
                                 rating_counts_complete=complete,
                                 comparison_basis=('complete_sum' if complete else
                                                   'declared_complete_sum' if complete_declaration else
                                                   'disclosed_lower_bound'),
                                 cancelled_project_count=cancelled_count)
            breakdown = '+'.join(f'{label}{count}' for label, count in parts.items())
            if cancelled:
                breakdown += f'+年内取消{cancelled_count}'
            comparison = ('评级分项之和不一致'
                           if complete or complete_declaration
                           else '已披露评级分项之和超过总数')
            issues.append(rule._issue(
                f'绩效自评项目数量与{comparison}：自评{declared}个，'
                f'{breakdown}={computed}个，相差{abs(declared - computed)}个。', location, 'error',
                evidence_text='\n'.join(ref['span'] for ref in refs),
                check_relation_id=f'{rule.code}:{key}'))
    return finish(rule, issues, unresolved)
