"""决算公开补充核验：功能说明、基金总额、支出占比和绩效数量。

操作数来自同一解析版本的唯一财政事实或同一绩效范围的原文声明。
未披露金额、评级数量及空白表格均不补零；关系证据不足时明确上报。
"""
from __future__ import annotations

import re
from decimal import Decimal

from .amount_math import classify_amount_diff
from .rule_policy import AMOUNT_TOLERANCE_WAN, amount_difference_exceeds_policy
from .native_narrative_checks import (
    _Text, _NUMBER, _UNIT, _wan, _metadata, _sections, _foreign_owner,
    _CALIBER_CHANGE,
)
from .native_table_arithmetic import table_facts, select, select_diagnostic, receipt, finish
from .native_table_text import is_general_expenditure_title, sections
from .native_three_public_ratios import operand_ref
from .diagnostics import (
    blocker, fact_candidate, make_diagnosis, merge_diagnoses, missing_text_operand,
    table_candidate, text_candidate,
)
from src.services.fiscal_code_hierarchy import hierarchy_paths


GENERAL = 'FIN_05_general_public_expenditure'
FUND = 'FIN_08_gov_fund'
_ITEM = re.compile(
    r'(?P<klass>[^\d，,。；;：:、.()“”]{1,60}?)\(类\)'
    r'(?P<kuan>[^\d，,。；;：:、.()“”]{1,60}?)\(款\)'
    r'(?P<xiang>[^\d，,。；;：:、.()“”]{1,60}?)\(项\)')
#: 不带（类）（款）（项）标记的写法：“社会保障和就业支出-行政事业单位养老支出-行政单位离退休”，
#: 连接号可为 - — –（全角－经 NFKC 后为 -），可带引号。类名一律以“支出”结尾，日期、区间不会误中。
_DASH_ITEM = re.compile(
    r'(?:^|(?<=[。；;:、\d]))[“"]?'  # 编号后漏写“、”（“3“社会保障…”）也认
    r'(?P<klass>[\u4e00-\u9fff]{1,28}支出)[-—–]'
    r'(?P<kuan>[\u4e00-\u9fff()]{2,40}?)[-—–]'
    r'(?P<xiang>[\u4e00-\u9fff()]{2,60}?)[”"]?(?=[。.]|主要用于)')
_DECISION = re.compile(rf'(?:支出决算(?:数)?|决算数)(?:为|是)?'
                       rf'(?P<amount>{_NUMBER})(?:为)?(?P<unit>{_UNIT})')
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


def _name(value):
    # Only the optional terminal 支出 is a documented naming variation. Do not
    # shorten subject stems: names such as 其他支出 are distinct identities.
    return re.sub(r'支出$', '', re.sub(r'\s+', '', str(value or '')))


def _unresolved(rule, doc, key, reason, unresolved, facts=(), formula='text = table',
                diagnosis=None, diagnostics=None):
    unresolved.append(reason)
    if diagnosis is None:
        from src.engine.fact_context import operand_rejection_diagnosis
        context = getattr(doc, 'fact_context', None)
        rejections = [
            operand_rejection_diagnosis(
                fact, getattr(context, 'org_name', None), role=key,
                table_code=getattr(fact, 'table_code', None),
                stage=getattr(fact, 'budget_stage', None),
                year=getattr(fact, 'fiscal_year', None),
                fund_scope=getattr(fact, 'fund_scope', None),
            )
            for fact in facts
            if getattr(fact, 'extra_dims', None)
        ]
        diagnosis = merge_diagnoses(rejections) or make_diagnosis(
            reason_codes=['rule_no_reason']
        )
    if diagnostics is not None:
        diagnostics.append(diagnosis)
    receipt(rule, doc, key, list(facts), status='insufficient_data',
            formula=formula, reason=reason, diagnosis=diagnosis)


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


def _text_span(text, start, end):
    """把紧凑文本偏移映射到单页原始区间 [start, end)。"""
    if start >= end:
        return None
    raw_start, raw_end = text.positions[start], text.positions[end - 1] + 1
    for page, (left, right) in enumerate(text.pages, 1):
        if left <= raw_start < right:
            return page, [max(0, raw_start - left), min(right, raw_end) - left]
    return None


def _sentence_diagnosis(text, start, end, *, section, role, code='text_statement_unparsed',
                        year=None, fund_scope='一般公共预算', stage='决算'):
    page_span = _text_span(text, start, end)
    page, span = (page_span if page_span else (None, None))
    candidates = []
    if page and span:
        candidates.append(text_candidate(
            section=section, page=page, span=span, value=None,
        ))
    return make_diagnosis(
        reason_codes=[code],
        missing_operands=[missing_text_operand(
            role=role, section=section, page=page, row_key=None, column=None,
            fund_scope=fund_scope, stage=stage, year=year,
        )],
        candidates=candidates,
    )


def _hierarchy_fact(doc, facts, item, *, siblings=(), aliases=None):
    paths = hierarchy_paths({str(f.subject_code) for f in facts
                             if str(f.subject_code or '').isdigit()})
    source_text = _Text.from_pages(getattr(doc, 'page_texts', []) or [])
    section = '一般公共预算财政拨款支出决算情况说明'
    year = getattr(getattr(doc, 'fact_context', None), 'fiscal_year', None)

    def text_diag(code, *, extra_candidates=(), role=None):
        page_span = _text_span(source_text, item.start(), item.end())
        page, span = (page_span if page_span else (None, None))
        candidates = list(extra_candidates)
        if page and span:
            candidates.insert(0, text_candidate(
                section=section, page=page, span=span,
                value=item.get('xiang') or item.get('kuan') or item.get('klass'),
            ))
        return make_diagnosis(
            reason_codes=[code],
            missing_operands=[missing_text_operand(
                role=role or item.get('xiang') or item.get('kuan') or item.get('klass') or '科目',
                section=section, page=page,
                row_key={key: item.get(key) for key in ('klass', 'kuan', 'xiang') if item.get(key)},
                fund_scope='一般公共预算', stage='决算', year=year,
            )],
            candidates=candidates,
        )

    if len(set(paths.values())) != len(paths):
        return None, '功能分类编码的层级身份有冲突', text_diag('row_identity_duplicate')
    parent = ''
    klass = ''

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
                return None, error, text_diag('text_binding_failed')
        fact, error = select(doc, facts, 'total_actual', code=leaf_code)
        return fact, error, (text_diag('text_binding_failed') if error else None)

    for group, width in [('klass', 3), ('kuan', 5), ('xiang', 7)]:
        candidates = {str(f.subject_code) for f in facts
                      if len(paths.get(str(f.subject_code), '')) == width
                      and str(f.subject_code).isdigit()
                      and paths[str(f.subject_code)].startswith(parent)
                      and _name(f.subject_name) == _name(item[group])}
        if len(candidates) != 1:
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
                    return fact, error, (text_diag('text_binding_failed') if error else None)
            if group == 'xiang' and not candidates:
                rebound = rebind_leaf_under_class()
                if rebound is not None:
                    fact, error, diagnosis = rebound
                    return fact, error, diagnosis
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
                    table_candidate_row = fact_candidate(candidate)
                    return None, (
                        f"科目名称差异候选：正文第{source[0]['page'] if source else '?'}页写“{item[group]}”；"
                        f"同款表格第{'、'.join(map(str,pages)) or '?'}页编码{candidate.subject_code}写“{candidate.subject_name}”。"
                        "名称不一致，尚未绑定金额；请核对科目名称后确认，不能仅凭金额相同认定一致。"
                    ), text_diag(
                        'text_binding_failed',
                        extra_candidates=[table_candidate_row] if table_candidate_row else [],
                        role=item.get(group),
                    )
                return None, (
                    f'说明中的{level}“{item[group]}”在表内找不到同名科目'
                    f'（可能使用了旧科目名称或写错），无法与表格金额对应'
                ), text_diag('subject_name_unlocated', role=item.get(group))
            return None, (
                f'说明中的{level}“{item[group]}”在表内对应多个科目，无法唯一对应'
            ), text_diag(
                'text_binding_failed',
                extra_candidates=[
                    candidate for candidate in (
                        fact_candidate(fact) for fact in facts
                        if str(fact.subject_code) in candidates
                    ) if candidate
                ],
                role=item.get(group),
            )
        parent = paths[next(iter(candidates))]
        if group == 'klass':
            klass = parent
    code = next(code for code, path in paths.items() if path == parent)
    fact, error = select(doc, facts, 'total_actual', code=code)
    return fact, error, (text_diag('text_binding_failed') if error else None)


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
    issues, unresolved, diagnostics = [], [], []
    year = getattr(getattr(doc, 'fact_context', None), 'fiscal_year', None)
    totals = list(_GENERAL_AMOUNT.finditer(text.compact, begin, end))
    if not totals:
        diagnosis = _sentence_diagnosis(
            text, begin, end, section=title, role='一般公共预算支出合计决算金额',
            year=year,
        )
        _unresolved(rule, doc, 'general:text:total:missing',
                    '一般公共预算说明未识别到显式本年支出合计及金额单位', unresolved,
                    diagnosis=diagnosis, diagnostics=diagnostics)
    for claim in totals:
        key = f'general:text:total:{claim.start()}'
        fact, error, diagnosis = select_diagnostic(doc, facts, 'total_actual', code='total')
        error = error or _guard(doc, text, begin, claim.start(), claim.end())
        if error:
            _unresolved(rule, doc, key, error, unresolved, [fact] if fact else [],
                        diagnosis=diagnosis, diagnostics=diagnostics)
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
            fact, error, diagnosis = select_diagnostic(doc, facts, 'total_actual', code=code)
            error = error or _guard(doc, text, begin, claim.start(), claim.end())
            if error:
                _unresolved(rule, doc, key, error, unresolved, [fact] if fact else [],
                            diagnosis=diagnosis, diagnostics=diagnostics)
            else:
                issues.extend(_amount(rule, doc, text, claim.start(), claim.end(), claim['amount'],
                              claim['unit'], fact, key, label, title))
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
        fact, error, hierarchy_diagnosis = _hierarchy_fact(
            doc, facts, item, siblings=items, aliases=aliases
        )
        # 博物馆、图书馆等 are literal fiscal subjects, not publisher names.
        # Their hierarchy is already validated above; only the declaration
        # prefix can introduce a foreign owner or year for this item.
        error = error or _guard(doc, text, begin, item.start(), item.start())
        claim_diagnosis = None
        if len(claims) != 1:
            error = error or f'{item["xiang"]}条目显式决算金额缺失或不唯一，不能用预算金额替代'
            claim_diagnosis = _sentence_diagnosis(
                text, item.start(), stop,
                section=title,
                role=item["xiang"],
                code='text_statement_unparsed' if not claims else 'text_binding_failed',
                year=year,
            )
        diagnosis = merge_diagnoses([hierarchy_diagnosis, claim_diagnosis])
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
            _unresolved(rule, doc, key, error, unresolved, [fact] if fact else [],
                        diagnosis=diagnosis, diagnostics=diagnostics)
        else:
            claim = claims[0]
            issues.extend(_amount(rule, doc, text, item.start(), claim.end(), claim['amount'],
                          claim['unit'], fact, key, item['xiang'], title, identity_sources=aliases))
    return finish(rule, issues, unresolved, diagnostics)


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


def _amount_from_derived(rule, doc, text, begin, end, amount, unit, derived, key, label, section):
    """说明金额 × 推导总额（类级行合计）的比对；推导依据写进 evidence/formula。"""
    value, operands, note = derived
    declared = _wan(amount, unit)
    difference = declared - value
    failed = amount_difference_exceeds_policy(difference)
    refs = text.refs(begin, end, '原文决算金额声明')
    ids, evidence = receipt(rule, doc, key, list(operands), status='fail' if failed else 'pass',
            formula='explicit narrative amount == sum of class rows (total cell not printed)',
            disclosure_sources=[dict(ref, file_hash=doc.fact_context.file_hash) for ref in refs])
    if not failed:
        return []
    location = _location(doc, refs, list(operands), section, 'final_disclosure_amount',
                         table_code=operands[0].table_code, subject_code='total',
                         indicator='total_actual', declared_amount_wan=str(declared),
                         table_amount_wan=str(value), declared_unit=unit,
                         difference_wan=str(difference), tolerance_wan=str(AMOUNT_TOLERANCE_WAN),
                         derived_total_from='class_rows', derived_total_note=note)
    message = (f'{label}表文金额不一致：原文{amount}{unit}折合{declared}万元，'
               f'表内类级行合计{value:.2f}万元（{note}），'
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
            # 评级互斥、数量非负；已披露分项已覆盖总数即可验证闭合。
            # 少于总数且仍有未披露类别时保留补核，不能把缺项当零制造错误。
            if reason is None and computed < declared and not complete:
                reason = '同一自评范围的已披露评级数量之和小于总数，剩余类别未完整披露，不能补零'
            if reason:
                _unresolved(rule, doc, key, reason, unresolved,
                            formula='self_evaluation_count compared with sum(disclosed distinct rating counts)')
                continue
            refs = text.refs(total.start(), evidence_end, '同一范围自评项目、已披露评级及取消数量')
            if not local_years:
                refs += year_refs
            file_hash = getattr(getattr(doc, 'fact_context', None), 'file_hash', None)
            formula = ('self_evaluation_count = sum(disclosed distinct rating counts)'
                       if complete or declared == computed else
                       'self_evaluation_count >= sum(disclosed distinct rating counts)')
            if cancelled:
                formula += ' + explicitly included cancelled projects'
            receipt(rule, doc, key, [], status='fail' if declared != computed else 'pass',
                    formula=formula,
                    disclosure_sources=[dict(ref, file_hash=file_hash) for ref in refs])
            if declared == computed:
                continue
            location = _location(doc, refs, [], title, 'performance_self_evaluation_count',
                                 declared_count=declared, rating_counts=parts,
                                 computed_count=computed, difference_count=declared - computed,
                                 current_year=year, tolerance_count=0,
                                 rating_counts_complete=complete,
                                 comparison_basis='complete_sum' if complete else 'disclosed_lower_bound',
                                 cancelled_project_count=cancelled_count)
            breakdown = '+'.join(f'{label}{count}' for label, count in parts.items())
            if cancelled:
                breakdown += f'+年内取消{cancelled_count}'
            comparison = '评级分项之和不一致' if complete else '已披露评级分项之和超过总数'
            issues.append(rule._issue(
                f'绩效自评项目数量与{comparison}：自评{declared}个，'
                f'{breakdown}={computed}个，相差{abs(declared - computed)}个。', location, 'error',
                evidence_text='\n'.join(ref['span'] for ref in refs),
                check_relation_id=f'{rule.code}:{key}'))
    return finish(rule, issues, unresolved)
