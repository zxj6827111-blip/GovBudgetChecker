"""V33-251 逐项完整性：应披露集合来自项级科目，缺失槽位不能当作通过。

独立复验 5ef8a427 的两个反例（全部分项决算缺失、一项缺失而其余恰好等于总额）
迁入此处，并补齐预算镜像、同一编号多科目、合法单项、明确无此项/零值、
依法不予公开、候选冲突与“上述（项）”叙述边界。
"""
from __future__ import annotations

import pytest

from src.engine.check_obligations import build_obligation_ledger
from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
from src.engine.pipeline import run_rules_with_outcomes
from src.engine.rule_outcome import RuleOutcomeSignal, summarize_rule_outcomes
from src.engine.rules_v33 import build_document

HEAD = (
    "（三）一般公共预算财政拨款支出决算具体情况\n"
    "一般公共预算财政拨款支出年初预算为{total_budget}万元，支出决算为{total_final}万元。其中：\n"
)
TAIL = "六、一般公共预算财政拨款基本支出决算情况说明"
EDU = "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。\n"
HOUSING = "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算40.00万元，支出决算为40.00万元。\n"


def _run(body, *, total_budget="100.00", total_final="90.00", tail=TAIL):
    doc = build_document(
        "/tmp/v33-251-coverage.pdf",
        [HEAD.format(total_budget=total_budget, total_final=total_final) + body + tail],
        [[]],
        1000,
    )
    doc.report_kind = "final"
    rule = R33251_FunctionalDetailCompleteness()
    try:
        return rule.apply(doc), doc, None
    except RuleOutcomeSignal as signal:
        return list(getattr(signal, "partial_issues", []) or []), doc, signal


def _outcomes(body, **kwargs):
    _issues, doc, _signal = _run(body, **kwargs)
    return doc, run_rules_with_outcomes(
        doc, report_kind="final", rules=[R33251_FunctionalDetailCompleteness()]
    )


def _ledger_obligation(doc, outcomes):
    ledger = build_obligation_ledger(
        None,
        report_kind="final",
        rule_execution_summary=summarize_rule_outcomes(outcomes),
    )
    return next(
        item for item in ledger["instances"]
        if item["obligation_id"] == "OBL-TXT-FINAL-DETAIL"
    )


def _receipt(doc, kind="final"):
    return next(
        (
            item for item in (getattr(doc, "fact_receipts", None) or [])
            if item.get("rule_id") == "V33-251"
            and item.get("sub_check_id") == f"V33-251:{kind}:item_sum"
        ),
        None,
    )


# ---------------------------------------------------------------------------
# 独立复验 5ef8a427 的两个反例
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算未提供。\n"
        "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算40.00万元，支出决算未提供。\n",
        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为90.00万元。\n"
        "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。主要用于职工住房公积金缴费，年初预算40.00万元。\n",
    ],
    ids=["all_item_final_amounts_missing", "one_item_missing_other_items_equal_total"],
)
def test_missing_item_amount_does_not_complete_obligation(body):
    doc, (issues, outcomes) = _outcomes(body)
    assert outcomes[0].status == "insufficient_data", outcomes[0].to_dict()
    assert "item_disclosure_missing" in outcomes[0].reason_codes
    obligation = _ledger_obligation(doc, outcomes)
    assert obligation["status"] == "insufficient_data"
    assert obligation["blocks_gate"] is True
    receipt = _receipt(doc)
    assert receipt is not None and receipt["status"] == "insufficient_data"
    assert "item_disclosure_missing" in receipt["reason_codes"]


def test_one_missing_item_keeps_other_confirmed_issue():
    """其余金额恰好等于总额时，已确认的加总回执不能把缺失项洗成 complete。"""
    body = (
        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为90.00万元。\n"
        "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。主要用于职工住房公积金缴费，年初预算40.00万元。\n"
    )
    issues, _doc, signal = _run(body)
    assert signal is not None and signal.status == "insufficient_data"
    issue = next(item for item in issues if "支出决算合计" in item.message)
    assert issue.location["completeness"] == "partial"
    assert "不能视为完整分项加总" in issue.message
    missing = [
        item for item in issue.location["not_included_items"]
        if item["not_included_reason"] == "item_disclosure_missing"
    ]
    assert missing and "住房公积金" in missing[0]["item_head"]


# ---------------------------------------------------------------------------
# 预算侧镜像与同一编号多科目
# ---------------------------------------------------------------------------


def test_budget_side_missing_mirror_defers():
    body = (
        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。\n"
        "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。支出决算为40.00万元。\n"
    )
    _issues, doc, signal = _run(body)
    assert signal is not None and signal.status == "insufficient_data"
    assert "item_disclosure_missing" in getattr(signal, "reason_codes", [])
    receipt = _receipt(doc, "budget")
    assert receipt is not None and receipt["status"] == "insufficient_data"


def test_all_item_budget_amounts_missing_defers():
    body = (
        "1、教育支出（类）普通教育（款）小学教育（项）。支出决算为50.00万元。\n"
        "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。支出决算为40.00万元。\n"
    )
    doc, (issues, outcomes) = _outcomes(body)
    assert outcomes[0].status == "insufficient_data"
    assert "item_disclosure_missing" in outcomes[0].reason_codes
    receipt = _receipt(doc, "budget")
    assert receipt["status"] == "insufficient_data"


def test_multiple_subjects_under_one_number_are_each_required():
    body = (
        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元；"
        "住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算40.00万元。\n"
    )
    issues, _doc, signal = _run(body)
    assert signal is not None and signal.status == "insufficient_data"
    assert "item_disclosure_missing" in getattr(signal, "reason_codes", [])
    issue = next(item for item in issues if "支出决算合计" in item.message)
    assert "住房公积金" in issue.message or any(
        "住房公积金" in str(item.get("item_head") or "")
        for item in issue.location["not_included_items"]
    )


def test_narrative_reference_to_previous_item_is_not_a_new_slot():
    """“除列入上述（项）外”不是新科目，不能把叙述误当缺失槽位。"""
    body = (
        "1、教育支出（类）普通教育（款）小学教育（项）。主要用于除列入上述（项）外的各类教育支出。"
        "年初预算90.00万元，支出决算为90.00万元。\n"
    )
    _issues, _doc, signal = _run(body, total_budget="90.00", total_final="90.00")
    assert signal is None


def test_reason_number_list_is_not_a_subject_slot():
    """原因说明里的 1、2、 编号列表不是项级科目，不能因此判缺失。"""
    body = (
        "1、因机构改革单位职能转变，当年部分支出减少。"
        "2、全年平均人数减少，人员经费减少。其中：\n"
        + EDU
        + HOUSING
    )
    _issues, _doc, signal = _run(body)
    assert signal is None


# ---------------------------------------------------------------------------
# 合法声明形式：不能把正常披露判成缺失
# ---------------------------------------------------------------------------


def test_single_legal_item_passes():
    body = EDU
    _issues, _doc, signal = _run(body, total_budget="60.00", total_final="50.00")
    assert signal is None


def test_explicit_no_initial_budget_with_final_declaration_passes():
    body = "1、教育支出（类）普通教育（款）小学教育（项）。年初无预算，决算数为90.00万元。\n"
    _issues, _doc, signal = _run(body, total_budget="0.00", total_final="90.00")
    assert signal is None


def test_explicit_zero_expense_disclosure_passes():
    body = "1、教育支出（类）普通教育（款）小学教育（项）。年初预算为10.00万元，本项无支出，也未发生支出。\n"
    _issues, _doc, signal = _run(body, total_budget="10.00", total_final="0.00")
    assert signal is None


def test_lawful_withholding_is_not_missing_disclosure():
    body = (
        "1、社会保障和就业支出（类）退役安置（款）支出决算为90.00万元。"
        "此款下各“项”中包含涉密内容，依法不予公开。\n"
    )
    _issues, _doc, signal = _run(body, total_budget="0.00", total_final="90.00")
    assert signal is None


def test_abbreviated_budget_word_is_recognized():
    body = "1、教育支出（类）普通教育（款）小学教育（项）。主要用于：单位支出。预算为90.00万元，支出决算为90.00万元。\n"
    _issues, _doc, signal = _run(body, total_budget="90.00", total_final="90.00")
    assert signal is None


def test_subject_header_amount_is_final_when_no_explicit_declaration():
    body = (
        "1、公共安全支出（类）司法（款）事业运行（项）支出90.00万元，主要用于：事业支出。"
        "年初预算为90.00万元。\n"
    )
    _issues, _doc, signal = _run(body, total_budget="90.00", total_final="90.00")
    assert signal is None


def test_zero_without_unit_remains_unusable_not_zero():
    body = "1、教育支出（类）普通教育（款）小学教育（项）。年初预算数为0，支出决算为50.00万元。\n"
    doc, (issues, outcomes) = _outcomes(body, total_budget="50.00", total_final="50.00")
    assert outcomes[0].status == "insufficient_data"
    assert "item_amount_not_included" in outcomes[0].reason_codes
    assert _ledger_obligation(doc, outcomes)["status"] == "insufficient_data"


def test_same_sentence_double_budget_is_a_candidate_conflict():
    body = (
        "1、教育支出（类）普通教育（款）小学教育（项）。"
        "年初预算为60.00万元，年初预算为70.00万元，支出决算为50.00万元。\n"
    )
    doc, (issues, outcomes) = _outcomes(body)
    assert outcomes[0].status == "insufficient_data"
    assert "candidate_amount_conflict" in outcomes[0].reason_codes
    assert _ledger_obligation(doc, outcomes)["status"] == "insufficient_data"
