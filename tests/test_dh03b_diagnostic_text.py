"""DH-03b：结构化字段生成说明文本，且不丢失同源冻结依据。"""

from __future__ import annotations

from src.engine.check_obligations import apply_obligation_diagnosis
from src.engine.diagnostic_text import render_obligation_detail, render_receipt_reason
from src.engine.diagnostics import make_diagnosis
from src.engine.fact_context import make_fact_receipt


def _diagnosis():
    return make_diagnosis(
        reason_codes=["operand_missing", "cell_unbound"],
        blocked_by=[{"dimension": "cell", "ref": "cell-7", "reason_code": "cell_unbound"}],
        missing_operands=[{
            "source": "table", "role": "total_actual", "table_code": "FIN_03_expenditure",
            "page": 7, "row_key": "208", "column": "project_actual",
            "fund_scope": "fiscal_appropriation", "stage": "决算", "year": 2025,
        }],
        next_action="system_recheck",
    )


def test_receipt_reason_answers_what_where_missing_and_next() -> None:
    text = render_receipt_reason("V33-241", "expenditure:horizontal:208", _diagnosis())
    assert "V33-241" in text and "expenditure:horizontal:208" in text
    assert "缺少" in text and "FIN_03_expenditure" in text and "第7页" in text
    assert "判断不了的原因" in text
    assert "下一步" in text and "定向补取" in text
    assert "缺失或不唯一" not in text


def test_obligation_detail_answers_six_questions() -> None:
    instance = {
        "obligation_id": "OBL-INTERNAL-T3-TOTAL",
        "title": "支出决算表合计与分项一致",
        "reason_codes": ["operand_missing"],
        "blocked_by": [],
        "missing_operands": [{
            "source": "table", "role": "total_actual", "table_code": "FIN_03_expenditure",
            "page": 7, "row_key": "208", "column": "project_actual",
            "fund_scope": None, "stage": "决算", "year": 2025,
        }],
        "candidates": [],
        "next_action": "system_recheck",
    }
    text = render_obligation_detail(instance, children=[{"operand_fact_ids": ["a", "b"]}])
    assert "正在核对：支出决算表合计与分项一致" in text
    assert "原文位置" in text and "FIN_03_expenditure" in text
    assert "已取到：2 个操作数" in text
    assert "缺少" in text and "208" in text
    assert "判断不了的原因" in text
    assert "下一步" in text and "定向补取" in text
    assert "缺失或不唯一" not in text


def test_make_fact_receipt_rewrites_only_unresolved_receipts() -> None:
    unresolved = make_fact_receipt(
        "V33-241", "expenditure:horizontal:208",
        status="insufficient_data",
        incomplete_reason="208的project_actual/决算操作数缺失或不唯一",
        reason_codes=["operand_missing"],
        missing_operands=[{
            "source": "table", "role": "project_actual", "table_code": "FIN_03_expenditure",
            "page": 7, "row_key": "208", "column": "project_actual",
            "fund_scope": None, "stage": "决算", "year": 2025,
        }],
    )
    assert "缺失或不唯一" not in (unresolved["incomplete_reason"] or "")
    assert "FIN_03_expenditure" in (unresolved["incomplete_reason"] or "")
    delivered = make_fact_receipt(
        "V33-241", "expenditure:horizontal:208",
        status="pass", incomplete_reason="正常说明文字",
    )
    assert delivered["incomplete_reason"] == "正常说明文字"


def test_obligation_detail_rewrite_keeps_frozen_coverage_terms() -> None:
    ledger = {
        "instances": [{
            "obligation_id": "OBL-INTERNAL-T3-TOTAL",
            "title": "支出决算表合计与分项一致",
            "status": "insufficient_data",
            "reason_codes": [],
            "detail": "说明中的款“旧科目名称”在表内找不到同名科目",
            "legacy_coverage_terms": ["旧科目名称"],
        }]
    }
    receipts = [{
        "obligation_id": "OBL-INTERNAL-T3-TOTAL",
        "rule_id": "V33-241", "sub_check_id": "s1",
        "status": "insufficient_data",
        "reason_codes": ["operand_missing"],
        "blocked_by": [], "missing_operands": [{
            "source": "table", "role": "project_actual", "table_code": "FIN_03_expenditure",
            "page": 7, "row_key": "208", "column": "project_actual",
            "fund_scope": None, "stage": "决算", "year": 2025,
        }], "candidates": [], "next_action": "system_recheck", "root_cause_key": "rc:x",
    }]
    apply_obligation_diagnosis(ledger, receipts)
    item = ledger["instances"][0]
    assert "正在核对" in item["detail"]
    assert "缺失或不唯一" not in item["detail"]
    assert item["legacy_coverage_terms"] == ["旧科目名称"]
