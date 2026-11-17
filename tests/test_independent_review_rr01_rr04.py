"""2026-11-17 独立复核 RR-01～RR-04 的正式回归反例。

这些用例直接来自独立复核目录的输入，覆盖：
- 共享“其中”分项改金额 / 换科目后不得继续 pass；
- 根问题不得跨页吞掉独立原因；
- 技术诊断与未确证候选不得在后台门禁中升格；
- review_summary 的待处理数必须与人工决定、忽略集合一致。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import build_document
from src.schemas.review_report import build_review_report
from src.services.obligation_finding_coverage import cascade_root_issue_ids
from src.services.review_lifecycle_service import (
    _problem_blockers,
    blocking_coverage_obligations,
)
from src.services.review_problem_set import collect_review_problems


SHARED_TEXT = (
    "（三）一般公共预算财政拨款支出决算具体情况\n"
    "一般公共预算财政拨款支出年初预算为 11654.43 万元，支出决算为 11685.76 万元，"
    "完成年初预算的 100.27%。决算数大于预算数的主要原因：年中追加。其中：\n"
    "1、教育支出（类）职业教育（款）中等职业教育（项）。主要用于：中专教育支出。"
    "年初预算为 8603.44 万元，支出决算为 8643.84 万元。\n"
    "2、社会保障和就业支出（类）行政事业单位养老支出（款）机关事业单位基本养老保险缴费支出（项）、"
    "机关事业单位职业年金缴费支出（项）、其他行政事业单位养老支出（项）。主要用于教职工养老。"
    "其中：机关事业单位基本养老保险缴费支出 985.78 万元，机关事业单位职业年金 492.39 万元，"
    "其他行政事业单位离退休支出 10.2 万元，事业单位离退休 566.77 万元。"
    "年初预算数总计 2064.21 万元；支出决算为 2055.14 万元，完成年初预算的 99.56%。\n"
    "3、卫生健康支出（类）行政事业单位医疗（款）事业单位医疗（项）。年初预算为 556.19 万元，"
    "支出决算为 556.19 万元。\n"
    "4、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算为 430.59 万元，"
    "支出决算为 430.59 万元。\n"
)


def _run_shared(text: str):
    doc = build_document("counterexample.pdf", [text], [], 1000)
    with pytest.raises(RuleDeferred) as caught:
        R33251_FunctionalDetailCompleteness().apply(doc)
    receipts = {row["sub_check_id"]: row for row in doc.fact_receipts}
    messages = [issue.message for issue in caught.value.partial_issues or []]
    return receipts, messages


def test_shared_component_amount_change_fails_group_arithmetic():
    receipts, messages = _run_shared(SHARED_TEXT.replace("985.78 万元", "1.00 万元"))
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "fail"
    assert "组内分项合计" in str(group.get("incomplete_reason") or "")
    # 组级合计不再作为可信的章节加总数：整节加总不能 pass。
    assert receipts["V33-251:final:item_sum"]["status"] == "insufficient_data"
    assert any("组内分项合计" in message for message in messages)


def test_shared_component_unrelated_name_keeps_subject_gap():
    receipts, messages = _run_shared(SHARED_TEXT.replace(
        "其中：机关事业单位基本养老保险缴费支出 985.78",
        "其中：其他无关项目支出 985.78",
    ))
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "insufficient_data"
    assert "未能对应到项级科目" in str(group.get("incomplete_reason") or "")
    assert receipts["V33-251:final:item_sum"]["status"] == "insufficient_data"
    assert any("共享分项" in message for message in messages)


# ---- 2026-11-17 第二次独立复核 RR-01 扩展边界 ----


def test_shared_positive_control_still_passes_group_and_section_sum():
    """正控：裸金额分项按决算口径、组算术与章节加总仍 pass。"""
    receipts, _ = _run_shared(SHARED_TEXT)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "pass"
    # 未归属的组内子项不再被清空，改为在回执原因中留痕。
    assert "未逐一对应科目" in str(group.get("incomplete_reason") or "")
    assert receipts["V33-251:final:item_sum"]["status"] == "pass"


def test_explicit_budget_components_do_not_prove_final_disclosure():
    """第二次复核反例 A：分项显式写“年初预算为”，不得证明逐项决算已披露。"""
    budget_enum = SHARED_TEXT.replace(
        "其中：机关事业单位基本养老保险缴费支出 985.78 万元，"
        "机关事业单位职业年金 492.39 万元，"
        "其他行政事业单位离退休支出 10.2 万元，事业单位离退休 566.77 万元。",
        "其中：机关事业单位基本养老保险缴费支出年初预算为 985.78 万元，"
        "机关事业单位职业年金年初预算为 492.39 万元，"
        "其他行政事业单位离退休支出年初预算为 10.2 万元，"
        "事业单位离退休年初预算为 566.77 万元。",
    )
    assert budget_enum != SHARED_TEXT
    receipts, _ = _run_shared(budget_enum)
    final_groups = [
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    ]
    assert all(value["status"] != "pass" for value in final_groups)
    assert receipts["V33-251:final:item_sum"]["status"] != "pass"
    # 预算口径独立出组回执：四个预算分项之和 2055.14 与组级预算 2064.21 差 9.07。
    budget_groups = [
        value for key, value in receipts.items()
        if key.startswith("V33-251:budget:group:")
    ]
    assert budget_groups, "预算分项应有独立的组级回执"
    assert all(value["status"] == "fail" for value in budget_groups)
    assert any("组内分项合计" in str(value.get("incomplete_reason") or "")
               for value in budget_groups)


def test_similar_component_name_is_candidate_not_binding():
    """第二次复核反例 B：名称相似不构成金额身份，组回执不得 pass。"""
    receipts, messages = _run_shared(SHARED_TEXT.replace(
        "其中：机关事业单位基本养老保险缴费支出 985.78",
        "其中：机关事业单位基本养老保险基金补助 985.78",
    ))
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "insufficient_data"
    assert "未能对应到项级科目" in str(group.get("incomplete_reason") or "")
    assert receipts["V33-251:final:item_sum"]["status"] == "insufficient_data"
    assert any("名称相似" in message or "共享分项" in message
               for message in messages)


def test_mixed_role_components_leave_bare_amounts_unresolved():
    """显式“年初预算”与裸金额混排：裸金额角色无法证明，不得参与组内算术。"""
    receipts, _ = _run_shared(SHARED_TEXT.replace(
        "其中：机关事业单位基本养老保险缴费支出 985.78 万元，",
        "其中：机关事业单位基本养老保险缴费支出年初预算为 985.78 万元，",
    ))
    groups = [
        value for key, value in receipts.items()
        if key.startswith("V33-251:") and ":group:" in key
    ]
    assert groups
    assert all(value["status"] != "pass" for value in groups)
    assert any("未写明金额阶段" in str(value.get("incomplete_reason") or "")
               for value in groups)


def test_duplicate_component_names_are_ambiguous_not_bound():
    """重复名称：同名分项同时命中同一科目时判歧义，不再按数量放行。"""
    receipts, _ = _run_shared(SHARED_TEXT.replace(
        "其中：机关事业单位基本养老保险缴费支出 985.78 万元，机关事业单位职业年金 492.39 万元，",
        "其中：机关事业单位基本养老保险缴费支出 985.78 万元，"
        "机关事业单位基本养老保险缴费支出 986.00 万元，",
    ))
    groups = [
        value for key, value in receipts.items()
        if key.startswith("V33-251:") and ":group:" in key
    ]
    assert groups
    assert all(value["status"] != "pass" for value in groups)
    assert any("歧义" in str(value.get("incomplete_reason") or "")
               for value in groups)


def _finding(issue_id: str, rule_id: str, page: int, message: str, **location):
    return {
        "id": issue_id, "rule_id": rule_id, "source": "rule", "severity": "warn",
        "message": message,
        "location": {"page": page, "defect_type": "amount_unit_duplicated", **location},
        "evidence_status": "complete",
        "evidence_verification": {"status": "verified", "source_identity_complete": True},
    }


def _obligation(**overrides):
    base = dict(
        obligation_id="OBL-TXT-T5", status="insufficient_data", blocks_gate=True,
        detail="", reason_codes=["operand_missing"], blocked_by=[],
        missing_operands=[], checkers=["V33-222"], next_action="system_recheck",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_cascade_rejects_unrelated_page_and_independent_reasons():
    item = _obligation(
        detail="第40页乙单位正文存在主体冲突；第41页独立项目金额尚未提取",
        reason_codes=["subject_conflict", "operand_missing"],
        missing_operands=[{"source": "table", "page": 41, "row_key": "独立项目"}],
    )
    finding = _finding(
        "name-on-page-2", "CMM-017", 2, "第2页甲单位名称多字：甲单位位",
        actual_name="甲单位位", expected_name="甲单位",
    )
    row = {"rule_findings": [finding]}
    assert cascade_root_issue_ids(item, row, {"name-on-page-2"}) == []
    assert blocking_coverage_obligations([item], row, visible_issue_ids={"name-on-page-2"}) == [item]


# ---- 2026-11-17 第二次独立复核 RR-02 扩展边界 ----


def test_cascade_rejects_partial_page_intersection_in_name_branch():
    """根问题移到部分相交页（40）后，第41页独立表缺数仍不得被整条归并。"""
    item = _obligation(
        detail="第40页乙单位正文存在主体冲突；第41页独立项目金额尚未提取",
        reason_codes=["subject_conflict", "operand_missing"],
        missing_operands=[{"source": "table", "page": 41, "row_key": "独立项目"}],
    )
    finding = _finding(
        "name-on-page-40", "CMM-017", 40, "第40页甲单位名称多字：甲单位位",
        actual_name="甲单位位", expected_name="甲单位",
    )
    row = {"rule_findings": [finding]}
    assert cascade_root_issue_ids(item, row, {"name-on-page-40"}) == []
    assert blocking_coverage_obligations([item], row, visible_issue_ids={"name-on-page-40"}) == [item]


def test_cascade_rejects_partial_page_intersection_in_v33251_branch():
    """V33-251 分支同样走守卫：第40页相交 + 第41页表映射失败仍整条保留。"""
    item = _obligation(
        detail="V33-251:budget:item_sum 第40页乙科目预算未提取；第41页另一表的读取映射失败",
        checkers=["V33-251", "V33-106"],
        reason_codes=["item_disclosure_missing", "operand_missing"],
        blocked_by=[{"ref": "V33-251:budget:item_sum", "reason_code": "operand_missing"}],
        missing_operands=[
            {"source": "text", "page": 40, "row_key": "乙科目"},
            {"source": "table", "page": 41, "row_key": "独立科目"},
        ],
    )
    finding = _finding(
        "budget-on-page-40", "V33-251", 40, "第40页甲科目年初预算未逐项列示",
        checklist_item="C-11", subject_name="甲科目",
    )
    finding["location"].pop("defect_type", None)
    row = {"rule_findings": [finding]}
    assert cascade_root_issue_ids(item, row, {"budget-on-page-40"}) == []
    assert blocking_coverage_obligations([item], row, visible_issue_ids={"budget-on-page-40"}) == [item]


def test_cascade_rejects_same_page_different_subject():
    """同页不同主体：甲单位名称问题承接不了同页乙单位的主体冲突。"""
    item = _obligation(
        detail="第40页乙单位正文存在主体冲突",
        reason_codes=["subject_conflict"],
        missing_operands=[{"source": "text", "page": 40, "row_key": "乙单位"}],
    )
    finding = _finding(
        "name-on-page-40", "CMM-017", 40, "第40页甲单位名称多字：甲单位位",
        actual_name="甲单位位", expected_name="甲单位",
    )
    row = {"rule_findings": [finding]}
    assert cascade_root_issue_ids(item, row, {"name-on-page-40"}) == []


def test_cascade_allows_same_page_same_subject_name_merge():
    """同页同主体的主体冲突仍由名称问题承接（不误伤真实同源）。"""
    item = _obligation(
        detail="第40页甲单位正文存在主体冲突，金额关系尚未完成",
        reason_codes=["subject_conflict"],
        missing_operands=[{"source": "text", "page": 40, "row_key": "甲单位"}],
    )
    finding = _finding(
        "name-on-page-40", "CMM-017", 40, "第40页甲单位名称多字：甲单位位",
        actual_name="甲单位位", expected_name="甲单位",
    )
    row = {"rule_findings": [finding]}
    assert cascade_root_issue_ids(item, row, {"name-on-page-40"}) == ["name-on-page-40"]


def test_cascade_rejects_same_page_different_subject_v33251():
    """V33-251：同页不同科目（乙科目 vs 甲科目）不承接。"""
    item = _obligation(
        detail="V33-251:budget:item_sum 第40页乙科目预算未提取",
        checkers=["V33-251"],
        reason_codes=["item_disclosure_missing", "operand_missing"],
        blocked_by=[{"ref": "V33-251:budget:item_sum", "reason_code": "operand_missing"}],
        missing_operands=[{"source": "text", "page": 40, "row_key": "乙科目"}],
    )
    finding = _finding(
        "budget-on-page-40", "V33-251", 40, "第40页甲科目年初预算未逐项列示",
        checklist_item="C-11", subject_name="甲科目",
    )
    finding["location"].pop("defect_type", None)
    row = {"rule_findings": [finding]}
    assert cascade_root_issue_ids(item, row, {"budget-on-page-40"}) == []


def test_cascade_without_pages_keeps_single_root_only():
    """无页码证据：唯一根候选可承接，多个根候选保守保留。"""
    finding = _finding(
        "name-only", "CMM-017", 3, "第3页甲单位名称多字：甲单位位",
        actual_name="甲单位位", expected_name="甲单位",
    )
    row = {"rule_findings": [finding]}
    item = _obligation(
        detail="甲单位正文存在主体冲突",
        reason_codes=["subject_conflict"],
    )
    item2 = _obligation(
        detail="第40页乙单位正文存在主体冲突",
        reason_codes=["subject_conflict"],
        missing_operands=[{"source": "table", "page": 41, "row_key": "独立项目"}],
    )
    # 名称分支：义务无可核对来源页时不跨页兜底（既有纪律）。
    assert cascade_root_issue_ids(item, row, {"name-only"}) == []
    # 守卫层：无页码 + 多根候选时拒绝；这里只有一个候选但义务携带独立表来源。
    assert cascade_root_issue_ids(item2, row, {"name-only"}) == []


def test_cascade_execution_error_code_always_blocks_subject_branches():
    """execution_error 属独立原因，任何主体类分支都不得承接。"""
    finding = _finding(
        "name-on-page-40", "CMM-017", 40, "第40页甲单位名称多字：甲单位位",
        actual_name="甲单位位", expected_name="甲单位",
    )
    row = {"rule_findings": [finding]}
    item = _obligation(
        detail="第40页甲单位正文存在主体冲突；规则执行中断",
        reason_codes=["subject_conflict", "execution_error"],
        missing_operands=[{"source": "text", "page": 40, "row_key": "甲单位"}],
    )
    assert cascade_root_issue_ids(item, row, {"name-on-page-40"}) == []


def test_composite_obligation_records_partial_root_association():
    """复合义务保守保留时，已确定的部分关联与未覆盖独立页如实入档。"""
    from src.services.obligation_finding_coverage import cascade_partial_associations

    finding = _finding(
        "name-on-page-40", "CMM-017", 40, "第40页甲单位名称多字：甲单位位",
        actual_name="甲单位位", expected_name="甲单位",
    )
    row = {"rule_findings": [finding]}
    item = _obligation(
        detail="第40页甲单位正文存在主体冲突；第41页另一表的读取映射失败",
        reason_codes=["subject_conflict", "table_mapping_incomplete"],
        missing_operands=[
            {"source": "text", "page": 40, "row_key": "甲单位"},
            {"source": "table", "page": 41, "row_key": "另一表"},
        ],
    )
    assert cascade_root_issue_ids(item, row, {"name-on-page-40"}) == []
    partial = cascade_partial_associations(item, row, {"name-on-page-40"})
    assert partial.get("root_issue_ids") == ["name-on-page-40"]
    assert partial.get("uncovered_independent_pages") == [41]


def test_cascade_rejects_same_page_different_table_reading_failure():
    """同页不同表：主体类根问题不能解释同页另一张表的映射失败。"""
    finding = _finding(
        "name-on-page-41", "CMM-017", 41, "第41页甲单位名称多字：甲单位位",
        actual_name="甲单位位", expected_name="甲单位",
    )
    row = {"rule_findings": [finding]}
    item = _obligation(
        detail="第41页甲单位正文存在主体冲突；第41页收入明细表映射失败",
        reason_codes=["subject_conflict", "table_mapping_incomplete"],
        missing_operands=[{"source": "table", "page": 41, "row_key": "收入明细"}],
    )
    assert cascade_root_issue_ids(item, row, {"name-on-page-41"}) == []


def test_cascade_multiple_root_candidates_without_pages_are_rejected():
    """无页码证据：多个根候选（单位缺陷分支）保守保留，不批量吞并。"""
    findings = [
        _finding("unit-1", "CMM-011", 5, "第5页金额单位重复：万万元"),
        _finding("unit-2", "CMM-011", 5, "第5页金额单位重复：万万元"),
    ]
    row = {"rule_findings": findings}
    item = _obligation(
        detail="金额单位重复（万万元），相关合计尚未完成",
        reason_codes=["operand_missing"],
    )
    assert cascade_root_issue_ids(item, row, {"unit-1", "unit-2"}) == []


def test_v33251_cascade_requires_same_page_not_just_same_stage():
    item = _obligation(
        detail="V33-251:budget:item_sum 第40页乙科目预算未提取；第41页另一表的读取映射失败",
        checkers=["V33-251", "V33-106"],
        reason_codes=["item_disclosure_missing", "table_mapping_incomplete"],
        blocked_by=[{"ref": "V33-251:budget:item_sum", "reason_code": "operand_missing"}],
        missing_operands=[
            {"source": "text", "page": 40, "row_key": "乙科目"},
            {"source": "table", "page": 41, "row_key": "独立科目"},
        ],
    )
    finding = _finding(
        "budget-on-page-5", "V33-251", 5, "第5页甲科目年初预算未逐项列示",
        checklist_item="C-11", subject_name="甲科目",
    )
    finding["location"].pop("defect_type", None)
    row = {"rule_findings": [finding]}
    assert cascade_root_issue_ids(item, row, {"budget-on-page-5"}) == []
    assert blocking_coverage_obligations([item], row, visible_issue_ids={"budget-on-page-5"}) == [item]


def test_structured_technical_item_does_not_block_background_gate():
    structured = {"review_items": [{
        "id": "structured-review:table-1", "type": "ambiguous_table_schema",
        "table_code": "FIN_05_general_expenditure", "page": 14,
        "message": "表格列映射尚未完成", "severity": "manual_review",
    }]}
    problems = collect_review_problems(
        job_uuid="probe-structured", structured_ingest=structured,
    )
    assert problems == []
    assert _problem_blockers(problems, {}) == []


def test_unverified_legacy_finding_is_candidate_not_gate_blocker():
    finding = {
        "id": "legacy-1", "rule_id": "CMM-017", "source": "rule", "severity": "warn",
        "message": "第2页单位名称多字", "location": {"page": 2},
        "evidence_status": "complete",
    }
    problems = collect_review_problems(job_uuid="legacy", rule_findings=[finding])
    assert len(problems) == 1 and problems[0].is_formal is False
    assert _problem_blockers(problems, {}) == []


def test_review_summary_pending_matches_decisions_and_ignored_set():
    issue = {
        "id": "issue-1", "rule_id": "CMM-017", "source": "rule", "severity": "warn",
        "message": "第2页单位名称多字", "location": {"page": 2},
        "evidence_status": "complete",
        "evidence_verification": {"status": "verified", "source_identity_complete": True},
    }
    base = {
        "job_id": "probe-job", "status": "done", "analysis_revision": "rev-probe",
        "check_coverage": {"unresolved_total": 0, "instances": []},
        "result": {"rule_findings": [issue]},
    }
    pending = build_review_report(base)
    assert pending["review_summary"]["pending_formal_issue_total"] == 1
    needs_review = build_review_report({
        **base,
        "human_decisions": [{"issue_id": "issue-1", "status": "needs_review"}],
    })
    assert needs_review["review_summary"]["pending_formal_issue_total"] == 1
    ignored = build_review_report({**base, "ignored_issue_ids": ["issue-1"]})
    assert ignored["review_summary"]["formal_issue_total"] == 0
    assert ignored["review_summary"]["pending_formal_issue_total"] == 0


def test_table_reading_root_covers_same_table_across_pages():
    """跨页同一张表：读取异常根问题引用一页时仍承接该表其他页的缺数。"""
    item = _obligation(
        detail="第5页、第6页「FIN_02_income」金额尚未取到，表格读取映射未完成",
        reason_codes=["table_mapping_incomplete"],
        missing_operands=[
            {"source": "table", "page": 5, "table_code": "FIN_02_income"},
            {"source": "table", "page": 6, "table_code": "FIN_02_income"},
        ],
    )
    structured = {"review_items": [{
        "id": "structured-income-root", "type": "table_mapping_incomplete",
        "table_code": "FIN_02_income", "page": 5,
        "message": "表格列映射尚未完成", "severity": "manual_review",
    }]}
    roots = cascade_root_issue_ids(
        item, {"rule_findings": []}, {"structured-income-root"},
        structured_ingest=structured,
    )
    assert roots == ["structured-income-root"]


def test_enum_embedded_group_total_is_not_a_component():
    """枚举句内夹带的组级合计不进入共享分项，组算术与章节加总保持正控语义。"""
    text = SHARED_TEXT.replace(
        "其中：机关事业单位基本养老保险缴费支出 985.78 万元，",
        "其中：机关事业单位基本养老保险缴费支出 985.78 万元，年初预算总计 2064.21 万元，",
    )
    receipts, _ = _run_shared(text)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "pass"
    items = group.get("component_items") or []
    assert all(not str(item.get("name") or "").endswith(("总计", "合计"))
               for item in items if isinstance(item, dict))
