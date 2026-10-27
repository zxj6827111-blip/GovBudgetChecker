"""行身份：编码列优先、编码/合计冲突转 ambiguous、合法合计保留。"""
from __future__ import annotations

import pytest

from src.engine.fact_context import operand_rejection_code, operand_rejection_reason
from src.schemas.parse_artifact import ParsedCell, ParsedTable
from src.services.fiscal_fact_materializer import FiscalFactMaterializer


def _cell(row, col, text):
    return {"row_idx": row, "col_idx": col, "raw_text": text, "id": row * 10 + col}


def _mapping(col, measure, labels=(0, 1)):
    return {"source_col_idx": col, "canonical_measure": measure,
            "source_col_name": measure, "source_label_cols": list(labels),
            "confidence": 0.9}


def _cells(rows):
    return [_cell(row, col, text) for row, values in enumerate(rows) for col, text in enumerate(values)]


def test_code_column_wins_over_name_total_substring():
    materializer = FiscalFactMaterializer(None)
    rows = [["编码", "科目名称", "本年收入合计"],
            ["20103", "项合计政府办公厅（室）及相关机构事务", "955.35"]]
    facts = materializer.build_facts_for_table(
        "FIN_02_income", _cells(rows), [_mapping(2, "total_actual")], 0.9,
        code_columns=[0],
    )
    fact = next(f for f in facts if f.classification_code == "20103")
    assert fact.classification_name == "项合计政府办公厅（室）及相关机构事务"
    assert fact.extra_dims["classification_status"] == "code_total_conflict"
    assert fact.extra_dims["classification_conflict"]["code_candidates"] == ["20103"]
    assert "合计" in fact.extra_dims["classification_conflict"]["total_markers"][0]


def test_clean_code_row_is_bound():
    materializer = FiscalFactMaterializer(None)
    rows = [["编码", "科目名称", "本年收入合计"],
            ["20103", "政府办公厅（室）及相关机构事务", "955.35"]]
    fact = materializer.build_facts_for_table(
        "FIN_02_income", _cells(rows), [_mapping(2, "total_actual")], 0.9,
        code_columns=[0],
    )[0]
    assert fact.classification_code == "20103"
    assert fact.extra_dims["classification_status"] == "code_bound"


def test_standalone_total_marker_remains_total():
    materializer = FiscalFactMaterializer(None)
    rows = [["编码", "科目名称", "本年收入合计"],
            ["合计", "", "31730.89"]]
    fact = materializer.build_facts_for_table(
        "FIN_02_income", _cells(rows), [_mapping(2, "total_actual")], 0.9,
        code_columns=[0],
    )[0]
    assert fact.classification_code == "total"
    assert fact.extra_dims["classification_status"] == "total_marker"


@pytest.mark.parametrize("label", ["人员经费合计", "公用经费合计"])
def test_structural_totals_are_preserved(label):
    materializer = FiscalFactMaterializer(None)
    rows = [["编码", "科目名称", "本年收入合计"],
            ["", label, "100.00"]]
    fact = materializer.build_facts_for_table(
        "FIN_02_income", _cells(rows), [_mapping(2, "total_actual")], 0.9,
        code_columns=[0],
    )[0]
    assert fact.classification_code == "total"
    assert fact.extra_dims["classification_status"] == "structural_total"
    assert fact.classification_name == label


def _parsed_table(rows):
    cells = []
    for row_idx, values in enumerate(rows):
        for col_idx, value in enumerate(values):
            cells.append(ParsedCell(
                row_idx=row_idx, col_idx=col_idx, raw_text=value,
                normalized_text=value, numeric_value=None, page_number=1,
                bbox=(0, row_idx * 10, 100, row_idx * 10 + 10),
                is_header=row_idx == 0, confidence=0.99, unit_hint=None,
                extraction_method="lines",
            ))
    return ParsedTable(
        table_code="FIN_02_income", title="收入决算表", page_number=1,
        table_index=0, bbox=(0, 0, 100, 30), header_row_count=1,
        confidence=0.99, extraction_method="lines", unit_hint="万元",
        rows=rows, cells=cells, fragment_uid="identity-fixture",
    )


def test_conflict_fact_is_ambiguous_and_rejected_with_structured_code():
    rows = [["编码", "科目名称", "本年收入合计"],
            ["2010302", "合计一般行政管理事务", "955.35"]]
    table = _parsed_table(rows)
    fact = FiscalFactMaterializer(None).build_fiscal_facts_for_table(
        table, [_mapping(2, "total_actual")], fiscal_year=2024,
        budget_stage="决算", org_name="测试单位",
    )[0]
    assert fact.subject_code == "2010302"
    assert fact.recognition_status == "ambiguous"
    assert fact.extra_dims["row_identity_status"] == "code_total_conflict"
    assert fact.extra_dims["qualification_evidence"]["identity"] == "conflict"
    assert operand_rejection_reason(fact) is not None
    assert operand_rejection_code(fact) == "identity_conflict"


def test_clean_code_fact_is_not_rejected_for_identity():
    rows = [["编码", "科目名称", "本年收入合计"],
            ["2010302", "一般行政管理事务", "955.35"]]
    table = _parsed_table(rows)
    fact = FiscalFactMaterializer(None).build_fiscal_facts_for_table(
        table, [_mapping(2, "total_actual")], fiscal_year=2024,
        budget_stage="决算", org_name="测试单位",
    )[0]
    assert fact.extra_dims["row_identity_status"] == "bound"
    assert operand_rejection_code(fact) != "identity_conflict"
