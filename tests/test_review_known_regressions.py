"""Regressions observed in uploaded reports: counts, native claims and T7."""
import asyncio

import pytest

from src.engine.fact_context import FactContext
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import (
    R33110_BudgetVsFinal_TextConsistency, R33224_Narrative7_T7,
    R33240_Table2_IncomeAdvancedCheck, R33NarrativeIndicatorRepeat,
    _unique_native_row_quote, build_document,
)
from src.schemas.issues import JobContext
from src.schemas.review_report import build_review_report, review_report_counts
from src.services.engine_rule_runner import EngineRuleRunner
from src.services.evidence_guard import apply_evidence_completeness
from src.services.rule_source_binding import bind_rule_sources
from tests.remediation.test_native_three_public_ratios import document


def test_material_counts_keep_tips_and_review_reasons_separate():
    findings = [{"id": str(index), "severity": severity, "source": "rule",
                 **({"evidence_status": "complete", "evidence_verification": {
                     "status": "verified", "source_identity_complete": True}}
                    if index < 3 else {})}
                for index, severity in enumerate(["high", "medium", "medium",
                                                 "manual_review", "manual_review", "manual_review",
                                                 "info", "info", "info"])]
    payload = {"job_id": "counts", "result": {"rule_findings": findings},
               "review_reasons": [{"code": str(i)} for i in range(3)]}
    report = build_review_report(payload)
    assert report["conclusion"]["issue_total"] == 3
    assert review_report_counts(report) == {
        "review_issue_total": 3, "review_candidate_total": 3, "formal_issue_total": 3,
        "tip_total": 3, "review_notice_total": 3,
    }
    assert review_report_counts(report, ["0"])["review_issue_total"] == 2
    legacy = build_review_report({"job_id": "legacy", "result": {"rule_findings": [
        {"severity": "info"}, {"severity": "manual_review"},
    ]}})
    assert review_report_counts(legacy)["review_issue_total"] == 0
    assert review_report_counts(legacy)["review_candidate_total"] == 1
    assert review_report_counts(legacy)["tip_total"] == 1


def test_income_table_row_quote_uses_unique_literal_native_page_text():
    row = ["201", "一般公共服务支出", "140995.63", "140995.63", "0.00",
           "0.00", "0.00", "0.00", "14.05"]
    pages = ["表头\n201\n一般公共服务支出\n140,995.63\n140,995.63\n0.00\n0.00\n0.00\n0.00\n14.05\n"]
    match = R33240_Table2_IncomeAdvancedCheck._native_row_quote(row, pages)
    assert match is not None
    page, quote = match
    assert page == 1
    assert quote == "201\n一般公共服务支出\n140,995.63\n140,995.63\n0.00\n0.00\n0.00\n0.00\n14.05"
    assert R33240_Table2_IncomeAdvancedCheck._native_row_quote(row, pages * 2) is None


def test_dual_amount_within_default_tolerance_is_a_tip():
    rule = R33NarrativeIndicatorRepeat()
    issues = rule._check_dual_amounts("增加133.07（133.08）万元", [0])
    assert len(issues) == 1
    assert issues[0].severity == "info"
    assert "可能为取整误差" in issues[0].message


def test_basic_expenditure_rows_bind_literal_cells_from_the_source_page():
    page = "一般公共预算财政拨款基本支出决算表\n302\n商品和服务支出\n16,262.05\n"
    quote = _unique_native_row_quote(
        ["302", "", "商品和服务支出", "16,262.05"], [page], 1,
    )
    assert quote == "302\n商品和服务支出\n16,262.05"


def test_verified_basic_expense_finding_absorbs_duplicate_detail_tips():
    verified = {
        "id": "summary", "rule_id": "V33-117", "severity": "medium", "page_number": 17,
        "evidence_status": "complete",
        "evidence_verification": {"status": "verified", "source_identity_complete": True},
        "check_relation_ids": ["V33-243:economic:class:302", "V33-243:economic:class:310"],
    }
    tips = [
        {"id": "detail-302", "rule_id": "V33-243", "severity": "info", "page_number": 17,
         "check_relation_id": "V33-243:economic:class:302"},
        {"id": "detail-310", "rule_id": "V33-243", "severity": "info", "page_number": 17,
         "check_relation_id": "V33-243:economic:class:310"},
        {"id": "rounding", "rule_id": "V33-117", "severity": "info", "page_number": 17},
    ]
    report = build_review_report({"job_id": "basic-expense", "result": {"rule_findings": [verified, *tips]}})
    assert [item["id"] for item in report["tips"]] == ["rounding"]


def test_same_page_summary_error_does_not_hide_independent_class_rounding():
    summary = {"id": "summary", "rule_id": "V33-117", "severity": "medium", "page_number": 17,
               "check_relation_id": "V33-117:economic:summary:人员经费合计",
               "evidence_status": "complete",
               "evidence_verification": {"status": "verified", "source_identity_complete": True}}
    detail = {"id": "class-301", "rule_id": "V33-243", "severity": "info", "page_number": 17,
              "check_relation_id": "V33-243:economic:class:301"}
    report = build_review_report({"job_id": "same-page", "result": {"rule_findings": [summary, detail]}})
    assert [item['id'] for item in report['tips']] == ['class-301']


def test_unverified_rounding_residual_is_a_tip_not_an_issue_candidate():
    residual = {
        "id": "rounding-residual", "rule_id": "V33-120", "severity": "manual_review",
        "message": "相差0.02万元，可能为显示取整误差。",
    }
    report = build_review_report({"job_id": "rounding", "result": {"rule_findings": [residual]}})
    assert report["issues"] == []
    assert report["tips"][0]["id"] == "rounding-residual"
    counts = review_report_counts(report)
    assert counts["review_issue_total"] == 0
    assert counts["review_candidate_total"] == 0
    assert counts["tip_total"] == 1


def test_repeated_direction_sentence_binds_to_its_own_amounts():
    pages = ["（三）一般公共预算财政拨款支出决算具体情况\n"
             "年初预算为0万元，支出决算为86.64万元。\n"
             "决算数大于预算数的主要原因：按实际发生数支出。\n"
             "年初预算为24.00万元，支出决算为22.17万元。\n"
             "决算数大于预算数的主要原因：按实际发生数支出。"]
    doc = build_document("repeated.pdf", pages, [[]], 0)
    rule = R33110_BudgetVsFinal_TextConsistency()
    issues = rule.apply(doc)
    assert len(issues) == 1
    runner = EngineRuleRunner()
    finding = runner._issue_to_finding(issues[0], rule_id=rule.code, document=doc).model_dump()
    result = {"rule_findings": [finding]}
    summary = bind_rule_sources(result, page_texts=pages, source_file_hash="a" * 64)
    assert summary["bound_rule_findings"] == 1
    quote = finding["evidence"][0]["quote"]
    assert "24.00" in quote and "22.17" in quote and "86.64" not in quote
    assert all(ref["source_identity_status"] == "verified" for ref in finding["location"]["table_refs"])
    assert finding["evidence_status"] is None  # Optional Pydantic field is present but unset.
    apply_evidence_completeness(result, document_page_count=1,
                                page_texts=pages, source_file_hash="a" * 64)
    assert finding["evidence_status"] == "complete"
    assert finding["evidence_verification"]["source_identity_complete"] is True


@pytest.mark.parametrize("artifact_present", [True, False])
def test_t7_thousands_amount_and_yoy_change_are_not_false_mismatches(artifact_present):
    doc = document(
        "公务用车购置及运行维护费支出决算3,242.84万元。\n"
        "公务用车购置及运行维护费支出决算增加242.84万元。",
        budget="3326.08", actual="3242.84")
    if not artifact_present:
        doc.parse_artifact = None
    with pytest.raises(RuleDeferred) as signal:
        R33224_Narrative7_T7().apply(doc)
    assert signal.value.partial_issues == []
    vehicle_receipt = next(r for r in doc.fact_receipts
                           if any(role in r['sub_check_id'] for role in ('car_total','vehicle_purchase_operation')))
    assert vehicle_receipt["status"] == "pass"


def test_genuine_t7_mismatch_uses_body_page_and_semantic_actual_cell():
    doc = document("公务用车购置及运行维护费支出决算3,242.84万元。", actual="3400")
    with pytest.raises(RuleDeferred) as signal:
        R33224_Narrative7_T7().apply(doc)
    assert len(signal.value.partial_issues) == 1
    issue = signal.value.partial_issues[0]
    assert issue.location["page"] == 2
    assert issue.location["operand_refs"][0]["budget_stage"] == "决算"
    assert issue.location["operand_refs"][0]["value"] == "3400"
    assert "3,242.84" in issue.location["table_refs"][0]["span"]


def test_dual_runner_preserves_current_facts_for_t7_without_reparsing():
    context = FactContext([], "a" * 64, "current")
    job = JobContext(job_id="run", pdf_path="run.pdf", page_texts=["甲局"],
                     fact_context=context.to_dict())
    runner = EngineRuleRunner()
    doc = asyncio.run(runner._prepare_document(job))
    assert doc.fact_context.parse_run_uid == "current"
    assert doc.fact_context.version_token == context.version_token
