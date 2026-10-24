"""三公说明的百分比与当前版本财政事实绑定。

分项身份取自声明前最近的业务名称，金额取自同主体、年度、实例及阶段的
FIN_07 事实。缺操作数明确返回未完成，不能把跳过检查记成通过。
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from typing import Any

from .fact_context import (
    _canonical_budget_stage, operand_rejection_reason, is_total_role_fact,
)

TABLE_CODE = "FIN_07_three_public"
_SUBJECT = re.compile(
    r"(?P<vehicle_purchase_operation>公务用车购置及运行(?:维护)?费?)"
    r"|(?P<vehicle_purchase>公务用车购置费?)"
    r"|(?P<vehicle_operation>公务用车运行(?:维护)?费?)"
    r"|(?P<overseas>因公出国(?:[（(]境[）)])?费?)"
    r"|(?P<reception>公务接待费?)"
    r"|(?P<total_actual>[\"“”「」]?三公[\"“”「」]?经费)"
)
LABELS = {
    "vehicle_purchase_operation": "公务用车购置及运行维护费",
    "vehicle_purchase": "公务用车购置费", "vehicle_operation": "公务用车运行维护费",
    "overseas": "因公出国（境）费", "reception": "公务接待费", "total_actual": "三公经费合计",
}


def subject_before(text: str) -> tuple[str | None, int]:
    # Map normalized characters back to the original excerpt for exact quotes.
    positions = [i for i, ch in enumerate(text) if not ch.isspace()]
    compact = "".join(text[i] for i in positions)
    matches = list(_SUBJECT.finditer(compact))
    if not matches:
        return None, 0
    match = matches[-1]
    return match.lastgroup, positions[match.start()]


def select_fact(context: Any, indicator: str, stage: str, year: int) -> tuple[Any, str | None]:
    if context is None:
        return None, "缺少当前版本财政事实"
    candidates = [f for f in context.facts
                  if f.table_code == TABLE_CODE and f.indicator == indicator
                  and f.fiscal_year == year and _canonical_budget_stage(f.budget_stage) == stage
                  and is_total_role_fact(f)]
    if not candidates:
        return None, f"未找到同年度、同口径的{LABELS[indicator]}{stage}事实"
    reasons = [operand_rejection_reason(f, context.org_name) for f in candidates]
    if any(reasons):
        return None, next(reason for reason in reasons if reason)
    instances = {f.instance_uid for f in candidates}
    identities = {f.fact_uid for f in candidates}
    if len(instances) != 1 or len(identities) != 1:
        return None, "存在多个表实例或操作数身份，无法唯一绑定"
    if not candidates[0].instance_uid or candidates[0].period not in (None, "本年"):
        return None, "缺少表实例身份或事实期间不是本年"
    return candidates[0], None


def _rounding_range(numerator: Any, denominator: Any):
    """按两数原件末位的一半推算比例可能范围；单位或精度不明时返回 None。"""
    from src.services.fact_column_arithmetic import _half_display_unit

    top_half, bottom_half = _half_display_unit(numerator), _half_display_unit(denominator)
    if top_half is None or bottom_half is None or denominator.normalized_value <= bottom_half:
        return None
    top, bottom = numerator.normalized_value, denominator.normalized_value
    return (max(top - top_half, Decimal(0)) / (bottom + bottom_half) * Decimal("100"),
            (top + top_half) / (bottom - bottom_half) * Decimal("100"))


def operand_ref(fact: Any, role: str) -> dict[str, Any]:
    return {"fact_uid": fact.fact_uid, "table_code": fact.table_code,
            "indicator": fact.indicator, "subject_code": fact.subject_code,
            "subject_name": fact.subject_name, "budget_stage": fact.budget_stage,
            "instance_uid": fact.instance_uid, "value": str(fact.normalized_value),
            "value_unit": "万元", "role": role, "operand_id": role}


def evaluate(rule: Any, doc: Any, claim: dict[str, Any], *, indicator: str,
             quote: str, denominator_stage: str, year: int, share: bool = False):
    """返回 (issues, unresolved)，每条声明均留下关系级执行回执。"""
    from .rules_v33 import _v33_relation_receipt, _v33_fact_identity
    context = getattr(doc, "fact_context", None)
    relation = f"three_public:{'share' if share else 'completion'}:{indicator}:{claim['start']}"
    numerator, nr = select_fact(context, indicator, "决算", year)
    denominator, dr = select_fact(context, "total_actual" if share else indicator,
                                  denominator_stage, year)
    from .native_budget_phase import phase_reason
    dr = dr or phase_reason(doc, denominator, denominator_stage, indicator)
    if share and nr and not dr and claim['pct'] == 0 and denominator.normalized_value > 0:
        # 分母已有当前金额；分项原格留空还必须有同句“决算0/无支出”证据。
        # 只登记无活动占比的适用性，不把未印出的分项金额物化为零事实。
        from .native_empty_operands import blank_term
        from .native_narrative_checks import _Text
        from .native_table_text import context_guard
        from .native_table_arithmetic import receipt
        compact = re.sub(r'\s+', '', unicodedata.normalize('NFKC', quote))
        literal_zero = bool(re.search(r'(?:支出)?决算(?:数)?(?:为|是)?0(?:\.0+)?(?:万元|元|亿元)', compact))
        literal_subject, _ = subject_before(compact[:compact.rfind('占')])
        term = blank_term(doc, TABLE_CODE, indicator, code='total', stage='决算')
        text = _Text.from_pages(getattr(doc, 'page_texts', []) or [])
        at = text.compact.find(compact)
        if (literal_zero and literal_subject == indicator and term is not None
                and at >= 0 and text.compact.count(compact) == 1
                and not context_guard(doc, text.compact, at, at + len(compact))
                and term.instance_uid == denominator.instance_uid):
            sources = [{**ref, 'file_hash': context.file_hash}
                       for ref in text.refs(at, at + len(compact), '分项明确无支出及占比0声明')]
            receipt(rule, doc, relation, [term, denominator], status='not_applicable',
                    formula='explicit no-activity numerator + positive disclosed denominator + declared zero share',
                    reason='分项原文明确无支出且原件格留空，总额有正数，占比0与无活动披露一致',
                    disclosure_sources=sources)
            return [], []
    denominator_zero = denominator is not None and denominator.normalized_value == 0
    # A declared finite percentage with a confirmed zero denominator is
    # undefined even when the numerator is not disclosed; never invent zero.
    reason = dr or (nr if not denominator_zero else None)
    if (not reason and numerator is not None and numerator.instance_uid != denominator.instance_uid):
        reason = "分子分母不属于同一张三公表实例"
    if not reason and numerator is not None and any(getattr(numerator, key) != getattr(denominator, key)
                          for key in ("org_scope", "fund_scope")):
        reason = "分子分母合并范围或资金口径不一致"
    artifact = getattr(doc, "parse_artifact", None)
    if not reason and (artifact.source_file_hash != context.file_hash
                       or artifact.parse_run_uid != context.parse_run_uid):
        reason = "财政事实与当前解析产物版本不一致"
    fact_ids, evidence_ids = _v33_fact_identity(numerator, denominator)
    if fact_ids:
        journal = getattr(doc, "structured_consumption", None)
        if journal is None:
            journal = doc.structured_consumption = []
        journal.append({"rule_id": rule.code, "table_code": TABLE_CODE,
                        "instance_uid": getattr(numerator or denominator, "instance_uid", None),
                        "fact_uids": fact_ids, "fact_version": context.version_token,
                        "normalized_unit": "万元"})
    formula = "decision / decision_total * 100" if share else "decision / budget * 100"
    if reason:
        reason = f"{LABELS[indicator]}声明「{claim['phrase']}」：{reason}"
        _v33_relation_receipt(doc, rule.code, relation, status="insufficient_data",
                              operand_fact_ids=fact_ids, formula=formula, incomplete_reason=reason)
        return [], [reason]
    top, bottom = (numerator.normalized_value if numerator is not None else None), denominator.normalized_value
    zero = bottom == 0
    computed = None if zero else top / bottom * Decimal("100")
    differs = zero or abs(computed - claim["pct"]) > Decimal("0.1")
    # 说明常按未取整金额计算（0.824 万元印成 0.82）：说明值落在两数显示取整范围内时
    # 不判不成立，按 AGENTS 口径标「可能为取整误差」；容差本身不放宽。
    envelope = None if zero or not differs or numerator is None else _rounding_range(numerator, denominator)
    rounding = envelope is not None and envelope[0] <= claim["pct"] <= envelope[1]
    _v33_relation_receipt(doc, rule.code, relation, status="fail" if differs and not rounding else "pass",
                          operand_fact_ids=fact_ids, formula=formula)
    if not differs:
        return [], []
    label = LABELS[indicator]
    relation_label = "占三公经费决算合计的比例" if share else f"{denominator_stage}完成率"
    recomputed = "无定义" if zero else f"{computed:.2f}%"
    if rounding:
        message = (f"{label}{relation_label}：说明{claim['pct']}%，表内{top:.2f}÷{bottom:.2f}×100={recomputed}，"
                   f"差异在两数显示取整范围内（{envelope[0]:.2f}%–{envelope[1]:.2f}%），可能为取整误差。")
    else:
        message = (f"{label}{relation_label}不成立：说明{claim['pct']}%，" +
                   ("表内分母为0，有限完成率没有数学定义。" if zero else
                    f"表内{top:.2f}÷{bottom:.2f}×100={recomputed}。"))
    refs = ([operand_ref(numerator, "分子")] if numerator is not None else []) + [operand_ref(denominator, "分母")]
    pages = {claim["page"]}
    for fact in (numerator, denominator):
        if fact is None:
            continue
        pages.update(ref.get("physical_page") for ref in fact.extra_dims.get("evidence_refs", [])
                     if ref.get("physical_page"))
    location = {"page": claim["page"], "pages": sorted(pages), "table_code": TABLE_CODE,
                "section": claim.get("section"), "indicator": indicator,
                "operand_refs": refs, "table_refs": claim.get("source_refs") or [{"page": claim["page"], "span": quote,
                    "role": "占比声明" if share else "完成率声明"}],
                "fact_context_version_token": context.version_token,
                "declared_percent": str(claim["pct"]),
                "recomputed_percent": None if zero else str(computed),
                "normalized_denominator_wan": str(bottom), "denominator_type": denominator_stage,
                "actual_amount": str(top) if top is not None else None}
    return [rule._issue(message, location, "info" if rounding else "error", evidence_text=quote,
                        check_relation_id=f"{rule.code}:{relation}",
                        fact_ids=fact_ids, evidence_ids=evidence_ids)], []


def completion_claim(rule: Any, doc: Any, claim: dict[str, Any], merged: str, year: int):
    """非三公声明返回 None，保留原规则的条目内绑定逻辑。"""
    if getattr(doc, "parse_artifact", None) is None or "三公" not in claim.get("section", ""):
        return None
    from .native_narrative_checks import _Text
    text = _Text.from_pages(doc.page_texts)
    begin, end = text.compact_range(claim["section_start"], claim["start"])
    indicator, start = subject_before(text.compact[begin:end])
    if not indicator:
        return [], ["三公完成率声明缺少可唯一识别的业务分项"]
    stage = claim.get("role_key") or "年初预算"
    stage = _canonical_budget_stage(stage)
    _, claim_end = text.compact_range(claim["start"], claim["end"])
    source_refs = text.refs(begin + start, claim_end, "完成率声明")
    quote = "\n".join(ref["span"] for ref in source_refs)
    claim = {**claim, "source_refs": source_refs}
    return evaluate(rule, doc, claim, indicator=indicator, quote=quote,
                    denominator_stage=stage, year=year)


def check_shares(rule: Any, doc: Any, scope: str, year: int | None):
    from .rules_v33 import (_txt_fund_merged_pages, _txt_fund_page_for,
                           _nar_repeat_foreign_year, _nar_repeat_sections)
    if not scope or year is None:
        return [], []
    merged, offsets = _txt_fund_merged_pages(doc)
    sections = [section for section in _nar_repeat_sections(merged, doc, offsets)
                if "三公" in section[0]]
    if sections:
        _, begin, end = sections[-1]
        scope = merged[begin:end]
    start = merged.find(scope)
    if start < 0:
        positions = [i for i, ch in enumerate(merged) if not ch.isspace()]
        compact = "".join(merged[i] for i in positions)
        key = re.sub(r"\s+", "", scope)
        at = compact.find(key)
        if at < 0 or not key:
            return [], (["三公占比说明无法定位到原始文本"] if re.search(r"占[^%％]{0,90}[%％]", scope) else [])
        start = positions[at]
        scope = merged[start:positions[at + len(key) - 1] + 1]
    issues, unresolved = [], []
    pattern = re.compile(r"占(?:[^。；;%％\n]|\n){0,90}?(?P<pct>\d+(?:\.\d+)?)\s*[%％]")
    for match in pattern.finditer(scope):
        # Explicit denominators (e.g. 一般公共预算支出) are not 三公总额。
        phrase = match.group(0)
        compact = re.sub(r"\s+", "", phrase)
        if not re.fullmatch(r"占(?:[\"“”]?三公[\"“”]?经费(?:财政拨款)?(?:支出)?(?:决算)?(?:总额|合计)?(?:的)?)?\d+(?:\.\d+)?[%％]", compact):
            continue
        pos = start + match.start()
        if _nar_repeat_foreign_year(merged, pos, phrase, year):
            continue
        indicator, subject_start = subject_before(scope[:match.start()])
        if not indicator or indicator == "total_actual":
            continue
        claim = {"start": pos, "phrase": phrase, "pct": Decimal(match.group("pct")),
                 "page": _txt_fund_page_for(offsets, pos), "section": "三公经费支出决算情况说明"}
        found, missing = evaluate(rule, doc, claim, indicator=indicator,
            quote=scope[subject_start:match.end()], denominator_stage="决算", year=year, share=True)
        issues.extend(found)
        unresolved.extend(missing)
    return issues, unresolved
