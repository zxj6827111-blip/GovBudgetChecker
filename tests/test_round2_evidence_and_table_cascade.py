"""Round 2 remediation regression tests: evidence completeness, rule_no_reason elimination, and per-table cascade."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pdfplumber
import pytest

from api import main as api_main
from src.engine.diagnostic_fields import aggregate, rule_level_entry
from src.engine.native_column_shift import column_shift_issue
from src.engine.rules_v33 import (
    R33004_CellNumberValidity,
    R33223_Narrative6_T6,
    build_document,
)
from src.schemas.issues import IssueItem
from src.schemas.material_detail import CoverageObligationItem
from src.services.evidence_guard import (
    EVIDENCE_STATUS_RULE_WARNING,
    apply_evidence_completeness,
)
from src.services.obligation_finding_coverage import cascade_root_issue_ids
from src.services.review_lifecycle_service import (
    blocking_coverage_obligations,
    cascaded_coverage_obligations,
)
from src.services.rule_source_binding import _native_span
from src.utils.issue_bbox import PDFBBoxLocator


def test_document_level_rules_do_not_trigger_rule_evidence_incomplete_gate() -> None:
    findings = [
        {"id": "doc-1", "rule_id": "V33-002", "source": "rule", "severity": "error", "message": "表顺序异常"},
        {"id": "doc-2", "rule_id": "V33-003", "source": "rule", "severity": "error", "message": "页数过少"},
        {"id": "doc-3", "rule_id": "BUD-001", "source": "rule", "severity": "critical", "message": "缺少预算表"},
    ]
    completeness = apply_evidence_completeness({"rule_findings": findings})
    assert completeness["rule_warning_count"] == 3
    assert completeness["formal_rule_warning_count"] == 0
    assert completeness["document_level_total"] == 3
    assert all(f["evidence_status"] == EVIDENCE_STATUS_RULE_WARNING for f in findings)

    gate = api_main._evaluate_quality_gate(
        {"page_coverage": 1.0, "scanned_page_count": 0},
        "final",
        2025,
        False,
        False,
        issue_total=3,
        evidence_completeness=completeness,
        rule_execution_summary={"total_rules": 10, "executed": 10},
        obligation_coverage={"catalog_version": "test", "instances": []},
    )
    assert "rule_evidence_incomplete" not in [r["code"] for r in gate["review_reasons"]]
    assert gate["status"] == "done"


def test_validate_evidence_keeps_source_identity_when_region_match_drifts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "drift.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "301 01 Salary 1,172.60", fontsize=12, fontname="helv")
    document.save(pdf_path)
    document.close()

    with pdfplumber.open(pdf_path) as pdf:
        texts = [p.extract_text() or "" for p in pdf.pages]
    file_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    ref = _native_span("301 01 Salary 1,172.60", 1, texts, file_hash)
    assert ref is not None

    locator = PDFBBoxLocator(str(pdf_path))
    try:
        # Simulate PyMuPDF clipping/word-order mismatch while native pdfplumber span matched 1:1
        monkeypatch.setattr(locator, "_region_text_matches", lambda *_args, **_kwargs: False)
        located = locator.locate(
            IssueItem(
                id="cmm-022-drift",
                source="rule",
                severity="medium",
                title="Code drift",
                message="Code drift",
                evidence=[ref],
                location={"page": 1, "pages": [1]},
            )
        )
    finally:
        locator.close()

    assert located.evidence[0]["source_identity_status"] == "verified"
    assert located.evidence[0].get("bbox") is None
    assert located.verification_status == "unverified"
    completeness = apply_evidence_completeness(
        {"rule_findings": [located.model_dump()]},
        document_page_count=1,
        page_texts=texts,
        source_file_hash=file_hash,
    )
    assert completeness["formal_rule_warning_count"] == 0


def test_r33004_attaches_verbatim_evidence_and_table_ref() -> None:
    page1 = "收入支出决算总表\n年初结转和结余 -31.98 年末结转和结余 494.16"
    tables1 = [[["年初结转和结余", "-31.98", "年末结转和结余", "494.16"]]]
    doc = build_document("/tmp/r33004.pdf", [page1], [tables1], 1000)
    issues = R33004_CellNumberValidity().apply(doc)
    assert len(issues) == 1
    assert issues[0].evidence_text
    assert "-31.98" in issues[0].evidence_text
    assert issues[0].location["table_refs"][0]["page"] == 1
    assert "-31.98" in issues[0].location["table_refs"][0]["span"]


def test_r33223_does_not_false_positive_when_section6_explicitly_none() -> None:
    page1 = (
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "本年度增加离退休人员经费补贴 64.73 万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明\n"
        "上海市农场管理局 2023 年度无一般公共预算财政拨款基本支出。"
    )
    doc = build_document("/tmp/r33223.pdf", [page1], [[]], 1000)
    issues = R33223_Narrative6_T6().apply(doc)
    assert issues == []


def test_column_shift_issue_uses_verbatim_page_quote() -> None:
    page1 = "支出决算表\n合计 78,916.73 62,771.75 16,144.98"
    doc = build_document("/tmp/shift.pdf", [page1], [[]], 1000)
    doc.fact_context = SimpleNamespace(
        facts=[SimpleNamespace(fact_uid="f1", physical_page=1)]
    )
    rule = SimpleNamespace(
        code="V33-241",
        _issue=lambda msg, loc, sev, **kw: SimpleNamespace(
            message=msg, location=loc, severity=sev, **kw
        ),
    )
    detection = {
        "direction": "left",
        "missing_label": "本年支出合计",
        "blank_label": "对附属单位补助支出",
        "pairs": [
            {
                "detail_label": "本年支出合计",
                "total_label": "基本支出",
                "detail_sum": Decimal("78916.73"),
                "total": Decimal("78916.73"),
            }
        ],
        "mismatched_columns": ["total_actual"],
        "rows_checked": 9,
        "rows_mismatched": 9,
        "fact_ids": ["f1"],
    }
    issue = column_shift_issue(rule, doc, "支出决算表", "FIN_03_expenditure", detection)
    assert issue.evidence_text
    assert issue.evidence_text != issue.message
    assert "78,916.73" in issue.evidence_text
    assert issue.location["table_refs"][0]["page"] == 1
    assert issue.location["table_refs"][0]["span"] == issue.evidence_text


@pytest.mark.parametrize(
    ("text", "expected_code"),
    [
        ("表缺失或无可解析行: 收入支出决算总表(T1)", "table_missing"),
        ("财政拨款总表未读取或列未映射，不能把政府性基金/国有资本口径当作零", "table_mapping_incomplete"),
        ("支出决算表(第10页)金额单位未知，无法可靠核验", "scope_unconfirmed"),
        ("表头仅披露“预算数”，未取得与原文一致的显式预算阶段证据，请复核年初/全年/调整后预算口径", "scope_unconfirmed"),
        ("封面文种缺少独立依据", "scope_unconfirmed"),
        ("封面主体与所选组织不一致，请核对归档", "subject_conflict"),
        ("经济分类表科目人员经费合计的完整原始行缺失", "row_level_unresolved"),
        ("基本支出表「公务用车购置费」决算数为空白且无独立取数依据", "cell_blank_semantics_unknown"),
        ("基本支出表未列「公务用车购置费」，且三公表该项决算数为 106.57 万元", "subject_name_unlocated"),
        ("指标「公务接待费」在本节存在多个不一致的本期金额披露，无法确定比较基期（parse_ambiguity）", "comparison_identity_unresolved"),
        ("说明未明示一般公共预算财政拨款支出总体金额，不能以结构分项替代", "text_statement_absent"),
        ("政府性基金：说明项未写明「支出决算为 X 万元」，项级支出金额不可确认", "text_statement_absent"),
    ],
)
def test_rule_level_entry_maps_specific_reason_codes_without_rule_no_reason(
    text: str, expected_code: str
) -> None:
    entry = rule_level_entry(text, rule_id="V33-TEST")
    assert entry["reason_codes"] == [expected_code]
    assert "rule_no_reason" not in entry["reason_codes"]


def test_aggregate_drops_rule_no_reason_when_specific_reason_present() -> None:
    summary = aggregate(
        [
            {"reason_codes": ["rule_no_reason"], "blocked_by": [], "missing_operands": [], "candidates": []},
            {"reason_codes": ["row_level_unresolved"], "blocked_by": [], "missing_operands": [], "candidates": []},
        ],
        obligation_id="OBL-INTERNAL-T2-TOTAL",
    )
    assert summary is not None
    assert summary["reason_codes"] == ["row_level_unresolved"]


def _make_item(
    obligation_id: str,
    *,
    reason_codes: List[str],
    detail: str,
    depends_on_tables: List[str],
    blocked_by: List[Dict[str, Any]] | None = None,
    missing_operands: List[Dict[str, Any]] | None = None,
    checkers: List[str] | None = None,
) -> CoverageObligationItem:
    return CoverageObligationItem(
        obligation_id=obligation_id,
        group_id="grp",
        group_title="grp",
        title=obligation_id,
        status="insufficient_data",
        blocks_gate=True,
        reason="insufficient_data",
        reason_label="数据不足",
        detail=detail,
        basis="basis",
        checkers=tuple(checkers or ()),
        depends_on=tuple(f"table:{t}" if not t.startswith("table:") else t for t in depends_on_tables),
        reason_codes=tuple(reason_codes),
        blocked_by=tuple(blocked_by or ()),
        missing_operands=tuple(missing_operands or ()),
    )


def test_per_table_reading_cascade_isolates_broken_table_and_preserves_other_checks() -> None:
    broken_root_id = "reading:mapping:frag_fin06"
    structured_ingest = {
        "review_items": [
            {
                "id": broken_root_id,
                "type": "table_mapping_incomplete",
                "severity": "warn",
                "table_code": "FIN_06_basic_expenditure",
                "page_number": 14,
                "message": "第 14 页基本支出决算表映射不完整",
            }
        ]
    }
    result_row: Dict[str, Any] = {
        "job_id": "job-tbl-cascade",
        "rule_findings": [],
        "ai_findings": [],
    }
    visible_ids = {broken_root_id}

    ob_fin06_explicit = _make_item(
        "OBL-INTERNAL-TABLE-TOTAL",
        reason_codes=["table_mapping_incomplete"],
        detail="来源表映射/读取未完成：第14页FIN_06_basic_expenditure",
        depends_on_tables=["FIN_01", "FIN_06"],
        blocked_by=[{"dimension": "instance_uid", "ref": "第14页FIN_06_basic_expenditure"}],
        checkers=["V33-005"],
    )
    ob_fin06_cross = _make_item(
        "OBL-CROSS-T5-T6",
        reason_codes=["cell_low_confidence"],
        detail="候选事实识别状态不可作操作数",
        depends_on_tables=["FIN_05", "FIN_06"],
        blocked_by=[{"dimension": "cell", "ref": "规则级:V33-203"}],
        checkers=["V33-203"],
    )
    ob_unrelated_table = _make_item(
        "OBL-INTERNAL-T2-TOTAL",
        reason_codes=["table_mapping_incomplete"],
        detail="来源表映射/读取未完成：第7页FIN_02_income",
        depends_on_tables=["FIN_02"],
        blocked_by=[{"dimension": "instance_uid", "ref": "第7页FIN_02_income"}],
    )
    ob_non_table_reason = _make_item(
        "OBL-TXT-T6",
        reason_codes=["text_statement_absent"],
        detail="未提取到基本支出情况说明中的人员经费",
        depends_on_tables=["FIN_06"],
    )

    assert cascade_root_issue_ids(
        ob_fin06_explicit, result_row, visible_ids, structured_ingest=structured_ingest
    ) == [broken_root_id]
    assert cascade_root_issue_ids(
        ob_fin06_cross, result_row, visible_ids, structured_ingest=structured_ingest
    ) == [broken_root_id]
    assert cascade_root_issue_ids(
        ob_unrelated_table, result_row, visible_ids, structured_ingest=structured_ingest
    ) == []
    assert cascade_root_issue_ids(
        ob_non_table_reason, result_row, visible_ids, structured_ingest=structured_ingest
    ) == []

    all_obs = [ob_fin06_explicit, ob_fin06_cross, ob_unrelated_table, ob_non_table_reason]
    remaining = blocking_coverage_obligations(
        all_obs, result_row, visible_issue_ids=visible_ids, structured_ingest=structured_ingest
    )
    assert [item.obligation_id for item in remaining] == ["OBL-INTERNAL-T2-TOTAL", "OBL-TXT-T6"]

    grouped = cascaded_coverage_obligations(
        all_obs, result_row, visible_issue_ids=visible_ids, structured_ingest=structured_ingest
    )
    assert [item.obligation_id for item in grouped[broken_root_id]] == [
        "OBL-INTERNAL-TABLE-TOTAL",
        "OBL-CROSS-T5-T6",
    ]


def test_quality_gate_suppresses_cascaded_table_blockers_while_keeping_reading_gate() -> None:
    structured_ingest = {
        "reading_import": {
            "reading_quality": {
                "gaps": [],
                "unbound_money_cells": [{"table_code": "FIN_06_basic_expenditure", "page": 14, "reason": "rule_admission_blocked"}],
                "mapping_incomplete_tables": [
                    {
                        "fragment_uid": "frag06",
                        "table_code": "FIN_06_basic_expenditure",
                        "page": 14,
                        "title": "一般公共预算财政拨款基本支出决算表",
                    }
                ],
                "fiscal_coverage": {"rule_blocked_count": 1},
            }
        },
        "review_items": [
            {
                "id": "reading:mapping:frag06",
                "type": "table_mapping_incomplete",
                "severity": "warn",
                "table_code": "FIN_06_basic_expenditure",
                "page_number": 14,
                "message": "第14页基本支出表映射不完整",
            }
        ],
    }
    coverage_dict = {
        "catalog_version": "test",
        "applicable_total": 1,
        "completed_total": 0,
        "not_applicable_total": 0,
        "unresolved_total": 1,
        "blocking_total": 1,
        "instances": [
            {
                "obligation_id": "OBL-CROSS-T5-T6",
                "group_id": "g",
                "group_title": "g",
                "title": "T5与T6勾稽",
                "status": "insufficient_data",
                "blocks_gate": True,
                "reason": "insufficient_data",
                "reason_label": "数据不足",
                "detail": "候选事实识别状态不可作操作数",
                "basis": "basis",
                "checkers": ["V33-203"],
                "depends_on": ["table:FIN_05", "table:FIN_06"],
                "reason_codes": ["cell_low_confidence"],
                "blocked_by": [{"dimension": "cell", "ref": "规则级:V33-203"}],
            }
        ],
    }
    summary = {
        "total_rules": 10,
        "executed": 9,
        "insufficient_data": 1,
        "unresolved_rules": [
            {
                "rule_id": "V33-203",
                "status": "insufficient_data",
                "reason_codes": ["cell_low_confidence"],
                "diagnostics": [{"reason_codes": ["cell_low_confidence"]}],
            }
        ],
    }
    result_row = {"job_id": "job-gate-cascade", "rule_findings": [], "ai_findings": []}
    gate = api_main._evaluate_quality_gate(
        {"page_coverage": 1.0, "scanned_page_count": 0},
        "final",
        2025,
        False,
        False,
        issue_total=1,
        structured_ingest=structured_ingest,
        obligation_coverage=coverage_dict,
        rule_execution_summary=summary,
        result_row=result_row,
    )
    codes = {r["code"] for r in gate["review_reasons"]}
    assert gate["status"] == "review_required"
    assert codes == {"table_reading_incomplete"}
