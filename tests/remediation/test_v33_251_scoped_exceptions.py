"""V33-251 例外声明必须绑定科目与预算阶段。

矩阵覆盖：预算/决算各自的零值、无此项、依法不予公开，科目全范围无此项，
例外与数字矛盾、不同科目不串用、已公开范围与全口径总额不可比，
以及“未知金额贡献不得制造完整加总错误”。
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
KNOWN = "1、教育支出（类）普通教育（款）小学教育（项）。年初预算100.00万元，支出决算为90.00万元。\n"
ITEM2 = "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。{body}\n"


def _run(body, *, total_budget="100.00", total_final="90.00"):
    doc = build_document(
        "/tmp/v33-251-scoped-exceptions.pdf",
        [HEAD.format(total_budget=total_budget, total_final=total_final) + body + TAIL],
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


def _ledger(doc, outcomes):
    return build_obligation_ledger(
        None,
        report_kind="final",
        rule_execution_summary=summarize_rule_outcomes(outcomes),
    )


def _obligation(doc, outcomes):
    return next(
        item for item in _ledger(doc, outcomes)["instances"]
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


def _assert_defers(body, *, code, **kwargs):
    doc, (issues, outcomes) = _outcomes(body, **kwargs)
    assert outcomes[0].status == "insufficient_data", outcomes[0].to_dict()
    assert code in outcomes[0].reason_codes, outcomes[0].reason_codes
    obligation = _obligation(doc, outcomes)
    assert obligation["status"] == "insufficient_data"
    assert obligation["blocks_gate"] is True
    return issues, outcomes


# ---------------------------------------------------------------------------
# 阶段作用域：预算例外不能豁免决算，反之亦然
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exception",
    ["年初无此项预算", "年初预算涉密依法不予公开"],
    ids=["budget_zero", "budget_withheld"],
)
def test_budget_exception_does_not_exempt_missing_final(exception):
    _assert_defers(
        KNOWN + ITEM2.format(body=f"{exception}。支出决算未提供。"),
        code="item_disclosure_missing",
    )


@pytest.mark.parametrize(
    "exception",
    ["支出决算无此项", "支出决算涉密依法不予公开"],
    ids=["final_zero", "final_withheld"],
)
def test_final_exception_does_not_exempt_missing_budget(exception):
    _assert_defers(
        KNOWN + ITEM2.format(body=f"{exception}。年初预算未提供。"),
        code="item_disclosure_missing",
    )


def test_stage_exception_does_not_cross_items():
    body = (
        "1、教育支出（类）普通教育（款）小学教育（项）。年初无预算，支出决算为50.00万元。\n"
        "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。支出决算为40.00万元。\n"
    )
    _assert_defers(body, code="item_disclosure_missing")


# ---------------------------------------------------------------------------
# 已公开范围与全口径总额不可比：不制造完整加总错误
# ---------------------------------------------------------------------------


def test_both_stages_withheld_is_not_a_complete_sum_mismatch():
    doc, (issues, outcomes) = _outcomes(
        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。\n"
        "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。"
        "该科目年初预算和支出决算涉密，依法不予公开。\n"
    )
    assert not any(
        item.location.get("completeness") == "complete"
        and "合计" in item.message and "不一致" in item.message
        for item in issues
    ), [item.message for item in issues]
    assert not any(
        item["status"] == "fail" and item.get("sub_check_id", "").endswith(":item_sum")
        for item in doc.fact_receipts
    )
    for kind in ("budget", "final"):
        receipt = _receipt(doc, kind)
        assert receipt is not None and receipt["status"] == "not_applicable"
        assert "withheld_amount_unknown" in receipt["reason_codes"]
        sources = receipt.get("disclosure_sources") or []
        assert any(
            set(item.get("stages") or []) == {"budget", "final"}
            and item.get("scope") == "stage"
            and "涉密" in str(item.get("text") or "")
            for item in sources
        ), sources
    assert outcomes[0].status == "pass"


def test_withheld_only_known_excess_is_reported_as_provable_mismatch():
    doc, (issues, outcomes) = _outcomes(
        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算100.00万元，支出决算为200.00万元。\n"
        "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。"
        "该科目年初预算和支出决算涉密，依法不予公开。\n"
    )
    excess = [item for item in issues if "已超过" in item.message]
    assert excess and excess[0].location["completeness"] == "partial"
    assert "即使未纳入或依法不予公开的分项为零" in excess[0].message
    receipt = _receipt(doc)
    assert receipt["status"] == "fail"
    assert "item_sum_mismatch" in receipt["reason_codes"]


# ---------------------------------------------------------------------------
# 科目全范围无此项 / 单阶段零值 / 数字零一致
# ---------------------------------------------------------------------------


def test_subject_wide_no_item_is_zero_for_both_stages():
    body = ITEM2.format(body="本项无此项。")
    _issues, doc, signal = _run(body, total_budget="0.00", total_final="0.00")
    assert signal is None
    sources = _receipt(doc, "budget")["disclosure_sources"]
    assert any(
        set(item.get("stages") or []) == {"budget", "final"}
        and item.get("scope") == "subject"
        for item in sources
    ), sources


@pytest.mark.parametrize(
    ("body", "total_budget", "total_final", "na_kind"),
    [
        ("年初预算涉密依法不予公开，支出决算为90.00万元。", "0.00", "90.00", "budget"),
        ("年初预算为100.00万元，支出决算涉密依法不予公开。", "100.00", "0.00", "final"),
    ],
    ids=["budget_only_withheld", "final_only_withheld"],
)
def test_single_stage_withholding_keeps_other_stage_pass(body, total_budget, total_final, na_kind):
    _issues, doc, signal = _run(
        ITEM2.format(body=body), total_budget=total_budget, total_final=total_final
    )
    assert signal is None
    other = "final" if na_kind == "budget" else "budget"
    assert _receipt(doc, na_kind)["status"] == "not_applicable"
    assert _receipt(doc, other)["status"] == "pass"


def test_ambiguous_exception_scope_stays_unresolved():
    body = ITEM2.format(
        body="年初预算为100.00万元以及相关说明材料且本项无此项。"
    )
    doc, (issues, outcomes) = _outcomes(body, total_budget="100.00")
    assert outcomes[0].status == "insufficient_data"
    assert "exception_scope_ambiguous" in outcomes[0].reason_codes
    assert _obligation(doc, outcomes)["blocks_gate"] is True
    sources = _receipt(doc, "final")["disclosure_sources"]
    assert any(item.get("scope") == "ambiguous" for item in sources), sources


def test_cross_comma_withholding_covers_both_named_stages():
    body = ITEM2.format(body="年初预算涉密，支出决算不公开。")
    _issues, doc, signal = _run(body)
    assert signal is None
    for kind in ("budget", "final"):
        receipt = _receipt(doc, kind)
        assert receipt["status"] == "not_applicable"
        assert any(
            set(item.get("stages") or []) == {"budget", "final"}
            for item in receipt["disclosure_sources"]
        ), receipt["disclosure_sources"]


def test_budget_zero_with_valid_final_passes():
    body = ITEM2.format(body="年初无预算，支出决算为90.00万元。")
    _issues, _doc, signal = _run(body, total_budget="0.00", total_final="90.00")
    assert signal is None


def test_final_zero_with_valid_budget_passes():
    body = ITEM2.format(body="年初预算为100.00万元，支出决算无此项。")
    _issues, _doc, signal = _run(body, total_budget="100.00", total_final="0.00")
    assert signal is None


def test_zero_exception_with_numeric_zero_is_consistent():
    body = ITEM2.format(body="年初无预算，年初预算为0.00万元，支出决算为0.00万元。")
    _issues, _doc, signal = _run(body, total_budget="0.00", total_final="0.00")
    assert signal is None


def test_subitem_withholding_keeps_disclosed_parent_amount():
    """“此款下项中涉密”约束子项明细，不否定该款已披露的决算金额。"""
    body = ITEM2.format(body="支出决算为90.00万元。此款下“项”中包含涉密内容，依法不予公开。")
    _issues, doc, signal = _run(body, total_budget="0.00", total_final="90.00")
    assert signal is None
    budget_receipt = _receipt(doc, "budget")
    assert budget_receipt is not None and budget_receipt["status"] == "not_applicable"
    assert _receipt(doc, "final")["status"] == "pass"


# ---------------------------------------------------------------------------
# 例外与数字矛盾：不能静默选择任一方
# ---------------------------------------------------------------------------


def test_zero_exception_conflicts_with_nonzero_amount():
    _assert_defers(
        ITEM2.format(body="年初无预算，年初预算为60.00万元，支出决算为50.00万元。"),
        code="exception_amount_conflict",
        total_budget="60.00",
    )


def test_withheld_exception_conflicts_with_numeric_amount():
    _assert_defers(
        ITEM2.format(body="支出决算涉密依法不予公开，支出决算为50.00万元。年初预算为60.00万元。"),
        code="exception_amount_conflict",
        total_budget="60.00",
    )
