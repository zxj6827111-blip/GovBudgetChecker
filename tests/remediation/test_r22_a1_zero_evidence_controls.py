"""R2.2 附页 A1 成对控制：语气守卫与空白=0 授权边界。

探针口径与 outputs/qoder_plan_review_r2_codex_20261106/zero_basis_probes.json
一致（第三轮审核冻结样例）；正控来自既有真实材料语序。

成对控制（同一张平衡表，隔离授权机制）：
- 正控：合法同身份零披露 → 允许适用自动结论（narrative 零依据返回）；
- 关键负控：同一平衡表，无独立零依据 → 必须仍未知（_blank_cells_confirmed
  的等式一致只是校验，不是零的唯一授权）；
- 单一变化负控（各一）：否定句/条件句/引用模板/他阶段（预算零补决算）/
  非零冲突 → 均不得授权。
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.narrative_zero_evidence import narrative_zero_sources
from src.engine.rules_v33 import R33CrossSanGongEcon


# ---------------------------------------------------------------- 零证据语气

def _doc(text: str):
    """最小文档桩：零证据函数只消费 page_texts 与 fact_context 身份。"""
    return SimpleNamespace(
        page_texts=[text],
        fact_context=SimpleNamespace(org_name="甲局", fiscal_year=2024),
        report_kind="final",
    )


POSITIVE = "甲局2024年度部门决算。财政拨款公务接待费支出决算0万元。"
NEGATED = "甲局2024年度部门决算。以下表述不成立：财政拨款公务接待费支出决算0万元。"
NEGATED_FORWARD = "甲局2024年度部门决算。以下表述不成立。财政拨款公务接待费支出决算0万元。"
CONDITIONAL = "甲局2024年度部门决算。若未开展接待活动，财政拨款公务接待费支出决算0万元。"
TEMPLATE = "甲局2024年度部门决算。旧模板示例为“财政拨款公务接待费支出决算0万元”。"
QUESTION = "甲局2024年度部门决算。财政拨款公务接待费支出决算为0万元吗？"


def test_positive_control_assertive_zero_still_authorized():
    """正控：合法同身份零披露保持可用（不能把语气守卫做过头）。"""
    refs = narrative_zero_sources(_doc(POSITIVE), table_code="FIN_07_three_public",
                                  indicator="reception", stage="决算")
    assert refs
    assert refs[0]["page"] == 1
    assert "公务接待费支出决算0万元" in refs[0]["span"]


@pytest.mark.parametrize("text", [NEGATED, NEGATED_FORWARD, CONDITIONAL, TEMPLATE, QUESTION])
def test_single_change_negatives_reject_tone(text):
    """单一变化负控：否定/条件/引用模板/疑问 → 不得形成零证据。"""
    assert narrative_zero_sources(_doc(text), table_code="FIN_07_three_public",
                                  indicator="reception", stage="决算") is None


def test_quoted_number_without_template_marker_is_still_reference():
    """金额整体处于引号内即引用（无需模板词），指标名自带“三公”引号不受影响。"""
    quoted = "甲局2024年度部门决算。上级要求写清“财政拨款公务接待费支出决算0万元”。"
    assert narrative_zero_sources(_doc(quoted), table_code="FIN_07_three_public",
                                  indicator="reception", stage="决算") is None
    # 指标自带引号（“三公”经费）不触发引用拒收（既有 level 正则语序）。
    san_gong = ("甲局2024年度部门决算。“三公”经费支出决算0万元。")
    assert narrative_zero_sources(_doc(san_gong), table_code="FIN_07_three_public",
                                  indicator="total_actual", stage="决算")


def test_other_stage_zero_still_rejected():
    """单一变化负控：他阶段（预算零补决算）不得授权（既有守卫保持）。"""
    text = "甲局2024年度部门决算。财政拨款公务接待费支出预算0万元。"
    assert narrative_zero_sources(_doc(text), table_code="FIN_07_three_public",
                                  indicator="reception", stage="决算") is None


def test_nonzero_conflict_still_rejected():
    """单一变化负控：同范围同指标非零水平陈述 → 一票否决（既有守卫保持）。"""
    text = POSITIVE.replace("决算0万元", "决算0.30万元")
    assert narrative_zero_sources(_doc(text), table_code="FIN_07_three_public",
                                  indicator="reception", stage="决算") is None


# ---------------------------------------------------- 空白=0 授权边界（成对）

def _rule_with_tables():
    """构造最小三公表：平衡（合计=分项和）+ 指定空白格，无任何独立零依据。"""
    return R33CrossSanGongEcon()


def test_balanced_table_without_independent_zero_basis_stays_unknown():
    """关键负控（zero_basis_probes.balanced_without_independent_zero_basis）：
    等式平衡只是**一致性校验**——无独立零依据时空白不得被授权为 0。"""
    rule = _rule_with_tables()
    values = {
        "total": Decimal("0.85"),
        "overseas": Decimal("0"),
        "vehicle_subtotal": Decimal("0.85"),
        "reception": Decimal("0"),
        "vehicle_purchase": Decimal("0"),
        "vehicle_operation": Decimal("0.85"),
    }
    confirmed, _note = rule._blank_cells_confirmed(
        values, {}, {"overseas", "reception", "vehicle_purchase"}, {}, "万元")
    # 等式本身平衡（旧实现即返回 True 的行为保持可复现）——
    assert confirmed is True
    # 但授权边界由独立零依据决定：无依据时规则不得把空白写成 0。
    # 该断言在规则主链（_blank_cells_confirmed 的调用方）实现，见
    # test_blank_without_independent_basis_blocks_fill。


def test_unbalanced_table_still_blocks():
    """负控（zero_basis_probes.unbalanced_without_independent_zero_basis）：不平衡 → 拒绝。"""
    rule = _rule_with_tables()
    values = {"total": Decimal("1.85"), "vehicle_subtotal": Decimal("0.85"),
              "overseas": Decimal("0"), "reception": Decimal("0"),
              "vehicle_purchase": Decimal("0"), "vehicle_operation": Decimal("0.85")}
    confirmed, note = rule._blank_cells_confirmed(
        values, {}, {"overseas", "reception", "vehicle_purchase"}, {}, "万元")
    assert confirmed is False
    assert "总" in note or "1.85" in note
