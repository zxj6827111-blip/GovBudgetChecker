"""2026-11-19 四类问题边界、正文校正与完成门禁回归。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.engine.checklist_final_rules import (
    R33251_FunctionalDetailCompleteness,
    _calculation_evidence,
)
from src.engine.native_issue_dedup import _annotate_unit_recoveries
from src.engine.rule_outcome import RuleOutcomeSignal
from src.engine.rules_v33 import Issue, build_document
from src.services.text_correction_targets import (
    build_text_correction_targets,
    duplicate_correction_record,
    text_corrections_from_records,
)

HEAD = (
    "（三）一般公共预算财政拨款支出决算具体情况\n"
    "一般公共预算财政拨款支出年初预算为100.00万元，支出决算为90.00万元。其中：\n"
)
TAIL = "六、一般公共预算财政拨款基本支出决算情况说明"
PAGE = (
    HEAD
    + "1、教育支出（类）普通教育（款）小学教育（项）。年初预算60.00万元，支出决算为50.00万元。\n"
    + "2、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算20.00万元，支出决算为40。\n"
    + "3、社会保障和就业支出（类）行政事业单位养老支出（款）行政单位离退休（项）。年初预算20.00万元，支出决算为10.00万元。\n"
    + TAIL
)


def _run_rule(*, corrections=None):
    doc = build_document("/tmp/v33-251-text-correction.pdf", [PAGE], [[]], 1000)
    doc.report_kind = "final"
    if corrections:
        doc.text_corrections = corrections
    try:
        issues = R33251_FunctionalDetailCompleteness().apply(doc)
        signal = None
    except RuleOutcomeSignal as exc:
        issues = list(getattr(exc, "partial_issues", []) or [])
        signal = exc
    receipt = next(
        (
            item for item in doc.fact_receipts
            if item.get("sub_check_id") == "V33-251:final:item_sum"
        ),
        None,
    )
    return doc, issues, signal, receipt


def test_missing_unit_without_table_basis_is_conditional_trial_not_pass():
    """只能按万元理解时给条件试算，但不计通过、不解除门禁。"""
    _, _, signal, receipt = _run_rule()
    assert signal is not None and signal.status == "insufficient_data"
    evidence = receipt["calculation_evidence"]
    assert evidence["included_count"] == 2
    assert evidence["subtotal_wan"] == "60.00"
    trial = evidence["trial"]
    assert trial["assumption"] == "未确定单位的金额按万元理解"
    assert trial["included_count"] == 3
    assert trial["subtotal_wan"] == "100.00"
    assert trial["difference_wan"] == "10.00"
    assert trial["is_conditional"] is True
    assert trial["counts_as_pass"] is False
    assert trial["clears_completion_gate"] is False


def test_manual_unit_correction_recalculates_with_text_value():
    """复核人确认万元后，三项均按正文数字参与加总并如实报差额。"""
    corrections = [{
        "correction_id": "cor-unit-1",
        "field": "unit",
        "value": "万元",
        "page": 1,
        "span": "支出决算为40",
        "actor": "reviewer",
        "analysis_basis_token": "job:rev-1",
    }]
    _, issues, signal, receipt = _run_rule(corrections=corrections)
    assert signal is None
    evidence = receipt["calculation_evidence"]
    assert evidence["included_count"] == 3
    assert evidence["subtotal_wan"] == "100.00"
    assert evidence["difference_wan"] == "10.00"
    assert "trial" not in evidence
    corrected = [
        item for item in evidence["included"]
        if item.get("manual_correction")
    ]
    assert corrected and corrected[0]["raw"] == "40"
    assert corrected[0]["value_wan"] == "40"
    assert any("合计100.00万元" in issue.message for issue in issues)


def test_ambiguous_text_correction_is_not_applied_to_multiple_records():
    """同页同额多处命中时校正不扩散：保留待核，不把一处确认当成多处事实。"""
    page = (
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为100.00万元，支出决算为90.00万元。其中：\n"
        "1、教育支出（类）普通教育（款）小学教育（项）。年初预算20.00万元，支出决算为40。\n"
        "2、教育支出（类）普通教育（款）初中教育（项）。年初预算20.00万元，支出决算为40。\n"
        "3、社会保障和就业支出（类）行政事业单位养老支出（款）行政单位离退休（项）。年初预算20.00万元，支出决算为10.00万元。\n"
        "六、一般公共预算财政拨款基本支出决算情况说明"
    )
    doc = build_document("/tmp/v33-251-ambiguous-correction.pdf", [page], [[]], 1000)
    doc.report_kind = "final"
    doc.text_corrections = [{
        "correction_id": "cor-ambiguous",
        "field": "unit",
        "value": "万元",
        "page": 1,
        "span": "支出决算为40",
        "actor": "reviewer",
        "analysis_basis_token": "job:rev-1",
    }]
    try:
        R33251_FunctionalDetailCompleteness().apply(doc)
        signal = None
    except RuleOutcomeSignal as exc:
        signal = exc
    assert signal is not None and signal.status == "insufficient_data"
    assert doc.applied_text_correction_ids == []
    assert doc.unapplied_text_corrections[0]["reason"] == "ambiguous_match"
    assert doc.unapplied_text_corrections[0]["match_count"] == 2


def test_manual_raw_value_correction_recalculates_but_keeps_original_issue():
    corrections = [{
        "correction_id": "cor-raw-1",
        "field": "raw_value",
        "value": "30",
        "page": 1,
        "span": "支出决算为40",
        "actor": "reviewer",
        "analysis_basis_token": "job:rev-1",
    }]
    _, _, signal, receipt = _run_rule(corrections=corrections)
    # 仍缺单位：校正读数本身不提供单位，不能把未完成算成通过。
    assert signal is not None and signal.status == "insufficient_data"
    evidence = receipt["calculation_evidence"]
    assert evidence["included_count"] == 2
    assert evidence["trial"]["subtotal_wan"] == "90.00"


def test_text_correction_targets_are_locatable_and_actionable():
    payload = {
        "analysis_revision": "rev-26",
        "result": {
            "issues": {
                "all": [{
                    "id": "CMM-011-24",
                    "rule_id": "CMM-011",
                    "title": "金额缺少单位“万元”",
                    "message": "金额缺少单位“万元”：“支出决算为 5,465.41”，同段其余金额均以万元列示。",
                    "location": {
                        "page": 26,
                        "pos": 534,
                        "verified_by": {
                            "subject_code": "2160250",
                            "subject_name": "事业运行",
                            "table_page": 16,
                            "table_code": "FIN_05_general_public_expenditure",
                        },
                    },
                    "evidence": [{
                        "physical_page": 26,
                        "bbox": [120.1, 672.9, 263.5, 687.9],
                        "quote": "支出决算为 5,465.41",
                        "verification_status": "verified",
                    }],
                }]
            }
        },
    }
    targets = build_text_correction_targets(payload)
    assert len(targets) == 1
    target = targets[0]
    assert target["target_type"] == "text_statement"
    assert target["page"] == 26
    assert target["amount_raw"] == "5,465.41"
    assert target["suggested_unit"] == "万元"
    assert target["subject"] == {"code": "2160250", "name": "事业运行"}
    assert target["evidence"][0]["bbox"] == [120.1, 672.9, 263.5, 687.9]
    assert {action["field"] for action in target["actions"]} >= {"unit", "raw_value"}


def test_v33_251_unincluded_item_becomes_text_target_with_page():
    payload = {
        "analysis_revision": "rev-1",
        "result": {
            "issues": {
                "all": [{
                    "id": "V33-251-22",
                    "rule_id": "V33-251",
                    "message": (
                        "已公开分项支出决算合计163494.13万元（纳入19项）；"
                        "另有1处支出决算金额缺少可归一单位或格式"
                        "（定位：第26页「支出决算为 5,465.41，决算」），未纳入合计。"
                    ),
                    "location": {"page": 23, "not_included_items": [{"raw": "5,465.41"}]},
                }]
            }
        },
    }
    targets = build_text_correction_targets(payload)
    assert len(targets) == 1
    assert targets[0]["page"] == 26
    assert "5,465.41" in targets[0]["span"]


def test_text_correction_record_projection_and_duplicate_detection():
    records = [{
        "record_id": "cor-1",
        "analysis_basis_token": "job:rev-1",
        "actor": "reviewer",
        "reason": "确认单位",
        "changes": [{
            "target_type": "text_statement",
            "target_id": "text-stmt:abc",
            "field": "unit",
            "value": "万元",
            "original_text": "支出决算为 5,465.41",
            "location": {"physical_page": 26, "bbox": [1, 2, 3, 4], "quote": "支出决算为 5,465.41"},
        }],
    }]
    corrections = text_corrections_from_records(records)
    assert corrections[0]["page"] == 26
    assert corrections[0]["field"] == "unit"
    assert corrections[0]["span"] == "支出决算为 5,465.41"
    assert corrections[0]["correction_id"] == "cor-1"

    duplicate = duplicate_correction_record(
        records,
        basis_token="job:rev-1",
        changes=records[0]["changes"],
    )
    assert duplicate is records[0]
    assert duplicate_correction_record(
        records, basis_token="job:rev-2", changes=records[0]["changes"]
    ) is None


def _unit_basis_fact():
    return {
        "fact_uid": "fact-2160250",
        "table_code": "FIN_05_general_public_expenditure",
        "subject_code": "2160250",
        "subject_name": "事业运行",
        "unit": "万元",
        "normalized_value": "5465.41",
        "page": 16,
        "source_cell_id": "t5:r32c4",
        "quote": "5,465.41",
        "role": "same_subject_table_basis",
    }


def test_cmm011_issue_gets_verified_by_annotation_after_v33_251_pass():
    issue = Issue(
        rule="CMM-011",
        severity="warn",
        message="金额缺少单位“万元”：“支出决算为5,465.41”，同段其余金额均以万元列示。",
        location={"page": 26, "pos": 534},
    )
    receipt = {
        "rule_id": "V33-251",
        "sub_check_id": "V33-251:final:item_sum",
        "status": "pass",
        "reason_codes": ["source_table_unit_confirmed"],
        "calculation_evidence": {
            "included_count": 20,
            "subtotal_wan": "168959.54",
            "included": [{
                "page": 26,
                "span": "支出决算为5,465.41，决算",
                "raw": "5,465.41",
                "unit": "万元",
                "value_wan": "5465.41",
                "basis": _unit_basis_fact(),
            }],
        },
    }
    _annotate_unit_recoveries([issue], [receipt])
    verification = issue.location["verified_by"]
    assert verification["table_page"] == 16
    assert verification["subject_code"] == "2160250"
    assert verification["included_count"] == 20
    assert verification["subtotal_wan"] == "168959.54"
    assert verification["counts_as_pass"] is True
    assert "已按第16页核对" in issue.message
    assert "已纳入20项支出决算分项加总" in issue.message


def test_cmm011_annotation_reports_text_shortfall_when_table_value_differs():
    issue = Issue(
        rule="CMM-011",
        severity="warn",
        message="金额缺少单位“万元”：“支出决算为5,455.41”，同段其余金额均以万元列示。",
        location={"page": 26, "pos": 534},
    )
    receipt = {
        "rule_id": "V33-251",
        "sub_check_id": "V33-251:final:item_sum",
        "status": "fail",
        "calculation_evidence": {
            "included_count": 20,
            "subtotal_wan": "168949.54",
            "difference_wan": "-10.00",
            "included": [{
                "page": 26,
                "span": "支出决算为5,455.41，决算",
                "raw": "5,455.41",
                "unit": "万元",
                "value_wan": "5455.41",
                "basis": _unit_basis_fact(),
            }],
        },
    }
    _annotate_unit_recoveries([issue], [receipt])
    verification = issue.location["verified_by"]
    assert verification["item_difference_wan"] == "-10.00"
    assert verification["counts_as_pass"] is False
    assert "正文披露5,455.41（未写单位，按万元核算）少10.00万元" in verification["note"]
    assert "正文5,455.41万元" not in verification["note"]
    assert "168949.54万元" in verification["note"]


def test_calculation_evidence_serializes_decimal_and_records_two_ends():
    record = {
        "kind": "final",
        "raw": "5,465.41",
        "unit": "万元",
        "pos": 10,
        "value_wan": Decimal("5465.41"),
        "table_unit_basis": _unit_basis_fact(),
        "basis_mode": "identity_only",
    }
    evidence = _calculation_evidence([record], [], [], lambda pos: (26, "支出决算为5,465.41", 0, 14))
    item = evidence["included"][0]
    assert item["value_wan"] == "5465.41"
    assert item["basis"]["page"] == 16
    assert item["basis_mode"] == "identity_only"
    assert item["page"] == 26
    assert item["span"] == "支出决算为5,465.41"
