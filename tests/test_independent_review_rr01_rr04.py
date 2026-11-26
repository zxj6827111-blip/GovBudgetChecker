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
    try:
        issues = R33251_FunctionalDetailCompleteness().apply(doc)
        receipts = {row["sub_check_id"]: row for row in doc.fact_receipts}
        messages = [issue.message for issue in issues]
    except RuleDeferred as caught:
        receipts = {row["sub_check_id"]: row for row in doc.fact_receipts}
        messages = [issue.message for issue in caught.partial_issues or []]
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


# ---- 2026-11-17 第三次独立复核：组算术可证明性与显示精度 ----

INCOMPLETE_STAGE_TEXT = (
    "（三）一般公共预算财政拨款支出决算具体情况\n"
    "一般公共预算财政拨款支出年初预算为 11645.36 万元，支出决算为 11685.76 万元，"
    "完成年初预算的 100.27%。决算数大于预算数的主要原因：年中追加。其中：\n"
    "1、教育支出（类）职业教育（款）中等职业教育（项）。主要用于：中专教育支出。"
    "年初预算为 8603.44 万元，支出决算为 8643.84 万元。\n"
    "2、社会保障和就业支出（类）行政事业单位养老支出（款）机关事业单位基本养老保险缴费支出（项）、"
    "机关事业单位职业年金缴费支出（项）、其他行政事业单位养老支出（项）。主要用于教职工养老。"
    "其中：机关事业单位基本养老保险缴费支出年初预算为 985.78 万元，机关事业单位职业年金 492.39 万元，"
    "其他行政事业单位离退休支出 10.2 万元，事业单位离退休 566.77 万元。"
    "年初预算数总计 2055.14 万元；支出决算为 2055.14 万元，完成年初预算的 99.56%。\n"
    "3、卫生健康支出（类）行政事业单位医疗（款）事业单位医疗（项）。年初预算为 556.19 万元，"
    "支出决算为 556.19 万元。\n"
    "4、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算为 430.59 万元，"
    "支出决算为 430.59 万元。\n"
)


ROUNDING_TEXT = SHARED_TEXT.replace(
    "支出决算为 2055.14 万元", "支出决算为 2055.17 万元"
)


def test_incomplete_stage_components_do_not_produce_amount_mismatch():
    """分项阶段未全部证明时，组算术只能记未完成，不得报具体差额。"""
    receipts, messages = _run_shared(INCOMPLETE_STAGE_TEXT)
    groups = [
        value for key, value in receipts.items()
        if key.startswith("V33-251:") and ":group:" in key
    ]
    assert groups, "应保留组级回执，说明为何无法核对"
    assert all(value["status"] != "fail" for value in groups)
    assert any("未写明金额阶段" in str(value.get("incomplete_reason") or "")
               for value in groups)
    assert not any("组内分项合计" in message and "不一致" in message
                   for message in messages)


def test_complete_explicit_stage_mismatch_still_reports_difference():
    """正控：阶段明确的四个预算分项与组级预算确实不一致时仍报差额。"""
    receipts, messages = _run_shared(SHARED_TEXT.replace(
        "其中：机关事业单位基本养老保险缴费支出 985.78 万元，"
        "机关事业单位职业年金 492.39 万元，"
        "其他行政事业单位离退休支出 10.2 万元，事业单位离退休 566.77 万元。",
        "其中：机关事业单位基本养老保险缴费支出年初预算为 985.78 万元，"
        "机关事业单位职业年金年初预算为 492.39 万元，"
        "其他行政事业单位离退休支出年初预算为 10.2 万元，"
        "事业单位离退休年初预算为 566.77 万元。",
    ))
    budget_group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:budget:group:")
    )
    assert budget_group["status"] == "fail"
    assert "差9.07" in str(budget_group.get("incomplete_reason") or "")
    assert any("差9.07" in message for message in messages)


def test_display_precision_rounding_is_not_an_amount_mismatch():
    """原文显示精度足以解释的差额（0.03）不得报金额不符。"""
    receipts, messages = _run_shared(ROUNDING_TEXT)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "pass"
    assert not any("差0.03" in message for message in messages)


def test_difference_beyond_display_precision_still_reported():
    """超出原文显示精度包络的差额（0.16）仍须报出正确差额。"""
    receipts, messages = _run_shared(
        ROUNDING_TEXT.replace("支出决算为 2055.17 万元", "支出决算为 2055.30 万元")
    )
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "fail"
    assert "差0.16" in str(group.get("incomplete_reason") or "")
    assert any("差0.16" in message for message in messages)


def test_component_amount_without_unit_does_not_produce_partial_sum_mismatch():
    """枚举里有金额缺单位、没能归入分项时，同样只记未完成，不算部分和差额。"""
    text = SHARED_TEXT.replace(
        "机关事业单位职业年金 492.39 万元，", "机关事业单位职业年金 492.39，"
    )
    assert text != SHARED_TEXT
    receipts, messages = _run_shared(text)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "insufficient_data"
    assert "未能归入分项" in str(group.get("incomplete_reason") or "")
    assert not any("组内分项合计" in message and "不一致" in message
                   for message in messages)
    assert not any("差492.39" in message for message in messages)


def test_component_name_without_amount_does_not_produce_partial_sum_mismatch():
    """枚举里科目名在、金额缺：分项集合不完整，不得拿剩余分项的和报差额。"""
    text = SHARED_TEXT.replace(
        "机关事业单位职业年金 492.39 万元，", "机关事业单位职业年金，"
    )
    assert text != SHARED_TEXT
    receipts, messages = _run_shared(text)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "insufficient_data"
    assert "未附金额的科目名" in str(group.get("incomplete_reason") or "")
    assert not any("组内分项合计" in message and "不一致" in message
                   for message in messages)


def test_trailing_component_name_without_amount_is_incomplete():
    """枚举最后一项只写科目名、没有金额：同样不得拿部分分项和报差额。"""
    text = SHARED_TEXT.replace("事业单位离退休 566.77 万元。", "事业单位离退休。")
    assert text != SHARED_TEXT
    receipts, messages = _run_shared(text)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "insufficient_data"
    assert "未附金额的科目名" in str(group.get("incomplete_reason") or "")
    assert not any("组内分项合计" in message and "不一致" in message
                   for message in messages)


# ---- 2026-11-18 第四轮独立核查返工：规则回执 + ReviewReport v3 正式清单 ----


def _run_shared_full_report(text: str):
    """沿“规则计算 → 证据核验 → ReviewReport v3 正式问题清单”全链路运行。"""
    from src.services.engine_rule_runner import EngineRuleRunner
    from src.services.evidence_guard import apply_evidence_completeness
    from src.services.rule_source_binding import bind_rule_sources

    doc = build_document("counterexample.pdf", [text], [[]], 1000)
    try:
        raw_issues = R33251_FunctionalDetailCompleteness().apply(doc)
    except RuleDeferred as caught:
        raw_issues = caught.partial_issues or []
    runner = EngineRuleRunner()
    findings, errs = runner._convert_and_locate_issues(
        raw_issues, "V33-251", doc, None, SimpleNamespace(locate=lambda item: item)
    )
    assert errs == []
    result = {"rule_findings": [item.model_dump(mode="json") for item in findings]}
    sha = "a" * 64
    bind_rule_sources(result, page_texts=[text], source_file_hash=sha)
    apply_evidence_completeness(
        result, document_page_count=1, page_texts=[text], source_file_hash=sha
    )
    report = build_review_report({
        "status": "done",
        "job_id": "rr04-r4-probe",
        "analysis_revision": "fixture",
        "result": result,
    })
    receipts = {row["sub_check_id"]: row for row in doc.fact_receipts}
    formal_group = [
        item.get("message")
        for item in report["issues"]
        if ":group:" in str((item.get("location") or {}).get("check_relation_id") or "")
    ]
    return receipts, report, formal_group


def test_missing_first_component_amount_does_not_report_difference():
    """首项只保留已知科目名、去掉金额：拦截差额，正式清单仅指出该科目未列金额。"""
    text = SHARED_TEXT.replace(
        "机关事业单位基本养老保险缴费支出 985.78 万元",
        "机关事业单位基本养老保险缴费支出",
    )
    receipts, report, formal_group = _run_shared_full_report(text)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "insufficient_data"
    assert "985.78" not in str(group.get("incomplete_reason") or "")
    assert "1069.36" not in str(group.get("incomplete_reason") or "")
    assert len(formal_group) == 1
    assert "机关事业单位基本养老保险缴费支出" in formal_group[0]
    assert "未列金额" in formal_group[0]
    assert not any("985.78" in str(item.get("message") or "") for item in report["issues"])


def test_leading_unknown_phrase_blocks_arithmetic_without_formal_defect():
    """首段不是本组已列科目（如引导语或未识别片段）：拦截差额结论，仅留内部诊断。"""
    text = SHARED_TEXT.replace(
        "其中：机关事业单位基本养老保险缴费支出 985.78 万元，",
        "其中：按人员养老项目分，",
    )
    receipts, _, formal_group = _run_shared_full_report(text)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "insufficient_data"
    assert "985.78" not in str(group.get("incomplete_reason") or "")
    assert formal_group == []


@pytest.mark.parametrize(
    "old,new,phrase,explicit_final",
    [
        (
            "机关事业单位职业年金 492.39 万元，",
            "机关事业单位职业年金 492.39 万元，其余为公用经费，",
            "其余为公用经费",
            False,
        ),
        (
            "事业单位离退休 566.77 万元。",
            "事业单位离退休 566.77 万元，以上金额均不含上年结转。",
            "以上金额均不含上年结转",
            False,
        ),
        (
            "事业单位离退休 566.77 万元。",
            "事业单位离退休 566.77 万元，以上金额均为年初预算数。",
            "以上金额均为年初预算数",
            True,
        ),
    ],
    ids=["middle_plain_comment", "scope_exclusion_comment", "conflicting_stage_comment"],
)
def test_uncertain_or_conflicting_comment_stays_insufficient_without_formal_issue(
    old, new, phrase, explicit_final
):
    """可能增减分项、改变口径或与显式阶段冲突的说明：回执为 insufficient_data，正式清单不报材料缺陷。"""
    text = SHARED_TEXT.replace(old, new)
    if explicit_final:
        for num in ("985.78", "492.39", "10.2", "566.77"):
            text = text.replace(f" {num} 万元", f"支出决算为 {num} 万元")
    receipts, report, formal_group = _run_shared_full_report(text)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "insufficient_data"
    assert formal_group == []
    assert not any(phrase in str(item.get("message") or "") for item in report["issues"])


@pytest.mark.parametrize(
    "old,new",
    [
        ("事业单位离退休 566.77 万元。", "事业单位离退休 566.77 万元，以上金额均为决算数。"),
        ("事业单位离退休 566.77 万元。", "事业单位离退休 566.77 万元（以上金额均为决算数）。"),
        ("事业单位离退休 566.77 万元。", "事业单位离退休 566.77 万元，注：以上金额均为决算数。"),
    ],
    ids=["comma_note", "parenthesized_note", "prefixed_note"],
)
def test_stage_reaffirming_note_preserves_pass_and_true_diff(old, new):
    """只重申决算口径的附注不改变分项集合：等额仍 pass，真差 9.07 仍报差额。"""
    pass_text = SHARED_TEXT.replace(old, new)
    pass_receipts, _, pass_formal_group = _run_shared_full_report(pass_text)
    pass_group = next(
        value for key, value in pass_receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert pass_group["status"] == "pass"
    assert pass_formal_group == []

    diff_text = pass_text.replace("支出决算为 2055.14 万元", "支出决算为 2064.21 万元")
    diff_receipts, _, diff_formal_group = _run_shared_full_report(diff_text)
    diff_group = next(
        value for key, value in diff_receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert diff_group["status"] == "fail"
    assert "差9.07" in str(diff_group.get("incomplete_reason") or "")
    assert len(diff_formal_group) == 1
    assert "差9.07" in diff_formal_group[0]


@pytest.mark.parametrize("with_note", [False, True], ids=["without_note", "with_note"])
def test_fully_matched_correct_and_wrong_paired_with_stage_note(with_note):
    """等额/不等额 × 有/无“以上金额均为决算数”四格对照：回执与正式清单一致。"""
    clean = (
        SHARED_TEXT
        .replace(
            "其他行政事业单位养老支出（项）。主要用于",
            "其他行政事业单位养老支出（项）、事业单位离退休（项）。主要用于",
        )
        .replace("机关事业单位职业年金 492", "机关事业单位职业年金缴费支出 492")
        .replace("其他行政事业单位离退休支出 10", "其他行政事业单位养老支出 10")
    )
    for num in ("985.78", "492.39", "10.2", "566.77"):
        clean = clean.replace(f" {num} 万元", f"支出决算为 {num} 万元")
    if with_note:
        clean = clean.replace("566.77 万元。", "566.77 万元，以上金额均为决算数。")
    wrong = clean.replace("985.78 万元", "976.71 万元")

    c_receipts, _, c_formal_group = _run_shared_full_report(clean)
    c_group = next(
        value for key, value in c_receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert c_group["status"] == "pass"
    assert c_formal_group == []

    w_receipts, _, w_formal_group = _run_shared_full_report(wrong)
    w_group = next(
        value for key, value in w_receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert w_group["status"] == "fail"
    assert "差9.07" in str(w_group.get("incomplete_reason") or "")
    assert len(w_formal_group) == 1
    assert "差9.07" in w_formal_group[0]


@pytest.mark.parametrize(
    "target,replacement,expected_subject",
    [
        (
            "机关事业单位基本养老保险缴费支出 985.78 万元",
            "机关事业单位基本养老保险缴费支出 —",
            "机关事业单位基本养老保险缴费支出",
        ),
        (
            "机关事业单位职业年金 492.39 万元",
            "机关事业单位职业年金 —",
            "机关事业单位职业年金",
        ),
        (
            "事业单位离退休 566.77 万元。",
            "事业单位离退休 —。",
            None,
        ),
        (
            "机关事业单位职业年金 492.39 万元，",
            "机关事业单位职业年金 492.39 万元，—，",
            None,
        ),
    ],
    ids=["dash_first", "dash_middle", "dash_last_unmatched", "standalone_dash"],
)
def test_dash_placeholder_never_treated_as_zero_or_difference(
    target, replacement, expected_subject
):
    """金额位置写“—”：首/中/尾及独立横线均不按零计算，也不得报部分和差额。"""
    text = SHARED_TEXT.replace(target, replacement)
    receipts, report, formal_group = _run_shared_full_report(text)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "insufficient_data"
    assert not any("不一致" in str(item.get("message") or "") for item in report["issues"])
    if expected_subject is not None:
        assert len(formal_group) == 1
        assert expected_subject in formal_group[0]
        assert "未列金额" in formal_group[0]
    else:
        assert formal_group == []


def test_explicit_zero_participates_in_arithmetic_unlike_dash():
    """对照横线：显式“0 万元”按零参与算术，等额 pass、不等额仍报真实差额。"""
    zero_mismatch = SHARED_TEXT.replace("985.78 万元", "0 万元")
    receipts, _, formal_group = _run_shared_full_report(zero_mismatch)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "fail"
    assert "差985.78" in str(group.get("incomplete_reason") or "")
    assert len(formal_group) == 1
    assert "差985.78" in formal_group[0]

    zero_match = zero_mismatch.replace("支出决算为 2055.14 万元", "支出决算为 1069.36 万元")
    m_receipts, _, m_formal_group = _run_shared_full_report(zero_match)
    m_group = next(
        value for key, value in m_receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert m_group["status"] == "pass"
    assert m_formal_group == []


# ---- 2026-11-18 第六轮独立复核：句号独立附注与空格占位词 ----


@pytest.mark.parametrize("sep", ["，", "。"], ids=["comma", "period"])
def test_budget_stage_note_comma_and_period_do_not_miscompare_against_final(sep):
    """枚举四项为年初预算且附“以上金额均为年初预算数”（逗号或句号引出）：不得错报决算差额。"""
    text = (
        SHARED_TEXT
        .replace("985.78 万元", "994.85 万元")
        .replace("566.77 万元。", f"566.77 万元{sep}以上金额均为年初预算数。")
    )
    receipts, report, formal_group = _run_shared_full_report(text)
    budget_group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:budget:group:")
    )
    assert budget_group["status"] == "pass"
    assert not any(key.startswith("V33-251:final:group:") for key in receipts)
    assert formal_group == []
    assert not any("9.07" in str(item.get("message") or "") for item in report["issues"])


def test_period_budget_stage_note_still_catches_real_budget_mismatch():
    """句号引出“以上金额均为年初预算数”且分项确与年初预算合计不等时，应按预算报真实差额。"""
    text = SHARED_TEXT.replace(
        "566.77 万元。", "566.77 万元。以上金额均为年初预算数。"
    )
    receipts, _, formal_group = _run_shared_full_report(text)
    budget_group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:budget:group:")
    )
    assert budget_group["status"] == "fail"
    assert "差9.07" in str(budget_group.get("incomplete_reason") or "")
    assert formal_group == ["组内分项合计2055.14与组级合计2064.21不一致，差9.07。"]


@pytest.mark.parametrize("sep", ["，", "。"], ids=["comma", "period"])
def test_exclusion_note_comma_and_period_both_intercept_false_difference(sep):
    """“以上金额均不含上年结转”无论是逗号还是句号引出，均不得改变可比性结论。"""
    text = (
        SHARED_TEXT
        .replace(
            "其他行政事业单位养老支出（项）。主要用于",
            "其他行政事业单位养老支出（项）、事业单位离退休（项）。主要用于",
        )
        .replace("机关事业单位职业年金 492", "机关事业单位职业年金缴费支出 492")
        .replace("其他行政事业单位离退休支出 10", "其他行政事业单位养老支出 10")
        .replace("985.78 万元", "976.71 万元")
    )
    for num in ("976.71", "492.39", "10.2", "566.77"):
        text = text.replace(f" {num} 万元", f"支出决算为 {num} 万元")
    text = text.replace("566.77 万元。", f"566.77 万元{sep}以上金额均不含上年结转。")
    receipts, report, formal_group = _run_shared_full_report(text)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "insufficient_data"
    assert formal_group == []
    assert not any("9.07" in str(item.get("message") or "") for item in report["issues"])


def test_backref_note_does_not_cross_to_another_group():
    """下一编号条目的口径附注不得跨组借用到上一组枚举。"""
    text = (
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为 2164.21 万元，支出决算为 2165.14 万元。\n"
        "1、教育支出（类）职业教育（款）中等职业教育（项）、高等职业教育（项）。"
        "其中：中等职业教育 50.00 万元，高等职业教育 50.00 万元。"
        "年初预算为 100.00 万元；支出决算为 110.00 万元。\n"
        "2、社会保障和就业支出（类）行政事业单位养老支出（款）"
        "机关事业单位基本养老保险缴费支出（项）、机关事业单位职业年金缴费支出（项）、"
        "其他行政事业单位养老支出（项）。主要用于教职工养老。"
        "其中：机关事业单位基本养老保险缴费支出 994.85 万元，机关事业单位职业年金 492.39 万元，"
        "其他行政事业单位离退休支出 10.2 万元，事业单位离退休 566.77 万元。以上金额均为年初预算数。"
        "年初预算数总计 2064.21 万元；支出决算为 2055.14 万元。\n"
    )
    receipts, _, formal_group = _run_shared_full_report(text)
    first_group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert first_group["status"] == "fail"
    assert "差10.00" in str(first_group.get("incomplete_reason") or "")
    assert any("差10.00" in msg for msg in formal_group)


@pytest.mark.parametrize(
    "replacement",
    [
        "机关事业单位职业年金 —",
        "机关事业单位职业年金（待定）",
        "机关事业单位职业年金 待定",
        "机关事业单位职业年金 无",
    ],
    ids=["dash", "paren_pending", "space_pending", "space_wu"],
)
def test_placeholder_formats_consistently_flag_known_subject_missing_amount(replacement):
    """同一已知科目的横线/括号/空格占位词写法，均应指出该科目未列金额且不报差额。"""
    text = SHARED_TEXT.replace("机关事业单位职业年金 492.39 万元", replacement)
    receipts, report, formal_group = _run_shared_full_report(text)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "insufficient_data"
    assert not any("不一致" in str(item.get("message") or "") for item in report["issues"])
    assert formal_group == ["共享分项中以下科目未列金额：机关事业单位职业年金。"]


def test_unmatched_subject_with_space_placeholder_does_not_fabricate_defect():
    """不明确归属的文本加空格占位词：拦截差额，不冒充已知缺项。"""
    text = SHARED_TEXT.replace("事业单位离退休 566.77 万元。", "其他无关条目 待定。")
    receipts, report, formal_group = _run_shared_full_report(text)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "insufficient_data"
    assert formal_group == []
    assert not any("不一致" in str(item.get("message") or "") for item in report["issues"])


def test_yi_yuan_vs_wan_display_precision_envelope_in_receipt_and_formal_report():
    """0.0986 亿元半个末位为 0.5 万元足以解释 0.22；986.00 万元精度更高须报差 0.22。"""
    yi_text = SHARED_TEXT.replace("985.78 万元", "0.0986 亿元")
    yi_receipts, _, yi_formal_group = _run_shared_full_report(yi_text)
    yi_group = next(
        value for key, value in yi_receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert yi_group["status"] == "pass"
    assert yi_receipts["V33-251:final:item_sum"]["status"] == "pass"
    assert yi_formal_group == []

    wan_text = SHARED_TEXT.replace("985.78 万元", "986.00 万元")
    wan_receipts, _, wan_formal_group = _run_shared_full_report(wan_text)
    wan_group = next(
        value for key, value in wan_receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert wan_group["status"] == "fail"
    assert "差0.22" in str(wan_group.get("incomplete_reason") or "")
    assert len(wan_formal_group) == 1
    assert "差0.22" in wan_formal_group[0]


@pytest.mark.parametrize(
    "total_str,expected_status,expected_formal_count",
    [
        ("2055.22", "pass", 0),
        ("2055.25", "fail", 1),
        ("2064.21", "fail", 1),
    ],
    ids=["within_0.08_envelope", "exceeds_at_0.11", "real_error_9.07"],
)
def test_pure_wan_envelope_boundary_and_positive_control(
    total_str, expected_status, expected_formal_count
):
    """纯万元样例：0.08 在组包络内通过；0.11 与 9.07 均同时体现在回执与正式问题清单。"""
    text = SHARED_TEXT.replace("支出决算为 2055.14 万元", f"支出决算为 {total_str} 万元")
    receipts, _, formal_group = _run_shared_full_report(text)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == expected_status
    assert len(formal_group) == expected_formal_count


def test_two_item_shared_group_with_missing_first_amount_does_not_double_count():
    """双科目共享组首项缺金额（含（项）标记）：不重复加总，仍拦截差额并指出该科目未列金额。"""
    text = (
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为 1000.00 万元，支出决算为 1478.17 万元。\n"
        "1、社会保障和就业支出（类）行政事业单位养老支出（款）"
        "机关事业单位基本养老保险缴费支出（项）、机关事业单位职业年金缴费支出（项）。"
        "主要用于教职工养老。"
        "其中：机关事业单位基本养老保险缴费支出（项），"
        "机关事业单位职业年金支出决算为 492.39 万元。"
        "年初预算数总计 1000.00 万元；支出决算为 1478.17 万元。\n"
    )
    receipts, report, formal_group = _run_shared_full_report(text)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "insufficient_data"
    assert len(formal_group) == 1
    assert "机关事业单位基本养老保险缴费支出" in formal_group[0]
    assert "未列金额" in formal_group[0]
    assert not any("不一致" in str(item.get("message") or "") for item in report["issues"])


@pytest.mark.parametrize(
    "note_suffix",
    [
        "均为年初预算数。",
        "（以上金额均为年初预算数）；",
        "（以上金额均为年初预算数。）（以上金额均不含上年结转。）",
    ],
    ids=["no_lead_period", "paren_semicolon", "double_paren_with_excl"],
)
def test_trailing_backref_variants_preserve_budget_caliber(note_suffix):
    """无“以上”前导、分号衔接或连续双括号附注均应准确识别同组口径，不跨阶段错报差额。"""
    budget = SHARED_TEXT.replace("985.78 万元", "994.85 万元")
    if note_suffix.endswith("；"):
        text = budget.replace("566.77 万元。年初预算数总计", f"566.77 万元。{note_suffix}年初预算数总计")
    else:
        text = budget.replace("566.77 万元。", f"566.77 万元。{note_suffix}")
    receipts, report, formal_group = _run_shared_full_report(text)
    budget_group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:budget:group:")
    )
    if "不含" in note_suffix:
        assert budget_group["status"] == "insufficient_data"
    else:
        assert budget_group["status"] == "pass"
    assert formal_group == []
    assert not any("9.07" in str(item.get("message") or "") for item in report["issues"])


def test_paired_paren_exclusion_note_after_reaffirm_still_intercepts_diff():
    """第一句“（以上金额均为决算数。）”后紧接“（均不含上年结转。）”时仍须拦截差额。"""
    text = (
        SHARED_TEXT
        .replace(
            "其他行政事业单位养老支出（项）。主要用于",
            "其他行政事业单位养老支出（项）、事业单位离退休（项）。主要用于",
        )
        .replace("机关事业单位职业年金 492", "机关事业单位职业年金缴费支出 492")
        .replace("其他行政事业单位离退休支出 10", "其他行政事业单位养老支出 10")
        .replace("985.78 万元", "976.71 万元")
        .replace("566.77 万元。", "566.77 万元。（以上金额均为决算数。）（均不含上年结转。）")
    )
    receipts, report, formal_group = _run_shared_full_report(text)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "insufficient_data"
    assert formal_group == []
    assert not any("9.07" in str(item.get("message") or "") for item in report["issues"])


@pytest.mark.parametrize(
    "replacement",
    [
        "（机关事业单位职业年金 待定）",
        "（机关事业单位职业年金 无）",
        "机关事业单位职业年金 未知",
        "机关事业单位职业年金 待核实",
    ],
    ids=["outer_paren_pending", "outer_paren_wu", "unknown", "wait_verify"],
)
def test_wrapped_and_extended_placeholders_strip_clean_subject_name(replacement):
    """整段括号包裹或常见扩展占位词均应剥离干净，只报告已知科目名称。"""
    text = SHARED_TEXT.replace("机关事业单位职业年金 492.39 万元", replacement)
    receipts, report, formal_group = _run_shared_full_report(text)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "insufficient_data"
    assert formal_group == ["共享分项中以下科目未列金额：机关事业单位职业年金。"]
    assert not any("不一致" in str(item.get("message") or "") for item in report["issues"])


def test_subject_prefixed_prose_note_not_emitted_as_missing_subject():
    """以已知科目开头但实为文字说明的片段只留内部诊断，不得冒充未列金额科目上报。"""
    text = SHARED_TEXT.replace(
        "机关事业单位职业年金 492.39 万元",
        "机关事业单位职业年金按规定另行安排",
    )
    receipts, report, formal_group = _run_shared_full_report(text)
    group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:final:group:")
    )
    assert group["status"] == "insufficient_data"
    assert formal_group == []
    assert not any("按规定另行安排" in str(item.get("message") or "") for item in report["issues"])


def test_shared_budget_components_without_group_budget_total_accounted_in_item_sum():
    """共享“其中”逐项列明年初预算且无同组预算小计时：正确和应 pass、错和应抓差、缺项须阻断。"""
    base = (
        SHARED_TEXT
        .replace(
            "其他行政事业单位养老支出（项）。主要用于",
            "其他行政事业单位养老支出（项）、事业单位离退休（项）。主要用于",
        )
        .replace("机关事业单位职业年金 492", "机关事业单位职业年金缴费支出 492")
        .replace("其他行政事业单位离退休支出 10", "其他行政事业单位养老支出 10")
        .replace("年初预算数总计 2064.21 万元；", "")
    )
    clean_ok = (
        base.replace("985.78 万元", "994.85 万元")
        .replace("566.77 万元。", "566.77 万元。以上金额均为年初预算数。")
    )
    ok_receipts, ok_report, ok_formal = _run_shared_full_report(clean_ok)
    assert ok_receipts["V33-251:budget:item_sum"]["status"] == "pass"
    assert ok_formal == []
    assert not any("9.07" in str(item.get("message") or "") for item in ok_report["issues"])

    clean_wrong = base.replace("566.77 万元。", "566.77 万元。以上金额均为年初预算数。")
    wrong_receipts, wrong_report, _ = _run_shared_full_report(clean_wrong)
    assert wrong_receipts["V33-251:budget:item_sum"]["status"] == "fail"
    assert any("9.07" in str(item.get("message") or "") for item in wrong_report["issues"])

    partial = (
        base.replace("机关事业单位职业年金缴费支出 492.39 万元", "机关事业单位职业年金缴费支出 待定")
        .replace("566.77 万元。", "566.77 万元。以上金额均为年初预算数。")
    )
    p_receipts, p_report, p_formal = _run_shared_full_report(partial)
    assert p_receipts["V33-251:budget:item_sum"]["status"] == "insufficient_data"
    assert p_formal == ["共享分项中以下科目未列金额：机关事业单位职业年金缴费支出。"]
    assert not any("不一致" in str(item.get("message") or "") for item in p_report["issues"])


def test_inline_group_total_inside_enum_does_not_double_count():
    """句内夹带“支出决算合计/年初预算合计”时不得因 section_components 重复加总。"""
    base_clean = (
        SHARED_TEXT
        .replace(
            "其他行政事业单位养老支出（项）。主要用于",
            "其他行政事业单位养老支出（项）、事业单位离退休（项）。主要用于",
        )
        .replace("机关事业单位职业年金 492", "机关事业单位职业年金缴费支出 492")
        .replace("其他行政事业单位离退休支出 10", "其他行政事业单位养老支出 10")
    )
    ok_final = base_clean.replace(
        "事业单位离退休 566.77 万元。年初预算数总计 2064.21 万元；支出决算为 2055.14 万元",
        "事业单位离退休 566.77 万元，支出决算合计 2055.14 万元。年初预算数总计 2064.21 万元",
    )
    ok_receipts, ok_report, ok_formal = _run_shared_full_report(ok_final)
    ok_group = next(v for k, v in ok_receipts.items() if k.startswith("V33-251:final:group:"))
    assert ok_group["status"] == "pass"
    assert ok_receipts["V33-251:final:item_sum"]["status"] == "pass"
    assert ok_formal == []
    assert not any("超出总额" in str(item.get("message") or "") for item in ok_report["issues"])

    wrong_final = ok_final.replace("985.78 万元", "976.71 万元")
    wrong_receipts, wrong_report, wrong_formal = _run_shared_full_report(wrong_final)
    wrong_group = next(v for k, v in wrong_receipts.items() if k.startswith("V33-251:final:group:"))
    assert wrong_group["status"] == "fail"
    assert "差9.07" in str(wrong_group.get("incomplete_reason") or "")
    assert wrong_formal == ["组内分项合计2046.07与组级合计2055.14不一致，差9.07。"]
    assert not any("2046.07万元；分项合计超出总额" in str(item.get("message") or "") for item in wrong_report["issues"])

    ok_budget = (
        base_clean.replace("985.78 万元", "994.85 万元")
        .replace(
            "事业单位离退休 566.77 万元。年初预算数总计 2064.21 万元；",
            "事业单位离退休 566.77 万元，年初预算合计 2064.21 万元。以上金额均为年初预算数。",
        )
    )
    b_receipts, b_report, b_formal = _run_shared_full_report(ok_budget)
    b_group = next(v for k, v in b_receipts.items() if k.startswith("V33-251:budget:group:"))
    assert b_group["status"] == "pass"
    assert b_receipts["V33-251:budget:item_sum"]["status"] == "pass"
    assert b_formal == []
    assert not any("超出总额" in str(item.get("message") or "") for item in b_report["issues"])


def test_integer_zero_declaration_does_not_inflate_rounding_envelope():
    """整数“0 万元 / 0 亿元”不带舍入误差，不得吞掉 0.44 万元或 100.00 万元真实差额。"""
    diff_wan = (
        SHARED_TEXT
        .replace("985.78 万元", "0 万元")
        .replace("支出决算为 2055.14 万元", "支出决算为 1069.80 万元")
    )
    r_wan, _, f_wan = _run_shared_full_report(diff_wan)
    g_wan = next(v for k, v in r_wan.items() if k.startswith("V33-251:final:group:"))
    assert g_wan["status"] == "fail"
    assert "差0.44" in str(g_wan.get("incomplete_reason") or "")
    assert len(f_wan) == 1 and "差0.44" in f_wan[0]

    diff_yi = (
        SHARED_TEXT
        .replace("985.78 万元", "0 亿元")
        .replace("支出决算为 2055.14 万元", "支出决算为 1169.36 万元")
    )
    r_yi, _, f_yi = _run_shared_full_report(diff_yi)
    g_yi = next(v for k, v in r_yi.items() if k.startswith("V33-251:final:group:"))
    assert g_yi["status"] == "fail"
    assert "差100.00" in str(g_yi.get("incomplete_reason") or "")
    assert len(f_yi) == 1 and "差100.00" in f_yi[0]

    exact_zero = (
        SHARED_TEXT
        .replace("985.78 万元", "0 万元")
        .replace("支出决算为 2055.14 万元", "支出决算为 1069.36 万元")
    )
    r_ok, _, f_ok = _run_shared_full_report(exact_zero)
    g_ok = next(v for k, v in r_ok.items() if k.startswith("V33-251:final:group:"))
    assert g_ok["status"] == "pass"
    assert f_ok == []


@pytest.mark.parametrize(
    "variant_kind",
    [
        "ordinals",
        "share_pct",
        "comma_4_items",
        "comma_year",
        "period_four_items",
        "period_all_items",
    ],
)
def test_non_amount_numbers_and_reaffirm_variants_preserve_real_diff(variant_kind):
    """枚举内序号、占比百分比、年份项数及口径重申常见变体均不得吞掉真实的 9.07 差额。"""
    wrong = (
        SHARED_TEXT
        .replace(
            "其他行政事业单位养老支出（项）。主要用于",
            "其他行政事业单位养老支出（项）、事业单位离退休（项）。主要用于",
        )
        .replace("机关事业单位职业年金 492", "机关事业单位职业年金缴费支出 492")
        .replace("其他行政事业单位离退休支出 10", "其他行政事业单位养老支出 10")
        .replace("985.78 万元", "976.71 万元")
    )
    if variant_kind == "ordinals":
        text = (
            wrong
            .replace("其中：机关事业单位基本养老保险缴费支出", "其中：（1）机关事业单位基本养老保险缴费支出")
            .replace("，机关事业单位职业年金缴费支出", "，（2）机关事业单位职业年金缴费支出")
            .replace("，其他行政事业单位养老支出", "，（3）其他行政事业单位养老支出")
            .replace("，事业单位离退休", "，（4）事业单位离退休")
        )
    elif variant_kind == "share_pct":
        text = wrong.replace("566.77 万元。", "566.77 万元（占27.58%）。")
    elif variant_kind == "comma_4_items":
        text = wrong.replace("566.77 万元。", "566.77 万元，以上4项金额均为决算数。")
    elif variant_kind == "comma_year":
        text = wrong.replace("566.77 万元。", "566.77 万元，以上金额均为2025年决算数。")
    elif variant_kind == "period_four_items":
        text = wrong.replace("566.77 万元。", "566.77 万元。以上四项均为决算数。")
    else:
        text = wrong.replace("566.77 万元。", "566.77 万元。以上金额全部为决算数。")

    receipts, _, formal_group = _run_shared_full_report(text)
    group = next(v for k, v in receipts.items() if k.startswith("V33-251:final:group:"))
    assert group["status"] == "fail"
    assert "差9.07" in str(group.get("incomplete_reason") or "")
    assert formal_group == ["组内分项合计2046.07与组级合计2055.14不一致，差9.07。"]


def test_post_total_explicit_subitem_backref_preserves_budget_caliber_and_ignores_purpose():
    """组级合计之后明确指向“分项”的预算附注须生效，而后续用途说明不得误作回指。"""
    with_post_note = (
        SHARED_TEXT
        .replace("985.78 万元", "994.85 万元")
        .replace("完成年初预算的 99.56%。", "完成年初预算的 99.56%。以上分项金额均为年初预算数。")
    )
    receipts, report, formal_group = _run_shared_full_report(with_post_note)
    budget_group = next(v for k, v in receipts.items() if k.startswith("V33-251:budget:group:"))
    assert budget_group["status"] == "pass"
    assert not any(k.startswith("V33-251:final:group:") for k in receipts)
    assert formal_group == []
    assert not any("9.07" in str(item.get("message") or "") for item in report["issues"])

    with_purpose_after = SHARED_TEXT.replace(
        "完成年初预算的 99.56%。",
        "完成年初预算的 99.56%。以上支出主要用于教职工养老保障。",
    )
    p_receipts, _, p_formal = _run_shared_full_report(with_purpose_after)
    p_group = next(v for k, v in p_receipts.items() if k.startswith("V33-251:final:group:"))
    assert p_group["status"] == "pass"
    assert p_formal == []


@pytest.mark.parametrize(
    "replacement",
    [
        "机关事业单位职业年金 N/A",
        "机关事业单位职业年金（N/A）",
    ],
    ids=["space_na", "paren_na"],
)
def test_na_placeholder_cleanly_flags_known_subject_missing_amount(replacement):
    """英文占位词 N/A 应按缺金额占位剥离并仅指向已知科目，不得计算虚假差额。"""
    text = SHARED_TEXT.replace("机关事业单位职业年金 492.39 万元", replacement)
    receipts, report, formal_group = _run_shared_full_report(text)
    group = next(v for k, v in receipts.items() if k.startswith("V33-251:final:group:"))
    assert group["status"] == "insufficient_data"
    assert formal_group == ["共享分项中以下科目未列金额：机关事业单位职业年金。"]
    assert not any("不一致" in str(item.get("message") or "") for item in report["issues"])


@pytest.mark.parametrize(
    "sep,note",
    [
        ("，", "以上金额均为年初预算数，同比持平。"),
        ("。", "以上金额均为年初预算数，同比持平。"),
        ("。", "以上金额均为年初预算数，较上年持平。"),
        ("。", "同比持平，以上金额均为年初预算数。"),
        ("。", "以上金额均为年初预算数（同比持平）。"),
    ],
    ids=[
        "comma_budget_plus_yoy",
        "period_budget_plus_yoy",
        "period_budget_plus_compare_last_year",
        "period_yoy_before_budget",
        "period_budget_paren_yoy",
    ],
)
def test_budget_stage_note_with_yoy_clause_does_not_miscompare_against_final(sep, note):
    """句号引出“以上金额均为年初预算数，同比持平。”等复合附注时不得丢弃口径而误报决算差额。"""
    text = (
        SHARED_TEXT
        .replace("985.78 万元", "994.85 万元")
        .replace("566.77 万元。", f"566.77 万元{sep}{note}")
    )
    receipts, report, formal_group = _run_shared_full_report(text)
    assert not any(
        key.startswith("V33-251:final:group:") and value.get("status") == "fail"
        for key, value in receipts.items()
    )
    assert formal_group == []
    assert not any("9.07" in str(item.get("message") or "") for item in report["issues"])
    assert not any("不一致" in str(item.get("message") or "") for item in report["issues"])
    budget_group = next(
        value for key, value in receipts.items()
        if key.startswith("V33-251:budget:group:")
    )
    assert budget_group["status"] == "pass"
    assert not any(key.startswith("V33-251:final:group:") for key in receipts)
    if note == "以上金额均为年初预算数，同比持平。":
        assert report["issues"] == []


def test_pure_yoy_sentence_after_period_still_preserves_real_final_diff():
    """仅写同比说明（无口径或分项豁免子句）时不得吞掉真实 9.07 决算差额。"""
    wrong = (
        SHARED_TEXT
        .replace(
            "其他行政事业单位养老支出（项）。主要用于",
            "其他行政事业单位养老支出（项）、事业单位离退休（项）。主要用于",
        )
        .replace("机关事业单位职业年金 492", "机关事业单位职业年金缴费支出 492")
        .replace("其他行政事业单位离退休支出 10", "其他行政事业单位养老支出 10")
        .replace("985.78 万元", "976.71 万元")
        .replace("566.77 万元。", "566.77 万元。以上支出同比持平。")
    )
    receipts, _, formal_group = _run_shared_full_report(wrong)
    group = next(v for k, v in receipts.items() if k.startswith("V33-251:final:group:"))
    assert group["status"] == "fail"
    assert "差9.07" in str(group.get("incomplete_reason") or "")
    assert formal_group == ["组内分项合计2046.07与组级合计2055.14不一致，差9.07。"]


def test_post_total_subitem_budget_backref_with_yoy_clause_prevents_false_final_diff():
    """组级合计之后出现“以上分项金额均为年初预算数，同比持平。”时仍须保留预算口径，不误报 9.07。"""
    text = (
        SHARED_TEXT
        .replace("985.78 万元", "994.85 万元")
        .replace("完成年初预算的 99.56%。", "完成年初预算的 99.56%。以上分项金额均为年初预算数，同比持平。")
    )
    receipts, report, formal_group = _run_shared_full_report(text)
    budget_group = next(v for k, v in receipts.items() if k.startswith("V33-251:budget:group:"))
    assert budget_group["status"] == "pass"
    assert not any(k.startswith("V33-251:final:group:") for k in receipts)
    assert formal_group == []
    assert not any("9.07" in str(item.get("message") or "") for item in report["issues"])


@pytest.mark.parametrize("sep", ["，", "。"], ids=["comma", "period"])
@pytest.mark.parametrize(
    "yoy_suffix",
    ["", "，同比持平", "，较上年持平", "，与上年基本持平", "（同比持平）"],
    ids=["plain", "yoy_flat", "yoy_compare", "yoy_basic_flat", "yoy_paren"],
)
@pytest.mark.parametrize("is_mismatch", [False, True], ids=["equal", "mismatch"])
def test_explicit_budget_items_preserve_arithmetic_with_or_without_yoy_note(
    sep, yoy_suffix, is_mismatch
):
    """同口径四项“年初预算为”配齐时，是否附带“同比持平”及使用逗号或句号都不得改变加总结果。"""
    first_val = "985.78" if is_mismatch else "994.85"
    text = (
        SHARED_TEXT
        .replace(
            "其他行政事业单位养老支出（项）。主要用于",
            "其他行政事业单位养老支出（项）、事业单位离退休（项）。主要用于",
        )
        .replace(
            "其中：机关事业单位基本养老保险缴费支出 985.78 万元，机关事业单位职业年金 492.39 万元，"
            "其他行政事业单位离退休支出 10.2 万元，事业单位离退休 566.77 万元。",
            f"其中：机关事业单位基本养老保险缴费支出年初预算为 {first_val} 万元，"
            "机关事业单位职业年金缴费支出年初预算为 492.39 万元，"
            "其他行政事业单位养老支出年初预算为 10.2 万元，"
            f"事业单位离退休年初预算为 566.77 万元{sep}以上金额均为年初预算数{yoy_suffix}。",
        )
    )
    receipts, report, formal_group = _run_shared_full_report(text)
    budget_group = next(v for k, v in receipts.items() if k.startswith("V33-251:budget:group:"))
    assert not any(k.startswith("V33-251:final:group:") for k in receipts)
    if is_mismatch:
        assert budget_group["status"] == "fail"
        assert "差9.07" in str(budget_group.get("incomplete_reason") or "")
        assert formal_group == ["组内分项合计2055.14与组级合计2064.21不一致，差9.07。"]
    else:
        assert budget_group["status"] == "pass"
        assert formal_group == []
        assert report["issues"] == []


@pytest.mark.parametrize(
    "unsafe_note",
    [
        "以上金额均为年初预算数，不含上年结转。",
        "以上金额均为年初预算数，其余同比持平。",
        "以上金额均为年初预算数（不含专项）。",
        "以上金额均为年初预算数，另行安排。",
    ],
    ids=["excl_carryover", "rest_yoy", "paren_excl", "separate_arrange"],
)
def test_scope_or_omission_note_alongside_explicit_budget_still_blocks_arithmetic(unsafe_note):
    """范围改变或存在剩余未列项的附注仍必须保守拦截，不能因放行单纯同比而误放行。"""
    text = (
        SHARED_TEXT
        .replace(
            "其他行政事业单位养老支出（项）。主要用于",
            "其他行政事业单位养老支出（项）、事业单位离退休（项）。主要用于",
        )
        .replace(
            "其中：机关事业单位基本养老保险缴费支出 985.78 万元，机关事业单位职业年金 492.39 万元，"
            "其他行政事业单位离退休支出 10.2 万元，事业单位离退休 566.77 万元。",
            "其中：机关事业单位基本养老保险缴费支出年初预算为 985.78 万元，"
            "机关事业单位职业年金缴费支出年初预算为 492.39 万元，"
            "其他行政事业单位养老支出年初预算为 10.2 万元，"
            f"事业单位离退休年初预算为 566.77 万元。{unsafe_note}",
        )
    )
    receipts, _, formal_group = _run_shared_full_report(text)
    budget_group = next(v for k, v in receipts.items() if k.startswith("V33-251:budget:group:"))
    assert budget_group["status"] == "insufficient_data"
    assert formal_group == []
