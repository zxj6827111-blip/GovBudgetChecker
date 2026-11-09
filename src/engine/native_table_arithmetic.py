"""原生表格勾稽：显式操作数、当前版本和完整行身份共同约束计算。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from decimal import Decimal

from .amount_math import classify_amount_diff
from .fact_context import _canonical_budget_stage, operand_rejection_reason
from .native_three_public_ratios import operand_ref
from .rule_outcome import RuleDeferred
from .native_empty_operands import NativeBlankTerm, blank_term, blank_observations, blank_operand_refs, money_facts, verified_blank_column


def table_facts(doc, code):
    context = getattr(doc, 'fact_context', None)
    artifact = getattr(doc, 'parse_artifact', None)
    if context is None or artifact is None:
        reason = '缺少当前版本财政事实'
        _record_table_failure(doc, code, reason)
        return [], reason
    if (context.file_hash != artifact.source_file_hash
            or context.parse_run_uid != artifact.parse_run_uid):
        reason = '财政事实与当前解析产物版本不一致'
        _record_table_failure(doc, code, reason)
        return [], reason
    facts = [f for f in context.facts if f.table_code == code]
    if not facts:
        from .native_empty_operands import blank_table_terms
        observed = blank_table_terms(doc, code)
        if observed:
            return observed, None
        reason = f'{code}没有可用金额事实'
        _record_table_failure(doc, code, reason)
        return [], reason
    if len({f.instance_uid for f in facts}) != 1 or not facts[0].instance_uid:
        reason = f'{code}存在多个或未识别的表实例'
        _record_table_failure(doc, code, reason)
        return [], reason
    return facts, None


# ---------------------------------------------------------------------------
# DH-02：在产生失败的地方登记结构化卡点（文本不变）
# ---------------------------------------------------------------------------
#: 表级失败 → 冻结原因（instance_uid 由全部规则声明，阻断维度合法）。
_TABLE_FAILURE_CODES = {
    '缺少当前版本财政事实': 'table_missing',
    '财政事实与当前解析产物版本不一致': 'table_mapping_incomplete',
}
_TABLE_FAILURE_SUFFIX_CODES = (
    ('没有可用金额事实', 'table_missing'),
    ('存在多个或未识别的表实例', 'table_mapping_incomplete'),
)


def _record_table_failure(doc, code, reason):
    from .diagnostic_fields import blocker, record_gap

    if reason in _TABLE_FAILURE_CODES:
        reason_code = _TABLE_FAILURE_CODES[reason]
    else:
        reason_code = next((value for token, value in _TABLE_FAILURE_SUFFIX_CODES
                            if token in str(reason)), None)
    if reason_code is None:
        return
    record_gap(doc, reason, reason_codes=[reason_code],
               blocked_by=[blocker(dimension='instance_uid', ref=str(code), reason_code=reason_code)])


def _cell_locations(fact):
    """事实绑定的原页原格 (cell_id, page)，只取已确认的位置。"""
    locations = []
    for ref in (getattr(fact, 'extra_dims', None) or {}).get('evidence_refs', []) or []:
        cell_id = ref.get('source_cell_id')
        page = ref.get('physical_page')
        if cell_id and isinstance(page, int) and page > 0:
            locations.append((str(cell_id), page))
    return locations


def _fact_bbox(fact):
    """R2.2 §5：事实证据的原格 bbox；多证据取第一个带坐标的，取不到 None。"""
    for ref in (getattr(fact, 'extra_dims', None) or {}).get('evidence_refs', []) or []:
        bbox = ref.get('bbox')
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            return bbox
    return None


def _operand_year(doc):
    value = getattr(getattr(doc, 'fact_context', None), 'fiscal_year', None)
    return value if isinstance(value, int) and value >= 1900 else None


def _record_operand_gap(doc, facts, candidates, reason, *, indicator, code, name, stage):
    """缺数/多候选：位置来自事实自身的原格；取不到位置时不编造。"""
    from .diagnostic_fields import blocker, record_gap, table_candidate, table_missing_operand

    table_code = getattr(facts[0], 'table_code', None) if facts else None
    if not table_code:
        return
    year = _operand_year(doc)
    page = next((page for fact in facts for _, page in _cell_locations(fact)), None)
    if page is None:
        from .diagnostic_fields import table_page

        page = table_page(doc, table_code)
    if len(candidates) > 1:
        locations, seen = [], set()
        for fact in candidates:
            for cell_id, cell_page in _cell_locations(fact):
                key = (fact.table_code, cell_page, cell_id)
                if key in seen:
                    continue
                seen.add(key)
                locations.append(table_candidate(table_code=fact.table_code, page=cell_page,
                                                 cell_id=cell_id, value=str(fact.normalized_value)))
        if len(locations) >= 2:
            record_gap(
                doc, reason, reason_codes=['operand_ambiguous', 'cell_unbound'], candidates=locations,
                blocked_by=[blocker(dimension='cell',
                                    ref=f'{table_code}:{indicator}:{len(locations)}个候选原格',
                                    reason_code='cell_unbound')],
            )
            return
    record_gap(
        doc, reason, reason_codes=['operand_missing'],
        missing_operands=[table_missing_operand(
            table_code=table_code, role=f'{indicator}/{stage}操作数', page=page,
            row_key=code or name, column=indicator,
            fund_scope=getattr(facts[0], 'fund_scope', None), stage=stage, year=year, bbox=next((ref.get('bbox') for fact in facts for ref in (getattr(fact, 'extra_dims', None) or {}).get('evidence_refs', []) or [] if ref.get('bbox')), None))],
    )


def _record_operand_rejection(doc, fact, reason, *, indicator, stage):
    """事实存在但不可作操作数：按拒绝原因挂上游阻断，同时保留缺数位置。"""
    from .diagnostic_fields import blocker, record_gap, table_missing_operand

    table_code = getattr(fact, 'table_code', None)
    if not table_code:
        return
    locations = _cell_locations(fact)
    page = locations[0][1] if locations else None
    if page is None:
        from .diagnostic_fields import table_page

        page = table_page(doc, table_code)
    reason_code, dimension = None, None
    if '材料汇总口径待确认' in reason or '取数口径' in reason:
        reason_code, dimension = 'scope_unconfirmed', 'fund_scope'
    elif '主体不一致' in reason:
        reason_code, dimension = 'subject_conflict', 'organization'
    elif '未完成读取' in reason:
        reason_code, dimension = 'table_mapping_incomplete', 'instance_uid'
    elif '识别状态不可作操作数' in reason:
        reason_code, dimension = 'cell_low_confidence', 'cell'
    elif '身份未确认' in reason or '身份冲突' in reason:
        reason_code, dimension = 'row_level_unresolved', 'row'
    elif '万元归一金额' in reason:
        reason_code, dimension = 'scope_unconfirmed', 'fund_scope'
    codes = ['operand_missing'] + ([reason_code] if reason_code else [])
    blockers = ([blocker(dimension=dimension,
                         ref=f'{table_code}:{indicator}:{locations[0][0] if locations else " operand"}',
                         reason_code=reason_code)] if reason_code else [])
    if dimension == 'cell':
        blockers = [blocker(dimension='cell',
                            ref=locations[0][0] if locations else f'{table_code}:{indicator}:candidate',
                            reason_code=reason_code)]
    record_gap(
        doc, reason, reason_codes=codes, blocked_by=blockers,
        missing_operands=[table_missing_operand(
            table_code=table_code, role=f'{indicator}/{stage}操作数', page=page,
            row_key=getattr(fact, 'subject_code', None), column=indicator,
            fund_scope=getattr(fact, 'fund_scope', None), stage=stage, year=_operand_year(doc),
            bbox=_fact_bbox(fact))],
    )


#: 供同包其它规则模块复用的登记入口（文本不变，只登记结构化事实）。
record_operand_gap = _record_operand_gap
record_operand_rejection = _record_operand_rejection


def select(doc, facts, indicator, *, code=None, name=None, stage='决算', allow_native_blank=False):
    context = doc.fact_context
    normalized_name = re.sub(r'\s+', '', str(name)) if name is not None else None
    candidates = [f for f in facts if f.indicator == indicator
                  and (code is None or f.subject_code == code)
                  and (normalized_name is None or (normalized_name in re.sub(r'\s+', '', f.subject_name or '')
                       and not any(other in re.sub(r'\s+', '', f.subject_name or '')
                                   for other in ('人员经费合计', '公用经费合计') if other != normalized_name)))
                  and _canonical_budget_stage(f.budget_stage) == stage]
    if len(candidates) > 1:
        # 整行排版重复（同一科目编码、同一金额在原表印两遍）会为两个不同的
        # 原格各生成一条事实；它们是同一行的副本，不是两个候选值，按
        # (科目, 指标, 原值, 归一值, 单位) 收敛。绑定同一个原格的多条事实是
        # 事实层重复登记，仍判不唯一。
        cells = {
            tuple(sorted({
                ref.get("source_cell_id")
                for ref in (fact.extra_dims or {}).get("evidence_refs", [])
                if ref.get("source_cell_id")
            }))
            for fact in candidates
        }
        if len(cells) > 1:
            unique = {}
            for fact in candidates:
                unique.setdefault(
                    (fact.subject_code, fact.indicator, str(fact.raw_value),
                     str(fact.normalized_value), fact.unit), fact)
            candidates = list(unique.values())
    if len(candidates) != 1:
        if not candidates and allow_native_blank and facts:
            term = blank_term(doc, facts[0].table_code, indicator, code=code,
                              names=(name,) if name else (), stage=stage)
            if term is not None:
                return term, None
        reason = f'{code or name or indicator}的{indicator}/{stage}操作数缺失或不唯一'
        _record_operand_gap(doc, facts, candidates, reason, indicator=indicator,
                            code=code, name=name, stage=stage)
        return None, reason
    fact = candidates[0]
    if isinstance(fact, NativeBlankTerm):
        from .native_empty_operands import valid_blank_observation
        observation = fact.extra_dims.get('native_blank_observation')
        if observation and valid_blank_observation(doc.parse_artifact, context, observation, getattr(doc,'page_texts',None)):
            return fact, None
        return None, '空白观察项未通过当前原件复验'
    reason = operand_rejection_reason(fact, context.org_name)
    if (reason or context.fiscal_year is None or fact.fiscal_year != context.fiscal_year
            or fact.period not in (None, '本年')):
        reason = reason or '操作数年度或期间不一致'
        _record_operand_rejection(doc, fact, reason, indicator=indicator, stage=stage)
        return None, reason
    return fact, None


def journal(rule, doc, facts):
    from .rules_v33 import _v33_fact_identity
    facts = money_facts(facts)
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


def receipt(rule, doc, relation, facts, *, status, formula, reason=None, disclosure_sources=(),
            reason_codes=(), amount_subcheck_status=None, explained_by=None,
            residual_difference_wan=None, cascade_of=None, cell_observations=()):
    from .rules_v33 import _v33_relation_receipt
    if any(f.table_code == 'FIN_06_basic_expenditure' for f in facts):
        disclosure_sources = [*disclosure_sources, *getattr(doc, '_economic_zero_layout_observations', [])]
    ids, evidence = journal(rule, doc, facts)
    _v33_relation_receipt(doc, rule.code, relation, status=status,
                          operand_fact_ids=ids, formula=formula, incomplete_reason=reason,
                          cell_observations=[*blank_observations(facts), *cell_observations],
                          disclosure_sources=disclosure_sources,
                          reason_codes=reason_codes,
                          amount_subcheck_status=amount_subcheck_status,
                          explained_by=explained_by,
                          residual_difference_wan=residual_difference_wan,
                          cascade_of=cascade_of)
    return ids, evidence


def _normalized_page_span(expected, page_text):
    chars, positions = [], []
    for index, char in enumerate(str(page_text or "")):
        for normalized in unicodedata.normalize("NFKC", char):
            if normalized.isspace() or normalized in {",", "，"}:
                continue
            chars.append(normalized)
            positions.append(index)
    needle = "".join(
        normalized
        for char in str(expected or "")
        for normalized in unicodedata.normalize("NFKC", char)
        if not normalized.isspace() and normalized not in {",", "，"}
    )
    haystack = "".join(chars)
    if not needle or haystack.count(needle) != 1:
        return None
    start = haystack.index(needle)
    return str(page_text)[positions[start]:positions[start + len(needle) - 1] + 1]


def _literal_fact_refs(facts, doc, relation_label):
    """Create source-text row spans for facts when parsed cell IDs are unavailable."""
    refs = []
    for fact in facts:
        raw_refs = fact.extra_dims.get("evidence_refs", [])
        raw = next((item for item in raw_refs if isinstance(item, dict)
                    and item.get("quote") and item.get("physical_page")), None)
        if raw is None:
            return []
        page = raw.get("physical_page")
        if type(page) is not int or not 1 <= page <= len(doc.page_texts):
            return []
        code = str(fact.subject_code or "").strip()
        name = str(fact.subject_name or "").strip()
        amount = str(raw.get("quote") or "").strip()
        expected = f"{code}{name}{amount}" if code and code != "total" else f"{name}{amount}"
        span = _normalized_page_span(expected, doc.page_texts[page - 1])
        if span is None:
            return []
        refs.append({
            "table": fact.table_code,
            "table_code": fact.table_code,
            "page": page,
            "span": span,
            "role": f"{relation_label} · {name or code}",
            "subject_code": code,
            "value": str(fact.normalized_value),
            "value_unit": "万元",
        })
    return refs


def finish(rule, issues, unresolved):
    issues = cluster_source_errors(rule, issues)
    if unresolved:
        reasons = list(dict.fromkeys(unresolved))
        raise RuleDeferred(rule.code, detail='；'.join(reasons), partial_issues=issues,
                           unresolved_reasons=reasons)
    return issues


def cluster_source_errors(rule, issues):
    """同表同类多行问题合并，完整保留每行差额、关系与证据。"""
    import copy
    if rule.code not in {"V33-240", "V33-241", "V33-233", "V33-119"}:
        return issues
    groups, retained = {}, []
    for issue in issues:
        location = issue.location if isinstance(issue.location, dict) else {}
        # 整列错位的根因问题须独立成条（高危、可定位到列位），
        # 不能并入"多行勾稽不平"的症状聚簇，否则严重度与措辞都会被稀释。
        if str(issue.check_relation_id or '').endswith(':column_shift'):
            retained.append(issue)
            continue
        # 支出纵向关系还需与 V33-120 按同一原格操作数去重；提前把不同
        # 金额列揉成一条，会丢失单一算式身份，导致跨规则重复问题无法合并。
        if rule.code == "V33-241" and ":expenditure:vertical:" in str(issue.check_relation_id):
            retained.append(issue)
            continue
        if issue.severity not in {"error", "warn"} or not location.get("table_code"):
            retained.append(issue)
            continue
        relation_kind = "income_sources" if "income_sources" in str(issue.check_relation_id) else "table_arithmetic"
        groups.setdefault((location["table_code"], relation_kind), []).append(issue)
    for (table_code, _kind), group in groups.items():
        if len(group) == 1:
            retained.extend(group)
            continue
        combined = copy.copy(group[0])
        title = group[0].location.get("table") or {
            'FIN_02_income': '收入决算表', 'FIN_03_expenditure': '支出决算表',
            'FIN_05_general_public_expenditure': '一般公共预算财政拨款支出决算表',
        }.get(table_code, '当前表格')
        combined.message = f"{title}多行勾稽不平：\n" + "\n".join(issue.message for issue in group)
        combined.evidence_text = "\n".join(issue.evidence_text or issue.message for issue in group)
        combined.location = {**group[0].location,
            "check_relation_ids": [issue.check_relation_id for issue in group if issue.check_relation_id],
            "relations": [{"message": issue.message, "relation_id": issue.check_relation_id, "location": issue.location} for issue in group],
            "operand_refs": [ref for issue in group for ref in issue.location.get("operand_refs", [])],
            "native_blank_sources": [ref for issue in group for ref in issue.location.get("native_blank_sources", [])],
            "native_blank_operands": [ref for issue in group for ref in issue.location.get("native_blank_operands", [])]}
        combined.fact_ids = list(dict.fromkeys(fid for issue in group for fid in issue.fact_ids))
        combined.evidence_ids = list(dict.fromkeys(eid for issue in group for eid in issue.evidence_ids))
        combined.check_relation_id = f"{rule.code}:{table_code}:source_errors"
        retained.append(combined)
    return retained


def _document_scoped_zero(parent, indicator):
    """两张基金表都明确“本表无数据”时，总表该资金列的缺失按零。

    只用于 V33-119 的财政拨款总表公式：政府性基金/国有资本两张表都明确
    无数据时，总表对应列留空属于版式空白，不是未知数值。合成事实带
    ``document_scoped_zero`` 标记，便于审计区分"原文写了 0"与"按披露推定 0"。
    """
    fund_scope = {
        "government_fund_budget": "government_fund",
        "state_capital_budget": "state_owned",
    }[indicator]
    return replace(
        parent,
        fact_uid=f"document-scoped-zero:{parent.fact_uid}:{indicator}",
        indicator=indicator,
        raw_value="0",
        normalized_value=Decimal("0"),
        fund_scope=fund_scope,
        recognition_status="document_scoped_zero",
        extra_dims={
            **dict(parent.extra_dims or {}),
            "document_scoped_zero": "both_fund_tables_disclosed_empty",
            # 合成零没有原格证据；不要伪造 bbox/页码。
            "evidence_refs": [],
        },
    )


def check_fiscal_grant_arithmetic(rule, doc):
    """财政拨款总表按事实列身份取数，缺失分项绝不使用位置推断或补零。

    唯一例外：政府性基金、国有资本经营两张表都明确披露“本表无数据”时，
    总表对应资金列的空白按 0 参与公式（带 ``document_scoped_zero`` 标记）；
    只有一张表为空、或有候选事实但身份不一致时，仍保持 fail-closed。
    """
    code = "FIN_04_fiscal_grant_total"
    facts, error = table_facts(doc, code)
    if error:
        return finish(rule, [], [error])
    fund_columns_zero = both_fund_tables_disclosed_empty(doc)
    issues, unresolved, checked = [], [], 0
    for row in sorted({fact.row_order for fact in facts if fact.indicator == "expenditure_actual"}, key=str):
        row_facts = [fact for fact in facts if fact.row_order == row]
        parent, error = select(doc, row_facts, "expenditure_actual")
        if error:
            unresolved.append(error)
            continue
        children = []
        for indicator in ("general_public_budget", "government_fund_budget", "state_capital_budget"):
            term, missing = select(doc, row_facts, indicator, code=parent.subject_code,
                                   name=parent.subject_name, allow_native_blank=True)
            if (
                missing
                and fund_columns_zero
                and indicator in ("government_fund_budget", "state_capital_budget")
                and not any(fact.indicator == indicator for fact in row_facts)
            ):
                # 两张基金表都明确无数据，且该行该资金列没有任何候选事实。
                term = _document_scoped_zero(parent, indicator)
                missing = None
            if missing:
                error = missing
                unresolved.append(missing)
            elif term is not None:
                children.append(term)
        key = f"{code}:row:{row}"
        if error:
            receipt(rule, doc, key, [parent, *children], status="insufficient_data",
                    formula="expenditure = general + government_fund + state_capital", reason=error)
            continue
        checked += 1
        found, missing = relation(rule, doc, key, f"财政拨款收入支出决算总表第{row + 1}行", parent, children,
                                  fund_composition=True)
        issues.extend(found)
        unresolved.extend(missing)
    if not checked and not unresolved:
        unresolved.append("财政拨款总表没有可确认的同一行合计及三个资金分项")
    return finish(rule, issues, unresolved)


def _target_disclosed_empty(doc, code):
    """对应基金表是否由原文明确披露为无数据，而不是仅凭没有金额事实推断。"""
    artifact = getattr(doc, "parse_artifact", None)
    if artifact is None:
        return False
    from src.engine.fact_context import _fragment_for_instance
    from src.services.fiscal_empty_disclosure import disclosed_empty_fiscal_table

    instances = []
    for instance in getattr(artifact, "logical_tables", []) or []:
        if instance.table_code != code:
            continue
        fragment = _fragment_for_instance(artifact, instance)
        if fragment is not None and fragment.extraction_method != "page_text_fallback":
            instances.append((instance, fragment))
    if len(instances) != 1:
        return False
    instance, fragment = instances[0]
    return bool(disclosed_empty_fiscal_table(doc, instance, fragment))


def both_fund_tables_disclosed_empty(doc) -> bool:
    """政府性基金、国有资本经营两张表是否都明确披露“本表无数据”。

    V33-122 的放宽条件：只有两张基金表都明确无数据时，才把财政拨款总表
    对应资金列的空白按版式空白（0）处理；只空一张时仍保持 fail-closed，
    不把空白当零。
    """
    return (
        _target_disclosed_empty(doc, "FIN_08_gov_fund")
        and _target_disclosed_empty(doc, "FIN_09_state_capital")
    )


def check_fund_zero_disclosures(rule, doc):
    """只用总表已确认资金列判断对应表是否有数，禁止按倒数列位置或科目编码加总。"""
    from .fact_table_projection import fact_bound_table_rows, ProjectionUnavailable
    from .rule_policy import amount_difference_exceeds_policy
    source, error = table_facts(doc, 'FIN_04_fiscal_grant_total')
    if error:
        return finish(rule, [], [error])
    issues, unresolved = [], []
    for indicator, fund, code, title in (
        ('government_fund_budget', 'government_fund', 'FIN_08_gov_fund', '政府性基金预算财政拨款收入支出决算表'),
        ('state_capital_budget', 'state_owned', 'FIN_09_state_capital', '国有资本经营预算财政拨款收入支出决算表'),
    ):
        terms = [fact for fact in source if fact.indicator == indicator]
        key = f'{code}:zero_source'
        # 总表该资金列整列原件空白（网格认证 + 空白值约定 U-5）＝该列明确为 0。
        column_blanks = [] if terms else (verified_blank_column(doc, 'FIN_04_fiscal_grant_total', indicator) or [])
        if (not terms and not column_blanks) or any(operand_rejection_reason(f, doc.fact_context.org_name)
                            or f.fund_scope != fund or f.fiscal_year != doc.fact_context.fiscal_year for f in terms):
            unresolved.append(f'{title}对应的总表资金列缺少明确可用数值，不能按零解释')
            continue
        if any(f.normalized_value != 0 for f in terms):
            receipt(rule, doc, key, terms, status='not_applicable', formula='总表资金列非零，本项空表条件不适用')
            continue
        try:
            target_rows = fact_bound_table_rows(doc, title, strict_unknown_values=True)
        except ProjectionUnavailable as exc:
            unresolved.append(str(exc))
            continue
        target, error = table_facts(doc, code)
        if error:
            if (target_rows and "没有可用金额事实" in error
                    and _target_disclosed_empty(doc, code)):
                # 原文明确本表无数据，且总表资金列明确为零：空表成立。
                receipt(rule, doc, key, terms, status='pass',
                        formula='原文明确本表无数据，总表资金列明确为零', cell_observations=column_blanks)
                continue
            unresolved.append(error)
            continue
        from .native_empty_operands import valid_blank_observation
        if any((not valid_blank_observation(doc.parse_artifact, doc.fact_context,
                    f.extra_dims.get('native_blank_observation') or {}, getattr(doc, 'page_texts', None))
                if isinstance(f, NativeBlankTerm) else operand_rejection_reason(f, doc.fact_context.org_name))
               or f.fiscal_year != doc.fact_context.fiscal_year or f.fund_scope != fund for f in target):
            unresolved.append(f'{title}的原格金额/身份未确认，不能断言为空表')
            continue
        nonzero = [f for f in target if amount_difference_exceeds_policy(f.normalized_value)]
        ids, evidence = receipt(rule, doc, key, [*terms, *target], status='fail' if nonzero else 'pass',
                                formula='总表资金列明确为零时，对应表不得出现非零金额',
                                cell_observations=column_blanks)
        if nonzero:
            message = f'{title}存在非零金额，但财政拨款总表对应资金列均明确为零，请核对资金口径。'
            issues.append(rule._issue(message, {'table': title, 'table_code': code,
                'operand_refs': [operand_ref(f, 'source_zero' if f in terms else 'target') for f in [*terms, *nonzero]]},
                'error', evidence_text=message, fact_ids=ids, evidence_ids=evidence,
                check_relation_id=f'{rule.code}:{key}'))
    return finish(rule, issues, unresolved)


def cluster_rounding(rule, issues):
    """同表同差额的取整提示合并，同时保留每条勾稽关系的可读位置。"""
    result, groups = [], {}
    for issue in issues:
        if issue.severity != 'info':
            result.append(issue)
            continue
        diff = re.search(r'差额([\d.]+)万元', issue.message)
        if not diff:
            result.append(issue)
            continue
        location = issue.location if isinstance(issue.location, dict) else {}
        table_key = str(location.get('table_code') or location.get('table') or '')
        groups.setdefault((diff.group(1), table_key), []).append(issue)
    for (diff, _table_key), group in groups.items():
        if len(group) == 1:
            result.extend(group)
            continue
        refs = [ref for issue in group
                for ref in (issue.location.get('operand_refs') or [])
                if isinstance(ref, dict)]
        table_refs = []
        for issue in group:
            relation_id = str(issue.check_relation_id or '')
            for index, ref in enumerate(issue.location.get('table_refs') or []):
                if not isinstance(ref, dict):
                    continue
                table_refs.append({**ref,
                                   'relation_id': ref.get('relation_id') or relation_id,
                                   'operand_id': ref.get('operand_id') or f'{relation_id}:{index}'})
        pages = sorted({page for issue in group
                        for page in (issue.location.get('pages') or [])
                        if isinstance(page, int) and page > 0})
        cases = [_rounding_case(issue, index + 1, diff)
                 for index, issue in enumerate(group)]
        cases = [case for case in cases if case]
        table_code = str(group[0].location.get('table_code') or '').strip() or None
        title = group[0].location.get('table')
        if not title:
            from .fact_table_projection import TABLE_CODES
            title = next((name for name, code in TABLE_CODES.items()
                          if name.startswith(('收', '支', '财政', '一般公共', '政府', '国有'))
                          and code == _table_key), None)
        title = title or _table_key or '当前表格'
        message = f'{title}共{len(group)}处合计相差{diff}万元，可能为显示取整误差。差异位置：'
        if cases:
            message += '\n' + '\n'.join(case['summary'] for case in cases)
        location = {
            'table': title,
            'table_code': table_code,
            'page': pages[0] if pages else None,
            'pages': pages,
            'operand_refs': refs,
            'table_refs': table_refs,
            'relations': [issue.check_relation_id for issue in group],
            'rounding_cases': cases,
            'fact_context_version_token': group[0].location.get('fact_context_version_token'),
        }
        result.append(rule._issue(message, location,
            'info', evidence_text='\n'.join(issue.message for issue in group),
            fact_ids=list(dict.fromkeys(uid for issue in group for uid in issue.fact_ids)),
            evidence_ids=list(dict.fromkeys(uid for issue in group for uid in issue.evidence_ids))))
    return result


def _rounding_case(issue, index, diff):
    """把合并前的一条取整关系展开成表名、行项、金额和来源页。"""
    location = issue.location if isinstance(issue.location, dict) else {}
    refs = location.get('operand_refs') or location.get('table_refs') or []
    refs = [ref for ref in refs if isinstance(ref, dict)]
    if not refs:
        return None

    parent = next((ref for ref in refs if ref.get('role') == 'parent'), refs[0])
    children = [ref for ref in refs if ref is not parent]
    table = str(location.get('table') or location.get('table_code') or '')
    row_label = (str(parent.get('subject_name') or '').strip()
                 or str(parent.get('subject_code') or '').strip()
                 or str(parent.get('row') or '').strip()
                 or '合计行')
    code = str(location.get('code') or parent.get('subject_code') or '').strip()
    if code and code != 'total' and code not in row_label:
        row_label = f'{code} {row_label}'

    def _amount(ref):
        value = ref.get('value', ref.get('current_value'))
        try:
            return Decimal(str(value))
        except Exception:
            return None

    parent_amount = _amount(parent)
    child_amounts = [_amount(ref) for ref in children]
    child_names = [str(ref.get('subject_name') or ref.get('subject_code') or ref.get('row') or '未识别科目').strip()
                   for ref in children]
    source_pages = sorted({source.get('physical_page') or source.get('page')
                           for ref in refs
                           for source in (ref.get('evidence_refs') or [])
                           if isinstance(source, dict)
                           and isinstance(source.get('physical_page') or source.get('page'), int)
                           and (source.get('physical_page') or source.get('page')) > 0})
    if not source_pages:
        source_pages = sorted({page for page in (location.get('pages') or [])
                               if isinstance(page, int) and page > 0})
    page_text = f'第{"、".join(str(page) for page in source_pages)}页' if source_pages else '页码未识别到'

    child_detail = '、'.join(
        f'{name}={amount:.2f}万元' if amount is not None else name
        for name, amount in zip(child_names, child_amounts, strict=False)
    )
    if parent_amount is not None and children and all(value is not None for value in child_amounts):
        children_total = sum(child_amounts, Decimal('0'))
        detail = (f'{row_label}合计{parent_amount:.2f}万元；明细{child_detail}，'
                  f'明细合计{children_total:.2f}万元')
    else:
        detail = f'{row_label}与明细（{child_detail or "未识别到"}）勾稽'
    raw_column = str(location.get('column') or parent.get('indicator') or '').strip()
    column = {
        'total_actual': '决算金额',
        'basic_actual': '基本支出决算',
        'project_actual': '项目支出决算',
        'income_actual': '收入决算金额',
        'expenditure_actual': '支出决算金额',
    }.get(raw_column, raw_column)
    column_text = f'，字段{column}' if column else ''
    summary = (f'{index}. {table} · {page_text} · {detail}{column_text}，'
               f'差额{diff}万元（可能为显示取整误差）')
    relation_id = next((str(ref.get('relation_id')) for ref in refs if ref.get('relation_id')),
                       str(issue.check_relation_id or f'rounding-{index}'))
    return {
        'relation_id': relation_id,
        'summary': summary,
        'table': table,
        'pages': source_pages,
        'code': code or None,
        'column': column or None,
        'operand_ids': [str(ref.get('operand_id')) for ref in refs if ref.get('operand_id')],
    }


def relation(rule, doc, key, label, parent, children, *, hints=True, fund_composition=False):
    facts = [parent] + children
    expected_funds = {'expenditure_actual': 'fiscal_appropriation',
                      'general_public_budget': 'general_public_budget',
                      'government_fund_budget': 'government_fund', 'state_capital_budget': 'state_owned'}
    # 财政拨款合计与三个资金分项本来就属于不同资金口径；仅此明确公式允许按已识别列身份组成。
    funds_match = (len(facts) == 4 and {f.indicator for f in facts} == set(expected_funds)
                   and all(f.table_code == 'FIN_04_fiscal_grant_total'
                           and f.fund_scope == expected_funds[f.indicator] for f in facts)) if fund_composition else (
                       all(f.fund_scope == parent.fund_scope for f in children))
    if not funds_match or any((f.instance_uid, f.org_scope, f.budget_stage, f.fiscal_year)
           != (parent.instance_uid, parent.org_scope, parent.budget_stage, parent.fiscal_year) for f in children):
        reason = f'{label}操作数的表实例或口径不一致'
        receipt(rule, doc, key, facts, status='insufficient_data', formula='total = sum(parts)', reason=reason)
        return [], [reason]
    total = sum((f.normalized_value for f in children), Decimal('0'))
    # U-5 的认证空白零值同样适用于合计格；只比较完整关系，不把分项反填到原格。
    if all(isinstance(f, NativeBlankTerm) for f in facts):
        receipt(rule, doc, key, facts, status='not_applicable', formula='原件完整网格各项均为空白，无发生额关系')
        return [], []
    level, diff = classify_amount_diff(parent.normalized_value, total, n_children=len(children))
    ids, evidence = receipt(rule, doc, key, facts,
                            status='fail' if level == 'mismatch' else 'pass',
                            formula='total = sum(parts) (万元)')
    if level != 'mismatch' and not (hints and level == 'rounding_hint'):
        return [], []
    refs = []
    for i, fact in enumerate(facts):
        if isinstance(fact, NativeBlankTerm):
            continue
        ref = operand_ref(fact, 'parent' if i == 0 else 'child')
        ref.update(operand_id=f'{key}:{i}', relation_id=key,
                   evidence_refs=list(fact.extra_dims.get('evidence_refs', [])))
        refs.append(ref)
    pages = sorted({e.get('physical_page') for f in facts for e in f.extra_dims.get('evidence_refs', [])
                    if e.get('physical_page')})
    severity = 'error' if level == 'mismatch' else 'info'
    message = (f'{label}：表内合计{parent.normalized_value:.2f}万元，分项之和{total:.2f}万元，'
               f'差额{diff:.2f}万元。' + ('可能为取整误差。' if severity == 'info' else '超过金额容差。'))
    issue = rule._issue(message, {'table_code': parent.table_code,
            'page': pages[0] if pages else None, 'pages': pages, 'operand_refs': refs,
            'native_blank_sources': blank_observations(facts),
            'native_blank_operands': blank_operand_refs(facts, key),
            'fact_context_version_token': doc.fact_context.version_token}, severity,
            evidence_text=message, check_relation_id=f'{rule.code}:{key}',
            fact_ids=ids, evidence_ids=evidence)
    has_cell_sources = all(any(isinstance(ref, dict) and ref.get('source_cell_id') and ref.get('bbox')
                              for ref in fact.extra_dims.get('evidence_refs', [])) for fact in facts)
    if rule.code == 'V33-117' and label == '公用经费合计' and not has_cell_sources:
        # A complete current cell contract survives text extraction artifacts
        # in neighboring name cells. Literal rows are only a fallback when
        # original amount-cell identities are unavailable.
        source_refs = _literal_fact_refs(facts, doc, label)
        if len(source_refs) == len(facts):
            issue.location.pop('operand_refs', None)
            issue.location['table_refs'] = source_refs
            issue.location['page'] = source_refs[0]['page']
            issue.evidence_text = '\n'.join(ref['span'] for ref in source_refs)
            issue.fact_ids = []
            issue.evidence_ids = []
    return [issue], []


def _native_grid_coverage_error(artifact, instance, table):
    """原物理片段不能因逻辑拼表漏行而从计算分母消失。"""
    from .native_empty_operands import cell_id

    source_uids = set(instance.fragment_uids) | set(table.source_fragment_uids) | {table.fragment_uid}
    physical = [fragment for fragment in artifact.physical_fragments if fragment.fragment_uid in source_uids]
    represented = {cell_id(table, cell) for cell in table.cells}
    if not physical or any(cell.bbox and not cell.is_header and cell_id(fragment, cell) not in represented
                           for fragment in physical for cell in fragment.cells):
        return f'{table.title}原件存在未进入完整逻辑表的行或单元格'
    return None


def _economic_zero_layout_tail(doc, table, cells, row_idx, mapping):
    """跨页首行只剩名称尾字且所有金额格认证为空白：数值贡献确定为0。

    不给尾字猜编码、不改写原名称，也不物化零事实。只在连续原页、同车道、
    上页末行有编码、本页首行无其他内容且逐格认证时排除其数值阻断。
    """
    from .cell_value_policy import interpret_cell_value
    from .native_empty_operands import cell_id

    labels = mapping.get('source_label_cols') or []
    if row_idx < 1 or len(labels) != 2:
        return None
    code_col, name_col = map(int, labels)
    amount_col = int(mapping['source_col_idx'])
    current = [cells.get((row_idx, col)) for col in range(len(table.rows[row_idx]))]
    name, code, amount = cells.get((row_idx, name_col)), cells.get((row_idx, code_col)), cells.get((row_idx, amount_col))
    previous_code, previous_name = cells.get((row_idx-1, code_col)), cells.get((row_idx-1, name_col))
    previous_amount = cells.get((row_idx-1, amount_col))
    if (any(c is None for c in [name, code, amount, previous_code, previous_name, previous_amount])
            or not str(name.raw_text or '').strip() or code.raw_text.strip() or not amount.bbox
            or name.ocr_provenance or name.extraction_method not in {'lines','lines_strict'}
            or not previous_amount.native_blank_verified
            or not re.fullmatch(r'3\d{2}(?:\d{2})?', previous_code.raw_text.strip())
            or not previous_name.raw_text.strip() or previous_amount.raw_text.strip()
            or name.page_number != previous_name.page_number + 1
            or name.origin_row_idx != 0 or not name.bbox or not previous_name.bbox
            or any(abs(name.bbox[index]-previous_name.bbox[index]) > .5 for index in (0,2))):
        return None
    context, artifact = doc.fact_context, doc.parse_artifact
    if (context is None or artifact is None or context.report_kind != 'final' or context.file_hash != artifact.source_file_hash
            or context.parse_run_uid != artifact.parse_run_uid):
        return None
    for cell in current:
        if cell is name:
            continue
        if (cell is None or cell.raw_text.strip() or cell.numeric_value is not None
                or not cell.native_blank_verified or cell.is_header
                or cell.extraction_method not in {'lines','lines_strict'}):
            return None
    sid = cell_id(table, amount)
    physical = [c for fragment in artifact.physical_fragments for c in fragment.cells
                if cell_id(fragment,c) == sid]
    if len(physical) != 1 or not physical[0].native_blank_verified or physical[0].raw_text.strip():
        return None
    value, _presence, convention = interpret_cell_value('', native_blank_verified=True,
        region=context.org_name, year=context.fiscal_year, table_code=table.table_code,
        row=row_idx, column=amount_col)
    if value != 0 or not convention:
        return None
    return dict(kind='zero_layout_tail', table_code=table.table_code, source_cell_id=sid,
                physical_page=amount.page_number, bbox=list(amount.bbox), raw_text='',
                name_tail=name.raw_text, value='0', convention=convention,
                source_file_hash=context.file_hash, parse_run_uid=context.parse_run_uid)


def _economic_table_view(doc, facts):
    """完整网格决定明细集合，事实只提供当前金额；左右车道独立绑定标签。"""
    from .fact_context import _column_mappings_for_instance, _fragment_for_instance, _stage_for_fact
    from src.services.fiscal_fact_materializer import FiscalFactMaterializer
    from src.services.fiscal_row_roles import is_index_header

    artifact = doc.parse_artifact
    pairs = [(instance, _fragment_for_instance(artifact, instance))
             for instance in getattr(artifact, 'logical_tables', [])
             if instance.table_code == 'FIN_06_basic_expenditure']
    pairs = [(instance, table) for instance, table in pairs if table is not None
             and table.extraction_method in {'lines', 'lines_strict'}]
    if len(pairs) != 1:
        return None, '经济分类表未找到唯一完整原件网格，不能按已物化事实子集归集'
    instance, table = pairs[0]
    if instance.identity_conflict or len({len(row) for row in table.rows}) != 1:
        return None, '经济分类表原件网格或续表身份不完整'
    mappings = [m for m in _column_mappings_for_instance(instance, table)
                if m.get('canonical_measure') == 'total_actual'
                and _canonical_budget_stage(_stage_for_fact(
                    'total_actual', m.get('source_col_name', ''), 'final', '决算')) == '决算']
    if (not mappings or any(not m.get('source_label_cols') for m in mappings)
            or len({m['source_col_idx'] for m in mappings}) != len(mappings)):
        return None, '经济分类表决算金额列及所属标签车道未唯一绑定'
    cells = {(cell.row_idx, cell.col_idx): cell for cell in table.cells}
    if len(cells) != len(table.cells):
        return None, '经济分类表原格身份重复'
    # 不让整个未解析行从明细分母消失：原物理片段中每个真实网格格都须进入逻辑表。
    coverage_error = _native_grid_coverage_error(artifact, instance, table)
    if coverage_error:
        return None, coverage_error
    from src.services.fiscal_column_identity import code_dimension_columns
    code_columns = code_dimension_columns(table)
    materializer, rows = FiscalFactMaterializer(None), {}
    for row_idx, row in enumerate(table.rows):
        if row_idx < table.header_row_count or is_index_header(row):
            continue
        from .native_grid_roles import merged_note_row
        if merged_note_row(table, row_idx):
            continue
        for mapping in mappings:
            col = int(mapping['source_col_idx'])
            label_cols = [int(value) for value in mapping['source_label_cols']]
            if any(c >= len(row) for c in [col, *label_cols]):
                return None, f'经济分类表第{row_idx + 1}行原格缺列'
            labels = [{'raw_text': row[c], 'col_idx': c} for c in label_cols]
            amount = cells.get((row_idx, col))
            has_label = any(str(item['raw_text'] or '').strip() for item in labels)
            if not has_label and (amount is None or not str(amount.raw_text or '').strip()):
                continue  # 另一车道有数据时，本车道可能只有排版填充格。
            classification = materializer._extract_classification(labels, code_columns)
            code = classification.get('classification_code') if classification else None
            name = re.sub(r'\s+', '', str(classification.get('classification_name') or '')) if classification else ''
            if (code != 'total' and not re.fullmatch(r'3\d{2}(?:\d{2})?', str(code or ''))
                    or classification and classification.get('classification_conflict')
                    or code == 'total' and name not in {'人员经费合计', '公用经费合计'}):
                zero_tail = _economic_zero_layout_tail(doc, table, cells, row_idx, mapping)
                if zero_tail is not None:
                    observed = getattr(doc, '_economic_zero_layout_observations', [])
                    if zero_tail not in observed:
                        observed.append(zero_tail)
                    doc._economic_zero_layout_observations = observed
                    continue
                return None, f'经济分类表第{row_idx + 1}行科目编码无法唯一识别，明细归集未完成'
            from .native_grid_roles import labels_covered_by_native_cells
            if amount is None or not labels_covered_by_native_cells(table, row_idx, label_cols):
                return None, f'经济分类表第{row_idx + 1}行原始标签或金额单元格缺失'
            key = name if code == 'total' else code
            if key in rows:
                return None, f'经济分类表科目{key}存在重复原行身份'
            rows[key] = (row_idx, code, mapping)
    unknown = [f for f in facts if f.subject_code != 'total'
               and not re.fullmatch(r'3\d{2}(?:\d{2})?', str(f.subject_code or ''))]
    if unknown:
        return None, '经济分类金额行的科目编码无法唯一识别，明细归集未完成'
    if not rows:
        return None, '经济分类表未取得原件科目行集合'
    if any(len(key) == 5 and key[:3] not in rows for key in rows if key.isdigit()):
        return None, '经济分类表项级科目的原始类级行缺失，明细归集未完成'
    return (instance, table, mappings, cells, rows, facts), None


def _economic_operand(doc, view, key):
    from .native_hierarchy_checks import _select

    instance, table, _mappings, cells, rows, facts = view
    if key not in rows:
        return None, f'经济分类表科目{key}的完整原始行缺失'
    row_idx, code, mapping = rows[key]
    return _select(doc, (instance, table, [mapping], cells, {code: row_idx}, facts), code, 'total_actual')


def check_basic_economics(rule, doc):
    facts, reason = table_facts(doc, 'FIN_06_basic_expenditure')
    if reason:
        return finish(rule, [], [reason])
    view, reason = _economic_table_view(doc, facts)
    if reason:
        from .diagnostic_fields import blocker, record_gap
        record_gap(doc, reason, reason_codes=['row_level_unresolved'],
                   blocked_by=[blocker(dimension='row', ref='FIN_06_basic_expenditure:原格行集合',
                                       reason_code='row_level_unresolved')])
        receipt(rule, doc, 'economic:identity', facts, status='insufficient_data',
                formula='class = sum(complete source rows)', reason=reason)
        return finish(rule, [], [reason])
    rows = view[4]
    issues, unresolved = [], []
    for name, codes in ([('人员经费合计', ('301', '303')), ('公用经费合计', ('302', '310'))]
                        if rule.code == 'V33-117' else []):
        parent, error = _economic_operand(doc, view, name)
        children = []
        for code in codes:
            child, cr = _economic_operand(doc, view, code)
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
    if rule.code == 'V33-117':
        return finish(rule, cluster_rounding(rule, issues), unresolved)
    for code in sorted(key for key in rows if re.fullmatch(r'3\d{2}', key)):
        parent, error = _economic_operand(doc, view, code)
        children = []
        child_codes = sorted(key for key in rows if key.startswith(code) and len(key) == 5)
        for child_code in child_codes:
            child, cr = _economic_operand(doc, view, child_code)
            error = error or cr
            if child is not None:
                children.append(child)
        if not child_codes and not error:
            continue
        if error:
            unresolved.append(error)
            receipt(rule, doc, f'economic:class:{code}', [f for f in [parent, *children] if f is not None],
                    status='insufficient_data', formula='class = sum(children)', reason=error)
            continue
        found, missing = relation(rule, doc, f'economic:class:{code}', f'经济分类{code}明细归集',
                                  parent, children, hints=True)
        issues.extend(found)
        unresolved.extend(missing)
    return finish(rule, cluster_rounding(rule, issues), unresolved)


def check_detail_row_formulas(rule, doc):
    """原生收入/支出表逐原行水平勾稽；空白只能经原页原格认证参加加法。"""
    from .fact_context import _column_mappings_for_instance
    from .native_hierarchy_checks import DETAIL_TABLES, _select, _table_view

    definitions = {
        'FIN_02_income': ('total_actual', 'fiscal_allocation', 'superior_subsidy_income',
                          'business_income', 'operational_income', 'subsidiary_income', 'other_income'),
        'FIN_03_expenditure': ('total_actual', 'basic_actual', 'project_actual',
                               'to_superior_actual', 'operational_actual', 'subsidiary_actual'),
        'FIN_05_general_public_expenditure': ('total_actual', 'basic_actual', 'project_actual'),
    }
    present = {item.table_code for item in getattr(doc.parse_artifact, 'logical_tables', [])}
    issues, unresolved = [], []
    for table_code, indicators in definitions.items():
        if table_code not in present:
            continue
        view, error = _table_view(doc, table_code, include_income_sources=True)
        if error:
            unresolved.append(error)
            receipt(rule, doc, f'{table_code}:horizontal:identity', [], status='insufficient_data',
                    formula='total = sum(complete parts)', reason=error)
            continue
        instance, table, _mappings, cells, rows, facts = view
        coverage_error = _native_grid_coverage_error(doc.parse_artifact, instance, table)
        if coverage_error:
            unresolved.append(coverage_error)
            receipt(rule, doc, f'{table_code}:horizontal:coverage', [], status='insufficient_data',
                    formula='total = sum(complete parts)', reason=coverage_error)
            continue
        mappings = [m for m in _column_mappings_for_instance(instance, table)
                    if m.get('canonical_measure') in indicators]
        if any(sum(m.get('canonical_measure') == indicator for m in mappings) != 1 for indicator in indicators):
            error = f'{DETAIL_TABLES[table_code]}水平合计所需金额列未唯一绑定'
            unresolved.append(error)
            receipt(rule, doc, f'{table_code}:horizontal:columns', [], status='insufficient_data',
                    formula='total = sum(complete parts)', reason=error)
            continue
        view = (instance, table, mappings, cells, rows, facts)
        for code in rows:
            operands, errors = [], []
            for indicator in indicators:
                term, error = _select(doc, view, code, indicator)
                if term is not None:
                    operands.append(term)
                if error:
                    errors.append(error)
            key = f'{table_code}:horizontal:{code}'
            if errors:
                unresolved.extend(errors)
                receipt(rule, doc, key, operands, status='insufficient_data',
                        formula='total = sum(complete parts)', reason='；'.join(errors))
                continue
            found, missing = relation(rule, doc, key, f'{DETAIL_TABLES[table_code]}科目{code}横向合计',
                                      operands[0], operands[1:])
            for issue in found:
                issue.location.update(table=DETAIL_TABLES[table_code], code=code)
            issues.extend(found)
            unresolved.extend(missing)
    if not present.intersection(definitions):
        unresolved.append('未找到可检查的原生收入、支出或一般公共预算决算表')
    return finish(rule, cluster_rounding(rule, issues), unresolved)


def check_three_public_arithmetic(rule, doc):
    from .native_no_activity import require_no_activity_disposition
    require_no_activity_disposition(rule, doc)
    facts, reason = table_facts(doc, 'FIN_07_three_public')
    if reason:
        return finish(rule, [], [reason])
    issues, unresolved = [], []
    for stage in ('年初预算', '决算'):
        for parent_key, parts in [('total_actual', ('overseas', 'vehicle_purchase_operation', 'reception')),
                                  ('vehicle_purchase_operation', ('vehicle_purchase', 'vehicle_operation'))]:
            operands, errors = [], []
            for key in (parent_key,) + parts:
                fact, error = select(doc, facts, key, code='total', stage=stage, allow_native_blank=True)
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
        reason = '支出金额行的科目编码无法唯一识别，层级归集未完成'
        unresolved.append(reason)
        from .diagnostic_fields import blocker as _blocker, record_gap as _record_gap
        _record_gap(doc, reason, reason_codes=['row_level_unresolved'],
                    blocked_by=[_blocker(dimension='row',
                                         ref='FIN_03_expenditure:非3位/5位编码金额行',
                                         reason_code='row_level_unresolved')])
    codes = sorted({f.subject_code for f in facts if f.subject_code == 'total'
                    or re.fullmatch(r'\d{3}(?:\d{2}){0,2}', str(f.subject_code or ''))})
    if 'total' not in codes:
        reason = '支出决算表未找到唯一合计行'
        unresolved.append(reason)
        from .diagnostic_fields import blocker as _blocker, record_gap as _record_gap
        _record_gap(doc, reason, reason_codes=['row_code_missing'],
                    blocked_by=[_blocker(dimension='row', ref='FIN_03_expenditure:合计行',
                                         reason_code='row_code_missing')])
    indicators = ('total_actual', 'basic_actual', 'project_actual', 'to_superior_actual',
                  'operational_actual', 'subsidiary_actual')
    from .native_column_shift import (
        EXPENSE_LABELS, column_shift_hint, column_shift_issue, detect_column_shift,
    )
    # 明细行整列错位（源文件漏列/错列）的根因判定：列合计证据一旦成立，
    # 输出一条高危根因问题，并在补核原因里给出材料视角提示。
    valid_codes = {code for code in codes if code == 'total'
                   or re.fullmatch(r'\d{3}(?:\d{2}){0,2}', str(code))}
    shift = detect_column_shift(
        [(indicator, EXPENSE_LABELS[indicator]) for indicator in indicators],
        [fact for fact in facts if fact.subject_code in valid_codes],
        org_name=doc.fact_context.org_name,
    )
    for code in codes:
        operands, errors = [], []
        for indicator in indicators:
            fact, error = select(doc, facts, indicator, code=code, allow_native_blank=True)
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
                parent, _ = select(doc, facts, indicator, code=code, allow_native_blank=True)
                children = [select(doc, facts, indicator, code=c, allow_native_blank=True)[0] for c in child_codes]
                found, missing = relation(rule, doc, f'expenditure:vertical:{indicator}:{code}',
                    f'支出决算表{code}/{indicator}层级合计', parent, children)
                issues.extend(found)
                unresolved.extend(missing)
    if shift:
        issues.append(column_shift_issue(rule, doc, '支出决算表', 'FIN_03_expenditure', shift))
        if unresolved:
            unresolved.append(column_shift_hint('支出决算表', rule.code))
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
