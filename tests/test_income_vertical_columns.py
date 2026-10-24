"""按独立金额真值核对收入来源列纵向关系，空白不默认补零。"""
from decimal import Decimal

import pytest

from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33240_Table2_IncomeAdvancedCheck
from src.schemas.parse_artifact import LogicalTableInstance, ParseArtifact, ParsedCell, ParsedTable


def document(*, unknown=False):
    rows = [
        ["功能分类科目编码", "科目名称", "本年收入合计", "财政拨款收入", "上级补助收入", "事业收入", "经营收入", "附属单位上缴收入", "其他收入"],
        ["合计", "", "300.00", "200.00", "0.00", "90.00", "0.00", "0.00", "0.00"],
        ["205", "教育支出", "200.00", "150.00", "0.00", "50.00", "0.00", "0.00", "0.00"],
        ["20502", "普通教育", "200.00", "150.00", "0.00", "50.00", "0.00", "0.00", "0.00"],
        ["208", "社会保障和就业支出", "100.00", "50.00", "0.00", "40.00", "0.00", "0.00", "0.00"],
        ["20805", "行政事业单位养老支出", "100.00", "50.00", "0.00", "40.00", "0.00", "0.00", "0.00"],
    ]
    if unknown:
        rows[2][3] = ""
    cells = [ParsedCell(r, c, value, value, Decimal(value) if r and c >= 2 and value else None,
                        1, (c * 70, r * 25, (c + 1) * 70, (r + 1) * 25), r == 0,
                        .99, "万元", "lines", native_blank_verified=not value)
             for r, row in enumerate(rows) for c, value in enumerate(row)]
    code = "FIN_02_income"
    table = ParsedTable(code, "收入决算表", 1, 0, (0, 0, 630, 150), 1, .99, "lines", "万元", rows, cells, "fragment")
    instance = LogicalTableInstance("instance", code, table.title, ["fragment"], 1, 1,
                                    org_name="甲市测试中心", fiscal_year=2025)
    artifact = ParseArtifact(source_file_hash="a" * 64, parse_run_uid="run", table_fragments=[table],
                             physical_fragments=[table], logical_tables=[instance])
    context = build_fact_context_from_artifact(artifact, org_name="甲市测试中心", fiscal_year=2025,
                                               report_kind="final", doc_type="unknown")
    doc = build_document("source.pdf", ["甲市测试中心2025年度决算"], [[rows]], 0)
    doc.parse_artifact, doc.fact_context = artifact, context
    return doc


def test_all_income_columns_execute_vertical_relations_with_scope_pending():
    doc = document()
    R33240_Table2_IncomeAdvancedCheck().apply(doc)
    receipts = {row["sub_check_id"]: row for row in doc.fact_receipts}
    for code in ("total", "205", "208"):
        for role in ("total_actual", "fiscal_allocation", "superior_subsidy_income", "business_income",
                     "operational_income", "subsidiary_income", "other_income"):
            assert receipts[f"FIN_02_income:{code}:{role}"]["status"] == "pass"
    assert receipts["FIN_02_income:208:income_sources"]["status"] == "fail"


def test_unknown_source_cell_blocks_only_dependent_relations(monkeypatch):
    monkeypatch.delenv("GOVBUDGET_EMPTY_VALUE_CONVENTIONS", raising=False)
    doc = document(unknown=True)
    with pytest.raises(RuleDeferred) as caught:
        R33240_Table2_IncomeAdvancedCheck().apply(doc)
    clue = next(issue for issue in caught.value.partial_issues if issue.severity == 'manual_review')
    assert '差额150.00万元' in clue.message and '差额可能由空白或未知格承担' in clue.message
    missing = clue.location['unknown_operand_cells'][0]
    assert missing['raw_text'] == '' and missing['normalized_value'] is None
    assert missing['physical_page'] == 1 and missing['bbox'] and missing['source_cell_id']
    receipts = {row["sub_check_id"]: row for row in doc.fact_receipts}
    assert receipts["FIN_02_income:total:fiscal_allocation"]["status"] == "insufficient_data"
    assert receipts["FIN_02_income:total:business_income"]["status"] == "pass"
