"""列身份的算术自检：同一张表多行的合计与分项系统性对不上，说明列映射有误。

原件个别行的勾稽差错交给规则报出；整列映射错位（如把"项"编码列当成金额列、
叶子列错配）会让大多数行同时失败，这时不能把它当成原件错误报给用户，
相关列的事实降为待复核，入库回执标记"映射不完整"。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from decimal import Decimal

from src.engine.amount_math import normalize_amount_to_wan, rounding_envelope

_BASIC_PROJECT = ("basic_actual", "project_actual")

#: 表 -> [(合计指标, 分项指标)]：同一行、同一预算阶段内必然成立的恒等式。
ROW_IDENTITIES = {
    # 单位版收支总表的支出侧分列；部门版只有一列，没有分项，不参与核对。
    "FIN_01_income_expenditure_total": (("expenditure_actual", ("expenditure_basic", "expenditure_personnel",
                                                                "expenditure_public", "expenditure_project")),),
    "FIN_02_income": (("total_actual", ("fiscal_allocation", "superior_subsidy_income", "business_income",
                                        "operational_income", "other_income", "subsidiary_income")),),
    "FIN_03_expenditure": (("total_actual", (*_BASIC_PROJECT, "to_superior_actual", "operational_actual",
                                             "subsidiary_actual")),),
    "FIN_04_fiscal_grant_total": (("expenditure_actual", ("general_public_budget", "government_fund_budget",
                                                          "state_capital_budget")),),
    "FIN_05_general_public_expenditure": (("total_actual", _BASIC_PROJECT),),
    "FIN_06_basic_expenditure": (("total_actual", ("personnel_actual", "public_actual")),),
    "FIN_07_three_public": (("total_actual", ("overseas", "vehicle_purchase_operation", "reception")),
                            ("vehicle_purchase_operation", ("vehicle_purchase", "vehicle_operation"))),
    "FIN_08_gov_fund": (("total_actual", _BASIC_PROJECT),),
    "FIN_09_state_capital": (("total_actual", _BASIC_PROJECT),),
}
#: 判为列映射问题而不是原件差错，需要：至少 3 行失败、失败行占可核对行一半以上、差额各不相同。
#: 原件一处数据差错会沿 项→款→类→合计 逐级带上去，但每级差额相同；列错位则每行差额都不一样。
MIN_FAILED_ROWS = 3
MIN_FAILED_SHARE = Decimal("0.5")


def _row_outcome(group, total, parts):
    totals = [f for f in group if f.indicator == total]
    present = [f for f in group if f.indicator in parts]
    if len(totals) != 1 or not present:
        return None
    if len({f.indicator for f in present}) != len(present):
        return None  # 同一行同一分项出现多次，列身份本身已有歧义，不在这里判。
    values = [f.normalized_value for f in totals + present]
    if any(value is None for value in values) or not any(values):
        return None
    diff = abs(totals[0].normalized_value - sum((f.normalized_value for f in present), Decimal("0")))
    halves = [_half_display_unit(f) for f in totals + present]
    # 判映射用原件显示精度的舍入包络，不用 0.5 万元的审校容差：元为单位的表上整列错位也只差几千元。
    envelope = sum(halves, Decimal("0")) if None not in halves else rounding_envelope(len(present))
    return diff > envelope, diff


def _half_display_unit(fact):
    """原件末位的一半，按事实自身单位折成万元；单位不明时返回 None。"""
    text = str(fact.raw_value or "").replace(",", "").strip()
    decimals = len(text.split(".", 1)[1]) if "." in text else 0
    factor = normalize_amount_to_wan(1, fact.unit)
    return None if factor is None else Decimal(5) * Decimal(10) ** -(decimals + 1) * factor


def column_arithmetic_conflicts(facts):
    """返回 {instance_uid: [冲突说明]}；每条说明列出恒等式、核对行数和失败样例。"""
    rows = defaultdict(list)
    for fact in facts:
        if fact.instance_uid and fact.row_order is not None and fact.table_code in ROW_IDENTITIES:
            rows[(fact.instance_uid, fact.table_code)].append(fact)
    conflicts = {}
    for (instance_uid, code), instance_facts in rows.items():
        groups = defaultdict(list)
        for fact in instance_facts:
            groups[(fact.row_order, fact.budget_stage, fact.period)].append(fact)
        for total, parts in ROW_IDENTITIES[code]:
            outcomes = [(key, outcome) for key, group in sorted(groups.items(), key=lambda item: str(item[0]))
                        if (outcome := _row_outcome(group, total, parts)) is not None]
            failed = [(key, diff) for key, (bad, diff) in outcomes if bad]
            if (len(failed) >= MIN_FAILED_SHARE * len(outcomes)
                    and len({diff for _, diff in failed}) >= MIN_FAILED_ROWS):
                conflicts.setdefault(instance_uid, []).append({
                    "identity": f"{total} = " + " + ".join(parts),
                    "indicators": [total, *parts],
                    "checked_rows": len(outcomes), "failed_rows": len(failed),
                    "examples": [{"row_order": key[0], "budget_stage": key[1], "difference_wan": str(diff)}
                                 for key, diff in failed[:3]],
                })
    return conflicts


def constrain_column_arithmetic(facts):
    """系统性对不上的列降为待复核：规则不再拿它们出确定性结论。

    不用行内恒等式反过来裁定单个存疑金额（如跨格折行的合计）：那样会让同一条
    勾稽规则在这些行上必然成立，查不出原件差错。
    """
    conflicts = column_arithmetic_conflicts(facts)
    if not conflicts:
        return facts
    constrained = []
    for fact in facts:
        found = [c for c in conflicts.get(fact.instance_uid, []) if fact.indicator in c["indicators"]]
        if found:
            fact = replace(fact, recognition_status="ambiguous", extra_dims={
                **fact.extra_dims, "column_identity_status": "arithmetic_conflict",
                "column_arithmetic_conflicts": found})
        constrained.append(fact)
    return constrained
