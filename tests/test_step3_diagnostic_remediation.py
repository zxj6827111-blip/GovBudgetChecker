"""Step 3 专项回归测试：材料错误直报、规则修复、证据门禁与自动复核待签分离。"""

from __future__ import annotations

import re
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Dict, List

from api import main as api_main
from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
from src.engine.checklist_text_rules import CMM011_AmountUnitWan, CMM012_YearExpression
from src.engine.diagnostic_fields import lookup_failure
from src.engine.native_empty_operands import NativeBlankTerm
from src.engine.native_final_disclosure import _record_text_gap
from src.engine.native_summary_checks import _pick as summary_pick
from src.engine.rule_outcome import RuleOutcomeSignal
from src.engine.rules_v33 import build_document
from src.schemas.material_slot import (
    STATUS_REASON_AWAITING_SIGNOFF,
    STATUS_REASON_FINDINGS_PENDING,
)
from src.services.evidence_guard import (
    EVIDENCE_STATUS_COMPLETE,
    apply_evidence_completeness,
    is_formal_finding,
)
from src.services.material_slot_status import ANALYSIS_DONE, REVIEW_REQUIRED, derive_slot_status
from src.services.obligation_finding_coverage import cascade_root_issue_ids
from src.services.review_context_query import ReviewAnalysis
from src.services.review_lifecycle_service import build_worklist_review_summary


def _run_rule(rule, doc) -> List[Any]:
    try:
        return rule.apply(doc) or []
    except RuleOutcomeSignal as signal:
        return list(getattr(signal, "partial_issues", []) or [])


def test_cmm012_flags_unexpected_comparison_years() -> None:
    pages = [
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "本年支出决算较2024年增加1.00万元。\n"
        "其中办公费较2023年预算减少2.00万元，较2026年决算增加3.00万元。\n"
        "与2025年年初预算相比持平。",
    ]
    doc = build_document("/tmp/cmm012.pdf", pages, [[]], 1000)
    doc.report_kind = "final"
    doc.document_profile = SimpleNamespace(
        organization_name=SimpleNamespace(value="测试单位"),
        report_year=SimpleNamespace(value=2025),
        subject_level=SimpleNamespace(value=None),
        jurisdiction=SimpleNamespace(value=None),
    )

    issues = _run_rule(CMM012_YearExpression(), doc)
    assert len(issues) == 2
    evidences = "\n".join(str(i.evidence_text or i.message) for i in issues)
    assert "较2023年预算" in evidences
    assert "较2026年决算" in evidences
    assert "2024年" not in evidences
    assert "2025年年初预算" not in evidences


def test_cmm011_and_v33251_flag_duplicated_unit_and_excess_known_sum() -> None:
    page = (
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为180.00万元，支出决算为90.00万元。其中：\n"
        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为28,146.97 万万元。\n"
        "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算120.00万元，支出决算为120.00万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc = build_document("/tmp/dup_unit.pdf", [page], [[]], 1000)
    doc.report_kind = "final"

    cmm011_issues = _run_rule(CMM011_AmountUnitWan(), doc)
    assert any("重复单位" in i.message and "28,146.97 万万元" in i.message for i in cmm011_issues)

    signal = None
    try:
        v251_issues = R33251_FunctionalDetailCompleteness().apply(doc) or []
    except RuleOutcomeSignal as exc:
        signal = exc
        v251_issues = list(exc.partial_issues or [])

    assert signal is not None
    assert "金额单位重复" in signal.detail
    assert "万万元" in signal.detail
    assert any("万万元" in i.message and "重复" in i.message for i in v251_issues)
    # 决算侧：即使 item1 未纳入，item2=120 > total_final=90 -> 明确超总额，仍直接报正式 warn
    final_fail = next(i for i in v251_issues if "支出决算" in i.message and "已超过" in i.message)
    assert final_fail.severity == "warn"
    receipt = next(
        r for r in (getattr(doc, "fact_receipts", None) or [])
        if r.get("sub_check_id") == "V33-251:final:item_sum"
    )
    assert receipt["status"] == "insufficient_data"

    # 若仅含依法不予公开（无缺损操作数）且已知合计数已超总额，直接判定 fail 而不阻塞
    page_withheld = (
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为180.00万元，支出决算为90.00万元。其中：\n"
        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算涉密依法不予公开。\n"
        "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算120.00万元，支出决算为120.00万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc_w = build_document("/tmp/withheld_exceed.pdf", [page_withheld], [[]], 1000)
    doc_w.report_kind = "final"
    signal_w = None
    try:
        issues_w = R33251_FunctionalDetailCompleteness().apply(doc_w) or []
    except RuleOutcomeSignal as exc_w:
        signal_w = exc_w
        issues_w = list(exc_w.partial_issues or [])
    assert signal_w is None
    assert any("已超过" in i.message and i.severity == "warn" for i in issues_w)
    receipt_w = next(
        r for r in (getattr(doc_w, "fact_receipts", None) or [])
        if r.get("sub_check_id") == "V33-251:final:item_sum"
    )
    assert receipt_w["status"] == "fail"


def test_v33222_duplicated_unit_records_material_defect_and_unparsed_gap() -> None:
    page = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "1.一般公共服务支出（类）行政运行（款）行政运行（项）。"
        "年初预算为10.00万元，支出决算为28,146.97万万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc = build_document("/tmp/v33222_dup.pdf", [page], [[]], 1000)
    doc.report_kind = "final"
    doc.fact_context = SimpleNamespace(org_name="测试单位", fiscal_year=2025)
    match = re.search(
        r"(?P<klass>一般公共服务支出)（类）(?P<kuan>行政运行)（款）(?P<xiang>行政运行)（项）",
        page,
    )
    assert match is not None
    reason_text = "行政运行条目决算金额单位书写有误（“28,146.97万万元”）"
    _record_text_gap(
        doc,
        [reason_text],
        match,
        codes=["text_statement_unparsed"],
        role="行政运行条目决算金额单位错误",
        sub_check_id="general:text:item:0",
    )
    recorded = lookup_failure(doc, reason_text, sub_check_id="general:text:item:0")
    assert len(recorded) == 1
    assert recorded[0]["reason_codes"] == ["text_statement_unparsed"]
    assert recorded[0]["missing_operands"][0]["role"] == "行政运行条目决算金额单位错误"


def test_native_summary_pick_accepts_verified_blank_term() -> None:
    import src.engine.native_empty_operands as neo

    observation = {
        "physical_page": 2,
        "bbox": [10.0, 20.0, 30.0, 40.0],
        "source_cell_id": "p2_t0_r1_c1",
        "grid_raw_text": "",
        "table_code": "FIN_04_fiscal_grant_total",
        "file_hash": "hash-1",
    }
    blank_fact = NativeBlankTerm(
        fact_uid="blank-1",
        org_name="测试单位",
        fiscal_year=2025,
        budget_stage="决算",
        table_code="FIN_04_fiscal_grant_total",
        subject_code="",
        subject_name="本年支出合计",
        indicator="expenditure_actual",
        unit="万元",
        raw_value="",
        normalized_value=Decimal("0"),
        extra_dims={"native_blank_observation": observation},
    )
    doc = SimpleNamespace(
        parse_artifact={
            "file_hash": "hash-1",
            "pages": {2: {"raw_text": "财政拨款收入支出决算总表\n本年支出合计"}},
        },
        fact_context=SimpleNamespace(org_name="测试单位", fiscal_year=2025, parse_run_uid="run-1"),
        page_texts={2: "财政拨款收入支出决算总表\n本年支出合计"},
        check_gaps=[],
    )

    orig = neo.valid_blank_observation
    neo.valid_blank_observation = lambda *_args, **_kwargs: True
    try:
        picked, reason = summary_pick(
            doc, [blank_fact], "expenditure_actual", ("本年支出合计",), allow_native_blank=True
        )
    finally:
        neo.valid_blank_observation = orig

    assert reason is None
    assert picked is blank_fact


def test_info_tip_without_evidence_does_not_gate_rule_evidence_incomplete() -> None:
    result: Dict[str, Any] = {
        "ai_findings": [],
        "rule_findings": [
            {
                "id": "info-tip-no-evidence",
                "source": "rule",
                "rule_id": "CMM-001",
                "severity": "info",
                "page_number": 0,
                "evidence": [],
            }
        ],
    }
    completeness = apply_evidence_completeness(result)
    finding = result["rule_findings"][0]
    assert is_formal_finding(finding) is False
    assert completeness["rule_warning_count"] == 1
    assert completeness["formal_rule_warning_count"] == 0

    page_assessment = {"page_coverage": 1.0, "scanned_page_count": 0, "low_text_pages": []}
    gate = api_main._evaluate_quality_gate(
        page_assessment,
        "final",
        2025,
        False,
        False,
        completeness["formal_issue_total"],
        evidence_degraded_count=completeness["degraded_count"],
        evidence_completeness=completeness,
        structured_ingest={"status": "skipped", "review_item_count": 0},
        rule_execution_summary={"total": 1, "executed": 1, "failed": 0, "skipped": 0},
    )
    assert "rule_evidence_incomplete" not in [r["code"] for r in gate["review_reasons"]]
    assert gate["status"] == "done"

    result_formal: Dict[str, Any] = {
        "ai_findings": [],
        "rule_findings": [
            {
                "id": "warn-no-evidence",
                "source": "rule",
                "rule_id": "V33-101",
                "severity": "high",
                "page_number": 0,
                "evidence": [],
            }
        ],
    }
    completeness_formal = apply_evidence_completeness(result_formal)
    assert completeness_formal["formal_rule_warning_count"] == 1
    gate_formal = api_main._evaluate_quality_gate(
        page_assessment,
        "final",
        2025,
        False,
        False,
        completeness_formal["formal_issue_total"],
        evidence_degraded_count=completeness_formal["degraded_count"],
        evidence_completeness=completeness_formal,
        structured_ingest={"status": "skipped", "review_item_count": 0},
        rule_execution_summary={"total": 1, "executed": 1, "failed": 0, "skipped": 0},
    )
    assert "rule_evidence_incomplete" in [r["code"] for r in gate_formal["review_reasons"]]
    assert gate_formal["status"] == "review_required"


def test_missing_three_public_section_cascades_to_formal_root() -> None:
    obligation = SimpleNamespace(
        status="insufficient_data",
        reason_codes=["text_statement_absent"],
        blocked_by=[],
        detail="未找到三公经费说明原文；结构：三公说明段缺失",
        missing_operands=[],
    )
    result_row = {
        "rule_findings": [
            {
                "id": "issue-v33-114",
                "rule_id": "V33-114",
                "severity": "warn",
                "message": "报告正文缺少财政拨款“三公”经费支出决算情况说明章节",
                "evidence_status": EVIDENCE_STATUS_COMPLETE,
            }
        ]
    }
    roots = cascade_root_issue_ids(obligation, result_row, visible_issue_ids=["issue-v33-114"])
    assert roots == ["issue-v33-114"]

    other_obligation = SimpleNamespace(
        status="insufficient_data",
        reason_codes=["text_statement_unparsed"],
        blocked_by=[],
        detail="三公经费说明未识别出金额",
        missing_operands=[],
    )
    assert cascade_root_issue_ids(other_obligation, result_row, visible_issue_ids=["issue-v33-114"]) == []


def test_awaiting_signoff_and_signoff_only_separation() -> None:
    res_signoff = derive_slot_status(
        has_current_document=True,
        analysis_state=ANALYSIS_DONE,
        review_state=REVIEW_REQUIRED,
        has_pending_review_work=False,
    )
    assert res_signoff.status == "review_required"
    assert res_signoff.reason == STATUS_REASON_AWAITING_SIGNOFF

    res_findings = derive_slot_status(
        has_current_document=True,
        analysis_state=ANALYSIS_DONE,
        review_state=REVIEW_REQUIRED,
        has_pending_review_work=True,
    )
    assert res_findings.status == "review_required"
    assert res_findings.reason == STATUS_REASON_FINDINGS_PENDING

    slot_row = {
        "slot_id": "slot-1",
        "current_document_version_id": 11,
        "report_kind": "final",
        "fiscal_year": 2025,
    }
    analysis_clean = ReviewAnalysis(
        job_uuid="job-1",
        analysis_revision=1,
        document_version_id=11,
        status="done",
        completed=True,
        unavailable_reason=None,
        result_row={
            "ai_findings": [],
            "rule_findings": [],
            "merged_result": {},
        },
        result_meta={
            "obligation_coverage": {
                "applicable_total": 1,
                "completed_total": 1,
                "not_applicable_total": 0,
                "unresolved_total": 0,
                "blocking_total": 0,
                "instances": [],
            }
        },
        structured_ingest={},
    )
    summary_clean = build_worklist_review_summary(slot_row, analysis_clean, [], [])
    assert summary_clean["pending_findings"] == 0
    assert summary_clean["pending_checks"] == 0
    assert summary_clean["signoff_only"] is True
    assert summary_clean["review_status"] == "pending"

    analysis_pending = ReviewAnalysis(
        job_uuid="job-2",
        analysis_revision=1,
        document_version_id=11,
        status="review_required",
        completed=True,
        unavailable_reason=None,
        result_row={
            "ai_findings": [],
            "rule_findings": [],
            "merged_result": {},
        },
        result_meta={
            "obligation_coverage": {
                "applicable_total": 1,
                "completed_total": 0,
                "not_applicable_total": 0,
                "unresolved_total": 1,
                "blocking_total": 1,
                "instances": [
                    {
                        "obligation_id": "V33-101:tot",
                        "rule_id": "V33-101",
                        "title": "总表平衡",
                        "status": "insufficient_data",
                        "blocks_gate": True,
                        "detail": "缺少操作数",
                    }
                ],
            }
        },
        structured_ingest={},
    )
    summary_pending = build_worklist_review_summary(slot_row, analysis_pending, [], [])
    assert summary_pending["signoff_only"] is False
    assert summary_pending["pending_checks"] == 1
    assert summary_pending["review_status"] == "pending"
