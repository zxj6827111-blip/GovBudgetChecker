"""新增十份固定材料的规则反例；数字和软换行来自原 PDF，不是规则输出。

来源：SH2024NEW_02 物理第 7、29 页，SH2024NEW_06 第 35—36 页，
SH2024NEW_09 第 37 页。真实摘录与合成的差错变体分开，避免仅删除误报。
"""

from copy import deepcopy

import pytest

from src.engine.common_rules import CMM007_ZeroBaseTrendRecompute
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import (
    R33235_NarrativeAmountConsistency,
    R33240_Table2_IncomeAdvancedCheck,
    build_document,
)


INCOME_HEADERS = [
    [
        "项目", None, "本年收入合计", "财政拨款收入", "上级补助\n收入",
        "事业收入", "经营收入", "附属单位\n上缴收入", "其他收入",
    ],
    ["功能分类\n科目编码", "科目名称", None, None, None, None, None, None, None],
]
SPORTS_INCOME_ROWS = [
    ["合计", None, "305,443.51", "284,218.54", "207.02", "14,707.75", "20.00", "0.00", "6,290.19"],
    ["206", "科学技术支出", "5,328.84", "4,898.83", "200.00", "225.64", "0.00", "0.00", "4.37"],
    ["20603", "应用研究", "5,328.84", "4,898.83", "200.00", "225.64", "0.00", "0.00", "4.37"],
    ["2060302", "社会公益研究", "5,328.84", "4,898.83", "200.00", "225.64", "0.00", "0.00", "4.37"],
    ["207", "文化旅游体育与传媒支出", "181,358.33", "164,727.40", "7.02", "13,548.67", "20.00", "0.00", "3,055.23"],
    ["20703", "体育", "181,358.33", "164,727.40", "7.02", "13,548.67", "20.00", "0.00", "3,055.23"],
    ["2070307", "体育场馆", "12,217.52", "7,728.17", "7.02", "3,904.32", "0.00", "0.00", "578.01"],
]

SPORTS_BUDGET_ITEM = (
    "2、其他支出（类）彩票发行销售机构业务费安排的支出（款）\n"
    "彩票市场调控资金支出（项）。主要用于：上海市体育彩票管理\n"
    "中心体彩市场调控支出。年初部门预算为 0 万元，支出决算为\n"
    "3,631.14 万元。决算数大于预算数的主要原因：中央转移支付"
)
SANGONG_SECTION = "七、财政拨款“三公”经费支出决算情况说明\n"
WATER_CURRENT = (
    "“三公”经费财政拨款支出年初预算为 607.31 万元，支出\n"
    "决算为 499.49 万元，完成预算的 82.25%，其中：因公出国（境）\n"
    "费决算为 241.15 万元，完成预算的 99.61%；公务用车购置及运\n"
    "行维护费支出决算为 250.60 万元，完成预算的 82.75%；公务接\n"
    "待费支出决算为 7.74 万元，完成预算的 12.41%。"
)
WATER_TREND = (
    "2024 年度“三公”经费财政拨款支出决算数比 2023 年度增\n"
    "加 195.40 万元，增长 64.28%，其中：因公出国（境）费支出决\n"
    "算增加 137.90 万元，增长 133.56%；公务用车购置及运行维护\n"
    "费支出决算增加 58.59 万元，增长 30.51%；公务接待费支出决\n"
    "算减少 1 万元，下降 11.44%。"
)
GREEN_CURRENT = (
    "“三公”经费财政拨款支出年初预算为 686.53 万元，支出\n"
    "决算为 480.17 万元，完成预算的 69.94%，其中：因公出国（境）\n"
    "费决算为 123.99 万元，完成预算的 99.16%；公务用车购置及运\n"
    "行维护费支出决算为 326.94 万元，完成预算的 68.68%；公务接\n"
    "待费支出决算为 29.25 万元，完成预算的 34.21%。"
)
GREEN_TREND = (
    "2024 年度“三公”经费财政拨款支出决算数比 2023 年度增\n"
    "加 153.95 万元，增长 47.19%，其中：因公出国（境）费支出决\n"
    "算增加 104.54 万元，增长 537.48%；公务用车购置及运行维护\n"
    "费支出决算增加 50.02 万元，增长 18.06%；公务接待费支出决\n"
    "算减少 0.6 万元，下降 2.01%。"
)


def narrative_document(*pages):
    return build_document(
        "固定回归2024年度部门决算.pdf", ["2024 年度部门决算", *pages], [], 0
    )


def income_document(rows):
    return build_document(
        "固定回归2024年度部门决算.pdf", ["收入决算表\n单位：万元"], [[rows]], 0
    )


@pytest.mark.parametrize("source_row", SPORTS_INCOME_ROWS)
def test_multiline_income_header_keeps_all_six_sources(source_row):
    rows = deepcopy(INCOME_HEADERS) + [deepcopy(source_row)]
    assert R33240_Table2_IncomeAdvancedCheck().apply(income_document(rows)) == []


def test_multiline_income_header_does_not_hide_real_horizontal_error():
    rows = deepcopy(INCOME_HEADERS) + [deepcopy(SPORTS_INCOME_ROWS[1])]
    rows[-1][2] = "5,338.84"  # 合成正例：真实总额额外错写 10 万元。
    issues = R33240_Table2_IncomeAdvancedCheck().apply(income_document(rows))
    assert len(issues) == 1
    assert issues[0].severity == "error"
    assert "差额=10.00" in issues[0].message
    assert "上级补助收入=200.00" in issues[0].message


def test_multiline_continuation_header_still_rejects_same_width_reordering():
    rows = deepcopy(INCOME_HEADERS) + [deepcopy(SPORTS_INCOME_ROWS[1])]
    swapped = deepcopy(INCOME_HEADERS[0])
    swapped[3], swapped[4] = swapped[4], swapped[3]
    rows.extend([swapped, deepcopy(SPORTS_INCOME_ROWS[1])])
    with pytest.raises(RuleDeferred, match="同宽列序变化"):
        R33240_Table2_IncomeAdvancedCheck().apply(income_document(rows))


@pytest.mark.parametrize("unknown", ["", None, "—", "未识别"])
def test_real_income_row_with_unknown_sources_is_not_a_zero_filled_error(unknown):
    # 国资委原页第8页215：原本事业收入等列为空，不能据总计差228.40反填该列。
    headers = deepcopy(INCOME_HEADERS)
    headers[1][0] = "功能分\n类科目\n编码"
    row = ["215", "资源勘探工业信息等支出", "16,191.60", "15,963.20", unknown, unknown, unknown, unknown, unknown]
    with pytest.raises(RuleDeferred, match="未以空白补零") as error:
        R33240_Table2_IncomeAdvancedCheck().apply(income_document(headers + [row]))
    assert error.value.partial_issues == []
    assert "215" in error.value.detail and "事业收入" in error.value.detail


def test_explicit_zero_sources_still_allow_a_real_income_mismatch():
    # 合成正例：若同一行明确写0.00，228.40差额必须报告，不能视为空白拒答。
    row = ["215", "资源勘探工业信息等支出", "16,191.60", "15,963.20", "0.00", "0.00", "0.00", "0.00", "0.00"]
    issues = R33240_Table2_IncomeAdvancedCheck().apply(income_document(deepcopy(INCOME_HEADERS) + [row]))
    assert len(issues) == 1 and "差额=228.40" in issues[0].message


def test_unknown_sources_do_not_discard_independently_complete_row_errors():
    unknown_row = ["215", "资源勘探工业信息等支出", "16,191.60", "15,963.20", "", "", "", "", ""]
    wrong_row = deepcopy(SPORTS_INCOME_ROWS[1])
    wrong_row[2] = "5,338.84"
    with pytest.raises(RuleDeferred, match="横向比较未完成") as error:
        R33240_Table2_IncomeAdvancedCheck().apply(
            income_document(deepcopy(INCOME_HEADERS) + [unknown_row, wrong_row])
        )
    assert len(error.value.partial_issues) == 1
    assert "差额=10.00" in error.value.partial_issues[0].message


@pytest.mark.parametrize("budget_label", ["年初部门预算", "年 初 部 门 预 算", "年初单位预算"])
def test_budget_amount_is_not_an_opening_final_amount(budget_label):
    text = SPORTS_BUDGET_ITEM.replace("年初部门预算", budget_label)
    assert R33235_NarrativeAmountConsistency().apply(narrative_document(text)) == []


def test_opening_final_amount_conflict_before_department_budget_is_retained():
    text = (
        "1、住房保障支出（类）购房补贴（项）207万元。"
        "年初部门预算为241.76万元，支出决算为170.03万元。"
    )
    issues = R33235_NarrativeAmountConsistency().apply(narrative_document(text))
    assert len(issues) == 1
    assert "条目开头=207.00万元" in issues[0].message
    assert "170.03万元" in issues[0].message


@pytest.mark.parametrize("current,trend", [(WATER_CURRENT, WATER_TREND), (GREEN_CURRENT, GREEN_TREND)])
def test_soft_wrapped_expense_subjects_do_not_cross_bind(current, trend):
    doc = narrative_document(SANGONG_SECTION + current, trend)
    assert CMM007_ZeroBaseTrendRecompute().apply(doc) == []


@pytest.mark.parametrize(
    "current,trend,current_amount,declared",
    [(WATER_CURRENT, WATER_TREND, "250.60", "30.51"), (GREEN_CURRENT, GREEN_TREND, "326.94", "18.06")],
)
def test_soft_wrapped_vehicle_subject_still_reports_real_percent_conflict(
    current, trend, current_amount, declared
):
    doc = narrative_document(SANGONG_SECTION + current, trend.replace(declared, "80.00"))
    issues = CMM007_ZeroBaseTrendRecompute().apply(doc)
    assert len(issues) == 1
    assert issues[0].location["indicator"] == "公务用车购置及运行维护费"
    assert issues[0].location["current_amount"] == current_amount
    assert issues[0].location["declared_percent"] == "80.00"


def test_expense_tail_without_full_subject_cannot_inherit_another_subject():
    doc = narrative_document(
        SANGONG_SECTION
        + "因公出国（境）费决算为241.15万元。费支出决算增加58.59万元，增长30.51%。"
    )
    with pytest.raises(RuleDeferred, match="拒绝跨项取数") as error:
        CMM007_ZeroBaseTrendRecompute().apply(doc)
    assert error.value.partial_issues == []


def test_wrapped_subject_with_conflicting_current_values_remains_unresolved():
    doc = narrative_document(
        SANGONG_SECTION
        + "公务用车运行维\n护费决算为120万元。公务用车运行维护费决算为130万元。"
        + "公务用车运行维\n护费支出决算增加20万元，增长20%。"
    )
    with pytest.raises(RuleDeferred, match="parse_ambiguity") as error:
        CMM007_ZeroBaseTrendRecompute().apply(doc)
    assert error.value.partial_issues == []
