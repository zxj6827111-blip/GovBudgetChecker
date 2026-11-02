"""明细行整列错位（源文件漏列/错列）的根因判定。

背景：2026-10-31 华东政法大学 2025 年度决算实测——《收入决算表》《支出决算表》
明细行金额整体左错一列：明细行缺"本年收入/支出合计"数值，其余金额各左移一列，
最右列在明细行全空。逐行"合计≠分项之和"在误报控制纪律下只能作为取数不足逐行
挂起，但列合计证据已足以确诊整列错位。本模块输出一条根因级问题，避免同一根因
散成几十条逐行条目。

判定签名（三条同时成立才认定，宁缺勿错）：

1. 常规对齐失败：至少一列的"明细之和"与合计行同列金额超出容差；
2. 平移对齐成立：所有有明细数据的列 k，其明细之和恰等于合计行第 k±1 列金额；
3. 逐行横向失败：不少于 ``min_rows`` 行明细中，≥``row_ratio`` 的行"合计≠已知分项之和"。

此外要求：平移对齐中至少 ``min_nonzero_pairs`` 对含非零金额（排除全零表的平凡对齐）；
错位方向边缘列（左错位为最右列、右错位为最左列）明细行无任何金额，锚点列有金额。
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .amount_math import classify_amount_diff
from .fact_context import operand_rejection_reason

INCOME_LABELS = {
    "total_actual": "本年收入合计",
    "fiscal_allocation": "财政拨款收入",
    "superior_subsidy_income": "上级补助收入",
    "business_income": "事业收入",
    "operational_income": "经营收入",
    "subsidiary_income": "附属单位上缴收入",
    "other_income": "其他收入",
}

EXPENSE_LABELS = {
    "total_actual": "本年支出合计",
    "basic_actual": "基本支出",
    "project_actual": "项目支出",
    "to_superior_actual": "上缴上级支出",
    "operational_actual": "经营支出",
    "subsidiary_actual": "对附属单位补助支出",
}

#: 视为"对齐"的差异等级；mismatch 之外的容差等级都接受（取整/政策容差内）。
_ALIGNED_LEVELS = frozenset({"ok", "rounding_hint", "within_policy_tolerance"})

_DIRECTION_TEXT = {"left": "左", "right": "右"}


def _column_total(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal("0"))


def _collect(columns, facts, org_name):
    """按列收集合计行金额与明细金额；身份不唯一时返回 None（不做推断）。

    科目编码按 3/5/7 位层级组织：列合计只累计根科目（类级）一次，
    逐行核对只取叶子科目，避免类/款/项重复累计。
    """
    roles = [role for role, _ in columns]
    totals: Dict[str, Decimal] = {}
    detail_values: Dict[str, Dict[str, Decimal]] = defaultdict(dict)
    fact_ids: List[str] = []
    seen = set()
    for fact in facts:
        role = fact.indicator
        if role not in roles:
            continue
        code = str(fact.subject_code or "").strip()
        value = fact.normalized_value
        if not code or value is None:
            continue
        if operand_rejection_reason(fact, org_name) is not None:
            continue
        key = (code, role)
        if key in seen:
            # 同一格存在多个候选事实：列身份不唯一，不能据此推断整列错位。
            return None
        seen.add(key)
        fact_ids.append(str(fact.fact_uid))
        if code == "total":
            totals[role] = value
        else:
            detail_values[role][code] = value
    codes = set()
    for values in detail_values.values():
        codes.update(values)
    roots = {code for code in codes
             if not any(other != code and code.startswith(other) for other in codes)}
    leaves = {code for code in codes
              if not any(other != code and other.startswith(code) for other in codes)}
    root_values = {role: [values[code] for code in values if code in roots]
                   for role, values in detail_values.items()}
    leaf_rows = {code: {role: values[code] for role, values in detail_values.items()
                        if code in values}
                 for code in leaves}
    present = {role: bool(values) for role, values in detail_values.items()}
    return totals, root_values, leaf_rows, present, fact_ids


def detect_column_shift(
    columns: Sequence[Tuple[str, str]],
    facts: Iterable[Any],
    *,
    org_name: Optional[str] = None,
    min_columns: int = 3,
    min_aligned: int = 2,
    min_nonzero_pairs: int = 2,
    min_rows: int = 3,
    row_ratio: float = 0.6,
) -> Optional[Dict[str, Any]]:
    """检测"明细行金额整体错位一列"的根因签名。

    ``columns`` 为按物理列顺序排列的 ``(role, label)``；``facts`` 为当前表实例的
    财政事实。命中返回判定字典，否则返回 None。
    """
    if len(columns) < min_columns or len({role for role, _ in columns}) != len(columns):
        return None
    collected = _collect(columns, facts, org_name)
    if collected is None:
        return None
    totals, root_values, leaf_rows, present, fact_ids = collected

    # 条件三：逐行"合计 ≠ 已知分项之和"须是系统性现象，而非个别行错误。
    checked = mismatched = 0
    for values in leaf_rows.values():
        total = values.get("total_actual")
        parts = [value for role, value in values.items() if role != "total_actual"]
        if total is None or not parts:
            continue
        checked += 1
        if classify_amount_diff(total, _column_total(parts), len(parts))[0] == "mismatch":
            mismatched += 1
    if checked < min_rows or mismatched / checked < row_ratio:
        return None

    # 条件一：常规对齐至少一列失败，否则本表本来就是对得上的。
    mismatched_columns = []
    for role, label in columns:
        if not root_values.get(role) or totals.get(role) is None:
            continue
        detail_sum = _column_total(root_values[role])
        level, diff = classify_amount_diff(totals[role], detail_sum, len(root_values[role]))
        if level == "mismatch":
            mismatched_columns.append({
                "role": role, "label": label, "total": totals[role],
                "detail_sum": detail_sum, "diff": diff,
            })
    if not mismatched_columns:
        return None

    # 条件二：平移对齐。左错位时明细列 k 对齐合计行第 k+1 列，右错位反之。
    for direction, offset in (("left", 1), ("right", -1)):
        pairs: List[Dict[str, Any]] = []
        nonzero = 0
        failed = False
        for index, (role, label) in enumerate(columns):
            target = index + offset
            if not (0 <= target < len(columns)) or not root_values.get(role):
                continue
            other_role, other_label = columns[target]
            if totals.get(other_role) is None:
                continue
            detail_sum = _column_total(root_values[role])
            level, _ = classify_amount_diff(totals[other_role], detail_sum, len(root_values[role]))
            if level not in _ALIGNED_LEVELS:
                failed = True
                break
            if detail_sum != 0 or totals[other_role] != 0:
                nonzero += 1
            pairs.append({
                "detail_role": role, "detail_label": label, "detail_sum": detail_sum,
                "total_role": other_role, "total_label": other_label,
                "total": totals[other_role],
            })
        if failed or len(pairs) < min_aligned or nonzero < min_nonzero_pairs:
            continue
        # 方向边缘列：错位后被"挤出"表外的列，明细行不得有任何金额；
        # 锚点列（被漏印的合计列一侧）必须有明细金额。
        edge, anchor = (columns[-1], columns[0]) if direction == "left" else (columns[0], columns[-1])
        if present.get(edge[0]) or not present.get(anchor[0]):
            continue
        return {
            "direction": direction,
            "pairs": pairs,
            "mismatched_columns": mismatched_columns,
            "rows_checked": checked,
            "rows_mismatched": mismatched,
            "fact_ids": fact_ids,
            "missing_label": anchor[1],
            "blank_label": edge[1],
        }
    return None


def column_shift_issue(rule, doc, title: str, table_code: str, detection: Dict[str, Any]):
    """把判定结果转成一条根因级问题（severity=error → 高危）。"""
    shown = detection["pairs"][:4]
    evidence = "；".join(
        f'明细「{pair["detail_label"]}」列之和 {pair["detail_sum"]:.2f} 万元 = '
        f'合计行「{pair["total_label"]}」列 {pair["total"]:.2f} 万元'
        for pair in shown
    )
    extra = "" if len(detection["pairs"]) <= len(shown) else (
        f'；另 {len(detection["pairs"]) - len(shown)} 列同样逐列对齐')
    direction = _DIRECTION_TEXT[detection["direction"]]
    message = (
        f'《{title}》明细行金额整体{direction}错一列：{evidence}{extra}；'
        f'{detection["rows_checked"]} 行明细中 {detection["rows_mismatched"]} 行'
        f'“合计 ≠ 已知分项之和”；明细行「{detection["blank_label"]}」列全部空白。'
        f'疑似源文件漏印「{detection["missing_label"]}」列或整列错位，'
        f'请回源文件核对列位后重新导出。'
    )
    pages = _fact_pages(doc, detection.get("fact_ids") or [])
    location = {
        "table": title,
        "table_code": table_code,
        "page": pages[0] if pages else None,
        "pages": pages,
        "column_shift": {
            "direction": detection["direction"],
            "missing_label": detection["missing_label"],
            "blank_label": detection["blank_label"],
            "pairs": detection["pairs"],
            "mismatched_columns": detection["mismatched_columns"],
            "rows_checked": detection["rows_checked"],
            "rows_mismatched": detection["rows_mismatched"],
        },
    }
    return rule._issue(message, location, "error", evidence_text=message,
                       check_relation_id=f'{rule.code}:{table_code}:column_shift',
                       fact_ids=detection.get("fact_ids"))


def column_shift_hint(title: str, rule_code: str) -> str:
    """补核项原因里的材料视角提示（取数不足不等于系统读不出，可能是原件错列）。"""
    return (
        f'原材料可能存在整列错位：{title}明细行金额整体错一列'
        f'（各列明细之和逐列等于合计行相邻列），请先核对原件列位；'
        f'取数不足可能由源文件漏列/错列造成（根因判定见 {rule_code}）'
    )


def _fact_pages(doc, fact_ids: Sequence[str]) -> List[int]:
    """从当前事实上下文取根因问题涉及的物理页码。"""
    context = getattr(doc, "fact_context", None)
    if context is None or not fact_ids:
        return []
    wanted = set(fact_ids)
    pages = set()
    for fact in getattr(context, "facts", []) or []:
        if getattr(fact, "fact_uid", None) not in wanted:
            continue
        for ref in (getattr(fact, "extra_dims", {}) or {}).get("evidence_refs", []) or []:
            page = ref.get("physical_page") if isinstance(ref, dict) else None
            if isinstance(page, int) and page > 0:
                pages.add(page)
    return sorted(pages)
