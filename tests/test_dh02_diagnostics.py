"""DH-02 结构化卡点：字段贯通、义务汇总与接口/导出投影。"""

from __future__ import annotations

from src.engine.check_obligations import apply_obligation_diagnosis, build_check_receipts
from src.engine.diagnostics import blocker, make_diagnosis
from src.engine.fact_context import make_fact_receipt
from src.schemas.parse_artifact import CheckReceipt
from src.schemas.review_lifecycle import ObligationReviewItem
from src.services.material_detail_query_service import build_coverage


def _missing(page=7):
    return {
        "source": "table", "role": "total_actual", "table_code": "FIN_03_expenditure",
        "page": page, "row_key": "total", "column": "total_actual",
        "fund_scope": "fiscal_appropriation", "stage": "决算", "year": 2025,
    }


def test_check_receipt_roundtrip_preserves_reason_codes_and_diagnostic_fields() -> None:
    receipt = CheckReceipt(
        rule_id="V33-104",
        sub_check_id="T3:total_actual:text_vs_table",
        status="insufficient_data",
        reason_codes=["operand_missing"],
        blocked_by=[blocker("cell", "cell-1", "cell_unbound")],
        missing_operands=[_missing()],
        candidates=[{
            "source": "table", "table_code": "FIN_03_expenditure", "page": 7,
            "cell_id": "cell-1", "value": "1.00",
        }],
        next_action="system_recheck",
        root_cause_key="rc:test",
    )
    restored = CheckReceipt.from_dict(receipt.to_dict())
    assert restored.reason_codes == ["operand_missing"]
    assert restored.blocked_by == receipt.blocked_by
    assert restored.missing_operands == receipt.missing_operands
    assert restored.candidates == receipt.candidates
    assert restored.next_action == "system_recheck"
    assert restored.root_cause_key == "rc:test"


def test_make_fact_receipt_falls_back_and_binds_identity() -> None:
    first = make_fact_receipt(
        "V33-104", "T3:total_actual:text_vs_table",
        status="insufficient_data", incomplete_reason="表侧取数失败",
    )
    second = make_fact_receipt(
        "V33-104", "T3:basic_actual:text_vs_table",
        status="insufficient_data", incomplete_reason="表侧取数失败",
    )
    assert first["reason_codes"] == ["rule_no_reason"]
    assert first["next_action"] == "tech_fix"
    assert first["root_cause_key"]
    # 没有可定位缺失页时，根因键必须包含子检查身份，不能把不同子检查并成一键。
    assert first["root_cause_key"] != second["root_cause_key"]


def test_build_check_receipts_projects_diagnostics_to_aggregate_and_obligation() -> None:
    coverage = {
        "instances": [{
            "obligation_id": "OBL-TXT-T3",
            "status": "insufficient_data",
            "checkers": ["V33-104"],
            "missing_checkers": [],
        }]
    }
    relation = make_fact_receipt(
        "V33-104", "T3:total_actual:text_vs_table",
        status="insufficient_data",
        incomplete_reason="表侧取数失败",
        reason_codes=["operand_missing"],
        missing_operands=[_missing(page=None)],
    )
    receipts = build_check_receipts(
        {},
        report_kind="final",
        rule_execution_summary={"rule_statuses": {"V33-104": "insufficient_data"}},
        obligation_coverage=coverage,
        fact_receipts=[relation],
    )
    aggregate = next(
        item for item in receipts
        if item["sub_check_id"] == "OBL-TXT-T3:V33-104"
    )
    projected = next(
        item for item in receipts
        if item["obligation_id"] == "OBL-TXT-T3"
        and item["sub_check_id"] == "T3:total_actual:text_vs_table"
    )
    assert aggregate["reason_codes"] == ["operand_missing"]
    assert aggregate["missing_operands"] == [_missing(page=None)]
    assert aggregate["root_cause_key"]
    assert projected["missing_operands"] == [_missing(page=None)]
    assert projected["root_cause_key"]
    # 同一关系投影到义务后根因键按义务身份重算，避免不同义务共用同一键。
    assert projected["root_cause_key"] != relation["root_cause_key"]


def test_apply_obligation_diagnosis_unions_children_and_uses_most_upstream() -> None:
    ledger = {
        "instances": [{
            "obligation_id": "OBL-TXT-T3",
            "status": "insufficient_data",
            "reason_codes": [],
        }]
    }
    receipts = [
        {
            "obligation_id": "OBL-TXT-T3", "rule_id": "V33-104", "sub_check_id": "s1",
            "status": "insufficient_data", "reason_codes": ["operand_missing"],
            "blocked_by": [], "missing_operands": [_missing()], "candidates": [],
            "next_action": "system_recheck", "root_cause_key": "rc:operand",
        },
        {
            "obligation_id": "OBL-TXT-T3", "rule_id": "V33-104", "sub_check_id": "s2",
            "status": "insufficient_data", "reason_codes": ["scope_unconfirmed"],
            "blocked_by": [blocker("year", 2025, "scope_unconfirmed")],
            "missing_operands": [], "candidates": [],
            "next_action": "human_confirm", "root_cause_key": "rc:year",
        },
    ]
    apply_obligation_diagnosis(ledger, receipts)
    item = ledger["instances"][0]
    assert set(item["reason_codes"]) == {"operand_missing", "scope_unconfirmed"}
    assert item["blocked_by"] == [blocker("year", 2025, "scope_unconfirmed")]
    assert item["missing_operands"] == [_missing()]
    assert item["next_action"] == "human_confirm"
    assert item["root_cause_key"] == "rc:year"


def test_coverage_projection_and_export_keep_diagnostic_fields() -> None:
    diagnosis = make_diagnosis(
        reason_codes=["operand_missing"],
        missing_operands=[_missing()],
    )
    raw = {
        "catalog_version": "obligations-v12-checklist-global-20261022",
        "applicable_total": 1, "completed_total": 0, "not_applicable_total": 0,
        "unresolved_total": 1, "blocking_total": 1,
        "instances": [{
            "obligation_id": "OBL-TXT-T3", "status": "insufficient_data",
            "detail": "具体说明未识别到显式金额", "checkers": ["V33-104"],
            **diagnosis,
        }],
    }
    coverage = build_coverage(raw)
    item = coverage.items[0]
    assert item.missing_operands == [_missing()]
    assert item.reason_codes == ["operand_missing"]
    assert item.root_cause_key == diagnosis["root_cause_key"]

    from api.routes.reports import _build_check_scope_export

    exported = _build_check_scope_export({
        "check_coverage": raw,
        "document_profile": {},
    })
    unfinished = exported["unfinished_checks"][0]
    assert unfinished["missing_operands"] == [_missing()]
    assert unfinished["reason_codes"] == ["operand_missing"]
    assert unfinished["next_action"] == "system_recheck"

    review_item = ObligationReviewItem(
        obligation_id="OBL-TXT-T3",
        status="insufficient_data",
        missing_operands=[_missing()],
        reason_codes=["operand_missing"],
        next_action="system_recheck",
        root_cause_key=diagnosis["root_cause_key"],
    )
    assert review_item.missing_operands == [_missing()]
    assert review_item.root_cause_key == diagnosis["root_cause_key"]
