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


# ---------------------------------------------------------------------------
# B1-A02/A03 返工：0/1/多项、总额缺失、条件性净增量
# ---------------------------------------------------------------------------

HEAD_NO_TOTAL = "（三）一般公共预算财政拨款支出决算具体情况\n其中：\n"


def _receipt(doc, kind="final"):
    return next((item for item in (getattr(doc, "fact_receipts", None) or [])
                 if item.get("rule_id") == "V33-251"
                 and item.get("sub_check_id") == f"V33-251:{kind}:item_sum"), None)


def test_one_included_item_with_missing_unit_still_defers():
    page = (
        HEAD.format(total_budget="100.00", total_final="90.00")
        + "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。\n"
        + "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算40.00万元，支出决算为40。\n"
        + TAIL
    )
    issues, doc, signal = _run([page])
    assert signal is not None and signal.status == "insufficient_data"
    assert "item_amount_not_included" in getattr(signal, "reason_codes", [])
    final_issue = next(issue for issue in issues if "支出决算合计" in issue.message)
    assert final_issue.location["included_subtotal_wan"] == "50.00"
    assert final_issue.location["completeness"] == "partial"
    assert final_issue.location["not_included_items"]
    receipt = _receipt(doc)
    assert receipt is not None and receipt["status"] == "insufficient_data"
    assert "item_amount_not_included" in receipt["reason_codes"]


def test_zero_included_items_still_defers_with_receipt():
    page = (
        HEAD.format(total_budget="100.00", total_final="90.00")
        + "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为40。\n"
        + TAIL
    )
    issues, doc, signal = _run([page])
    assert signal is not None and signal.status == "insufficient_data"
    assert any("无已纳入分项" in reason for reason in getattr(signal, "unresolved_reasons", []))
    receipt = _receipt(doc)
    assert receipt is not None and receipt["status"] == "insufficient_data"


def test_single_legal_item_equal_to_total_passes_with_receipt():
    page = (
        HEAD.format(total_budget="90.00", total_final="90.00")
        + "1、教育支出（类）普通教育（款）小学教育（项）。年初预算90.00万元，支出决算为90.00万元。\n"
        + TAIL
    )
    issues, doc, signal = _run([page])
    assert signal is None
    assert not any("不一致" in issue.message for issue in issues)
    receipt = _receipt(doc)
    assert receipt is not None and receipt["status"] == "pass"


def test_single_legal_item_mismatch_is_checked():
    page = (
        HEAD.format(total_budget="90.00", total_final="90.00")
        + "1、教育支出（类）普通教育（款）小学教育（项）。年初预算90.00万元，支出决算为80.00万元。\n"
        + TAIL
    )
    issues, doc, signal = _run([page])
    assert signal is None
    final_issue = next(issue for issue in issues if "支出决算合计" in issue.message)
    assert "纳入1项" in final_issue.message
    assert final_issue.location["difference_wan"] == "-10.00"


def test_missing_stated_total_defers_instead_of_silent_pass():
    page = (
        HEAD_NO_TOTAL
        + "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。\n"
        + TAIL
    )
    issues, doc, signal = _run([page])
    assert signal is not None and signal.status == "insufficient_data"
    assert "stated_total_missing" in getattr(signal, "reason_codes", [])
    receipt = next((item for item in (getattr(doc, "fact_receipts", None) or [])
                    if item.get("sub_check_id") == "V33-251:total:missing"), None)
    assert receipt is not None and receipt["status"] == "insufficient_data"


def test_many_included_items_complete_pass():
    page = (
        HEAD.format(total_budget="100.00", total_final="90.00")
        + "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。\n"
        + "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算20.00万元，支出决算为20.00万元。\n"
        + "3、社会保障和就业支出（类）行政事业单位养老支出（款）行政单位离退休（项）。年初预算20.00万元，支出决算为20.00万元。\n"
        + TAIL
    )
    issues, doc, signal = _run([page])
    assert signal is None
    assert not any("不一致" in issue.message for issue in issues)
    receipt = _receipt(doc)
    assert receipt is not None and receipt["status"] == "pass"


def test_conditional_increase_is_net_of_already_counted_yuan():
    page = (
        HEAD.format(total_budget="100.00", total_final="90.00")
        + "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。\n"
        + "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算20.00万元，支出决算为179.82元。\n"
        + "3、社会保障和就业支出（类）行政事业单位养老支出（款）行政单位离退休（项）。年初预算20.00万元，支出决算为10.00万元。\n"
        + TAIL
    )
    issues, doc, signal = _run([page])
    assert signal is None
    final_issue = next(issue for issue in issues if issue.location.get("unit_normalized_items"))
    assert "合计将增加179.802018万元" in final_issue.message
    assert final_issue.location["unit_normalized_items"][0]["value_wan"] == "0.017982"
    assert final_issue.location["conditional_included_wan"] == "0.017982"
    assert final_issue.location["conditional_assumed_wan"] == "179.82"
    assert final_issue.location["conditional_delta_wan"] == "179.802018"


def test_conditional_delta_for_yi_yuan_is_negative():
    page = (
        HEAD.format(total_budget="20000.00", total_final="20000.00")
        + "1、教育支出（类）普通教育（款）小学教育（项）。年初预算10000.00万元，支出决算为2亿元。\n"
        + "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算10000.00万元，支出决算为10.00万元。\n"
        + TAIL
    )
    issues, doc, signal = _run([page])
    assert signal is None
    final_issue = next(issue for issue in issues if issue.location.get("unit_normalized_items"))
    assert "合计将减少19998万元" in final_issue.message
    assert final_issue.location["conditional_included_wan"] == "20000"
    assert final_issue.location["conditional_assumed_wan"] == "2"
    assert final_issue.location["conditional_delta_wan"] == "-19998"


def test_conditional_delta_mixed_units_is_net():
    page = (
        HEAD.format(total_budget="20000.00", total_final="20000.00")
        + "1、教育支出（类）普通教育（款）小学教育（项）。年初预算10000.00万元，支出决算为2亿元。\n"
        + "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算1999.00万元，支出决算为179.82元。\n"
        + "3、社会保障和就业支出（类）行政事业单位养老支出（款）行政单位离退休（项）。年初预算1.00万元，支出决算为10.00万元。\n"
        + TAIL
    )
    issues, doc, signal = _run([page])
    assert signal is None
    final_issue = next(issue for issue in issues if issue.location.get("unit_normalized_items"))
    assert "合计将减少19818.197982万元" in final_issue.message
    assert final_issue.location["conditional_included_wan"] == "20000.017982"
    assert final_issue.location["conditional_assumed_wan"] == "181.82"
    assert final_issue.location["conditional_delta_wan"] == "-19818.197982"
