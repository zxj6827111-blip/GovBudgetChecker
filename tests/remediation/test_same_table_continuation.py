from copy import deepcopy

import pytest

from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.pipeline import build_issues_payload
from src.engine.rules_v33 import (
    R33233_DetailRowFormulaConsistency,
    R33240_Table2_IncomeAdvancedCheck,
    R33241_Table3_ExpenseAdvancedCheck,
)
from src.services.structured_ingest_runner import _reading_review_items
from src.services.table_import_receipt import build_table_import_receipt
from src.services.table_reading_assessment import READING_VERSION
from tests.remediation.test_native_blank_cells import grid_document


@pytest.mark.parametrize(("proof", "token"), [
    ("contained", "60.00"), ("contained", "0.00"),
    ("crosses_column", "60.00"), ("no_original_cell", "60.00"),
])
def test_single_amount_conflict_preserves_independent_rows(proof, token):
    code = "FIN_02_income"
    rows = [
        ["功能分类科目编码", "科目名称", "本年收入合计", "财政拨款收入", "上级补助收入",
         "事业收入", "经营收入", "附属单位上缴收入", "其他收入"],
        ["合计", "", "100.00", "90.00", "0", "0", "0", "0", "10.00"],
        ["201", "一般公共服务", "60.00", "60.009", "0", "0", "0", "0", "0"],
        ["208", "社会保障", "40.00", "30.00", "0", "0", "0", "0", "9.00"],
    ]
    doc = grid_document(rows, code=code, headers=1)
    artifact = doc.parse_artifact
    table = artifact.table_fragments[0]
    table.title = artifact.logical_tables[0].title = "收入决算表"
    cell = next(c for c in table.cells if (c.row_idx, c.col_idx) == (2, 3))
    cell.label_recovery = {"status": "amount_cell_visibility_conflict",
                           "source_cell_id": "fragment:r2c3", "original_text": cell.raw_text,
                           "excluded_characters": [{"text": "9"}]}
    x0, y0, x1, _ = cell.bbox
    if proof == "no_original_cell":
        artifact.physical_fragments = []
    right = x1 + 1 if proof == "crosses_column" else x1 - 10
    artifact.reading_assessment = {"version": READING_VERSION, "pages": [{
        "page": 1, "width": 650, "height": 800, "blank_verified": False,
        "table_hints": [code], "image_regions": [],
        "amounts": [{"raw_text": token, "bbox": [x0 + 10, y0 + 5, right, y0 + 15]}],
    }]}
    doc.fact_context = build_fact_context_from_artifact(
        artifact, org_name="甲局", fiscal_year=2024, report_kind="final", doc_type="dept_final")
    payload = build_issues_payload(doc, report_kind="final", rules=[
        R33233_DetailRowFormulaConsistency, R33240_Table2_IncomeAdvancedCheck])
    results = {r["sub_check_id"]: r["status"] for r in payload["fact_receipts"] if r["rule_id"] == "V33-233"}
    if proof == "no_original_cell":
        assert results == {f"{code}:horizontal:coverage": "insufficient_data"}
        assert not payload["issues"]["all"]
        return
    assert results[f"{code}:horizontal:201"] == "insufficient_data"
    if proof != "contained":
        assert results[f"{code}:horizontal:208"] == "insufficient_data"
        assert not payload["issues"]["all"]
        return
    assert results[f"{code}:horizontal:total"] == "pass"
    assert results[f"{code}:horizontal:208"] == "fail"
    assert any(r["status"] == "fail" and r["rule_id"] == "V33-240" for r in payload["fact_receipts"])
    assert any("208" in i["message"] for i in payload["issues"]["all"] if i["severity"] != "manual_review")
    reading = build_table_import_receipt(artifact, doc.fact_context.facts, org_name="甲局")["reading_quality"]
    [pending] = _reading_review_items(reading)
    assert pending["cells"][0]["source_cell_id"] == "fragment:r2c3"
    assert "60.009" in pending["message"]


@pytest.mark.parametrize("project_total", ["70.00", "71.00"])
def test_expense_missing_basic_amount_keeps_other_vertical_column_checks(project_total):
    doc = grid_document([
        ["功能分类科目编码", "科目名称", "本年支出合计", "基本支出", "项目支出",
         "上缴上级支出", "经营支出", "对附属单位补助支出"],
        ["合计", "", "150.00", "80.00", project_total, "0", "0", "0"],
        ["201", "一般公共服务", "100.00", "5.0.00", "50.00", "0", "0", "0"],
        ["208", "社会保障", "50.00", "30.00", "20.00", "0", "0", "0"],
    ], headers=1)
    payload = build_issues_payload(doc, report_kind="final", rules=[R33241_Table3_ExpenseAdvancedCheck])
    receipts = {r["sub_check_id"]: r for r in payload["fact_receipts"]}
    assert receipts["expenditure:horizontal:201"]["status"] == "insufficient_data"
    assert receipts["expenditure:vertical:basic_actual:total"]["status"] == "insufficient_data"
    [pending] = payload["rule_execution_summary"]["unresolved_rules"]
    assert pending["status"] == "insufficient_data"
    expected = "pass" if project_total == "70.00" else "fail"
    key = "expenditure:vertical:project_actual:total"
    assert receipts[key]["status"] == expected
    assert any(i.get("check_relation_id") == f"V33-241:{key}"
               for i in payload["issues"]["all"]) == (expected == "fail")

    # 缺失原件行的反例不得拿剩余行求出一个可交付的纵向结论。
    table = doc.parse_artifact.table_fragments[0]
    doc.parse_artifact.physical_fragments = [deepcopy(table)]
    table.cells.pop()
    doc.fact_receipts.clear()
    doc.__dict__.pop("_table_admission_receipt", None)
    replay = build_issues_payload(doc, report_kind="final", rules=[R33241_Table3_ExpenseAdvancedCheck])
    assert not any(r["sub_check_id"] == key for r in replay.get("fact_receipts", []))
