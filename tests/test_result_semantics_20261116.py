"""2026-11-16 结果语义契约：技术诊断退出业务问题队列。

被测产品行为：
- 新报告 schema_version=3，未完成检查只进入 diagnostics，不进入 manual_review；
- cause_kind 由结构化原因码/根问题关联决定，不从中文提示猜；
- processing_summary 分开表达执行状态与覆盖状态；
- review_summary 统一正式问题、候选与签字口径；
- 历史 v2 记录保留 legacy_unknown，不自动升格为材料问题。
"""

from __future__ import annotations

from src.schemas.result_semantics import (
    CAUSE_EXECUTION_FAILURE,
    CAUSE_EXTRACTION_FAILURE,
    CAUSE_IDENTITY_CONFLICT,
    CAUSE_LEGACY_UNKNOWN,
    CAUSE_UPSTREAM_MATERIAL_DEFECT,
    annotate_diagnostic,
    cause_kind_for_codes,
    processing_summary,
)
from src.schemas.review_report import build_review_report


def _ledger(*instances):
    return {
        "applicable_total": len(instances),
        "completed_total": 0,
        "not_applicable_total": 0,
        "unresolved_total": len(instances),
        "blocking_total": len(instances),
        "by_reason": {"insufficient_data": len(instances)},
        "by_group": [],
        "instances": list(instances),
    }


def _unfinished(**overrides):
    item = {
        "obligation_id": "OBL-TXT-T5",
        "group_id": "TABLE_TEXT",
        "group_title": "表文关系",
        "title": "一般公共预算支出决算表 与 功能分类说明一致",
        "status": "insufficient_data",
        "reason": "insufficient_data",
        "reason_label": "取数不足",
        "detail": "说明文本中未提取到功能分类科目提及",
        "reason_codes": ["operand_missing"],
        "blocked_by": [],
        "missing_operands": [{
            "source": "table", "table_code": "FIN_05_general_public_expenditure",
            "role": "total_actual/决算操作数", "page": 14, "row_key": "2013499",
            "column": "total_actual", "fund_scope": None, "stage": "决算", "year": 2024,
            "bbox": None,
        }],
        "candidates": [],
        "next_action": "system_recheck",
        "root_cause_key": "rc:test",
        "depends_on": ["table:FIN_05"],
        "checkers": ["V33-227"],
    }
    item.update(overrides)
    return item


def test_cause_kind_is_structured_not_text_inferred():
    assert cause_kind_for_codes(["operand_missing"]) == CAUSE_EXTRACTION_FAILURE
    assert cause_kind_for_codes(["subject_conflict"]) == CAUSE_IDENTITY_CONFLICT
    assert cause_kind_for_codes(["execution_error"]) == CAUSE_EXECUTION_FAILURE
    assert cause_kind_for_codes(["operand_missing"], schema_version=2) == CAUSE_LEGACY_UNKNOWN
    upstream = annotate_diagnostic(
        {"reason_codes": ["operand_missing"]}, root_issue_ids=["V33-222-17"],
    )
    assert upstream["cause_kind"] == CAUSE_UPSTREAM_MATERIAL_DEFECT
    assert upstream["root_issue_ids"] == ["V33-222-17"]


def test_unfinished_check_goes_to_diagnostics_not_manual_review():
    report = build_review_report({
        "job_id": "job-1",
        "status": "review_required",
        "analysis_revision": "rev-1",
        "check_coverage": _ledger(_unfinished()),
        "rule_findings": [],
        "issues": {"critical": [], "error": [], "warn": [], "info": [], "tips": []},
    })
    assert report["schema_version"] == 3
    assert not [item for item in report["manual_review"]
                if item.get("record_type") == "unfinished_check"]
    diagnostic = next(item for item in report["diagnostics"]
                      if item.get("record_type") == "unfinished_check")
    assert diagnostic["cause_kind"] == CAUSE_EXTRACTION_FAILURE
    assert diagnostic["obligation_id"] == "OBL-TXT-T5"
    assert report["processing_summary"]["execution_state"] == "completed"
    assert report["processing_summary"]["coverage_state"] == "partial"
    assert report["processing_summary"]["technical_diagnostic_total"] == 1
    assert report["review_summary"]["formal_issue_total"] == 0


def test_legacy_unfinished_without_codes_is_legacy_unknown():
    report = build_review_report({
        "job_id": "job-legacy",
        "status": "review_required",
        "check_coverage": _ledger(_unfinished(
            reason_codes=[], missing_operands=[], blocked_by=[],
        )),
        "rule_findings": [],
        "issues": {"critical": [], "error": [], "warn": [], "info": [], "tips": []},
    })
    diagnostic = next(item for item in report["diagnostics"]
                      if item.get("record_type") == "unfinished_check")
    assert diagnostic["cause_kind"] == CAUSE_LEGACY_UNKNOWN


def test_processing_summary_never_claims_complete_without_coverage():
    summary = processing_summary(
        status="done", obligation_coverage=None, diagnostics=[], formal_issue_total=0,
    )
    assert summary["execution_state"] == "completed"
    assert summary["coverage_state"] == "unknown"
    assert summary["check_complete"] is False


def test_unit_defect_root_marks_obligation_as_upstream_material_defect():
    report = build_review_report({
        "job_id": "job-unit",
        "status": "review_required",
        "analysis_revision": "rev-unit",
        "check_coverage": _ledger(_unfinished(
            detail="其他广播电视教育支出条目决算金额单位书写有误，本项金额核对未完成",
            reason_codes=["text_statement_unparsed"],
            missing_operands=[{
                "source": "text", "section": "支出决算具体情况说明", "page": 32,
                "row_key": "其他广播电视教育支出", "column": "决算金额", "stage": "决算",
            }],
        )),
        "result": {"rule_findings": [{
            "id": "V33-222-17", "source": "rule", "rule_id": "V33-222",
            "severity": "warn",
            "message": "说明第32页科目决算金额单位重复：“28,146.97万万元”。",
            "location": {"page": 32, "defect_type": "amount_unit_duplicated"},
            "evidence_status": "complete",
            "evidence_verification": {"status": "verified", "source_identity_complete": True},
        }]},
        "issues": {"critical": [], "error": [], "warn": [], "info": [], "tips": []},
    })
    diagnostic = next(item for item in report["diagnostics"]
                      if item.get("record_type") == "unfinished_check")
    assert diagnostic["cause_kind"] == CAUSE_UPSTREAM_MATERIAL_DEFECT
    assert diagnostic["root_issue_ids"] == ["V33-222-17"]
    # 正式问题仍只有一条，不受未完成关系影响；计算未完成事实也保留。
    assert report["processing_summary"]["formal_issue_total"] == 1
    assert report["processing_summary"]["material_defect_blocked_total"] == 1
