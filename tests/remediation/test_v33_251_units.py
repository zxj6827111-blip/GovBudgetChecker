"""V33-251：按原文单位归一分项；部分加总不能冒充完整合计。"""
from __future__ import annotations

from decimal import Decimal

from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
from src.engine.rule_outcome import RuleOutcomeSignal
from src.engine.rules_v33 import build_document


def _run(pages):
    doc = build_document("/tmp/v33-251.pdf", list(pages), [[] for _ in pages], 1000)
    doc.report_kind = "final"
    rule = R33251_FunctionalDetailCompleteness()
    try:
        return rule.apply(doc), doc, None
    except RuleOutcomeSignal as signal:
        return list(getattr(signal, "partial_issues", []) or []), doc, signal


HEAD = ("（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为{total_budget}万元，支出决算为{total_final}万元。其中：\n")
TAIL = "六、一般公共预算财政拨款基本支出决算情况说明"


def test_yuan_items_are_normalized_not_treated_as_wan():
    page = (
        HEAD.format(total_budget="100.00", total_final="90.00")
        + "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。\n"
        + "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算20.00万元，支出决算为179.82元。\n"
        + "3、社会保障和就业支出（类）行政事业单位养老支出（款）行政单位离退休（项）。年初预算10.00万元，支出决算为10.00万元。\n"
        + TAIL
    )
    issues, doc, signal = _run([page])
    assert signal is None
    final_issue = next(issue for issue in issues if "支出决算合计" in issue.message)
    location = final_issue.location
    assert location["included_subtotal_wan"] == "60.017982"
    assert location["difference_wan"] == "-29.982018"
    assert location["completeness"] == "complete"
    assert location["unit_normalized_items"][0]["amount"] == "179.82"
    assert location["unit_normalized_items"][0]["value_wan"] == "0.017982"
    assert "条件性说明" in final_issue.message
    assert "按万元名义值计入" in final_issue.message
    # 预算侧 60+20+10=90，与 100 相差 10，独立报告。
    budget_issue = next(issue for issue in issues if "年初预算合计" in issue.message)
    assert budget_issue.location["difference_wan"] == "-10.00"


def test_complete_wan_items_within_envelope_pass():
    page = (
        HEAD.format(total_budget="100.00", total_final="90.00")
        + "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。\n"
        + "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算20.00万元，支出决算为20.00万元。\n"
        + "3、社会保障和就业支出（类）行政事业单位养老支出（款）行政单位离退休（项）。年初预算20.00万元，支出决算为20.00万元。\n"
        + TAIL
    )
    issues, doc, signal = _run([page])
    assert signal is None
    assert not any("合计" in issue.message and "不一致" in issue.message for issue in issues)


def test_amount_without_unit_keeps_obligation_incomplete():
    page = (
        HEAD.format(total_budget="100.00", total_final="90.00")
        + "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。\n"
        + "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算20.00万元，支出决算为40。\n"
        + "3、社会保障和就业支出（类）行政事业单位养老支出（款）行政单位离退休（项）。年初预算20.00万元，支出决算为10.00万元。\n"
        + TAIL
    )
    issues, doc, signal = _run([page])
    assert signal is not None and signal.status == "insufficient_data"
    assert "item_amount_not_included" in getattr(signal, "reason_codes", [])
    final_issue = next(issue for issue in issues if "支出决算合计" in issue.message)
    assert final_issue.location["completeness"] == "partial"
    assert final_issue.location["not_included_items"]
    assert "不能视为完整分项加总" in final_issue.message


def test_completion_rate_amount_is_not_a_declaration():
    page = (
        HEAD.format(total_budget="100.00", total_final="90.00")
        + "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。\n"
        + "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算20.00万元，支出决算为20.00万元。\n"
        + "3、社会保障和就业支出（类）行政事业单位养老支出（款）行政单位离退休（项）。年初预算20.00万元，支出决算为20.00万元。完成年初预算的90.00%。\n"
        + TAIL
    )
    issues, doc, signal = _run([page])
    assert signal is None
    assert not any("未纳入合计" in issue.message for issue in issues)
