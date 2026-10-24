"""三公经费数量要素披露（2026-10-22 口径决议④；清单决算 A-08、预算 A-09）。

金额驱动：三公表（FIN_07）对应项金额大于 0 时，三公说明须披露数量——
公务用车购置费→购置数量；因公出国（境）费→团组数、人数；公务接待费→
批次、人数，缺则 medium。金额为 0、或说明写明未发生/未安排，视为已披露；
表内金额取不到或不唯一时不要求数量（宁漏报不误报）。车辆保有量是存量
指标，始终要求，全文（含国有资产占有使用情况）任一处披露即可。

与 V33-246 的分工：V33-246 处理“已写批次/人次但只写了外宾”的不完整
披露；本模块只在说明里完全没有对应数量时报出，两者不重叠。
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, List, Optional, Sequence, Tuple

from .fact_context import _canonical_budget_stage, operand_rejection_reason

THREE_PUBLIC_TABLE = "FIN_07_three_public"

_NUM = r"(?:\d+|[一二三四五六七八九十两]+)"

#: (三公表指标, 标签, 说明内主体词干, ((数量字段, 字段名, 披露形态), ...))
_COUNT_SPECS: Tuple[Tuple[str, str, str, Tuple[Tuple[str, str, "re.Pattern[str]"], ...]], ...] = (
    (
        "vehicle_purchase",
        "公务用车购置费",
        r"公务用车购置(?!及)|购置公务用车|购置车辆",
        (
            (
                "purchase_count",
                "公务用车购置数量",
                re.compile(
                    rf"(?:购置|购买|新购|更新)了?(?:公务用车|公务车辆|公务车|车辆)?{_NUM}辆"
                    rf"|购置(?:数量?|车辆数)(?:为|共|计)?{_NUM}辆?"
                    rf"|{_NUM}辆(?:公务用车|公务车辆|车辆)?的?(?:购置|更新)"
                ),
            ),
        ),
    ),
    (
        "overseas",
        "因公出国（境）费",
        r"因公出国|出访",
        (
            (
                "overseas_groups",
                "因公出国（境）团组数",
                re.compile(rf"{_NUM}个?(?:出访|出国(?:[（(]境[）)])?)?团组|团组(?:数)?(?:为|共|计)?{_NUM}个?"),
            ),
            (
                "overseas_people",
                "因公出国（境）人数",
                re.compile(rf"(?:团组|出访|出国)[^。；;]{{0,30}}?{_NUM}人(?:次)?|{_NUM}人(?:次)?[^。；;]{{0,8}}(?:出访|出国)"),
            ),
        ),
    ),
    (
        "reception",
        "公务接待费",
        r"公务接待|接待",
        (
            ("reception_batches", "公务接待批次", re.compile(rf"{_NUM}个?批次?|批次(?:为|共|计)?{_NUM}")),
            (
                "reception_people",
                "公务接待人数",
                re.compile(rf"批次?[^。；;]{{0,12}}?{_NUM}人(?:次)?|接待[^。；;]{{0,30}}?{_NUM}人(?:次)?|人次(?:为|共|计)?{_NUM}"),
            ),
        ),
    ),
)

_NOT_OCCURRED_RE = re.compile(
    r"未(?:发生|安排|组织|开展|新增|购置|新购|更新)(?!变化|改变|变动)|无(?:此项|相关)?(?:支出|安排)"
)
_VEHICLE_STOCK_RE = re.compile(
    r"保有量?|(?:共有|实有|现有|拥有)(?:公务用车|公务车辆|公车|车辆)|车辆(?:总数|总量)"
)


def _stage_matches(fact: Any, report_kind: str) -> bool:
    stage = _canonical_budget_stage(getattr(fact, "budget_stage", None))
    return stage == "决算" if report_kind == "final" else stage in ("年初预算", "全年预算")


def item_amounts(doc: Any, report_kind: str) -> dict:
    """三公表各项金额（万元）：每项须唯一命中当前材料、当前阶段的合计行事实。"""
    context = getattr(doc, "fact_context", None)
    if context is None:
        return {}
    org_name = getattr(context, "org_name", None)
    facts = [f for f in getattr(context, "facts", []) or [] if f.table_code == THREE_PUBLIC_TABLE]
    if len({f.instance_uid for f in facts}) != 1:
        return {}
    amounts = {}
    for indicator, *_rest in _COUNT_SPECS:
        candidates = [
            f for f in facts
            if f.indicator == indicator and f.subject_code == "total"
            and f.normalized_value is not None and _stage_matches(f, report_kind)
        ]
        if len(candidates) == 1 and not operand_rejection_reason(candidates[0], org_name):
            amounts[indicator] = Decimal(str(candidates[0].normalized_value))
    return amounts


def _item_paragraphs(paras: Sequence[str], stem: str) -> List[str]:
    """说明内属于该项的段落：段首编号切换主体，段内出现词干也算。"""
    picked: List[str] = []
    current = False
    for para in paras:
        head = re.match(r"\s*[（(]?[一二三四五六七八九十\d]+[）)、.．]\s*(\S{0,12})", para)
        if head:
            current = bool(re.search(stem, head.group(1) or ""))
        if current or re.search(stem, para):
            picked.append(para)
    return picked


def count_issues(
    rule: Any,
    doc: Any,
    *,
    report_kind: str,
    scope: str,
    section_title: Optional[str],
    locate_page,
    items: Optional[Sequence[str]] = None,
) -> List[Any]:
    """返回数量要素缺失的问题列表（每个缺失字段一条，medium）。

    ``items`` 限定参与的三公项（预算侧按清单 A-09 只要求购置数量）。
    """
    from src.utils.narration import merge_page_texts, merge_soft_wrapped_lines

    paras = merge_soft_wrapped_lines(scope)
    amounts = item_amounts(doc, report_kind)
    stage_label = "决算数" if report_kind == "final" else "预算数"
    issues: List[Any] = []
    for indicator, label, stem, fields in _COUNT_SPECS:
        if items is not None and indicator not in items:
            continue
        amount = amounts.get(indicator)
        if amount is None or amount <= 0:
            continue
        item_paras = _item_paragraphs(paras, stem)
        compact = re.sub(r"\s+", "", "".join(item_paras))
        if _NOT_OCCURRED_RE.search(compact):
            continue
        evidence = item_paras[0] if item_paras else (paras[0] if paras else scope[:200])
        for field_key, field_label, pattern in fields:
            if pattern.search(compact):
                continue
            issues.append(rule._issue(
                f"三公经费数量要素未披露：{label}{stage_label}{amount:.2f}万元，"
                f"但三公经费说明未披露{field_label}。请补充{field_label}"
                "（金额为 0 或未发生的无需披露数量）。",
                {
                    "page": locate_page(doc, evidence[:40]),
                    "three_public_item": label,
                    "count_field": field_key,
                    "item_amount_wan": str(amount),
                    "section": (section_title or "").strip()[:60] or None,
                    "expected_disclosure": f"披露{field_label}",
                },
                severity="medium",
                evidence_text=evidence[:200],
            ))

    whole = re.sub(r"\s+", "", merge_page_texts(list(getattr(doc, "page_texts", None) or [])))
    if not _VEHICLE_STOCK_RE.search(whole):
        evidence = next((p for p in paras if "公务用车" in p), paras[0] if paras else scope[:200])
        issues.append(rule._issue(
            "三公经费数量要素未披露：全文未披露公务用车（车辆）保有量。"
            "请在三公经费说明或国有资产占有使用情况中写明车辆保有量（无车辆的写明 0 辆）。",
            {
                "page": locate_page(doc, evidence[:40]),
                "three_public_item": "公务用车",
                "count_field": "vehicle_stock",
                "section": (section_title or "").strip()[:60] or None,
                "expected_disclosure": "披露车辆保有量",
            },
            severity="medium",
            evidence_text=evidence[:200],
        ))
    return issues
