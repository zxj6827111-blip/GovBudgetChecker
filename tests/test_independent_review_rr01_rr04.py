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
